"""EXPLAIN (ANALYZE, BUFFERS) of the SQL behind the dashboard endpoints, copied from app.py / threads/routes.py /
approvals.py. Read-only except expire_stale (run inside a rolled-back transaction).

  TEST_DATABASE_URL=... python tests/perf/explain.py [name-filter] [--indexes]
--indexes creates the recommended indexes inside a transaction, re-runs the plans and rolls back
(nothing persists), to show the effect of the recommended fixes.
"""
import os
import sys
import time

import psycopg

DB = os.environ['TEST_DATABASE_URL']
flt = [a for a in sys.argv[1:] if not a.startswith('--')]
with_indexes = '--indexes' in sys.argv

TASKS_SELECT = r"""SELECT t.id, t.created_at, t.finished_at, t.status, t.worker_model, t.openrouter_cost_usd,
    t.agent_id, a.name AS agent_name, t.workspace, t.risk_probability, t.error_message,
    left(btrim(coalesce(substring(t.task_text from 'LATEST USER MESSAGE:\s*(.*)$'), t.task_text)), 300) AS summary
  FROM tasks t LEFT JOIN agents a ON a.id = t.agent_id {where} ORDER BY t.created_at DESC LIMIT 26 OFFSET {off}"""
PENDING = '''(SELECT count(*) FROM agent_thread_messages m JOIN tasks k ON k.id=m.task_id
    WHERE m.thread_id=t.id AND ((m.kind='approval_request' AND k.status='human_approval_required')
        OR (m.kind IS DISTINCT FROM 'approval_request' AND m.role='assistant' AND k.pending_command->>'status'='pending')))::int AS pending_approvals'''

