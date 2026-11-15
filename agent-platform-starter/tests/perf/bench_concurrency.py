"""Concurrent load: dashboard readers, conversation runs and SSE followers, with an event-loop probe.

  BASE=http://127.0.0.1:8772 TEST_DATABASE_URL=... python tests/perf/bench_concurrency.py \
      --scenario readers --readers 20 --duration 20
Scenarios:
  idle      probe only (baseline)
  readers   N clients polling the dashboard read endpoints back-to-back
  runs      K conversation runs back-to-back (each followed over SSE: time to first content / done)
  mixed     readers + runs
  sse       M SSE streams following one task that stays 'running'; the bench updates its partial_text
            every second and measures how late each stream sees the update
The probe calls GET /ui/api/deployments every 100 ms: an `async def` route with no DB access, so its
latency is a proxy for event-loop stalls (anything blocking the loop delays it).
DB load is read from pg_stat_database (sessions = new connections, xact_commit) for the run window.
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import time
import uuid

import httpx
import psycopg

BASE = os.environ.get('BASE', 'http://127.0.0.1:' + os.environ.get('DEV_PORT', '8772'))
DB = os.environ['TEST_DATABASE_URL']
H = {'x-agent-ui': '1'}


def pct(xs, p):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summary(xs):
    return {'n': len(xs), 'p50': round(pct(xs, 50), 1), 'p95': round(pct(xs, 95), 1), 'max': round(max(xs), 1) if xs else None}


def db_stats():
    with psycopg.connect(DB) as c:
        return c.execute("SELECT sessions, xact_commit, tup_returned FROM pg_stat_database WHERE datname=current_database()").fetchone()


async def client():
    c = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(120, read=None))
    (await c.post('/ui/api/login', json={'password': 'pw'})).raise_for_status()
    return c


async def probe(stop, out):
    c = await client()
    while not stop.is_set():
        t = time.perf_counter()
        await c.get('/ui/api/deployments')
        out.append((time.perf_counter() - t) * 1000)
        await asyncio.sleep(0.1)
    await c.aclose()


async def reader(stop, out, paths):
    c = await client()
    while not stop.is_set():
        name, path = random.choice(paths)
        t = time.perf_counter()
        r = await c.get(path)
        out.setdefault(name, []).append((time.perf_counter() - t) * 1000)
        if r.status_code != 200:
            out.setdefault('errors', []).append(r.status_code)
    await c.aclose()


async def follow(c, task_id, t0, res):
    first = None
    async with c.stream('GET', f'/ui/api/tasks/{task_id}/events') as r:
        event = None
        async for line in r.aiter_lines():
            if line.startswith('event: '):
                event = line[7:]
                if event == 'content' and first is None:
                    first = time.perf_counter()
                    res['ttft'].append((first - t0) * 1000)
                if event == 'done':
                    res['done'].append((time.perf_counter() - t0) * 1000)
                    return


async def runner(stop, res, idx):
    c = await client()
    tid = (await c.post('/ui/api/threads', headers=H, json={'title': f'perf run {idx}', 'workspace': 'home'})).json()['id']
    while not stop.is_set():
        t0 = time.perf_counter()
        r = await c.post(f'/ui/api/threads/{tid}/messages', headers=H,
                         json={'content': 'Check nginx and docker status on the monitoring servers and summarise backup and cpu usage.'})
        res['post'].append((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            res['errors'].append(r.text[:120]); await asyncio.sleep(0.5); continue
        await follow(c, r.json()['task_id'], t0, res)
        for _ in range(100):  # wait for the thread to go idle (reply inserted)
            if (await c.get(f'/ui/api/threads/{tid}')).json()['thread']['status'] == 'idle':
                break
            await asyncio.sleep(0.05)
    await c.aclose()


async def sse_watcher(stop, task_id, seen, idx, connected):
    c = await client()
    async with c.stream('GET', f'/ui/api/tasks/{task_id}/events') as r:
        connected.append(idx)
        event = None
        async for line in r.aiter_lines():
            if stop.is_set():
                break
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: ') and event == 'content':
                text = json.loads(line[6:])['text']
                if text.startswith('marker:'):
                    seen.append(time.time() - float(text.split(':')[1]))
    await c.aclose()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default='readers', choices=['idle', 'readers', 'runs', 'mixed', 'sse'])
    ap.add_argument('--readers', type=int, default=20)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--sse', type=int, default=50)
    ap.add_argument('--duration', type=float, default=20)
    ap.add_argument('--skip', default='', help='comma-separated reader endpoints to leave out, e.g. tasks')
    ap.add_argument('--json')
    a = ap.parse_args()
    c = await client()
    threads = (await c.get('/ui/api/threads')).json()['threads']
    await c.aclose()
    paths = [('summary', '/ui/api/summary'), ('tasks', '/ui/api/tasks?limit=25'), ('threads', '/ui/api/threads'),
             ('thread', '/ui/api/threads/' + threads[min(5, len(threads) - 1)]['id']), ('facts', '/ui/api/facts?workspace=home&limit=25'),
             ('task-detail', None)]
    c = await client()
    tasks = (await c.get('/ui/api/tasks?limit=5&status=completed')).json()['tasks']
    await c.aclose()
    paths[-1] = ('task-detail', '/ui/api/tasks/' + tasks[0]['id'])
    paths = [p for p in paths if p[0] not in a.skip.split(',')]

    stop = asyncio.Event()
    probe_lat, reads = [], {}
    res = {'post': [], 'ttft': [], 'done': [], 'errors': []}
    seen, connected = [], []
    jobs = [asyncio.create_task(probe(stop, probe_lat))]
    sse_task = None
    if a.scenario in ('readers', 'mixed'):
        jobs += [asyncio.create_task(reader(stop, reads, paths)) for _ in range(a.readers)]
    if a.scenario in ('runs', 'mixed'):
        jobs += [asyncio.create_task(runner(stop, res, i)) for i in range(a.runs)]
    if a.scenario == 'sse':
        sse_task = uuid.uuid4()
        with psycopg.connect(DB) as d:
            d.execute("INSERT INTO tasks(id,task_text,status,workspace) VALUES (%s,'perf sse','running','home')", (sse_task,))
        jobs += [asyncio.create_task(sse_watcher(stop, sse_task, seen, i, connected)) for i in range(a.sse)]
        for _ in range(100):
            if len(connected) >= a.sse: break
            await asyncio.sleep(0.1)
    s0, t0 = db_stats(), time.time()
    end = time.time() + a.duration
    while time.time() < end:
        if sse_task:
            with psycopg.connect(DB) as d:
                d.execute('UPDATE tasks SET partial_text=%s WHERE id=%s', ('marker:' + repr(time.time()), sse_task))
        await asyncio.sleep(1)
    s1, t1 = db_stats(), time.time()
    stop.set()
    if sse_task:
        with psycopg.connect(DB) as d:
            d.execute("UPDATE tasks SET status='failed',error_message='perf sse done',finished_at=NOW() WHERE id=%s", (sse_task,))
    await asyncio.wait(jobs, timeout=30)
    for j in jobs: j.cancel()
    secs = t1 - t0
    out = {'scenario': a.scenario, 'skip': a.skip, 'readers': a.readers if a.scenario in ('readers', 'mixed') else 0,
           'runs': a.runs if a.scenario in ('runs', 'mixed') else 0, 'sse': a.sse if a.scenario == 'sse' else 0,
           'probe_ms': summary(probe_lat),
           'db_new_connections_per_s': round((s1[0] - s0[0]) / secs, 1), 'db_commits_per_s': round((s1[1] - s0[1]) / secs, 1)}
    if reads:
        total = sum(len(v) for k, v in reads.items() if k != 'errors')
        out['reader_rps'] = round(total / a.duration, 1)
        out['reads'] = {k: summary(v) for k, v in reads.items() if k != 'errors'}
        out['read_errors'] = len(reads.get('errors', []))
    if res['post']:
        out['runs_completed'] = len(res['done'])
        out['run_post_ms'] = summary(res['post'])
        out['run_first_content_ms'] = summary(res['ttft'])
        out['run_done_ms'] = summary(res['done'])
        out['run_errors'] = res['errors'][:3]
    if seen:
        out['sse_update_delay_ms'] = summary([x * 1000 for x in seen])
    print(json.dumps(out, indent=1))
    if a.json:
        with open(a.json, 'a') as f:
            f.write(json.dumps(out) + '\n')


asyncio.run(main())
