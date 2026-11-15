#!/bin/bash
# Start a fresh seeded preview server for the audit on port 8771 / database audit_ui; waits for READY.
# Needs the test environment from tests/local-env.sh: ENV_DIR=<dir> (the directory you passed to it).
source "${ENV_DIR:?set ENV_DIR to the directory built by tests/local-env.sh}/env.sh"
# Same Postgres as env.sh, database audit_ui (create it once: createdb or CREATE DATABASE audit_ui).
export TEST_DATABASE_URL="${TEST_DATABASE_URL%%/postgres\?*}/audit_ui?${TEST_DATABASE_URL#*\?}" DEV_PORT=8771
cd "$(dirname "$0")/../../.." && I_UNDERSTAND_THIS_WIPES_TEST_DB=1 DEV_DELAY=${DEV_DELAY:-1} nohup python tests/e2e/preview_server.py > tests/e2e/audit/server.log 2>&1 &
for i in $(seq 1 60); do grep -q READY tests/e2e/audit/server.log 2>/dev/null && { echo READY; exit 0; }; sleep 1; done; cat tests/e2e/audit/server.log; exit 1
