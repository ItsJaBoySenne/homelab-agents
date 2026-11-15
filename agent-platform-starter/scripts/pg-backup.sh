#!/bin/sh
# Platform database backups (POSIX sh; runs in the compose `backup` service on postgres:17-alpine).
#
#   pg-backup.sh loop                nightly at BACKUP_TIME (TZ), forever (the service's command)
#   pg-backup.sh once [manual]       one backup now; "manual" ones are kept until you delete them
#   pg-backup.sh rotate              apply the retention only
#   pg-backup.sh restore FILE DB     restore FILE into the NEW database DB (fails if DB exists)
#
# Connection from the standard libpq variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE).
# Each dump is written as .partial, checked with pg_restore --list, then renamed to
# BACKUP_DIR/BACKUP_NAME-YYYYmmdd-HHMM.dump (mode 600). Retention keeps the newest dump of each of the
# last BACKUP_KEEP_DAYS days that have one, plus the newest of each of the last BACKUP_KEEP_WEEKS ISO weeks.
# BACKUP_DIR/status.json: last attempt, last success, file, size, error, next run.
set -u
umask 077

BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_NAME=${BACKUP_NAME:-agent_platform}
BACKUP_TIME=${BACKUP_TIME:-03:30}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
BACKUP_KEEP_WEEKS=${BACKUP_KEEP_WEEKS:-8}
STATUS="$BACKUP_DIR/status.json"
ERRLOG=${TMPDIR:-/tmp}/pg-backup.$$.err

log() { echo "$(date '+%Y-%m-%d %H:%M:%S %Z') backup: $*"; }

# JSON string: drop control characters, escape backslashes and quotes, keep it short.
json_str() { printf '"%s"' "$(printf '%s' "$1" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g' | cut -c1-500)"; }

# Keeps fields from the previous status file that this write does not change.
prev_field() { [ -f "$STATUS" ] && sed -n "s/^  \"$1\": \\(.*\\),\$/\\1/p" "$STATUS" | head -n 1; }

write_status() { # ok|error, file, size, error
    now=$(date '+%Y-%m-%dT%H:%M:%S%z')
    if [ "$1" = ok ]; then
        success=$(json_str "$now"); file=$(json_str "$2"); size=$3; err=null
    else
        success=$(prev_field last_success); file=$(prev_field last_file); size=$(prev_field size_bytes)
        if [ -n "$4" ]; then err=$(json_str "$4"); else err=null; fi
    fi
    tmp="$STATUS.tmp.$$"
    {
        printf '{\n'
        printf '  "last_attempt": %s,\n' "$(json_str "$now")"
        printf '  "last_success": %s,\n' "${success:-null}"
        printf '  "last_file": %s,\n' "${file:-null}"
        printf '  "size_bytes": %s,\n' "${size:-null}"
        printf '  "error": %s,\n' "$err"
        if [ -n "${NEXT_RUN:-}" ]; then nr=$(json_str "$NEXT_RUN"); else nr=null; fi
        printf '  "next_run": %s\n' "$nr"
        printf '}\n'
    } > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$STATUS"
}

# Only the next_run line changes; the rest of the status stays as the last attempt left it.
set_next_run() {
    tmp="$STATUS.tmp.$$"
    sed "s/^  \"next_run\": .*/  \"next_run\": $(json_str "$NEXT_RUN")/" "$STATUS" > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$STATUS"
}

fail() { log "FAILED: $1"; write_status error "" "" "$1"; rm -f "$ERRLOG"; return 1; }

# Last line of the tool's stderr, for the status file (pg_dump never prints the password).
last_err() { tail -n 1 "$ERRLOG" 2>/dev/null; }

backup_once() { # [manual]
    mkdir -p "$BACKUP_DIR" || { log "cannot create $BACKUP_DIR"; return 1; }
    suffix=""; [ "${1:-}" = manual ] && suffix="-manual"
    out="$BACKUP_DIR/$BACKUP_NAME-$(date +%Y%m%d-%H%M)$suffix.dump"
    part="$out.partial"
    log "dumping database ${PGDATABASE:-?} to $(basename "$out")"
    if ! pg_dump -Fc --no-password -f "$part" 2> "$ERRLOG"; then
        rm -f "$part"; fail "pg_dump failed: $(last_err)"; return 1
    fi
    if ! pg_restore --list "$part" > /dev/null 2> "$ERRLOG"; then
        rm -f "$part"; fail "verification (pg_restore --list) failed: $(last_err)"; return 1
    fi
    if ! { chmod 600 "$part" && mv -f "$part" "$out"; }; then fail "cannot move dump into place"; return 1; fi
    size=$(wc -c < "$out" | tr -d ' ')
    entries=$(pg_restore --list "$out" | grep -c '^[0-9]')
    rm -f "$ERRLOG"
    log "ok: $(basename "$out"), $size bytes, $entries entries"
    rotate
    write_status ok "$(basename "$out")" "$size"
}

