# Tests

Not shipped in the image (the Dockerfile copies named files and directories only).

No Docker or root needed: `tests/local-env.sh <dir>` builds a Python venv, a throwaway Postgres 17
(unix socket only), Node and Playwright Chromium from Debian packages and official tarballs, then
`source <dir>/env.sh` sets `TEST_DATABASE_URL` and the paths used below. `<dir>/pg-stop.sh` stops Postgres.
Gateway suites: every `../ssh-gateway/tests/test_*.py` (`test_audit.py` covers the audit read endpoint and masking).

Both suites need a **throwaway** Postgres: they drop and recreate its `public` schema.
They refuse to run unless `TEST_DATABASE_URL` and `I_UNDERSTAND_THIS_WIPES_TEST_DB=1` are set.

## Backend: approvals, identity, read-only runs, task list

Mocks TypeSafe, OpenRouter and the SSH gateway at the HTTP layer; everything else is the real app.

```bash
pip install -r requirements.txt
TEST_DATABASE_URL=postgresql://user@localhost:5433/scratch I_UNDERSTAND_THIS_WIPES_TEST_DB=1 \
  python tests/test_thread_approval.py
```

`tests/test_audit_recheck.py` (same variables) covers the Audit page's proxy (`/ui/api/audit`, against the gateway's
real `gateway/audit.py` on SQLite) and the stale-fact re-check.

## Dashboard: browser end-to-end and screenshots

`e2e/preview_server.py` runs the real app on http://127.0.0.1:8765/ui/ (password `pw`) with seeded
tasks and conversations, mocked model providers, and a tool-enabled "Infra Agent" (mocked SSH gateway)
so live replies show tool steps. Useful on its own for UI work.

```bash
TEST_DATABASE_URL=... I_UNDERSTAND_THIS_WIPES_TEST_DB=1 DEV_DELAY=1 python tests/e2e/preview_server.py &
cd tests/e2e && npm install --no-save playwright@1.48.2 && npx playwright install chromium
node e2e.js                 # interaction checks (approve in thread, composer, drawer, filters, phone)
mkdir -p shots && node shots.js shots   # every view at desktop, laptop and phone width
```

Restart the preview server before re-running `e2e.js`; it approves and rejects the seeded requests.
