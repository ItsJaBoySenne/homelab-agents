#!/bin/sh
# Restore a dump from ./backups. By default into a NEW database next to the live one, which is not touched:
#   sudo scripts/restore-backup.sh agent_platform-20261108-0330.dump            -> agent_platform_restore_20261108_0330
#   sudo scripts/restore-backup.sh agent_platform-20261108-0330.dump --name test_copy
# --replace makes the restored copy the live database: it restores into a new database first, asks you to
# confirm, stops agent-router and queue-worker, renames the live database to <db>_before_restore_<time>
# (kept, drop it yourself later) and the restored one to <db>, then starts both again. New connections to the live
# database are blocked during the switch and the rename is retried; if it still fails, the live database is opened
# again unchanged.
set -eu
usage() { echo "usage: $0 FILE [--name NEW_DB] [--replace]" >&2; exit 2; }
cd "$(dirname "$0")/.." || exit 1

file=""; name=""; replace=no
while [ $# -gt 0 ]; do
    case "$1" in
        --replace) replace=yes ;;
        --name) [ $# -ge 2 ] || usage; name=$2; shift ;;
        -h|--help) usage ;;
        -*) usage ;;
        *) [ -z "$file" ] || usage; file=$1 ;;
    esac
    shift
done
[ -n "$file" ] || usage

# The dump must be in ./backups, which is what the backup container sees as /backups.
base=$(basename "$file")
if [ ! -f "backups/$base" ]; then
    echo "backups/$base not found. Copy the dump into $(pwd)/backups first (sudo install -m 600 FILE backups/)." >&2
    exit 1
fi
if [ -e "$file" ] && ! cmp -s "$file" "backups/$base"; then
    echo "$file differs from backups/$base. Copy it into backups/ under another name first." >&2
    exit 1
fi
if [ -z "$name" ]; then
    stamp=$(echo "$base" | sed -n 's/.*-\([0-9]\{8\}\)-\([0-9]\{4\}\).*\.dump$/\1_\2/p')
    name="agent_platform_restore_${stamp:-$(date +%Y%m%d_%H%M)}"
fi
case "$name" in *[!A-Za-z0-9_]*|"") echo "database name must be letters, digits and _" >&2; exit 2 ;; esac

# shellcheck disable=SC2016  # $PGDATABASE expands inside the container
live=$(./dc run --rm -T --no-deps --entrypoint /bin/sh backup -c 'echo "$PGDATABASE"' | tr -d '\r')
[ -n "$live" ] || { echo "cannot read the database name from the backup service" >&2; exit 1; }
[ "$name" != "$live" ] || { echo "--name must differ from the live database ($live); use --replace to overwrite it" >&2; exit 2; }

if [ "$replace" = yes ]; then
    echo "This replaces the LIVE database '$live' with backups/$base."
    echo "agent-router and queue-worker stop for the switch; the current database is kept as ${live}_before_restore_<time>."
    printf "Type the live database name (%s) to continue: " "$live"
    read -r answer || answer=""
    [ "$answer" = "$live" ] || { echo "Not confirmed; nothing changed." >&2; exit 1; }
fi

echo "Restoring backups/$base into new database $name ..."
./dc run --rm -T backup restore "/backups/$base" "$name"

if [ "$replace" = no ]; then
    echo
    echo "Done. The live database '$live' is unchanged. To inspect: sudo ./dc exec database psql -U <POSTGRES_USER> -d $name"
    echo "To drop the copy: sudo ./dc exec database dropdb -U <POSTGRES_USER> $name"
    exit 0
fi

old="${live}_before_restore_$(date +%Y%m%d_%H%M%S)"
psql_admin() { ./dc run --rm -T --no-deps --entrypoint psql backup -X -q -v ON_ERROR_STOP=1 -d postgres; }
echo "Stopping agent-router and queue-worker ..."
./dc stop agent-router queue-worker
# No new connections to the live database while it is switched (other clients would otherwise reconnect between the
# disconnect and the rename, and the rename fails while anyone is connected).
if ! echo "ALTER DATABASE \"$live\" ALLOW_CONNECTIONS false;" | psql_admin; then
    echo "Could not block new connections; '$live' is unchanged. Starting agent-router and queue-worker again." >&2
    ./dc start agent-router queue-worker
    exit 1
fi
switched=no
for attempt in 1 2 3 4 5; do
    # Disconnect what is left (waiting up to 5 s for each to end), then both renames in one transaction: either both
    # happen or neither does.
    if psql_admin <<SQL
SELECT count(pg_terminate_backend(pid, 5000)) AS disconnected FROM pg_stat_activity WHERE datname IN ('$live', '$name') AND pid <> pg_backend_pid();
BEGIN;
ALTER DATABASE "$live" RENAME TO "$old";
ALTER DATABASE "$name" RENAME TO "$live";
COMMIT;
SQL
    then
        switched=yes
        break
    fi
    echo "Switch attempt $attempt of 5 failed (usually a connection that was still open)." >&2
    [ "$attempt" = 5 ] || sleep 2
done
if [ "$switched" = no ]; then
    echo "ALTER DATABASE \"$live\" ALLOW_CONNECTIONS true;" | psql_admin \
        || echo "Could not allow connections again; run: ALTER DATABASE \"$live\" ALLOW_CONNECTIONS true;" >&2
    echo "Switch failed; '$live' is unchanged. Starting agent-router and queue-worker again." >&2
    ./dc start agent-router queue-worker
    exit 1
fi
# The kept copy (renamed with the flag set) can be opened for inspection.
echo "ALTER DATABASE \"$old\" ALLOW_CONNECTIONS true;" | psql_admin \
    || echo "Note: '$old' does not accept connections; ALTER DATABASE \"$old\" ALLOW_CONNECTIONS true; to inspect it." >&2
echo "Starting agent-router and queue-worker ..."
./dc start agent-router queue-worker
echo
echo "Done. '$live' now holds backups/$base. The previous database is kept as '$old'."
echo "Check http://<platform-host>:8088/health, then drop the old one: sudo ./dc exec database dropdb -U <POSTGRES_USER> $old"
