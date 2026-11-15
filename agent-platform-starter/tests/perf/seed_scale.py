"""Scale the preview database to production-like volume (run AFTER preview_server.py printed READY).

Adds, on top of the preview seed:
  * 10 agents
  * ~50k tasks (half standalone, half conversation runs with the full transcript prompt as task_text),
    with result_text (markdown), progress JSONB on tool runs, a few pending approvals / commands
  * 2k conversations, the largest with 500 messages (one task per assistant reply)
  * task_approval_events for approval-gated tasks
  * 20k agent_memories facts (+ task-history rows) and 5k knowledge notes
Then ANALYZE. Usage:
  TEST_DATABASE_URL=... python tests/perf/seed_scale.py [--tasks 50000] [--threads 2000] [--facts 20000] [--notes 5000]
"""
import argparse
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

p = argparse.ArgumentParser()
p.add_argument('--tasks', type=int, default=50000)
p.add_argument('--threads', type=int, default=2000)
p.add_argument('--facts', type=int, default=20000)
p.add_argument('--notes', type=int, default=5000)
p.add_argument('--largest', type=int, default=500, help='messages in the largest conversation')
args = p.parse_args()
DB = os.environ['TEST_DATABASE_URL']
rnd = random.Random(42)
now = datetime.now(timezone.utc)

WORDS = ('server disk nginx docker proxmox backup container volume mount permission share samba nfs jellyfin gpu '
         'transcode monitoring grafana prometheus alert memory cpu load network vlan firewall certificate traefik '
         'authelia deploy rollback compose image registry snapshot restore checksum latency throughput error').split()

def words(n):
    return ' '.join(rnd.choice(WORDS) for _ in range(n))

def markdown(size_paras):
    parts = ['## ' + words(4).capitalize()]
    for _ in range(size_paras):
        kind = rnd.random()
        if kind < 0.15:
            parts.append('| Host | Value | State |\n|---|---|---|\n' + '\n'.join(
                f'| `{rnd.choice(WORDS)}-{i}` | {rnd.randint(1, 99)}% | **{rnd.choice(["ok", "warn", "fail"])}** |' for i in range(6)))
        elif kind < 0.3:
            parts.append('```bash\n' + '\n'.join('sudo ' + words(5) for _ in range(4)) + '\n```')
        elif kind < 0.5:
            parts.append('\n'.join(f'{i + 1}. {words(10)} `{rnd.choice(WORDS)}`' for i in range(5)))
        else:
            parts.append(words(60) + ' **' + words(3) + '** ' + words(30))
    return '\n\n'.join(parts)

# Ordinary English prose: real notes contain words like "use", "new", "user", "history" (see REPORT: knowledge retrieval).
PROSE = ['Use this procedure when a new user reports that the share is not visible; check the history of recent changes first.',
         'The agent may contain stale context, so verify the current state before you follow the steps below.',
         'This note describes how we use the backup history to restore a user folder to a previous version.',
         'If the message in the log mentions a new certificate, follow the renewal steps and check the context of the error.']

STATUSES = ['completed'] * 80 + ['failed'] * 7 + ['incomplete'] * 5 + ['rejected'] * 3 + ['cancelled'] * 3 + ['expired'] * 2

def step(i):
    return {'tool': rnd.choice(['server_diagnostic', 'server_maintenance', 'knowledge_search']),
            'target': rnd.choice(WORDS) + '-0' + str(i), 'status': 'ok', 'exit_code': rnd.choice([0, 0, 0, 1]),
            'seconds': round(rnd.random() * 4, 2)}

