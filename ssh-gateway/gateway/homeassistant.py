"""Home Assistant through the gateway (HLA_HA_V1): REST and WebSocket calls with a token read from Infisical.

The token (HA_TOKEN in /HomeAssistant) is read on every request and never leaves the gateway; HA_URL must equal the
pinned URL in core.SERVICES. Levels are min(agent level, homeassistant level), like servers:
  1  read: GET /api/... and read-only WebSocket commands (states, registries, dashboards and their config)
  4  change: call services, save a dashboard (the current config is backed up first), restore a backup,
     create/update dashboards and dashboard resources
  6  (or unhinged mode) any /api/ request and any WebSocket command
Responses are returned as text pages of PAGE characters (offset/eof), like file reads.
"""
import base64
import hashlib
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .core import SERVICES, infisical_secrets

NAME = 'homeassistant'
PAGE = 10000                 # characters of response per call
MAX_RESPONSE = 4 * 1024 * 1024
MAX_CONFIG = 512 * 1024      # serialized dashboard config
TIMEOUT = 20
KEEP_BACKUPS = 50            # per dashboard
OPERATIONS = {'entities': 1, 'rest': 1, 'websocket': 1, 'save_dashboard': 4, 'backups': 1, 'restore_dashboard': 4}
READ_WS = {'get_states', 'get_config', 'get_services', 'get_panels', 'lovelace/config', 'lovelace/dashboards/list',
           'lovelace/resources', 'config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list',
           'config/floor_registry/list', 'config/label_registry/list', 'frontend/get_themes', 'system_health/info'}
CHANGE_WS = {'call_service', 'lovelace/dashboards/create', 'lovelace/dashboards/update', 'lovelace/resources/create',
             'lovelace/resources/update'}
