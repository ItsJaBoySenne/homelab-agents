#!/usr/bin/env bash
# Homelab Agents: first-time setup and re-configuration.
#
#   ./setup.sh              interactive: asks a few questions, writes the settings, starts the stack
#   ./setup.sh --yes        non-interactive (CI, automation): defaults plus the flags/environment below
#   ./setup.sh --check      validate the configuration; changes nothing
#   ./setup.sh --help       all options
#
# Safe to re-run: existing secrets are kept (never regenerated), every settings file is backed up with a timestamp
# before it changes, and no secret is ever printed except the dashboard password, once, when it is first created.
# Works with bash 3.2+ (macOS) and needs only POSIX tools plus openssl or python3.

set -u
umask 077

ROOT=$(cd "$(dirname "$0")" && pwd -P) || exit 1
cd "$ROOT" || exit 1

ROUTER_ENV="agent-platform-starter/.env"
ROUTER_EXAMPLE="agent-platform-starter/.env.example"
GATEWAY_ENV="ssh-gateway/.env.gateway"
GATEWAY_EXAMPLE="ssh-gateway/.env.gateway.example"
TOP_ENV=".env"
INVENTORY="config/inventory.yaml"
INVENTORY_EXAMPLE="config/inventory.example.yaml"
FLEET_KEYS="${FLEET_KEYS:-$HOME/.ssh/hla-fleet}"
STAMP=$(date +%Y%m%d-%H%M%S)

# ---------------------------------------------------------------------------------------------------------------
# Options (flags win over environment variables)
# ---------------------------------------------------------------------------------------------------------------
MODE=setup                                   # setup | check
ASSUME_YES=false
START=true
RESET_PASSWORD=false
OPT_LISTEN="${HLA_LISTEN_ADDRESS:-}"
OPT_PROVIDER="${HLA_PROVIDER:-}"             # openrouter | xai | subscription | skip
OPT_MODEL="${HLA_DEFAULT_MODEL:-}"
OPT_VOICE="${HLA_VOICE:-}"                   # yes | no
OPT_SSH_KEYS="${HLA_SSH_KEYS:-}"             # yes | no
OPT_GATEWAY_IP="${HLA_GATEWAY_IP:-}"
OPT_SERVERS="${HLA_SERVERS:-}"               # space separated name=[user@]host[:port]
WAIT_SECONDS="${HLA_WAIT_SECONDS:-240}"

usage() {
  cat <<'EOF'
Usage: ./setup.sh [options]

  -y, --yes              Non-interactive: never ask, use defaults and the options below.
      --check            Validate the configuration (files, tokens, inventory, compose) and exit. Changes nothing.
      --no-start         Write the configuration but do not build or start the containers.
      --listen ADDR      Address the dashboard listens on: 127.0.0.1 (default, this machine only), a LAN IP of
                         this machine, or 0.0.0.0 (all interfaces).            env: HLA_LISTEN_ADDRESS
      --provider NAME    openrouter | xai | subscription | skip                 env: HLA_PROVIDER
                         Keys come from OPENROUTER_API_KEY / XAI_API_KEY in the environment (never a flag, so
                         they stay out of your shell history and process list).
      --model ID         Default model, e.g. anthropic/claude-sonnet-5.5 or xai/grok-4.  env: HLA_DEFAULT_MODEL
      --voice | --no-voice
                         Run Kokoro text-to-speech for the Chief's car mode (about 2 GB RAM). env: HLA_VOICE=yes|no
      --server SPEC      Add a server to config/inventory.yaml: name=[user@]host[:port] (repeatable).
                                                                                env: HLA_SERVERS (space separated)
      --ssh-keys | --no-ssh-keys
                         Generate a gateway SSH key per server (ssh-fleet/) and import it. env: HLA_SSH_KEYS=yes|no
      --gateway-ip IP    The address your servers see the gateway's SSH connections from (default: this
                         machine's main IPv4 address).                           env: HLA_GATEWAY_IP
      --reset-password   Generate a new dashboard password (shown once at the end).
  -h, --help             This help.

Examples:
  ./setup.sh
  OPENROUTER_API_KEY=... ./setup.sh --yes --provider openrouter --listen 192.168.1.10 --voice
  ./setup.sh --yes --no-start --provider skip          # CI: write config only
  ./setup.sh --check
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=true ;;
    --check) MODE=check ;;
    --no-start) START=false ;;
    --listen) OPT_LISTEN="${2:-}"; shift ;;
    --listen=*) OPT_LISTEN="${1#*=}" ;;
    --provider) OPT_PROVIDER="${2:-}"; shift ;;
    --provider=*) OPT_PROVIDER="${1#*=}" ;;
    --model) OPT_MODEL="${2:-}"; shift ;;
    --model=*) OPT_MODEL="${1#*=}" ;;
    --voice) OPT_VOICE=yes ;;
    --no-voice) OPT_VOICE=no ;;
    --server) OPT_SERVERS="$OPT_SERVERS ${2:-}"; shift ;;
    --server=*) OPT_SERVERS="$OPT_SERVERS ${1#*=}" ;;
    --ssh-keys) OPT_SSH_KEYS=yes ;;
    --no-ssh-keys) OPT_SSH_KEYS=no ;;
    --gateway-ip) OPT_GATEWAY_IP="${2:-}"; shift ;;
    --gateway-ip=*) OPT_GATEWAY_IP="${1#*=}" ;;
    --reset-password) RESET_PASSWORD=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s (see ./setup.sh --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# Not a terminal: behave as --yes rather than hang on a prompt.
if [ "$MODE" = setup ] && [ "$ASSUME_YES" = false ] && [ ! -t 0 ]; then
  ASSUME_YES=true
fi

# ---------------------------------------------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$(printf '\033[1m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m'); R=$(printf '\033[31m'); N=$(printf '\033[0m')
else
  B=''; G=''; Y=''; R=''; N=''
fi
step()  { printf '\n%s==> %s%s\n' "$B" "$1" "$N"; }
ok()    { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
info()  { printf '  %s\n' "$1"; }
warn()  { printf '  %s!%s %s\n' "$Y" "$N" "$1"; WARNINGS=$((WARNINGS + 1)); }
fail()  { printf '  %s✗%s %s\n' "$R" "$N" "$1"; ERRORS=$((ERRORS + 1)); }
die()   { printf '\n%sSetup stopped:%s %s\n' "$R" "$N" "$1" >&2; [ -n "${2:-}" ] && printf '%s\n' "$2" >&2; exit 1; }
WARNINGS=0
ERRORS=0
_choice=''; _name=''; _host=''; _user=''; _port=''; _desc=''; _key=''; _fp=''; NEW_SERVERS=''   # set by ask/ask_secret (eval)

ask() {  # ask VAR "Question" "default"
  _var=$1; _q=$2; _def=${3:-}
  if [ "$ASSUME_YES" = true ]; then eval "$_var=\$_def"; return; fi
  if [ -n "$_def" ]; then printf '  %s [%s]: ' "$_q" "$_def"; else printf '  %s: ' "$_q"; fi
  IFS= read -r _ans || _ans=''
  [ -z "$_ans" ] && _ans=$_def
  eval "$_var=\$_ans"
}
ask_secret() {  # ask_secret VAR "Question": typed without echo; never printed
  _var=$1
  if [ "$ASSUME_YES" = true ]; then eval "$_var=''"; return; fi
  printf '  %s (input hidden, Enter to skip): ' "$2"
  _old=$(stty -g 2>/dev/null) && stty -echo 2>/dev/null
  IFS= read -r _ans || _ans=''
  [ -n "${_old:-}" ] && stty "$_old" 2>/dev/null
  printf '\n'
  eval "$_var=\$_ans"
  _ans=''
}
yesno() {  # yesno "Question" default(yes|no) -> exit status
  _def=$2
  if [ "$ASSUME_YES" = true ]; then [ "$_def" = yes ]; return; fi
  while :; do
    if [ "$_def" = yes ]; then printf '  %s [Y/n]: ' "$1"; else printf '  %s [y/N]: ' "$1"; fi
    IFS= read -r _ans || _ans=''
    case "$_ans" in
      '') [ "$_def" = yes ]; return ;;
      y|Y|yes|YES|Yes) return 0 ;;
      n|N|no|NO|No) return 1 ;;
    esac
  done
}
is_yes() { case "$1" in y|Y|yes|YES|true|1|on) return 0 ;; *) return 1 ;; esac; }

