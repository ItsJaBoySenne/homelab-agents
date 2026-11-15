#!/usr/bin/env bash
# Usage: bash 03-verify-from-agents.sh <hosts-file> [keys-dir]
# <hosts-file>: one "<name> <address>" per line (# comments allowed), e.g.
#   server1 192.168.1.21
#   server2 192.168.1.22
set -Eeuo pipefail
HOSTS_FILE="${1:?usage: bash $0 <hosts-file> [keys-dir]}"
BASE="${2:-${FLEET_KEYS:-$HOME/.ssh/hla-fleet}}"
while read -r name addr _; do
  [[ -z "${name:-}" || "$name" == \#* ]] && continue
  echo "== $name ($addr) =="
  if [[ ! -f "$BASE/$name" ]]; then echo 'SKIP: missing key'; continue; fi
  # Strict checking: caller must verify/add each SSH host key beforehand.
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 -o IdentitiesOnly=yes \
      -i "$BASE/$name" "svc-hla-agent@$addr" 'hostname; id; sudo -n id 2>/dev/null || echo "sudo not enabled"' </dev/null || true
done < "$HOSTS_FILE"
