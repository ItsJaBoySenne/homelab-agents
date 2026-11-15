# Performance tests

Load and latency benchmarks for the API and dashboard with production-like data volume. Findings: `REPORT.md`.
They need the toolchain from `tests/local-env.sh` (Python venv, Postgres 17, Node + Playwright). Use a **throwaway** database
(the preview server wipes it) and a free port.

```bash
source <env-dir>/env.sh
export TEST_DATABASE_URL='postgresql://agents@/audit_perf?host=<pg-socket-dir>&port=54329' DEV_PORT=8772 I_UNDERSTAND_THIS_WIPES_TEST_DB=1

# 1. real app with mocked model providers / SSH gateway (wait for READY)
DEV_DELAY=0.05 nohup python tests/e2e/preview_server.py > /tmp/perf_server.log 2>&1 &
# 2. scale the data: 50k tasks, 2k conversations (largest 500 messages), 30k memories, 5k knowledge notes (~20 s)
python tests/perf/seed_scale.py
# 3. sequential latency of every dashboard read endpoint (p50/p95/max, size)
python tests/perf/bench_api.py --n 20 --json tests/perf/results/api.json
# 4. EXPLAIN (ANALYZE, BUFFERS) of the SQL behind them; --indexes = with the recommended indexes (rolled back)
python tests/perf/explain.py [name-filter] [--indexes]
# 5. concurrency: readers, conversation runs, SSE followers, with an event-loop probe and DB connection rate
python tests/perf/bench_concurrency.py --scenario readers --readers 20 --duration 20
python tests/perf/bench_concurrency.py --scenario runs --runs 5
python tests/perf/bench_concurrency.py --scenario mixed --readers 20 --runs 5
python tests/perf/bench_concurrency.py --scenario sse --sse 50
# 6. dashboard rendering in headless Chromium (wall time, long tasks, DOM size, background requests)
BASE=http://127.0.0.1:$DEV_PORT node tests/perf/perf_ui.js [--trace trace.json]
# what-if: create the recommended indexes, re-run 3/5, then drop them again
python tests/perf/whatif_indexes.py create   # ... benchmarks ...   python tests/perf/whatif_indexes.py drop
```

Stop the preview server by finding its PID with `ps -eo pid,args | grep "[p]review_server"` and killing that PID.

`bench_concurrency.py`'s probe is `GET /ui/api/deployments`, an `async def` route with no database access: its latency
is a proxy for event-loop stalls. The `runs` scenario creates new conversations and posts messages back to back,
following each run over SSE. Raw results from the runs in REPORT.md are in `results/`. Files prefixed `a7e52cd_` come
from the commit before the live-events rework; the others come from `c490006`.