# ---------------------------------------------------------------------------------------------------------------
# Random values (never printed, never on a command line)
# ---------------------------------------------------------------------------------------------------------------
rand_hex() {  # 32 bytes as 64 hex characters
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else python3 -c 'import secrets;print(secrets.token_hex(32))'; fi
}
rand_b64key() {  # 32 bytes, URL-safe base64 (44 characters): HLA_SECRETS_KEY / GATEWAY_CREDENTIAL_KEY
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
  else openssl rand -base64 32 | tr '+/' '-_'; fi
}
rand_password() {  # 24 characters, letters and digits only (easy to type on a phone)
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(24)))'
  else openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-24; fi
}

# ---------------------------------------------------------------------------------------------------------------
# .env files: edited as a working copy in a private temp dir, committed (with a timestamped backup) at the end
# ---------------------------------------------------------------------------------------------------------------
WORK=$(mktemp -d 2>/dev/null || mktemp -d -t hla-setup) || die "Could not create a temporary directory."
# shellcheck disable=SC2329  # called by the EXIT trap
cleanup() {
  for _f in "$WORK"/*; do [ -f "$_f" ] && : > "$_f"; done   # blank the working copies (they hold secrets) first
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'stty echo 2>/dev/null; exit 130' INT TERM

# Placeholder values shipped in the examples: treated as "not set".
is_placeholder() {
  case "$1" in
    ''|replace_me|REPLACE_ME|replace_with_long_random_secret|provider/model|user@pve!token|https://infisical.example) return 0 ;;
    *) return 1 ;;
  esac
}

# env_get FILE KEY -> value of the last KEY= line (quotes removed); empty when absent.
env_get() {
  [ -f "$1" ] || return 0
  awk -v k="$2" '
    { line=$0; sub(/^[ \t]*export[ \t]+/, "", line) }
    index(line, k "=") == 1 { v=substr(line, length(k)+2); found=1 }
    END {
      if (!found) exit
      if (v ~ /^".*"$/ || v ~ /^\047.*\047$/) v=substr(v, 2, length(v)-2)
      else sub(/[ \t]+#.*$/, "", v)
      print v
    }' "$1"
}
env_has() { [ -f "$1" ] && grep -q "^[[:space:]]*$2=" "$1"; }

# env_set FILE KEY VALUE: replace the first KEY= line (also a commented "# KEY=" line), else append. The value goes
# through the environment, not argv, so it never shows up in a process list.
env_set() {
  _file=$1; _key=$2; _val=$3
  case "$_val" in
    *"
"*|*"'"*) die "The value for $_key contains a newline or a single quote; refusing to write it." ;;
  esac
  if [ -z "$_val" ] || printf '%s' "$_val" | grep -q '^[A-Za-z0-9._:/@+=,!-]*$'; then _line="$_key=$_val"; else _line="$_key='$_val'"; fi
  HLA_LINE="$_line" awk -v k="$_key" '
    BEGIN { line=ENVIRON["HLA_LINE"] }
    !done && (index($0, k "=") == 1) { print line; done=1; next }
    { print }
    END { if (!done) print line }' "$_file" > "$_file.new" || die "Could not update $_key."
  # Second pass: a commented example line ("# KEY=") is replaced instead of appending, when nothing set it yet.
  if ! grep -q "^$_key=" "$_file"; then
    if grep -q "^#[[:space:]]*$_key=" "$_file"; then
      HLA_LINE="$_line" awk -v k="$_key" '
        BEGIN { line=ENVIRON["HLA_LINE"] }
        !done && ($0 ~ "^#[ \t]*" k "=") { print line; done=1; next }
        { print }' "$_file" > "$_file.new" || die "Could not update $_key."
    fi
  fi
  cat "$_file.new" > "$_file" && : > "$_file.new"
  _line=''; _val=''
}
# env_default FILE KEY VALUE: set only when missing or still a placeholder (keeps what the owner chose).
env_default() { is_placeholder "$(env_get "$1" "$2")" && env_set "$1" "$2" "$3"; return 0; }
# env_clear_placeholder FILE KEY: blank an example value that would otherwise be used as if it were real.
env_clear_placeholder() {
  env_has "$1" "$2" || return 0
  _cur=$(env_get "$1" "$2")
  if [ -n "$_cur" ] && is_placeholder "$_cur"; then env_set "$1" "$2" ""; fi
  return 0
}

# commit FILE WORKCOPY: back up the old file (if any and if it changed) and install the new one, mode 600.
commit_file() {
  _dst=$1; _src=$2
  if [ -f "$_dst" ] && cmp -s "$_dst" "$_src"; then ok "$_dst unchanged"; return 0; fi
  if [ -f "$_dst" ]; then
    cp -p "$_dst" "$_dst.backup-$STAMP" && chmod 600 "$_dst.backup-$STAMP"
    info "backed up the previous $_dst to $_dst.backup-$STAMP"
  fi
  mkdir -p "$(dirname "$_dst")"
  if ! { cat "$_src" > "$_dst.tmp-$STAMP" && chmod 600 "$_dst.tmp-$STAMP" && mv -f "$_dst.tmp-$STAMP" "$_dst"; }; then
    die "Could not write $_dst."
  fi
  ok "wrote $_dst"
}

# ---------------------------------------------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------------------------------------------
DOCKER="docker"
docker_cli_ok()      { command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; }
pick_docker() {  # [interactive]: use sudo when the daemon is only reachable as root (asks for a password only when
  docker info >/dev/null 2>&1 && { DOCKER="docker"; return 0; }   # called with "interactive", i.e. right before starting)
  if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    if sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo docker"; return 0; fi
    if [ "${1:-}" = interactive ] && [ "$ASSUME_YES" = false ] && [ -t 0 ]; then
      info "Docker needs root here; asking for your sudo password."
      if sudo docker info >/dev/null 2>&1; then DOCKER="sudo docker"; return 0; fi
    fi
  fi
  return 1
}
compose() { $DOCKER compose "$@"; }

valid_ipv4() {
  printf '%s' "$1" | awk -F. 'NF==4 { for (i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1; exit 0 } { exit 1 }'
}
main_ipv4() {
  _ip=''
  if command -v ip >/dev/null 2>&1; then
    _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i=1;i<NF;i++) if ($i=="src") { print $(i+1); exit } }')
  fi
  [ -z "$_ip" ] && command -v hostname >/dev/null 2>&1 && _ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  printf '%s' "$_ip"
}

# ---------------------------------------------------------------------------------------------------------------
# Inventory (config/inventory.yaml): plain text edits, no YAML library needed
# ---------------------------------------------------------------------------------------------------------------
inventory_skeleton() {
  cat <<'EOF'
# Homelab Agents inventory: the servers and services the gateway may reach. Mounted read-only into the router and
# the gateway at /config/inventory.yaml. Edit it, then: docker compose up -d
# (format: config/inventory.example.yaml, docs/configuration.md)
servers: []
#  - name: web1                  # short name agents use (letters, digits, dashes)
#    host: 192.168.1.20
#    user: svc-hla-agent          # default
#    port: 22                     # default
#    description: "Docker host with the family apps"
#    sandbox: false               # true: a disposable VM where agents may get a free shell

services: {}
#  proxmox: {url: "https://192.168.1.5:8006", nodes: [pve], reserved_vmids: [], verify_tls: true}
#  omada: {url: "https://192.168.1.2:8043", site: Default, verify_tls: false}
#  homeassistant: {url: "http://192.168.1.30:8123"}
#  github: {}

public_ips: []
EOF
}
inventory_has_server() { grep -Eq "^[[:space:]]*-?[[:space:]]*name:[[:space:]]*[\"']?$2[\"']?[[:space:]]*(#.*)?$" "$1"; }
inventory_add_server() {  # FILE NAME HOST USER PORT DESCRIPTION [HOST_FINGERPRINT]
  _f=$1
  _entry=$(printf '  - name: %s\n    host: %s\n    user: %s\n    port: %s\n    description: "%s"\n' "$2" "$3" "$4" "$5" "$6")
  [ -n "${7:-}" ] && _entry=$(printf '%s\n    host_fingerprint: "%s"\n' "$_entry" "$7")
  if grep -q '^servers:[[:space:]]*\[\][[:space:]]*\(#.*\)\{0,1\}$' "$_f"; then
    HLA_ENTRY="$_entry" awk '!done && /^servers:[ \t]*\[\]/ { print "servers:"; print ENVIRON["HLA_ENTRY"]; done=1; next } { print }' "$_f" > "$_f.new"
  elif grep -q '^servers:[[:space:]]*\(#.*\)\{0,1\}$' "$_f"; then
    # Append after the last entry of the section: the section ends at the first line that does not start with a
    # space or a dash (a top-level key, or the example's column-0 comments).
    HLA_ENTRY="$_entry" awk '
      !done && insec && !/^[ \t-]/ { print ENVIRON["HLA_ENTRY"]; done=1; insec=0 }
      !done && /^servers:/ { insec=1 }
      { print }
      END { if (!done) print ENVIRON["HLA_ENTRY"] }' "$_f" > "$_f.new"
  else
    { cat "$_f"; printf 'servers:\n%s\n' "$_entry"; } > "$_f.new"
  fi
  cat "$_f.new" > "$_f" && : > "$_f.new"
}
inventory_server_names() {  # names in the servers: section (best effort, for the key step)
  awk '
    /^[A-Za-z_]+:/ { insec = ($0 ~ /^servers:/) }
    insec && /^[ \t]*-[ \t]*name:/ { v=$0; sub(/^[ \t]*-[ \t]*name:[ \t]*/, "", v); sub(/[ \t]*#.*$/, "", v); gsub(/["\047]/, "", v); print v }' "$1"
}
inventory_server_field() {  # FILE NAME FIELD DEFAULT
  _u=$(awk -v n="$2" -v f="$3" '
    /^[A-Za-z_]+:/ { insec = ($0 ~ /^servers:/) }
    insec && /^[ \t]*-[ \t]*name:/ { v=$0; sub(/^[ \t]*-[ \t]*name:[ \t]*/, "", v); sub(/[ \t]*#.*$/, "", v); gsub(/["\047]/, "", v); cur=v }
    insec && cur==n && $0 ~ "^[ \t]*" f ":" { v=$0; sub("^[ \t]*" f ":[ \t]*", "", v); sub(/[ \t]*#.*$/, "", v); gsub(/["\047]/, "", v); print v; exit }' "$1")
  printf '%s' "${_u:-$4}"
}
valid_fingerprint() { printf '%s' "$1" | grep -Eq '^SHA256:[A-Za-z0-9+/]{43}=?$'; }
valid_name() { printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]{0,62}$'; }
valid_host() { printf '%s' "$1" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]{0,252})$|^\[?[0-9A-Fa-f:]+\]?$'; }
valid_user() { printf '%s' "$1" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$'; }

# Parse name=[user@]host[:port] into S_NAME S_HOST S_USER S_PORT; returns 1 when malformed.
parse_server_spec() {
  S_NAME=${1%%=*}; _rest=${1#*=}
  [ "$S_NAME" = "$1" ] && return 1
  S_USER=svc-hla-agent; S_PORT=22
  case "$_rest" in *@*) S_USER=${_rest%%@*}; _rest=${_rest#*@} ;; esac
  case "$_rest" in *:*) S_PORT=${_rest##*:}; _rest=${_rest%:*} ;; esac
  S_HOST=$_rest
  valid_name "$S_NAME" && valid_host "$S_HOST" && valid_user "$S_USER" && printf '%s' "$S_PORT" | grep -Eq '^[0-9]{1,5}$'
}

# ===============================================================================================================
# --check: validate only
# ===============================================================================================================
check_token() {  # FILE KEY minlen
  _v=$(env_get "$1" "$2")
  if is_placeholder "$_v"; then fail "$1: $2 is not set"; return 1; fi
  if [ "${#_v}" -lt "$3" ]; then fail "$1: $2 is shorter than $3 characters"; return 1; fi
  ok "$1: $2 is set"
}
check_b64key() {  # FILE KEY (optional unless required=1)
  _v=$(env_get "$1" "$2")
  if [ -z "$_v" ]; then [ "${3:-0}" = 1 ] && fail "$1: $2 is not set"; return 0; fi
  _n=''
  if command -v python3 >/dev/null 2>&1; then
    _n=$(HLA_V="$_v" python3 -c 'import base64,os
try: print(len(base64.urlsafe_b64decode(os.environ["HLA_V"].encode())))
except Exception: print(0)')
  fi
  if [ -n "$_n" ] && [ "$_n" != 32 ]; then fail "$1: $2 must be 32 random bytes, base64 (see .env example)"; else ok "$1: $2 is set"; fi
}

run_check() {
  step "Prerequisites"
  if docker_cli_ok; then ok "docker and docker compose v2: $(docker compose version --short 2>/dev/null)"
  elif command -v docker >/dev/null 2>&1; then fail "docker is installed but the compose v2 plugin is missing (docker compose version)"
  else fail "docker is not installed"; fi
  if command -v openssl >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then ok "openssl or python3 available"; else fail "neither openssl nor python3 found"; fi

  step "Settings files"
  for f in "$ROUTER_ENV" "$GATEWAY_ENV"; do
    if [ -f "$f" ]; then
      ok "$f exists"
      # shellcheck disable=SC2012  # portable (GNU and BSD) way to read the mode
      _mode=$(ls -l "$f" | cut -c5-10)
      [ "$_mode" = "------" ] || warn "$f is readable by other users: chmod 600 $f"
    else fail "$f is missing: run ./setup.sh"; fi
  done
  if [ -f "$ROUTER_ENV" ]; then
    check_token "$ROUTER_ENV" DASHBOARD_PASSWORD 12
    check_token "$ROUTER_ENV" POSTGRES_PASSWORD 16
    check_token "$ROUTER_ENV" AGENT_PLATFORM_TOKEN 32
    _la=$(env_get "$ROUTER_ENV" HLA_LISTEN_ADDRESS); _la=${_la:-127.0.0.1}
    if valid_ipv4 "$_la"; then ok "listen address $_la:8088"; else fail "$ROUTER_ENV: HLA_LISTEN_ADDRESS '$_la' is not an IPv4 address"; fi
    _or=$(env_get "$ROUTER_ENV" OPENROUTER_API_KEY); _xa=$(env_get "$ROUTER_ENV" XAI_API_KEY)
    if is_placeholder "$_or" && is_placeholder "$_xa"; then
      warn "no model API key set (OPENROUTER_API_KEY / XAI_API_KEY): fine if you sign in with a subscription (Settings › Models › Subscriptions)"
    else ok "a model API key is set"; fi
    _m=$(env_get "$ROUTER_ENV" OPENROUTER_WORKER_MODEL)
    is_placeholder "$_m" && warn "no default model (OPENROUTER_WORKER_MODEL): pick one on the Models page after signing in"
  fi
  if [ -f "$GATEWAY_ENV" ]; then
    check_token "$GATEWAY_ENV" TOOL_GATEWAY_TOKEN 32
    check_token "$GATEWAY_ENV" POLICY_ADMIN_TOKEN 32
    _backend=$(env_get "$GATEWAY_ENV" HLA_SECRETS_BACKEND); _backend=${_backend:-local}
    case "$_backend" in
      local) ok "secret store: built-in (local)"; check_b64key "$GATEWAY_ENV" HLA_SECRETS_KEY 1 ;;
      infisical)
        ok "secret store: Infisical"
        for k in INFISICAL_URL INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET INFISICAL_PROJECT_ID; do
          is_placeholder "$(env_get "$GATEWAY_ENV" "$k")" && fail "$GATEWAY_ENV: $k is required with HLA_SECRETS_BACKEND=infisical"
        done ;;
      *) fail "$GATEWAY_ENV: HLA_SECRETS_BACKEND must be local or infisical" ;;
    esac
    check_b64key "$GATEWAY_ENV" GATEWAY_CREDENTIAL_KEY 0
  fi
  if [ -f "$ROUTER_ENV" ] && [ -f "$GATEWAY_ENV" ]; then
    _a=$(env_get "$ROUTER_ENV" TOOL_GATEWAY_TOKEN); _b=$(env_get "$GATEWAY_ENV" TOOL_GATEWAY_TOKEN)
    if [ "$_a" = "$_b" ]; then ok "TOOL_GATEWAY_TOKEN matches in both files"; else fail "TOOL_GATEWAY_TOKEN differs between $ROUTER_ENV and $GATEWAY_ENV"; fi
    _c=$(env_get "$ROUTER_ENV" POLICY_ADMIN_TOKEN); _d=$(env_get "$GATEWAY_ENV" POLICY_ADMIN_TOKEN)
    if [ "$_c" = "$_d" ]; then ok "POLICY_ADMIN_TOKEN matches in both files"; else fail "POLICY_ADMIN_TOKEN differs between $ROUTER_ENV and $GATEWAY_ENV"; fi
    [ -n "$_a" ] && [ "$_a" = "$_c" ] && fail "POLICY_ADMIN_TOKEN must differ from TOOL_GATEWAY_TOKEN"
    _a=''; _b=''; _c=''; _d=''
  fi

  step "Inventory"
  if [ -d "$INVENTORY" ]; then
    fail "$INVENTORY is a folder (Docker made it when the file was missing): move it away, then run ./setup.sh"
  elif [ -f "$INVENTORY" ]; then
    ok "$INVENTORY exists"
    grep -q '^servers:' "$INVENTORY" || warn "$INVENTORY has no 'servers:' section"
    if grep -q "$(printf '\t')" "$INVENTORY"; then fail "$INVENTORY contains tab characters (YAML needs spaces)"; fi
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      # The gateway's own loader (ssh-gateway/gateway/inventory.py): the same strict checks it runs at start.
      if _out=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from gateway import inventory
try: inventory.load(sys.argv[2])
except inventory.InventoryError as exc: sys.exit(str(exc).replace(sys.argv[2], sys.argv[3]))' "$ROOT/ssh-gateway" "$ROOT/$INVENTORY" "$INVENTORY" 2>&1); then
        ok "$INVENTORY passes the gateway's checks"
      else fail "$(printf '%s' "$_out" | tail -1)"; fi
    else
      info "(install PyYAML, e.g. apt install python3-yaml, for the full inventory check; the gateway checks it at start)"
    fi
    info "servers: $(inventory_server_names "$INVENTORY" | tr '\n' ' ')"
  else fail "$INVENTORY is missing: run ./setup.sh (or copy $INVENTORY_EXAMPLE)"; fi

  step "Compose file"
  if docker_cli_ok && [ -f "$ROUTER_ENV" ] && [ -f "$GATEWAY_ENV" ]; then
    if _out=$(docker compose config -q 2>&1); then ok "docker compose config: valid"
    else fail "docker compose config failed: $(printf '%s' "$_out" | head -3 | tr '\n' ' ')"; fi
  else info "skipped (needs docker compose and both settings files)"; fi

  printf '\n'
  if [ "$ERRORS" -gt 0 ]; then printf '%s%d problem(s)%s, %d warning(s).\n' "$R" "$ERRORS" "$N" "$WARNINGS"; exit 1; fi
  printf '%sConfiguration looks good%s (%d warning(s)).\n' "$G" "$N" "$WARNINGS"
  exit 0
}

[ "$MODE" = check ] && run_check

# ===============================================================================================================
# Setup
# ===============================================================================================================
printf '%sHomelab Agents setup%s  (%s)\n' "$B" "$N" "$ROOT"
[ "$ASSUME_YES" = true ] && info "non-interactive mode (--yes)"

# --- 1. Prerequisites -------------------------------------------------------------------------------------------
step "1/6 Checking prerequisites"
if ! command -v docker >/dev/null 2>&1; then
  die "Docker is not installed." "Install Docker Engine with the compose plugin:
  curl -fsSL https://get.docker.com | sh       (Debian/Ubuntu/Fedora; or see https://docs.docker.com/engine/install/)
  sudo usermod -aG docker \"\$USER\"             (then log out and back in)
Then run ./setup.sh again."
fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose v2 is missing (the 'docker compose' plugin)." "Install it:
  Debian/Ubuntu: sudo apt-get install docker-compose-plugin
  Fedora/RHEL:   sudo dnf install docker-compose-plugin
  (The old 'docker-compose' v1 is not supported.) Then run ./setup.sh again."
fi
ok "docker compose $(docker compose version --short 2>/dev/null)"
if ! command -v openssl >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  die "Need openssl or python3 to generate passwords and keys." "Install one: sudo apt-get install openssl"
fi
ok "random generator: $(command -v openssl >/dev/null 2>&1 && echo openssl || echo python3)"
for f in "$ROUTER_EXAMPLE" "$GATEWAY_EXAMPLE" agent-platform-starter/compose.yaml ssh-gateway/compose.tools-api.yaml compose.yaml; do
  [ -f "$f" ] || die "$f is missing. Run setup.sh from a complete checkout of the repository."
done
DAEMON_OK=false
if pick_docker; then
  DAEMON_OK=true
  if [ "$DOCKER" = docker ]; then ok "Docker daemon reachable"; else ok "Docker daemon reachable (with sudo)"; fi
else
  if [ "$START" = true ]; then
    warn "the Docker daemon is not reachable without a password; setup will try sudo right before starting"
    info "  (if Docker is not running: sudo systemctl start docker; to skip sudo: sudo usermod -aG docker \"\$USER\")"
  fi
fi

# --- 2. Settings files ------------------------------------------------------------------------------------------
step "2/6 Settings files and secrets"
R_WORK="$WORK/router.env"; G_WORK="$WORK/gateway.env"; T_WORK="$WORK/top.env"; I_WORK="$WORK/inventory.yaml"
if [ -f "$ROUTER_ENV" ]; then cat "$ROUTER_ENV" > "$R_WORK"; info "keeping $ROUTER_ENV (existing values stay)"
else cat "$ROUTER_EXAMPLE" > "$R_WORK"; info "creating $ROUTER_ENV from the example"; fi
if [ -f "$GATEWAY_ENV" ]; then cat "$GATEWAY_ENV" > "$G_WORK"; info "keeping $GATEWAY_ENV (existing values stay)"
else cat "$GATEWAY_EXAMPLE" > "$G_WORK"; info "creating $GATEWAY_ENV from the example"; fi
if [ -f "$TOP_ENV" ]; then cat "$TOP_ENV" > "$T_WORK"
else printf '# Compose-level settings for the top-level compose.yaml (written by setup.sh). Component settings live in\n# agent-platform-starter/.env and ssh-gateway/.env.gateway.\n' > "$T_WORK"; fi

NEW_PASSWORD=''
if [ "$RESET_PASSWORD" = true ] || is_placeholder "$(env_get "$R_WORK" DASHBOARD_PASSWORD)"; then
  NEW_PASSWORD=$(rand_password)
  env_set "$R_WORK" DASHBOARD_PASSWORD "$NEW_PASSWORD"
  ok "dashboard password generated (shown once at the end)"
else ok "dashboard password kept (--reset-password makes a new one)"; fi
if is_placeholder "$(env_get "$R_WORK" POSTGRES_PASSWORD)"; then
  if [ -f "$ROUTER_ENV" ] && [ "$DAEMON_OK" = true ] && $DOCKER volume inspect homelab-agents_agent_db >/dev/null 2>&1; then
    warn "a database volume already exists but POSTGRES_PASSWORD is not set; a new password will not match it"
  fi
  env_set "$R_WORK" POSTGRES_PASSWORD "$(rand_hex | cut -c1-40)"; ok "database password generated"
else ok "database password kept"; fi
env_default "$R_WORK" POSTGRES_USER agents
env_default "$R_WORK" POSTGRES_DB agent_platform
env_default "$R_WORK" AGENT_PLATFORM_TOKEN "$(rand_hex)"

# Gateway tokens: one value shared by both files. An existing value (in either file) wins.
share_token() {  # KEY
  _v=$(env_get "$G_WORK" "$1"); is_placeholder "$_v" && _v=$(env_get "$R_WORK" "$1")
  if is_placeholder "$_v" || [ "${#_v}" -lt 32 ]; then _v=$(rand_hex); ok "$1 generated"; else ok "$1 kept"; fi
  env_set "$G_WORK" "$1" "$_v"; env_set "$R_WORK" "$1" "$_v"; _v=''
}
share_token TOOL_GATEWAY_TOKEN
share_token POLICY_ADMIN_TOKEN
if [ "$(env_get "$R_WORK" TOOL_GATEWAY_TOKEN)" = "$(env_get "$R_WORK" POLICY_ADMIN_TOKEN)" ]; then
  _v=$(rand_hex); env_set "$G_WORK" POLICY_ADMIN_TOKEN "$_v"; env_set "$R_WORK" POLICY_ADMIN_TOKEN "$_v"; _v=''
fi
env_default "$R_WORK" SSH_TOOL_ENABLED true
[ "$(env_get "$R_WORK" SSH_TOOL_ENABLED)" = false ] && [ ! -f "$ROUTER_ENV" ] && env_set "$R_WORK" SSH_TOOL_ENABLED true

# Secret store: built-in encrypted file unless the owner already chose Infisical.
_backend=$(env_get "$G_WORK" HLA_SECRETS_BACKEND)
if [ -z "$_backend" ]; then env_set "$G_WORK" HLA_SECRETS_BACKEND local; _backend=local; fi
if [ "$_backend" = local ]; then
  if [ -z "$(env_get "$G_WORK" HLA_SECRETS_KEY)" ]; then env_set "$G_WORK" HLA_SECRETS_KEY "$(rand_b64key)"; ok "secret store key generated (HLA_SECRETS_KEY)"
  else ok "secret store key kept"; fi
  for k in INFISICAL_URL INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET INFISICAL_PROJECT_ID; do env_clear_placeholder "$G_WORK" "$k"; done
else ok "secret store: Infisical (HLA_SECRETS_BACKEND=$_backend), left as configured"; fi
if [ -z "$(env_get "$G_WORK" GATEWAY_CREDENTIAL_KEY)" ]; then env_set "$G_WORK" GATEWAY_CREDENTIAL_KEY "$(rand_b64key)"; ok "credential store key generated"; fi
# Example values that point at someone else's network: blank them so nothing tries to use them.
for k in PROXMOX_TOKEN_ID PROXMOX_TOKEN_SECRET TYPESAFE_API_KEY; do env_clear_placeholder "$R_WORK" "$k"; done
if [ ! -f "$ROUTER_ENV" ]; then
  [ "$(env_get "$R_WORK" PROXMOX_API_URL)" = "https://proxmox.home.arpa:8006" ] && env_set "$R_WORK" PROXMOX_API_URL ""
  [ "$(env_get "$R_WORK" SANDBOX_PROXMOX_TOKEN_ID)" = 'Agents@pam!agents' ] && env_set "$R_WORK" SANDBOX_PROXMOX_TOKEN_ID ""
fi
if [ ! -f "$GATEWAY_ENV" ]; then
  [ "$(env_get "$G_WORK" PUBLIC_IPS)" = "203.0.113.10" ] && env_set "$G_WORK" PUBLIC_IPS ""
fi

# --- 3. Questions -----------------------------------------------------------------------------------------------
step "3/6 A few questions"

# Listen address
_cur=$(env_get "$R_WORK" HLA_LISTEN_ADDRESS); _lan=$(main_ipv4)
if [ -n "$OPT_LISTEN" ]; then LISTEN=$OPT_LISTEN
elif [ "$ASSUME_YES" = true ]; then LISTEN=${_cur:-127.0.0.1}
else
  info "Where should the dashboard listen?"
  info "  1) 127.0.0.1 only: this machine (use an SSH tunnel or a reverse proxy here)  [safest]"
  [ -n "$_lan" ] && info "  2) $_lan: your LAN (phones and laptops at home; put HTTPS in front for anything more)"
  info "  or type another IPv4 address of this machine (0.0.0.0 = every interface)"
  _def=1; [ -n "$_cur" ] && [ "$_cur" != 127.0.0.1 ] && _def=$_cur
  ask _choice "Choice" "$_def"
  case "$_choice" in 1) LISTEN=127.0.0.1 ;; 2) LISTEN=${_lan:-127.0.0.1} ;; *) LISTEN=$_choice ;; esac
fi
valid_ipv4 "$LISTEN" || die "Listen address '$LISTEN' is not an IPv4 address." "Use 127.0.0.1, a LAN IP of this machine or 0.0.0.0."
env_set "$R_WORK" HLA_LISTEN_ADDRESS "$LISTEN"
ok "dashboard on $LISTEN:8088"

# Model provider
_or=$(env_get "$R_WORK" OPENROUTER_API_KEY); _xa=$(env_get "$R_WORK" XAI_API_KEY)
PROVIDER=$OPT_PROVIDER
if [ -z "$PROVIDER" ]; then
  if ! is_placeholder "$_or"; then PROVIDER=keep-openrouter
  elif ! is_placeholder "$_xa"; then PROVIDER=keep-xai
  elif [ -n "${OPENROUTER_API_KEY:-}" ]; then PROVIDER=openrouter
  elif [ -n "${XAI_API_KEY:-}" ]; then PROVIDER=xai
  elif [ "$ASSUME_YES" = true ]; then PROVIDER=skip
  else
    info "Which models should the agents use? (more later under Settings › Models)"
    info "  1) OpenRouter API key  (hundreds of models, pay per use: https://openrouter.ai/keys)"
    info "  2) xAI API key         (Grok models: https://console.x.ai)"
    info "  3) I'll sign in with a ChatGPT or Claude subscription later (Settings › Models › Subscriptions)"
    info "  4) Skip for now"
    ask _choice "Choice" 1
    case "$_choice" in 1) PROVIDER=openrouter ;; 2) PROVIDER=xai ;; 3) PROVIDER=subscription ;; *) PROVIDER=skip ;; esac
  fi
