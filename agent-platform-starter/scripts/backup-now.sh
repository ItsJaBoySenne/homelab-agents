#!/bin/sh
# One backup of the platform database now, into ./backups as <db>-YYYYmmdd-HHMM-manual.dump.
# Manual dumps are verified like the nightly ones but never rotated out: delete them yourself.
#   cd agent-platform-starter && sudo scripts/backup-now.sh
set -eu
cd "$(dirname "$0")/.." || exit 1
if [ -n "$(./dc ps -q --status running backup 2>/dev/null)" ]; then
    exec ./dc exec -T backup /bin/sh /usr/local/bin/pg-backup.sh once manual
fi
# The backup service is not running (not deployed yet, or stopped): a one-off container does the same.
exec ./dc run --rm -T backup once manual
