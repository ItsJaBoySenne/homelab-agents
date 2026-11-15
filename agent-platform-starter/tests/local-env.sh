#!/usr/bin/env bash
# Build a throwaway test toolchain without root or Docker: Python venv with the app's requirements,
# Postgres 17 (extracted from Debian packages, unix socket only), Node 22 + Playwright Chromium.
#   tests/local-env.sh <dir>          # build (idempotent) and start Postgres
#   source <dir>/env.sh               # TEST_DATABASE_URL, PATH, LD_LIBRARY_PATH, FONTCONFIG_FILE
#   <dir>/pg-stop.sh                  # stop Postgres
set -Eeuo pipefail
D="$(realpath -m "${1:?usage: tests/local-env.sh <dir>}")"
APP="$(cd "$(dirname "$0")/.." && pwd)"
NODE_VERSION=22.11.0
# Unix socket paths are limited to 107 bytes, so the socket lives in a short directory.
SOCK="${PG_SOCKET_DIR:-$HOME/.cache/hla-test-pg}"
mkdir -p "$D"
extract() {  # extract() <root> <packages...>: download Debian packages and unpack them without installing
  local root="$1"; shift; mkdir -p "$D/debs" "$root"
  (cd "$D/debs" && apt-get download "$@" >/dev/null)
  for p in "$@"; do dpkg -x "$(ls "$D"/debs/"${p}"_*.deb | head -1)" "$root"; done
}

if [[ ! -x "$D/venv/bin/python" ]]; then
  python3 -m venv --without-pip "$D/venv"
  curl -fsS --max-time 60 https://bootstrap.pypa.io/get-pip.py -o "$D/get-pip.py"
  "$D/venv/bin/python" "$D/get-pip.py" -q
  "$D/venv/bin/pip" install -q -r "$APP/requirements.txt"
fi
# The gateway's policy module (used by the preview server and gateway tests) imports paramiko.
"$D/venv/bin/python" -c 'import paramiko' 2>/dev/null || "$D/venv/bin/pip" install -q 'paramiko>=3.5,<5'

PG="$D/pg/usr/lib/postgresql/17/bin"
if [[ ! -x "$PG/postgres" ]]; then extract "$D/pg" postgresql-17 libpq5 libicu76; fi
PGLIB="$D/pg/usr/lib/x86_64-linux-gnu"
if [[ ! -d "$D/pgdata" ]]; then
  LD_LIBRARY_PATH="$PGLIB" "$PG/initdb" -D "$D/pgdata" -U agents --auth=trust -L "$D/pg/usr/share/postgresql/17" >/dev/null
fi
mkdir -p "$SOCK"
if ! LD_LIBRARY_PATH="$PGLIB" "$PG/pg_ctl" -D "$D/pgdata" status >/dev/null 2>&1; then
  LD_LIBRARY_PATH="$PGLIB" "$PG/pg_ctl" -D "$D/pgdata" -o "-k $SOCK -p 54329 -c listen_addresses=''" -l "$D/pg.log" start >/dev/null
fi
printf '#!/bin/sh\nLD_LIBRARY_PATH=%s %s/pg_ctl -D %s stop -m fast\n' "$PGLIB" "$PG" "$D/pgdata" > "$D/pg-stop.sh"; chmod +x "$D/pg-stop.sh"

NODE="$D/node-v$NODE_VERSION-linux-x64/bin"
if [[ ! -x "$NODE/node" ]]; then
  (cd "$D" && curl -fsSO --max-time 180 "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" \
     && curl -fsS --max-time 30 "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" | grep " node-v$NODE_VERSION-linux-x64.tar.xz\$" | sha256sum -c - >/dev/null \
     && tar xf "node-v$NODE_VERSION-linux-x64.tar.xz")
fi
if [[ ! -d "$D/e2e/node_modules/playwright" ]]; then
  mkdir -p "$D/e2e" && (cd "$D/e2e" && PATH="$NODE:$PATH" npm init -y >/dev/null && PATH="$NODE:$PATH" npm install --silent --no-audit --no-fund playwright@1.48.2 \
     && PATH="$NODE:$PATH" npx playwright install chromium >/dev/null 2>&1 || true)
fi
# Chromium's desktop libraries and fonts, extracted like Postgres (only the ones this host lacks).
if [[ ! -d "$D/chrome/usr/lib" ]]; then
  WANT="libasound2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 libdrm2 libgbm1 libnspr4 libnss3 libpango-1.0-0 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 fonts-dejavu-core"
  ALL=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances $WANT 2>/dev/null | grep -E '^[a-z0-9]' | sort -u)
  NEED=$(for p in $ALL; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || echo "$p"; done)
  extract "$D/chrome" $NEED
fi
cat > "$D/fonts.conf" <<EOF
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig><dir>$D/chrome/usr/share/fonts</dir><cachedir>$D/fontcache</cachedir>
<alias><family>sans-serif</family><prefer><family>DejaVu Sans</family></prefer></alias>
<alias><family>system-ui</family><prefer><family>DejaVu Sans</family></prefer></alias>
<alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias></fontconfig>
EOF
cat > "$D/env.sh" <<EOF
export TEST_DATABASE_URL='postgresql://agents@/postgres?host=$SOCK&port=54329'
export I_UNDERSTAND_THIS_WIPES_TEST_DB=1 PYTHONDONTWRITEBYTECODE=1
export PATH='$D/venv/bin:$NODE':"\$PATH"
export LD_LIBRARY_PATH='$D/chrome/usr/lib/x86_64-linux-gnu:$D/chrome/lib/x86_64-linux-gnu'
export FONTCONFIG_FILE='$D/fonts.conf' NODE_PATH='$D/e2e/node_modules'
EOF
echo "Ready. source $D/env.sh"