fi
DEFAULT_MODEL=''
case "$PROVIDER" in
  openrouter)
    _key=${OPENROUTER_API_KEY:-}; [ -z "$_key" ] && ask_secret _key "OpenRouter API key"
    if [ -n "$_key" ]; then env_set "$R_WORK" OPENROUTER_API_KEY "$_key"; ok "OpenRouter key saved"
    elif is_placeholder "$_or"; then warn "no OpenRouter key given; add OPENROUTER_API_KEY to $ROUTER_ENV later"; fi
    _key=''; DEFAULT_MODEL=anthropic/claude-sonnet-5.5 ;;
  xai)
    _key=${XAI_API_KEY:-}; [ -z "$_key" ] && ask_secret _key "xAI API key"
    if [ -n "$_key" ]; then env_set "$R_WORK" XAI_API_KEY "$_key"; ok "xAI key saved"
    elif is_placeholder "$_xa"; then warn "no xAI key given; add XAI_API_KEY to $ROUTER_ENV later"; fi
    _key=''; DEFAULT_MODEL=xai/grok-4 ;;
  subscription) info "after the first login: Settings › Models › Subscriptions → sign in, then pick the default model there" ;;
  skip) info "no model provider for now: the dashboard works, agents answer once a provider is added" ;;
  keep-openrouter) ok "OpenRouter key already set (kept)"; DEFAULT_MODEL=anthropic/claude-sonnet-5.5 ;;
  keep-xai) ok "xAI key already set (kept)"; DEFAULT_MODEL=xai/grok-4 ;;
  *) die "Unknown provider '$PROVIDER'." "Use openrouter, xai, subscription or skip." ;;