QUERIES = {
    'expire_stale.status': """UPDATE tasks SET status='expired', error_message='Approval request expired'
        WHERE status='human_approval_required' AND created_at < NOW() - make_interval(hours => 72) RETURNING id""",
    'expire_stale.command': """UPDATE tasks SET pending_command=pending_command||'{"status":"expired"}'
        WHERE pending_command->>'status'='pending' AND created_at < NOW() - make_interval(hours => 72) RETURNING id""",
    'summary': '''SELECT count(*)::int, count(*) FILTER (WHERE status='completed')::int, coalesce(sum(openrouter_cost_usd),0),
        coalesce(sum(input_tokens),0)::bigint FROM tasks''',
    'tasks.page1': TASKS_SELECT.format(where='', off=0),
    'tasks.offset49000': TASKS_SELECT.format(where='', off=49000),
    'tasks.status_failed': TASKS_SELECT.format(where="WHERE t.status = ANY('{failed}')", off=0),
    'tasks.workspace_work': TASKS_SELECT.format(where="WHERE t.workspace = 'work'", off=0),
    'tasks.q_nomatch': TASKS_SELECT.format(where="WHERE t.task_text ILIKE '%zzzznomatch%'", off=0),
    'threads.list': '''SELECT t.*,a.name AS agent_name,''' + PENDING + ''' FROM agent_threads t
        LEFT JOIN agents a ON a.id=t.agent_id ORDER BY t.updated_at DESC LIMIT 100''',
    'thread.largest': '''SELECT * FROM (SELECT m.id,m.role,m.content,m.task_id,m.kind,m.created_at,
            t.status AS task_status,t.risk_probability,t.progress,t.pending_command,d.actor AS decided_by,d.scope AS decision_scope
        FROM agent_thread_messages m LEFT JOIN tasks t ON t.id=m.task_id
        LEFT JOIN LATERAL (SELECT actor,scope FROM task_approval_events e
            WHERE e.task_id=m.task_id AND m.kind='approval_request' AND e.action IN ('approved','rejected')
            ORDER BY e.created_at DESC LIMIT 1) d ON TRUE
        WHERE m.thread_id=(SELECT thread_id FROM agent_thread_messages GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)
        ORDER BY m.created_at DESC,m.id DESC LIMIT 500) recent ORDER BY created_at,id''',
    'knowledge.home': '''SELECT id,workspace,title,body,source,created_at,updated_at FROM knowledge WHERE workspace='home'
        ORDER BY updated_at DESC LIMIT 100''',
    'knowledge.q': '''SELECT id FROM knowledge WHERE workspace='home' AND (title ILIKE '%nginx%' OR body ILIKE '%nginx%')
        ORDER BY updated_at DESC LIMIT 100''',
    'facts.page': '''SELECT m.id,m.key,m.fact,a.name FROM agent_memories m LEFT JOIN agents a ON a.id=m.agent_id
        WHERE m.workspace='home' AND m.kind IS DISTINCT FROM 'task' ORDER BY m.updated_at DESC LIMIT 26 OFFSET 0''',
    'facts.counts': """SELECT count(*) FILTER (WHERE confidence='verified' AND (expires_at IS NULL OR expires_at>NOW())),
        count(*) FILTER (WHERE confidence='verified' AND expires_at<=NOW()), count(*) FILTER (WHERE confidence='unverified')
        FROM agent_memories WHERE workspace='home' AND kind IS DISTINCT FROM 'task'""",
    'agents.list': '''SELECT a.*, (SELECT COUNT(*) FROM tasks t WHERE t.agent_id=a.id)::int AS task_count,
        COALESCE((SELECT SUM(t.openrouter_cost_usd) FROM tasks t WHERE t.agent_id=a.id),0) AS total_cost_usd
        FROM agents a ORDER BY name''',
    'memory.retrieve': '''SELECT key,fact,source,confidence,verified_at,expires_at,kind FROM agent_memories
        WHERE workspace='home' ORDER BY updated_at DESC LIMIT 400''',
    # Recommended rewrite: page first (index order, no regex), compute the regex summary only for the page.
    'tasks.rewrite_page1': TASKS_SELECT.replace('FROM tasks t LEFT', 'FROM (SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT 26 OFFSET {off}) t LEFT')
        .replace('{where} ORDER BY t.created_at DESC LIMIT 26 OFFSET {off}', 'ORDER BY t.created_at DESC').format(where='', off=0),
    'tasks.rewrite_offset49000': TASKS_SELECT.replace('FROM tasks t LEFT', 'FROM (SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT 26 OFFSET {off}) t LEFT')
        .replace('{where} ORDER BY t.created_at DESC LIMIT 26 OFFSET {off}', 'ORDER BY t.created_at DESC').format(where='', off=49000),
    'sse.poll': '''SELECT status,partial_text,result_text,error_message,progress FROM tasks WHERE id=(SELECT id FROM tasks LIMIT 1)''',
}

INDEXES = [
    'CREATE INDEX tasks_created_idx ON tasks (created_at DESC)',
    'CREATE INDEX tasks_status_created_idx ON tasks (status, created_at DESC)',
    'CREATE INDEX tasks_workspace_created_idx ON tasks (workspace, created_at DESC)',
    "CREATE INDEX tasks_awaiting_idx ON tasks (created_at) WHERE status='human_approval_required'",
    "CREATE INDEX tasks_pending_cmd_idx ON tasks (created_at) WHERE pending_command->>'status'='pending'",
    'CREATE INDEX knowledge_ws_updated_idx ON knowledge (workspace, updated_at DESC)',
]

with psycopg.connect(DB) as db:
    t = time.perf_counter()
    for _ in range(20):
        psycopg.connect(DB).close()
    print(f'psycopg.connect()+close over unix socket: {(time.perf_counter() - t) / 20 * 1000:.2f} ms each\n')
    if with_indexes:
        db.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        for ix in INDEXES:
            db.execute(ix)
        db.execute('ANALYZE tasks'); db.execute('ANALYZE knowledge')
    for name, sql in QUERIES.items():
        if flt and not any(f in name for f in flt):
            continue
        plan = db.execute('EXPLAIN (ANALYZE, BUFFERS) ' + sql).fetchall()
        print('=== ' + name)
        for (line,) in plan:
            print('   ' + line)
        print()
    db.rollback()