# Dumps under rotation: NAME-YYYYmmdd-HHMM.dump only (manual and other files are left alone).
rotate() {
    days_kept=0; weeks_kept=0; last_day=""; seen_weeks=" "
    # Names sort chronologically; newest first.
    for f in $(for p in "$BACKUP_DIR/$BACKUP_NAME"-*.dump; do echo "${p##*/}"; done | grep -E "^$BACKUP_NAME-[0-9]{8}-[0-9]{4}\.dump\$" | sort -r); do
        stamp=${f#"$BACKUP_NAME"-}; day=${stamp%%-*}
        week=$(date -d "$(echo "$day" | sed 's/\(....\)\(..\)\(..\)/\1-\2-\3/')" +%G-%V 2>/dev/null) || week=""
        keep=no
        if [ "$day" != "$last_day" ]; then
            last_day=$day
            [ "$days_kept" -lt "$BACKUP_KEEP_DAYS" ] && { keep=yes; days_kept=$((days_kept + 1)); }
            case "$seen_weeks" in
                *" $week "*) ;;
                *) seen_weeks="$seen_weeks$week "
                   [ -n "$week" ] && [ "$weeks_kept" -lt "$BACKUP_KEEP_WEEKS" ] && { keep=yes; weeks_kept=$((weeks_kept + 1)); } ;;
            esac
        fi
        if [ "$keep" = no ]; then
            rm -f "$BACKUP_DIR/$f" && log "rotated out $f"
        fi
    done
    # Leftovers of an interrupted run.
    find "$BACKUP_DIR" -maxdepth 1 -name '*.partial' -mmin +720 -exec rm -f {} + 2>/dev/null
    return 0
}

# Seconds until the next BACKUP_TIME (recomputed each night, so DST changes are followed).
next_run_epoch() {
    now=$(date +%s)
    t=$(date -d "$(date +%Y-%m-%d) $BACKUP_TIME" +%s) || return 1
    [ "$t" -le "$now" ] && t=$(date -d "$(date -d "@$((now + 86400))" +%Y-%m-%d) $BACKUP_TIME" +%s)
    echo "$t"
}

loop() {
    case "$BACKUP_TIME" in
        [0-1][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) log "BACKUP_TIME must be HH:MM (got '$BACKUP_TIME')"; exit 2 ;;
    esac
    trap 'log "stopping"; exit 0' TERM INT
    log "nightly at $BACKUP_TIME ${TZ:-UTC}, keeping $BACKUP_KEEP_DAYS daily and $BACKUP_KEEP_WEEKS weekly dumps in $BACKUP_DIR"
    while :; do
        t=$(next_run_epoch) || { log "cannot compute the next run"; exit 2; }
        NEXT_RUN=$(date -d "@$t" '+%Y-%m-%dT%H:%M:%S%z')
        if [ -f "$STATUS" ]; then set_next_run; else write_status error "" "" ""; fi  # no backup yet
        log "next backup at $NEXT_RUN"
        sleep $((t - $(date +%s))) & wait $!
        n=$(next_run_epoch); NEXT_RUN=$(date -d "@$n" '+%Y-%m-%dT%H:%M:%S%z')
        backup_once || true
    done
}

restore() { # FILE DB
    file=$1; db=$2
    case "$db" in *[!A-Za-z0-9_]*|"") echo "database name must be letters, digits and _" >&2; return 2 ;; esac
    [ -r "$file" ] || { echo "cannot read $file" >&2; return 2; }
    pg_restore --list "$file" > /dev/null || { echo "$file is not a valid pg_dump -Fc archive" >&2; return 1; }
    exists=$(psql -X -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'") || return 1
    [ "$exists" = 1 ] && { echo "database $db already exists; choose another name or drop it first" >&2; return 1; }
    createdb -T template0 "$db" || return 1
    if ! pg_restore --exit-on-error --single-transaction --no-owner --no-privileges -d "$db" "$file"; then
        echo "restore failed; dropping $db" >&2; dropdb "$db"; return 1
    fi
    echo "restored $(basename "$file") into $db; tables and rows:"
    psql -X -d "$db" -tA -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1" |
        while read -r t; do
            printf '  %s %s\n' "$t" "$(psql -X -d "$db" -tAc "SELECT count(*) FROM public.\"$t\"")"
        done
}

case "${1:-loop}" in
    loop) loop ;;
    once) backup_once "${2:-}" ;;
    rotate) rotate ;;
    restore) [ $# -eq 3 ] || { echo "usage: $0 restore FILE NEW_DB" >&2; exit 2; }; restore "$2" "$3" ;;
    *) echo "usage: $0 loop | once [manual] | rotate | restore FILE NEW_DB" >&2; exit 2 ;;
esac