esac
is_placeholder "$(env_get "$R_WORK" OPENROUTER_API_KEY)" && env_set "$R_WORK" OPENROUTER_API_KEY ""
[ -n "$OPT_MODEL" ] && DEFAULT_MODEL=$OPT_MODEL
if [ -n "$DEFAULT_MODEL" ]; then
  if [ "$ASSUME_YES" = false ] && [ -z "$OPT_MODEL" ] && is_placeholder "$(env_get "$R_WORK" OPENROUTER_WORKER_MODEL)"; then
    ask DEFAULT_MODEL "Default model" "$DEFAULT_MODEL"
  fi
  if [ -n "$OPT_MODEL" ] || is_placeholder "$(env_get "$R_WORK" OPENROUTER_WORKER_MODEL)"; then
    env_set "$R_WORK" OPENROUTER_WORKER_MODEL "$DEFAULT_MODEL"; ok "default model: $DEFAULT_MODEL"
  fi
  is_placeholder "$(env_get "$R_WORK" AGENT_ALLOWED_MODELS)" && env_set "$R_WORK" AGENT_ALLOWED_MODELS "$DEFAULT_MODEL"
fi
# Never leave the example's "provider/model" in place: an empty value lets the Models page decide.
is_placeholder "$(env_get "$R_WORK" OPENROUTER_WORKER_MODEL)" && env_set "$R_WORK" OPENROUTER_WORKER_MODEL ""
is_placeholder "$(env_get "$R_WORK" AGENT_ALLOWED_MODELS)" && env_set "$R_WORK" AGENT_ALLOWED_MODELS ""

