#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Usage: bash 01-generate-keys.sh <server> [server ...]   Keys go to $FLEET_KEYS (default ~/.ssh/hla-fleet).
BASE="${FLEET_KEYS:-$HOME/.ssh/hla-fleet}"
mkdir -p "$BASE"
chmod 700 "$BASE"
# Use server-specific keys: compromise/revocation affects one machine only.
if [[ $# -lt 1 ]]; then echo "Usage: bash $0 <server> [server ...]   e.g. bash $0 server1 server2" >&2; exit 1; fi
SERVERS=("$@")
for name in "${SERVERS[@]}"; do
  key="$BASE/$name"
  if [[ -e "$key" || -e "$key.pub" ]]; then
    echo "SKIP $name: existing key (never overwrite)"
    continue
  fi
  # Headless gateway: encrypted at rest in the gateway's secret store rather than by a key passphrase.
  ssh-keygen -q -t ed25519 -N '' -C "hla-gateway-$name" -f "$key"
  chmod 600 "$key"; chmod 644 "$key.pub"
  echo "CREATED $name"
done
printf '\nPrivate keys: %s (DO NOT copy to target VMs or Git)\n' "$BASE"
printf 'Public keys:  %s/<name>.pub\n' "$BASE"
printf 'Store each private key in the gateway (./setup.sh does this for you):\n  docker compose exec -T ssh-gateway-api python -m gateway.cli secrets import-ssh-key <name> /dev/stdin < %s/<name>\n' "$BASE"
