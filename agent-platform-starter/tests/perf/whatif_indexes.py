"""What-if: create (or drop) the indexes recommended in REPORT.md on the perf database, to measure their effect
with bench_api.py / bench_concurrency.py without touching application code.

  TEST_DATABASE_URL=... python tests/perf/whatif_indexes.py create
  TEST_DATABASE_URL=... python tests/perf/whatif_indexes.py drop
"""
import os
import sys
import time

import psycopg

INDEXES = {
    'perf_tasks_created_idx': 'ON tasks (created_at DESC)',
    'perf_tasks_status_created_idx': 'ON tasks (status, created_at DESC)',
    'perf_tasks_workspace_created_idx': 'ON tasks (workspace, created_at DESC)',
    'perf_tasks_awaiting_idx': "ON tasks (created_at) WHERE status = 'human_approval_required'",
    'perf_tasks_pending_cmd_idx': "ON tasks (created_at) WHERE pending_command->>'status' = 'pending'",
    'perf_tasks_text_trgm_idx': 'ON tasks USING gin (task_text gin_trgm_ops)',
    'perf_knowledge_ws_updated_idx': 'ON knowledge (workspace, updated_at DESC)',
}

with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as db:
    if sys.argv[1:] == ['create']:
        db.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        for name, spec in INDEXES.items():
            t = time.perf_counter()
            db.execute(f'CREATE INDEX IF NOT EXISTS {name} {spec}')
            print(f'{name}: {time.perf_counter() - t:.1f}s')
        db.execute('ANALYZE tasks'); db.execute('ANALYZE knowledge')
    elif sys.argv[1:] == ['drop']:
        for name in INDEXES:
            db.execute(f'DROP INDEX IF EXISTS {name}')
        print('dropped')
    else:
        sys.exit(__doc__)
