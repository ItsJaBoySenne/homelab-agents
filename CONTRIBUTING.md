# Contributing

Thanks for helping! Bug reports, docs fixes and pull requests are all welcome. For anything larger than a small fix,
open an issue first so we can agree on the approach. Security problems: see [SECURITY.md](SECURITY.md), not the issue
tracker.

## Layout

| Path | Language | Tests |
|---|---|---|
| `agent-platform-starter/` | Python 3.12 (FastAPI), vanilla JS dashboard in `ui/` | `tests/test_*.py` (need Postgres), `tests/e2e/` (Playwright) |
| `ssh-gateway/` | Python 3.12, stdlib + paramiko | `tests/test_*.py` (no database, SSH and APIs stubbed) |
| `ssh-fleet/`, `setup.sh` | bash | `bash -n`, `shellcheck` |
| `compose.yaml`, `deploy/` | Compose | `docker compose config` |

## Running the tests

The backend suites need a **throwaway** Postgres: they drop and recreate its `public` schema, and refuse to run
unless you confirm that with `I_UNDERSTAND_THIS_WIPES_TEST_DB=1`. One line gives you one:

```bash
docker run -d --rm --name hla-test-pg -e POSTGRES_USER=agents -e POSTGRES_PASSWORD=agents -e POSTGRES_DB=hla_test \
  -p 127.0.0.1:55432:5432 postgres:17
export TEST_DATABASE_URL=postgresql://agents:agents@127.0.0.1:55432/hla_test I_UNDERSTAND_THIS_WIPES_TEST_DB=1
```

(`docker stop hla-test-pg` removes it again. No Docker? `agent-platform-starter/tests/local-env.sh <dir>` builds
Postgres, a venv, Node and Chromium without root; see [its README](agent-platform-starter/tests/README.md).)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r agent-platform-starter/requirements.txt 'paramiko>=3.5,<5'

# Gateway (no database needed)
cd ssh-gateway && for t in tests/test_*.py; do python "$t" || echo "FAILED: $t"; done; cd ..

# Router
cd agent-platform-starter
for t in test_thread_approval test_attachments test_providers test_audit_recheck test_voice test_engines; do
  python tests/$t.py || echo "FAILED: $t"
done

# Browser end-to-end (Node 22): the preview server runs the real app with mocked providers on :8765
python tests/e2e/preview_server.py &        # wait for "READY"; password "pw"
(cd tests/e2e && npm install --no-save playwright@1.48.2 && npx playwright install chromium && node e2e.js)
kill %1
```

Each suite prints `PASS`/`FAIL` lines and ends with `ALL PASSED`, or exits non-zero. Restart the preview server
before re-running `e2e.js` (it approves and rejects its seeded requests).

Scripts and compose:

```bash
bash -n setup.sh && shellcheck setup.sh ssh-fleet/*.sh
cp agent-platform-starter/.env.example agent-platform-starter/.env      # if you have none yet
cp ssh-gateway/.env.gateway.example ssh-gateway/.env.gateway
mkdir -p config && [ -f config/inventory.yaml ] || cp config/inventory.example.yaml config/inventory.yaml
docker compose config -q
```

CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs all of this on every push and pull request,
without any secrets.

## Guidelines

- Keep the security model intact: credentials only in the gateway, every new action behind a level, audited, and
  approvals for anything destructive. Explain the threat model of a new tool in the pull request.
- Add or update tests with each change; the suites are plain scripts, no framework needed.
- No new runtime dependency without a good reason (the gateway is deliberately stdlib + paramiko).
- Never commit real hostnames, addresses, tokens or keys: use `example.com`, `192.168.1.x`, `203.0.113.x`.
- Docs: write for someone setting this up for the first time.
- By contributing you agree that your work is released under the [MIT license](LICENSE).