_PATH = re.compile(r'^/api/[A-Za-z0-9._~/%:+-]*(\?[A-Za-z0-9._~/%:+=&,-]*)?$')
_SERVICE = re.compile(r'^/api/services/[a-z0-9_]+/[a-z0-9_]+$')
_DASHBOARD = re.compile(r'^[a-z0-9][a-z0-9_-]{0,49}$')
_WS_TYPE = re.compile(r'^[a-z0-9_/]{1,80}$')


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ha_backups (id INTEGER PRIMARY KEY, timestamp INTEGER NOT NULL,
        dashboard TEXT NOT NULL, config TEXT, actor TEXT)''')


def connection():
    """(base URL, token) from Infisical; the URL must match the pinned one."""
    pinned, path = SERVICES[NAME]
    values = infisical_secrets(path, ('HA_URL', 'HA_TOKEN'))
    if values['HA_URL'].strip().rstrip('/') != pinned:
        raise RuntimeError('HA_URL does not match the pinned Home Assistant URL')
    token = values['HA_TOKEN'].strip()
    if not token or any(c in token for c in '\r\n '):
        raise RuntimeError('Invalid HA_TOKEN')
    return pinned, token


class HAError(Exception):
    """A refusal or failure with a reason safe to show to the agent."""
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


def check(op, body, level, free):
    """Validate a request against the level: returns the normalized request or raises HAError."""
    if op not in OPERATIONS:
        raise HAError(400, 'invalid_operation', 'operation must be one of ' + ', '.join(OPERATIONS))
    if level < 1:
        raise HAError(403, 'policy_denied')
    need = OPERATIONS[op]
    offset = body.get('offset', 0) or 0
    if type(offset) is not int or offset < 0:
        raise HAError(400, 'invalid_offset')
    req = {'op': op, 'offset': offset}
    if op == 'rest':
        method, path = str(body.get('method') or 'GET').upper(), body.get('path')
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            raise HAError(400, 'invalid_method')
        if not isinstance(path, str) or len(path) > 300 or not _PATH.match(path) or '..' in path:
            raise HAError(400, 'invalid_path', 'path must start with /api/')
        if path.startswith('/api/hassio') and not free:
            raise HAError(403, 'policy_denied', 'the Supervisor API needs a free shell level')
        if method != 'GET':
            need = 4 if method == 'POST' and _SERVICE.match(path) else 6
        req.update(method=method, path=path, data=body.get('data'))
    elif op == 'websocket':
        message = body.get('message')
        if not isinstance(message, dict) or not isinstance(message.get('type'), str) or not _WS_TYPE.match(message['type']):
            raise HAError(400, 'invalid_message', 'message must be an object with a type, e.g. {"type": "get_states"}')
        kind = message['type']
        if kind in ('auth', 'lovelace/config/save'):
            raise HAError(400, 'use_save_dashboard' if kind != 'auth' else 'invalid_message',
                          'save dashboards with operation save_dashboard (it keeps a backup)' if kind != 'auth' else '')
        need = 1 if kind in READ_WS else 4 if kind in CHANGE_WS else 6
        req['message'] = {k: v for k, v in message.items() if k != 'id'}
    elif op in ('save_dashboard', 'restore_dashboard', 'backups'):
        dashboard = body.get('dashboard')
        if dashboard is not None and (not isinstance(dashboard, str) or not _DASHBOARD.match(dashboard)):
            raise HAError(400, 'invalid_dashboard', 'dashboard is the url_path (e.g. "dashboard-home"), or null for the default one')
        req['dashboard'] = dashboard
        if op == 'save_dashboard':
            config = body.get('config')
            if not isinstance(config, dict) or not (isinstance(config.get('views'), list) or 'strategy' in config):
                raise HAError(400, 'invalid_config', 'config must be a dashboard object with a views list (or a strategy)')
            if len(json.dumps(config)) > MAX_CONFIG:
                raise HAError(400, 'config_too_large')
            req['config'] = config
        if op == 'restore_dashboard':
            if type(body.get('backup_id')) is not int:
                raise HAError(400, 'invalid_backup_id')
            req['backup_id'] = body['backup_id']
    elif op == 'entities':
        search = body.get('search') or ''
        if not isinstance(search, str) or len(search) > 100:
            raise HAError(400, 'invalid_search')
        req['search'] = search.lower()
    if not free and level < need:  # free: level 6 or unhinged mode, everything is allowed
        raise HAError(403, 'policy_denied', f'needs level {need} on homeassistant (you have {level})' if need < 6 else
                      'needs level 6 or unhinged mode on homeassistant')
    req['need'] = need
    return req


def rest(base, token, method, path, data=None):
    """(HTTP status, response text)."""
    payload = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(base + path, data=payload, method=method, headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_RESPONSE).decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4000).decode('utf-8', 'replace')


class WebSocket:
    """Minimal RFC 6455 client for HA's /api/websocket: text frames, client masking, ping/pong, fragments."""

    def __init__(self, base, timeout=TIMEOUT):
        url = urlsplit(base)
        port = url.port or (443 if url.scheme == 'https' else 80)
        sock = socket.create_connection((url.hostname, port), timeout=timeout)
        if url.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=url.hostname)
        self.sock = sock
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f'GET /api/websocket HTTP/1.1\r\nHost: {url.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                      f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        self.reader = sock.makefile('rb')
        status = self.reader.readline()
        headers = {}
        while True:
            line = self.reader.readline()
            if line in (b'\r\n', b'\n', b''):
                break
            name, _, value = line.decode('latin-1').partition(':')
            headers[name.strip().lower()] = value.strip()
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if b' 101 ' not in status or headers.get('sec-websocket-accept') != accept:
            raise RuntimeError('WebSocket upgrade failed')

    def _frame(self, opcode, data):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, 'big')
                                        if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, 'big'))
        key = int.from_bytes((mask * (n // 4 + 1))[:n], 'big')
        self.sock.sendall(head + mask + (int.from_bytes(data, 'big') ^ key).to_bytes(n, 'big'))

    def send(self, obj):
        self._frame(0x1, json.dumps(obj).encode())

    def _read(self, n):
        data = self.reader.read(n)
        if len(data) != n:
            raise RuntimeError('WebSocket closed')
        return data

    def recv(self):
        parts, total = [], 0
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126: n = int.from_bytes(self._read(2), 'big')
            elif n == 127: n = int.from_bytes(self._read(8), 'big')
            mask = self._read(4) if b2 & 0x80 else None
            total += n
            if total > MAX_RESPONSE:
                raise RuntimeError('WebSocket message too large')
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise RuntimeError('WebSocket closed')
            if opcode == 0x9:
                self._frame(0xA, data); total -= n; continue
            if opcode == 0xA:
                total -= n; continue
            parts.append(data)
            if b1 & 0x80:
                return json.loads(b''.join(parts))

    def close(self):
        try: self._frame(0x8, b'')
        except OSError: pass
        self.sock.close()


def ws_session(base, token):
    ws = WebSocket(base)
    try:
        if ws.recv().get('type') != 'auth_required':
            raise RuntimeError('Unexpected WebSocket greeting')
        ws.send({'type': 'auth', 'access_token': token})
        if ws.recv().get('type') != 'auth_ok':
            ws.close()
            raise HAError(502, 'ha_auth_failed', 'Home Assistant rejected HA_TOKEN')
    except Exception:
        ws.sock.close()
        raise
    ws.next_id = 1
    return ws


def ws_call(ws, message):
    """One command: (success, result or error)."""
    ident, ws.next_id = ws.next_id, ws.next_id + 1
    ws.send({**message, 'id': ident})
    while True:
        reply = ws.recv()
        if reply.get('id') == ident and reply.get('type') == 'result':
            return bool(reply.get('success')), reply.get('result') if reply.get('success') else reply.get('error')


def page(text, offset):
    chunk = text[offset:offset + PAGE]
    return {'size': len(text), 'offset': offset, 'content': chunk, 'eof': offset + len(chunk) >= len(text)}


def backup(db, dashboard, config, actor):
    db.execute('INSERT INTO ha_backups(timestamp,dashboard,config,actor) VALUES (?,?,?,?)',
               (int(time.time()), dashboard or '', None if config is None else json.dumps(config), actor))
    ident = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.execute('''DELETE FROM ha_backups WHERE dashboard=? AND id NOT IN
        (SELECT id FROM ha_backups WHERE dashboard=? ORDER BY id DESC LIMIT ?)''', (dashboard or '', dashboard or '', KEEP_BACKUPS))
    return ident


def _current(ws, dashboard):
    """The stored config, or None while the dashboard is auto-generated (no saved config yet)."""
    ok, result = ws_call(ws, {'type': 'lovelace/config', 'url_path': dashboard})
    if ok:
        return result
    if isinstance(result, dict) and result.get('code') == 'config_not_found':
        return None
    raise HAError(502, 'ha_error', json.dumps(result)[:300])


def run(db, req, actor):
    """Carry out a checked request. Returns the reply payload (a dict)."""
    op = req['op']
    if op == 'backups':
        rows = db.execute('SELECT id,timestamp,dashboard,actor,length(config) FROM ha_backups WHERE dashboard=? ORDER BY id DESC LIMIT 20',
                          (req['dashboard'] or '',)).fetchall()
        return {'backups': [{'id': i, 'timestamp': t, 'dashboard': d or None, 'actor': a, 'chars': n or 0,
                             'auto_generated': n is None} for i, t, d, a, n in rows]}
    base, token = connection()
    if op == 'rest':
        status, text = rest(base, token, req['method'], req['path'], req.get('data'))
        return {'status': status, **page(text, req['offset'])}
    if op == 'entities':
        status, text = rest(base, token, 'GET', '/api/states')
        if status != 200:
            raise HAError(502, 'ha_error', f'HTTP {status}: {text[:200]}')
        lines = []
        for item in json.loads(text):
            attrs = item.get('attributes') or {}
            line = ' | '.join(str(x) for x in (item.get('entity_id'), item.get('state'), attrs.get('friendly_name', ''),
                                                 attrs.get('unit_of_measurement', '')) if x != '')
            if req['search'] in line.lower():
                lines.append(line)
        return {'count': len(lines), **page('\n'.join(sorted(lines)), req['offset'])}
    ws = ws_session(base, token)
    try:
        if op == 'websocket':
            ok, result = ws_call(ws, req['message'])
            return {'success': ok, **page(json.dumps(result, indent=1, ensure_ascii=False), req['offset'])}
        dashboard = req['dashboard']
        if op == 'save_dashboard':
            ident = backup(db, dashboard, _current(ws, dashboard), actor)
            ok, result = ws_call(ws, {'type': 'lovelace/config/save', 'url_path': dashboard, 'config': req['config']})
            if not ok:
                raise HAError(502, 'ha_error', json.dumps(result)[:300])
            return {'saved': True, 'dashboard': dashboard, 'backup_id': ident}
        row = db.execute('SELECT dashboard,config FROM ha_backups WHERE id=?', (req['backup_id'],)).fetchone()
        if not row or (row[0] or None) != dashboard:
            raise HAError(404, 'unknown_backup', 'no backup with that id for this dashboard')
        ident = backup(db, dashboard, _current(ws, dashboard), actor)
        if row[1] is None:  # it was auto-generated before: remove the stored config again
            ok, result = ws_call(ws, {'type': 'lovelace/config/delete', 'url_path': dashboard})
        else:
            ok, result = ws_call(ws, {'type': 'lovelace/config/save', 'url_path': dashboard, 'config': json.loads(row[1])})
        if not ok:
            raise HAError(502, 'ha_error', json.dumps(result)[:300])
        return {'restored': req['backup_id'], 'dashboard': dashboard, 'backup_id': ident}
    finally:
        ws.close()