with psycopg.connect(DB) as db:
    agents = [uuid.uuid4() for _ in range(10)]
    with db.cursor().copy('COPY agents(id,name,role,instructions,workspace,model) FROM STDIN') as cp:
        for i, a in enumerate(agents):
            cp.write_row((a, f'Perf Agent {i}', 'role ' + words(3), words(80), 'home' if i % 3 else 'work', 'qwen/qwen3-235b-a22b'))

    # Conversation sizes: one of exactly `largest`, a few big ones, the rest geometric-ish (mean ~12 msgs).
    sizes = [args.largest] + [rnd.randint(150, 400) for _ in range(9)]
    sizes += [max(2, int(rnd.expovariate(1 / 12))) // 2 * 2 for _ in range(args.threads - len(sizes))]
    thread_tasks = sum(s // 2 for s in sizes)
    standalone = max(0, args.tasks - thread_tasks)
    print('threads', len(sizes), 'messages', sum(sizes), 'thread tasks', thread_tasks, 'standalone tasks', standalone)

    task_cols = ('id,created_at,finished_at,task_text,status,provider,worker_model,result_text,input_tokens,output_tokens,'
                 'openrouter_cost_usd,agent_id,workspace,error_message,risk_probability,expert_probability,progress,pending_command')
    events = []
    largest_thread = None
    with db.cursor().copy(f'COPY tasks({task_cols}) FROM STDIN') as tcp:
        def task_row(tid, created, text, ws, agent, status=None, pending=None):
            st = status or rnd.choice(STATUSES)
            res = markdown(rnd.randint(2, 8)) if st == 'completed' else None
            prog = json.dumps([step(i) for i in range(rnd.randint(1, 6))]) if agent and rnd.random() < 0.5 else None
            err = {'failed': 'OpenRouter failed: ReadTimeout', 'incomplete': 'Model response truncated at output token limit',
                   'rejected': 'Rejected by dashboard_session', 'cancelled': 'Stopped by user', 'expired': 'Approval request expired'}.get(st)
            tcp.write_row((tid, created, created + timedelta(seconds=rnd.randint(3, 120)), text, st, 'openrouter',
                           'qwen/qwen3-235b-a22b', res, rnd.randint(300, 9000), rnd.randint(100, 3000),
                           round(rnd.random() / 500, 8), agent, ws, err, rnd.random() * 0.39, rnd.random(), prog,
                           json.dumps(pending) if pending else None))
            if st in ('rejected', 'expired'):
                events.append((uuid.uuid4(), tid, 'requested', 'typesafe', created))
                events.append((uuid.uuid4(), tid, 'rejected' if st == 'rejected' else 'expired', 'dashboard_session', created + timedelta(minutes=5)))
            return st

        for i in range(standalone):
            created = now - timedelta(seconds=rnd.randint(60, 365 * 86400))
            # A handful of live approval holds (some older than the 72h TTL -> expire_stale has work on first read).
            st = 'human_approval_required' if i < 40 else None
            if st and i < 10:
                created = now - timedelta(hours=100 + i)
            elif st:
                created = now - timedelta(hours=rnd.randint(1, 48))
            text = rnd.choice(['Check disk usage on ', 'Install jq on ', 'Why does ', 'Summarise ', 'Explain ']) + words(rnd.randint(10, 120))
            task_row(uuid.uuid4(), created, text, 'home' if rnd.random() < 0.75 else 'work', rnd.choice(agents + [None] * 5), st)

        thread_rows, msg_rows = [], []
        for n, size in enumerate(sizes):
            tid = uuid.uuid4()
            if n == 0:
                largest_thread = tid
            ws = 'home' if rnd.random() < 0.8 else 'work'
            agent = rnd.choice(agents + [None, None])
            start = now - timedelta(seconds=rnd.randint(3600, 300 * 86400))
            t = start
            history = []
            for k in range(size // 2):
                t += timedelta(minutes=rnd.randint(1, 600) if n else rnd.randint(1, 30))
                user = words(rnd.randint(5, 60))
                transcript = '\n\n'.join(history[-14:])
                prompt = ('This is a follow-up in an existing agent conversation...\n\n'
                          + ('PREVIOUS CONVERSATION:\n' + transcript + '\n\n' if transcript else '')
                          + 'LATEST USER MESSAGE:\n' + user)[-11900:]
                task_id = uuid.uuid4()
                pending = None
                if n < 30 and k == size // 2 - 1:
                    pending = {'server': 'dev', 'command': 'sudo systemctl restart ' + rnd.choice(WORDS), 'status': 'pending'}
                st = task_row(task_id, t, prompt, ws, agent, 'completed' if pending else None, pending)
                reply = markdown(rnd.randint(1, 6))
                msg_rows.append((uuid.uuid4(), tid, 'user', user, None, None, t))
                msg_rows.append((uuid.uuid4(), tid, 'assistant', reply, task_id,
                                 'approval_request' if st in ('rejected', 'expired') else None, t + timedelta(seconds=30)))
                history += ['USER: ' + user, 'ASSISTANT: ' + reply[:3500]]
            # The largest one is kept among the 100 most recent (the list endpoint shows only those) so the UI can open it.
            thread_rows.append((tid, 'Conversation ' + str(n) + ': ' + words(5), ws, agent, start, now - timedelta(hours=1) if n == 0 else t, 'idle'))

    with db.cursor().copy('COPY agent_threads(id,title,workspace,agent_id,created_at,updated_at,status) FROM STDIN') as cp:
        for r in thread_rows: cp.write_row(r)
    with db.cursor().copy('COPY agent_thread_messages(id,thread_id,role,content,task_id,kind,created_at) FROM STDIN') as cp:
        for r in msg_rows: cp.write_row(r)
    with db.cursor().copy('COPY task_approval_events(id,task_id,action,actor,created_at) FROM STDIN') as cp:
        for r in events: cp.write_row(r)

    with db.cursor().copy('COPY agent_memories(id,workspace,key,fact,source,confidence,verified_at,updated_at,kind,server,task_id,agent_id,expires_at,previous_fact) FROM STDIN') as cp:
        for i in range(args.facts):
            ws = 'home' if rnd.random() < 0.85 else 'work'
            kind = rnd.choice(['diagnostic'] * 6 + ['change'] * 2 + ['proxmox'])
            server = rnd.choice(['dev', 'fs-01', 'docker-01', 'public-website', 'pve1', 'nas'])
            upd = now - timedelta(seconds=rnd.randint(60, 200 * 86400))
            verified = rnd.random() < 0.85
            cp.write_row((uuid.uuid4(), ws, f'diag:{server}:{kind}:{i}', f'{kind} on {server}:\n' + words(rnd.randint(20, 200)),
                          'ssh-gateway:diagnostic', 'verified' if verified else 'unverified', upd if verified else None, upd,
                          kind, server, None, rnd.choice(agents), upd + timedelta(hours=rnd.choice([1, 6, 24, 720])),
                          words(30) if rnd.random() < 0.1 else None))
        # Unverified task history (excluded from the Facts page, but scanned by retrieve()).
        for i in range(args.facts // 2):
            upd = now - timedelta(seconds=rnd.randint(60, 80 * 86400))
            cp.write_row((uuid.uuid4(), 'home', f'task:{uuid.uuid4()}', 'Prior task: ' + words(40) + ' | Answer summary: ' + words(80),
                          'task-history', 'unverified', None, upd, 'task', None, None, None, None, None))

    with db.cursor().copy('COPY knowledge(id,workspace,title,body,source,created_at,updated_at) FROM STDIN') as cp:
        for i in range(args.notes):
            upd = now - timedelta(seconds=rnd.randint(60, 400 * 86400))
            cp.write_row((uuid.uuid4(), 'home' if rnd.random() < 0.7 else 'work', words(6).capitalize(), rnd.choice(PROSE) + '\n\n' + markdown(rnd.randint(2, 10)),
                          'https://wiki.example/' + words(2).replace(' ', '/'), upd, upd))
    db.commit()
    db.execute('ANALYZE')
    print('largest thread', largest_thread)
    for t in ('tasks', 'agent_threads', 'agent_thread_messages', 'agent_memories', 'knowledge', 'task_approval_events'):
        print(t, db.execute(f'SELECT count(*), pg_size_pretty(pg_total_relation_size(%s)) FROM {t}', (t,)).fetchone())
