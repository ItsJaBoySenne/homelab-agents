#!/usr/bin/env bash
# Run on the target server with sudo/root:
#   sudo bash 02-install-on-server.sh <public-key-file | 'ssh-ed25519 AAAA... comment'> <gateway-ip> [--user NAME] [--admin]
# <gateway-ip>: the address this server sees SSH connections from (the machine running the SSH gateway).
# --user: the account the gateway logs in as (default svc-hla-agent; must match "user" in config/inventory.yaml).
# ./setup.sh prints a ready-made one-liner (public key inline) for every server in the inventory.
set -Eeuo pipefail
if [[ "$(id -u)" -ne 0 ]]; then echo "Run with sudo/root" >&2; exit 1; fi
usage() { echo "Usage: sudo bash $0 <public-key-file | 'ssh-ed25519 AAAA...'> <gateway-ip> [--user NAME] [--admin]" >&2; exit 1; }
[[ $# -ge 2 ]] || usage
PUB="$1"; SOURCE_IP="$2"; shift 2
MODE=''; USER_NAME=svc-hla-agent
while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin) MODE='--admin' ;;
    --user) USER_NAME="${2:-}"; shift ;;
    *) usage ;;
  esac
  shift
done
if [[ ! "$SOURCE_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then echo 'gateway-ip must be an IPv4 address, e.g. 192.168.1.10' >&2; exit 1; fi
if [[ ! "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then echo 'user must be a valid Linux user name' >&2; exit 1; fi
if [[ "$PUB" == ssh-* ]]; then
  # Key given inline (one line, as setup.sh prints it).
  if [[ "$PUB" == *$'\n'* ]]; then echo 'Provide exactly one public key' >&2; exit 1; fi
  read -r typ key _ <<< "$PUB"
else
  [[ -f "$PUB" && ! -L "$PUB" ]] || { echo 'Public key file not found or symlink' >&2; exit 1; }
  if [[ $(wc -l < "$PUB") -ne 1 ]]; then echo 'Provide exactly one public key' >&2; exit 1; fi
  read -r typ key _ < "$PUB"
fi
if [[ "$typ" != 'ssh-ed25519' || ! "$key" =~ ^[A-Za-z0-9+/=]+$ ]]; then echo 'Expected a single Ed25519 SSH public key' >&2; exit 1; fi
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --user-group "$USER_NAME"
  passwd -l "$USER_NAME" >/dev/null
else
  echo "Account already exists: $USER_NAME"
fi
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ "$HOME_DIR" == /* && -d "$HOME_DIR" ]] || { echo 'Invalid/missing home directory' >&2; exit 1; }
install -d -m 700 -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$HOME_DIR/.ssh"
AUTHORIZED="$HOME_DIR/.ssh/authorized_keys"
touch "$AUTHORIZED"
chmod 600 "$AUTHORIZED"
chown "$USER_NAME:$(id -gn "$USER_NAME")" "$AUTHORIZED"
# Restrict SSH key to the gateway's IP. Do not allow forwarding or SSH agent forwarding.
ENTRY="from=\"$SOURCE_IP\",no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 $key hla-gateway"
if ! grep -Fq "$key" "$AUTHORIZED"; then
  printf '%s\n' "$ENTRY" >> "$AUTHORIZED"
  echo 'Public key installed.'
else
  echo 'Public key already present; not duplicated. Review old unrestricted entries if any.'
fi
# Password authentication is disabled by the locked account password.
# Avoid altering global sshd configuration automatically on critical hosts.
# Review password/PAM/authorized_keys restrictions separately if your SSH policy differs.
if [[ "$MODE" == '--admin' ]]; then
  echo 'WARNING: enabling full passwordless sudo (equivalent to root).'
  SUDOFILE=/etc/sudoers.d/90-hla-agent
  if [[ -e "$SUDOFILE" ]]; then
    echo "Preserving existing $SUDOFILE; confirm it allows intended operations."
  else
    printf '%s ALL=(root) NOPASSWD: ALL\n' "$USER_NAME" > "$SUDOFILE"
    chmod 440 "$SUDOFILE"
  fi
  visudo -cf "$SUDOFILE"
else
  echo 'No sudo privileges granted by this script; use --admin only after risk review.'
fi
printf '\nHost ED25519 fingerprint: pin it in the gateway (secrets pin-host-key <server>, or host_fingerprint in\nconfig/inventory.yaml) after checking it matches:\n'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
printf '\nDone. Test login from %s before closing your admin session.\n' "$SOURCE_IP"