# Voice
_profiles=$(env_get "$T_WORK" COMPOSE_PROFILES)
case ",$_profiles," in *,voice,*) _vdef=yes ;; *) _vdef=no ;; esac
if [ -n "$OPT_VOICE" ]; then VOICE=$OPT_VOICE
elif yesno "Enable server text-to-speech for the Chief's car mode (Kokoro, ~2 GB RAM; browser speech works without it)?" "$_vdef"; then VOICE=yes
else VOICE=no; fi
_new_profiles=$(printf '%s' "$_profiles" | tr ',' '\n' | grep -v '^voice$' | grep -v '^$' | tr '\n' ',' | sed 's/,$//')
if is_yes "$VOICE"; then
  _new_profiles=${_new_profiles:+$_new_profiles,}voice
  env_default "$R_WORK" VOICE_TTS_BASE_URL http://kokoro:8880/v1
  if [ "$(env_get "$R_WORK" VOICE_TTS_BASE_URL)" = http://kokoro:8880/v1 ]; then
    [ "$(env_get "$R_WORK" VOICE_TTS_MODEL)" = tts-1 ] || [ -z "$(env_get "$R_WORK" VOICE_TTS_MODEL)" ] && env_set "$R_WORK" VOICE_TTS_MODEL kokoro
    [ "$(env_get "$R_WORK" VOICE_TTS_VOICE)" = alloy ] || [ -z "$(env_get "$R_WORK" VOICE_TTS_VOICE)" ] && env_set "$R_WORK" VOICE_TTS_VOICE af_heart
    is_placeholder "$(env_get "$R_WORK" VOICE_TTS_VOICES)" && env_set "$R_WORK" VOICE_TTS_VOICES "af_heart,af_bella,am_michael,bf_emma,bm_george"
  fi
  ok "voice: Kokoro on (profile voice)"
else
  if [ "$(env_get "$R_WORK" VOICE_TTS_BASE_URL)" = http://kokoro:8880/v1 ]; then env_set "$R_WORK" VOICE_TTS_BASE_URL ""; fi
  ok "voice: browser speech only"
fi
env_has "$R_WORK" HLA_VOICE_LANGUAGE || env_set "$R_WORK" HLA_VOICE_LANGUAGE en-US
env_set "$T_WORK" COMPOSE_PROFILES "$_new_profiles"

# --- 4. Inventory -----------------------------------------------------------------------------------------------
step "4/6 Server inventory ($INVENTORY)"
if [ -f "$INVENTORY" ]; then cat "$INVENTORY" > "$I_WORK"; info "keeping $INVENTORY"
elif [ -f "$INVENTORY_EXAMPLE" ]; then
  # The example's sample entries are for reading; start the real file with empty lists.
  awk '
    /^servers:/ { print "servers: []"; skip=1; next }
    /^services:/ { print "services: {}"; skip=1; next }
    /^public_ips:/ { print "public_ips: []"; skip=1; next }
    /^[A-Za-z_]/ { skip=0 }
    skip && /^[ \t]*#/ { print; next }
    skip && /^[ \t]*$/ { print; next }
    skip { print "#" $0; next }
    { print }' "$INVENTORY_EXAMPLE" > "$I_WORK"
  info "creating $INVENTORY from the example (samples kept as comments)"
else inventory_skeleton > "$I_WORK"; info "creating $INVENTORY"; fi

add_server() {  # NAME HOST USER PORT DESC [FINGERPRINT]
  if inventory_has_server "$I_WORK" "$1"; then warn "server '$1' is already in the inventory; skipped"; return; fi
  inventory_add_server "$I_WORK" "$1" "$2" "$3" "$4" "$5" "${6:-}"; ok "added $1 ($3@$2:$4)"; NEW_SERVERS="$NEW_SERVERS $1"
}
for spec in $OPT_SERVERS; do
  if parse_server_spec "$spec"; then add_server "$S_NAME" "$S_HOST" "$S_USER" "$S_PORT" ""
  else die "Bad --server '$spec'." "Format: name=[user@]host[:port], e.g. web1=192.168.1.20 or nas=svc-hla-agent@nas.lan:2222"; fi
done
if [ "$ASSUME_YES" = false ]; then
  _n=$(inventory_server_names "$I_WORK" | grep -c . || true)
  info "Servers the agents may reach over SSH ($_n configured). The platform works without any; add them now or later."
  _first=no; [ "$_n" -eq 0 ] && _first=yes
  while yesno "Add a server?" "$_first"; do
    _first=no
    ask _name "  Name (short, e.g. web1)" ""
    valid_name "$_name" || { warn "use lowercase letters, digits and dashes"; continue; }
    ask _host "  Host name or IP" ""
    valid_host "$_host" || { warn "that does not look like a host name or IP"; continue; }
    ask _user "  SSH user the gateway logs in as" "svc-hla-agent"
    valid_user "$_user" || { warn "not a valid Linux user name"; continue; }
    ask _port "  SSH port" "22"
    printf '%s' "$_port" | grep -Eq '^[0-9]{1,5}$' || { warn "not a port number"; continue; }
    ask _desc "  Description (what runs there; agents read this)" ""
    _desc=$(printf '%s' "$_desc" | tr -d "\"\\\\" | cut -c1-200)
    info "  The gateway only connects to a host key you pinned. If you know it (on the server:"
    info "  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub), paste it now; otherwise pin it after installing the key."
    ask _fp "  Host key fingerprint (SHA256:..., Enter to pin later)" ""
    if [ -n "$_fp" ] && ! valid_fingerprint "$_fp"; then warn "not an SHA256 fingerprint; pin it later instead"; _fp=''; fi
    add_server "$_name" "$_host" "$_user" "$_port" "$_desc" "$_fp"
  done
fi

# --- 5. Write files ---------------------------------------------------------------------------------------------
step "5/6 Writing configuration"
commit_file "$ROUTER_ENV" "$R_WORK"
commit_file "$GATEWAY_ENV" "$G_WORK"
commit_file "$TOP_ENV" "$T_WORK"
INV_EXISTED=false; [ -f "$INVENTORY" ] && INV_EXISTED=true
cmp -s "$INVENTORY" "$I_WORK" 2>/dev/null && INV_CHANGED=false || INV_CHANGED=true
commit_file "$INVENTORY" "$I_WORK"
chmod 644 "$INVENTORY" 2>/dev/null   # read by the containers' unprivileged users; holds no secrets

# SSH keys: one Ed25519 key per server (ssh-fleet/01-generate-keys.sh), imported into the gateway's store.
SERVERS_ALL=$(inventory_server_names "$INVENTORY" | tr '\n' ' ')
KEY_SERVERS=''
if [ -n "$(printf '%s' "$SERVERS_ALL" | tr -d ' ')" ]; then
  _missing=''
  for s in $SERVERS_ALL; do [ -f "$FLEET_KEYS/$s" ] || _missing="$_missing $s"; done
  if [ -n "$OPT_SSH_KEYS" ]; then _do_keys=$OPT_SSH_KEYS
  elif [ -z "$_missing" ]; then _do_keys=yes
  elif yesno "Generate the gateway's SSH key for:$_missing (stored in $FLEET_KEYS)?" yes; then _do_keys=yes
  else _do_keys=no; fi
  if is_yes "$_do_keys"; then
    command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen is missing." "Install OpenSSH client: sudo apt-get install openssh-client"
    # shellcheck disable=SC2086
    [ -n "$_missing" ] && FLEET_KEYS="$FLEET_KEYS" bash ssh-fleet/01-generate-keys.sh $_missing | sed 's/^/  /'
    KEY_SERVERS=$SERVERS_ALL
  fi
fi

# --- 6. Start ---------------------------------------------------------------------------------------------------
step "6/6 Starting the stack"
STARTED=false
START_ERR=''; START_HINT=''
start_stack() {  # returns 1 with START_ERR/START_HINT set; the summary (with the password) is printed either way
  if [ "$DAEMON_OK" = false ] && ! pick_docker interactive; then
    START_ERR="Cannot reach the Docker daemon, so the stack was not started."
    START_HINT="Your configuration is saved. Fix Docker, then run ./setup.sh again (it keeps every value):
  sudo systemctl start docker                         (daemon not running)
  sudo usermod -aG docker \"\$USER\" && newgrp docker   (permission denied on /var/run/docker.sock)
  or start it yourself: sudo docker compose up -d --build"
    return 1
  fi
  if ! compose config -q >/dev/null 2>&1; then
    compose config -q 2>&1 | sed 's/^/  /' | head -20
    START_ERR="docker compose rejected the configuration."; START_HINT="Run ./setup.sh --check for details."
    return 1
  fi
  info "building and starting (the first build takes a few minutes)..."
  if ! compose up -d --build --remove-orphans; then
    START_ERR="docker compose up failed."; START_HINT="See the output above; logs: docker compose logs --tail 100"
    return 1
  fi
  # The inventory is read at start only: running containers pick up a changed file after a restart.
  if [ "$INV_EXISTED" = true ] && [ "$INV_CHANGED" = true ]; then
    info "inventory changed: restarting the router, worker and gateway so they read it"
    compose restart agent-router queue-worker ssh-gateway-api >/dev/null 2>&1 || warn "restart failed; run: docker compose restart"
  fi
  info "waiting for the dashboard to become healthy (up to ${WAIT_SECONDS}s)..."
  _cid=$(compose ps -q agent-router 2>/dev/null | head -1)
  _t=0; _state=''
  while [ "$_t" -lt "$WAIT_SECONDS" ]; do
    _state=$($DOCKER inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$_cid" 2>/dev/null)
    [ "$_state" = healthy ] && break
    sleep 3; _t=$((_t + 3))
  done
  if [ "$_state" = healthy ]; then ok "agent-router is healthy"; STARTED=true
  else
    START_ERR="agent-router did not become healthy (state: ${_state:-unknown})."
    START_HINT="Check: docker compose ps   and   docker compose logs --tail 80 agent-router"
  fi
  # Import each server's private key into the gateway's secret store (built-in store only).
  if [ -n "$KEY_SERVERS" ] && [ "$(env_get "$GATEWAY_ENV" HLA_SECRETS_BACKEND)" = local ]; then
    for s in $KEY_SERVERS; do
      if compose exec -T ssh-gateway-api python -m gateway.cli secrets import-ssh-key "$s" /dev/stdin < "$FLEET_KEYS/$s" >/dev/null 2>&1; then
        ok "imported the SSH key for $s into the gateway"
      else
        warn "could not import the SSH key for $s; do it by hand:"
        info "  docker compose exec -T ssh-gateway-api python -m gateway.cli secrets import-ssh-key $s /dev/stdin < $FLEET_KEYS/$s"
      fi
    done
  fi
  [ -z "$START_ERR" ]
}
if [ "$START" = false ]; then
  info "skipped (--no-start). Start later with: docker compose up -d --build"
else
  start_stack || fail "$START_ERR"
fi

# --- Summary ----------------------------------------------------------------------------------------------------
case "$LISTEN" in 0.0.0.0) URL_HOST=${_lan:-localhost} ;; *) URL_HOST=$LISTEN ;; esac
printf '\n%s────────────────────────────────────────────────────────────────%s\n' "$B" "$N"
if [ "$STARTED" = true ]; then printf '%sHomelab Agents is running.%s\n' "$G" "$N"; else printf '%sConfiguration saved.%s\n' "$B" "$N"; fi
printf '\n  Dashboard:  http://%s:8088/ui/\n' "$URL_HOST"
[ "$LISTEN" = 127.0.0.1 ] && printf '              from another computer: ssh -L 8088:127.0.0.1:8088 %s@%s  then http://localhost:8088/ui/\n' "${SUDO_USER:-${USER:-you}}" "${_lan:-this-host}"
if [ -n "$NEW_PASSWORD" ]; then
  printf '  Password:   %s%s%s\n' "$B" "$NEW_PASSWORD" "$N"
  printf '              (shown only now; it is stored as DASHBOARD_PASSWORD in %s)\n' "$ROUTER_ENV"
else
  printf '  Password:   unchanged (DASHBOARD_PASSWORD in %s; ./setup.sh --reset-password makes a new one)\n' "$ROUTER_ENV"
fi
NEW_PASSWORD=''

if [ -n "$KEY_SERVERS" ]; then
  GWIP=$OPT_GATEWAY_IP; [ -z "$GWIP" ] && GWIP=$(main_ipv4); [ -z "$GWIP" ] && GWIP=GATEWAY_IP
  printf '\n  %sInstall the gateway key on each server%s (run from this machine; uses your own admin login there):\n' "$B" "$N"
  for s in $KEY_SERVERS; do
    _h=$(inventory_server_field "$INVENTORY" "$s" host ''); _u=$(inventory_server_field "$INVENTORY" "$s" user svc-hla-agent)
    _p=$(inventory_server_field "$INVENTORY" "$s" port 22)
    [ -f "$FLEET_KEYS/$s.pub" ] || continue
    _pub=$(cut -d' ' -f1-2 "$FLEET_KEYS/$s.pub")
    _sp=''; _cp=''; [ "$_p" != 22 ] && { _sp="-P $_p "; _cp="-p $_p "; }
    printf '\n    # %s\n' "$s"
    printf '    scp %sssh-fleet/02-install-on-server.sh ADMIN@%s:/tmp/hla-install.sh && ssh -t %sADMIN@%s "sudo bash /tmp/hla-install.sh '"'"'%s hla-gateway-%s'"'"' %s --user %s; rm -f /tmp/hla-install.sh"\n' \
      "$_sp" "$_h" "$_cp" "$_h" "$_pub" "$s" "$GWIP" "$_u"
  done
  printf '\n    (ADMIN = your usual login on that server. %s is the address the servers see the gateway connect from;\n' "$GWIP"
  printf '     override with --gateway-ip. Public keys only: nothing secret in these commands. Details: ssh-fleet/README.md)\n'
fi

# Host keys: the gateway only talks to a server whose ed25519 host key is pinned (inventory host_fingerprint, or
# servers/<name>/SSH_HOST_FINGERPRINT in the secret store). List the servers that still need it, with the exact command.
DC_SHOW="docker compose"; [ "$DOCKER" = "sudo docker" ] && DC_SHOW="sudo docker compose"
PINNED=''
if [ "$STARTED" = true ]; then
  PINNED=$(compose exec -T ssh-gateway-api python -m gateway.cli secrets list 2>/dev/null | sed -n 's#^servers/\([^/]*\)/SSH_HOST_FINGERPRINT$#\1#p' | tr '\n' ' ')
fi
TO_PIN=''
for s in $SERVERS_ALL; do
  [ -n "$(inventory_server_field "$INVENTORY" "$s" host_fingerprint '')$(inventory_server_field "$INVENTORY" "$s" host_key_fingerprint '')" ] && continue
  case " $PINNED " in *" $s "*) continue ;; esac
  TO_PIN="$TO_PIN $s"
