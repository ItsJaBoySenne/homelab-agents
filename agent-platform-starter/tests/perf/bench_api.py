"""Sequential latency of every dashboard read endpoint (p50/p95/max, response size).

  BASE=http://127.0.0.1:8772 python tests/perf/bench_api.py [--n 30] [--json out.json]
Requests are issued one at a time (no concurrency) so the numbers are per-request cost.
"""
import argparse
import asyncio
import json
import os
import statistics
import time

import httpx

BASE = os.environ.get('BASE', 'http://127.0.0.1:' + os.environ.get('DEV_PORT', '8772'))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def login(c):
    r = await c.post('/ui/api/login', json={'password': 'pw'})
    r.raise_for_status()


async def ids(c):
    threads = (await c.get('/ui/api/threads')).json()['threads']
    tasks = (await c.get('/ui/api/tasks?limit=5')).json()['tasks']
    return threads, tasks


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=30)
    ap.add_argument('--json')
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        await login(c)
        threads, tasks = await ids(c)
        # seed_scale.py names the largest conversation "Conversation 0: ..." and keeps it in the visible top 100
        largest = os.environ.get('LARGEST_THREAD') or next(
            (t['id'] for t in threads if t['title'].startswith('Conversation 0:')), threads[0]['id'])
        endpoints = [
            ('summary', '/ui/api/summary'),
            ('tasks (page 1)', '/ui/api/tasks?limit=25&offset=0'),
            ('tasks status=failed', '/ui/api/tasks?limit=25&offset=0&status=failed'),
            ('tasks status=human_approval_required', '/ui/api/tasks?limit=25&status=human_approval_required'),
            ('tasks workspace=work', '/ui/api/tasks?limit=25&workspace=work'),
            ('tasks q=nginx', '/ui/api/tasks?limit=25&q=nginx'),
            ('tasks q=zzzz (no match)', '/ui/api/tasks?limit=25&q=zzzznomatch'),
            ('tasks offset=10000', '/ui/api/tasks?limit=25&offset=10000'),
            ('tasks offset=49000', '/ui/api/tasks?limit=25&offset=49000'),
            ('task detail', '/ui/api/tasks/' + tasks[0]['id']),
            ('threads (list)', '/ui/api/threads'),
            ('threads?workspace=home', '/ui/api/threads?workspace=home'),
            ('thread largest (500 msgs)', '/ui/api/threads/' + largest),
            ('knowledge home', '/ui/api/knowledge?workspace=home'),
            ('knowledge q=nginx', '/ui/api/knowledge?workspace=home&q=nginx'),
            ('facts home', '/ui/api/facts?workspace=home&limit=25'),
            ('facts state=stale', '/ui/api/facts?workspace=home&state=stale&limit=25'),
            ('facts q=nginx', '/ui/api/facts?workspace=home&q=nginx&limit=25'),
            ('facts offset=5000', '/ui/api/facts?workspace=home&limit=25&offset=5000'),
            ('policy', '/ui/api/policy'),
            ('deployments', '/ui/api/deployments'),
            ('sandbox', '/ui/api/sandbox'),
            ('agents', '/ui/api/agents'),
            ('session', '/ui/api/session'),
        ]
        out = []
        print(f"{'endpoint':42} {'p50ms':>8} {'p95ms':>8} {'max':>8} {'KB':>8}")
        for name, path in endpoints:
            await c.get(path)  # warm
            lat, size = [], 0
            for _ in range(a.n):
                t = time.perf_counter()
                r = await c.get(path)
                lat.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, (path, r.status_code, r.text[:200])
                size = len(r.content)
            row = {'endpoint': name, 'path': path, 'p50': statistics.median(lat), 'p95': pct(lat, 95), 'max': max(lat), 'kb': size / 1024}
            out.append(row)
            print(f"{name:42} {row['p50']:8.1f} {row['p95']:8.1f} {row['max']:8.1f} {row['kb']:8.1f}", flush=True)
        if a.json:
            json.dump(out, open(a.json, 'w'), indent=1)


asyncio.run(main())
