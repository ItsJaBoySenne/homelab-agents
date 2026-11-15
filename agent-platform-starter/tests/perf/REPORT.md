# Performance audit: API, database, SSE, dashboard (2026-10-28)

**Data** (`seed_scale.py`): 50k tasks (12.8k conversation runs with full-transcript prompts), 2k conversations
(largest 500 messages), 30k agent_memories, 5k knowledge notes. **Setup:** single-process uvicorn
(`tests/e2e/preview_server.py`, `DEV_DELAY=0.05`), Postgres 17 with default config, 4 vCPU.
"Before" numbers are at `c490006` (SSE also at `a7e52cd`). The fixes and "after" numbers are at the end.

## Before

| Endpoint (sequential) | p50 ms |
|---|---:|
| tasks page 1 / q=nginx / q=no match | 512 / 817 / 1098 |
| tasks status=failed / workspace=work | 143 / 328 |
| tasks/{id} | 47 |
| threads list | 48 |
| threads/{largest}, 500 msgs, 678 KB | 65 |
| knowledge q=nginx | 99 |
| agents | 42 |
| facts, summary, policy | 20–28, 22, 6 |

| Concurrency | Result |
|---|---|
| 1 run | API stalled **2.95 s** (no-DB probe) |
| 5 runs | probe stalled up to 14.6 s; first content 12 s |
| 10 runs | first content 27 s |
| 20 readers | 14 rps, tasks p50 9.2 s |
| 20 readers + 5 runs | 3.9 rps |
| 50 SSE streams | 99 new DB connections/s at `a7e52cd`; 16/s with the event bus |

| Browser | Result |
|---|---|
| open a 500-message conversation | 538 ms long task, 15.1k DOM nodes |
| refresh it | 523 ms, again on every finished run |

## Findings, ranked
1. **Each run froze the API for about 3 s.** `retrieve_knowledge` ran synchronously inside async `execute_task`
   (a 3 s seq scan recomputing `to_tsvector`). Its terms came from the conversation preamble instead of the
   user's message. More sync psycopg ran on the event loop in `execute_task` and in async routes.
2. **The tasks list projected a regex summary over every row** before the top-N sort. There was no
   `created_at`/`status`/`workspace` index, and `q` was an unindexed `ILIKE`.
3. **`approvals.expire_stale` ran two seq-scan UPDATEs on almost every read** (about 33 ms).
4. **There was no connection pool:** one `psycopg.connect()` per call (2 ms, up to 200 connections/s).
5. **Opening or refreshing a big conversation** rebuilt 500 messages with two forced layouts. The threads list
   was capped at 100 with no search, so older conversations could not be reached.
6. **Smaller:** the knowledge list had no `(workspace, updated_at)` index; agent cost sums lacked a covering index.

Scripts: `bench_api.py`, `bench_concurrency.py`, `explain.py` (`--indexes`), `whatif_indexes.py`, `perf_ui.js`,
`seed_scale.py`; see README.md. Raw output is in `results/`.

## After the fixes (same data and machine)

Fixes:
- Run DB work moved off the event loop (agent check, knowledge and memory recall, task updates, audit events,
  fact capture).
- Knowledge search uses the user's message and a stored, indexed `tsv` column.
- Task-list indexes: `created_at`, `status`, `workspace`, a trigram index for `q`, and partial indexes for approval
  expiry. The page is selected first and summaries are built only for it.
- Connection pool (`psycopg-pool`).
- Conversation messages paged (newest 100, "Show earlier"), with `content-visibility` and no re-render while hidden.
- Server-side conversation search.

| Measurement | Before | After |
|---|---:|---:|
| tasks page 1 / q=nginx / q=no match (p50 ms) | 512 / 817 / 1098 | 7.8 / 10.8 / 4.0 |
| tasks offset 49000 | 533 | 53 |
| task detail / threads list | 47 / 48 | 3.6 / 10 |
| largest thread (ms, KB) | 65 ms, 678 KB | 8.7 ms, 119 KB |
| 1 run: probe max / done | 2,951 ms / 3.1 s | 16 ms / 0.31 s |
| 5 runs: probe max / done | 14,649 ms / 14.9 s | 38 ms / 0.42 s |
| 10 runs: first content / done | 27.1 s / 27.4 s | 0.41 s / 0.92 s |
| 20 readers + 5 runs: probe max / run done | 11,933 ms / 12.7 s | 73 ms / 1.2 s |
| open largest conversation (wall, longest task) | 770 ms, 538 ms | 145 ms, none |
| refresh it | 835 ms, 523 ms | 179 ms, none |
| Knowledge view right after Send | 3,586 ms | 652 ms |

Remaining: the agents list is 46 ms (per-agent COUNT/SUM). Knowledge list bodies are still sent in full (354 KB);
the edit form uses them. Note for the local test Postgres: it has no LLVM, so set `jit = off` on the perf database
(`ALTER DATABASE audit_perf SET jit = off`).