done
if [ -n "$TO_PIN" ]; then
  printf '\n  %sPin each server%ss host key%s (the gateway refuses servers without one), from this folder:\n' "$B" "'" "$N"
  for s in $TO_PIN; do printf '    %s exec ssh-gateway-api python -m gateway.cli secrets pin-host-key %s\n' "$DC_SHOW" "$s"; done
  printf '    It prints the fingerprint it recorded: it must equal the "Host ED25519 fingerprint" the installer printed\n'
  printf '    (or: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server). Instead, you can put it in\n'
  printf '    %s as  host_fingerprint: "SHA256:..."  under the server, then run ./setup.sh again.\n' "$INVENTORY"
  [ "$STARTED" = true ] || printf '    (Start the stack first; these need the gateway running.)\n'
fi

printf '\n  %sNext steps%s\n' "$B" "$N"
case "$PROVIDER" in
  subscription) printf '    - Settings › Models › Subscriptions: sign in with your Claude or ChatGPT plan, then pick the default model.\n' ;;
  skip) printf '    - Add a model provider: OPENROUTER_API_KEY or XAI_API_KEY in %s (then ./setup.sh again), or sign in under Settings › Models › Subscriptions.\n' "$ROUTER_ENV" ;;
esac
printf '    - Policy page: give each server and agent a level (everything starts at the safe end).\n'
printf '    - Optional: HTTPS + single sign-on in front of the dashboard (docs/security.md), integrations (docs/integrations.md).\n'
printf '    - Check the configuration any time: ./setup.sh --check   Logs: docker compose logs -f agent-router\n'
if [ -n "$START_ERR" ]; then
  printf '\n%sSetup stopped:%s %s\n%s\n' "$R" "$N" "$START_ERR" "$START_HINT" >&2
  exit 1
fi
exit 0
