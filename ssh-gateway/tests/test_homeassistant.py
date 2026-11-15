"""Home Assistant through the gateway API: real HTTP server and SQLite, a fake Home Assistant (REST and a real
RFC 6455 WebSocket) on localhost, Infisical replaced by a stand-in.

python3 tests/test_homeassistant.py   (needs paramiko importable; no network)
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import homeassistant as ha  # noqa: E402

TOKEN = 'ha-token-' + 'x' * 30
HA = {'dashboards': {None: None, 'dashboard-home': {'views': [{'title': 'Old'}]}}, 'calls': [], 'ws': [], 'big': 'y' * 70000}


class FakeHA(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/websocket':
            return self.websocket()
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.reply(401, {'message': 'unauthorized'})
        if self.path == '/api/states':
            return self.reply(200, [{'entity_id': 'light.kitchen', 'state': 'on', 'attributes': {'friendly_name': 'Kitchen'}},
                                    {'entity_id': 'sensor.power', 'state': '420', 'attributes': {'friendly_name': 'Power', 'unit_of_measurement': 'W'}}])
        if self.path == '/api/config':
            return self.reply(200, {'version': '2026.10.1', 'big': HA['big']})
        return self.reply(404, {'message': 'not found'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0')) or 0) or b'null')
        HA['calls'].append((self.path, body))
        return self.reply(200, [])

    # --- WebSocket (server side: unmasked frames out, masked frames in) ---
    def websocket(self):
        key = self.headers['Sec-WebSocket-Key']
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101); self.send_header('Upgrade', 'websocket'); self.send_header('Connection', 'Upgrade')
        self.send_header('Sec-WebSocket-Accept', accept); self.end_headers(); self.wfile.flush()
        self.send_ws({'type': 'auth_required'})
        if self.recv_ws().get('access_token') != TOKEN:
            self.send_ws({'type': 'auth_invalid'}); return
        self.send_ws({'type': 'auth_ok'})
        self.send_ping = True
        while True:
            try: msg = self.recv_ws()
            except (ConnectionError, ValueError): return
            if msg is None: return
            HA['ws'].append(msg)
            self.send_ws({'type': 'event', 'id': 999, 'event': {}})  # unrelated traffic is skipped
            kind, path = msg['type'], msg.get('url_path')
            if kind == 'lovelace/config':
                cfg = HA['dashboards'].get(path)
                self.send_ws({'id': msg['id'], 'type': 'result', 'success': cfg is not None, 'result': cfg} if cfg is not None else
                             {'id': msg['id'], 'type': 'result', 'success': False, 'error': {'code': 'config_not_found', 'message': 'No config found.'}})
            elif kind == 'lovelace/config/save':
                HA['dashboards'][path] = msg['config']
                self.send_ws({'id': msg['id'], 'type': 'result', 'success': True, 'result': None})
            elif kind == 'lovelace/config/delete':
                HA['dashboards'][path] = None
                self.send_ws({'id': msg['id'], 'type': 'result', 'success': True, 'result': None})
            elif kind == 'get_states':
                self.send_ws({'id': msg['id'], 'type': 'result', 'success': True, 'result': [{'entity_id': 'z' * 40000}]})
            else:
                self.send_ws({'id': msg['id'], 'type': 'result', 'success': True, 'result': {'echo': kind}})

    def send_ws(self, obj):
        data = json.dumps(obj).encode()
        if getattr(self, 'send_ping', False):
            self.wfile.write(bytes([0x89, 0])); self.send_ping = False  # a ping the client must answer
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, 'big') if n < 65536 else bytes([127]) + n.to_bytes(8, 'big'))
        self.wfile.write(head + data); self.wfile.flush()

    def recv_ws(self):
        while True:
            b1, b2 = self.rfile.read(2)
            n = b2 & 0x7F
            if n == 126: n = int.from_bytes(self.rfile.read(2), 'big')
            elif n == 127: n = int.from_bytes(self.rfile.read(8), 'big')
            assert b2 & 0x80, 'client frames must be masked'
            mask = self.rfile.read(4)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(self.rfile.read(n)))
            if b1 & 0x0F == 0x8: return None
            if b1 & 0x0F == 0xA: continue  # pong
            return json.loads(data)

    def log_message(self, *a): pass


fake = ThreadingHTTPServer(('127.0.0.1', 0), FakeHA)
threading.Thread(target=fake.serve_forever, daemon=True).start()
SECRETS = {'HA_URL': ha.SERVICES[ha.NAME][0], 'HA_TOKEN': TOKEN}
READS = []
def fake_secrets(path, names):
    READS.append(path)
    return {n: SECRETS[n] for n in names}
ha.infisical_secrets = fake_secrets
real_connection = ha.connection
ha.connection = lambda: (real_connection()[0] and f'http://127.0.0.1:{fake.server_port}', real_connection()[1])

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT = '11111111-1111-4111-8111-111111111111'

def call(method, path, body=None, token='t' * 40):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)
def policy(change): return call('PUT', '/v1/policy', {'change': change, 'actor': 'jordan'}, token='a' * 40)
def hass(**kw): return call('POST', '/v1/homeassistant', {'agent_id': AGENT, **kw})

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

code, snap = call('GET', '/v1/policy')
check(snap['services'].get('homeassistant') == {'level': 1} and 'homeassistant' not in snap['servers'], 'homeassistant seeded read-only, apart from servers')
check(hass(operation='entities')[1]['error'] == 'policy_denied', 'agent without policy denied')
check(call('POST', '/v1/homeassistant', {'agent_id': AGENT, 'operation': 'entities'}, token='a' * 40)[0] == 401, 'needs the tool token')
policy({'agents': {AGENT: {'name': 'Dev', 'level': 5}}})
eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)[1]
check(eff['services']['homeassistant']['level'] == 1 and 'homeassistant' not in eff['servers'], 'effective level = min(agent, service)')

# Level 1: reads.
code, body = hass(operation='entities', search='kitchen')
check(code == 200 and body['count'] == 1 and body['content'] == 'light.kitchen | on | Kitchen' and READS[-1] == '/HomeAssistant',
      'entities: compact, searchable list; token read from /HomeAssistant')
code, body = hass(operation='rest', method='GET', path='/api/config')
check(code == 200 and body['status'] == 200 and body['size'] > 70000 and len(body['content']) == ha.PAGE and not body['eof'], 'REST GET paged')
code, body2 = hass(operation='rest', method='GET', path='/api/config', offset=70000)
check(body2['eof'] and body2['offset'] == 70000, 'next page by offset')
check(TOKEN not in json.dumps(body) + json.dumps(body2), 'the token is never returned')
code, body = hass(operation='websocket', message={'type': 'get_states'})
check(code == 200 and body['success'] and body['size'] > 40000, 'WebSocket read (large message, ping answered, other events skipped)')
code, body = hass(operation='websocket', message={'type': 'lovelace/config', 'url_path': 'dashboard-home'})
check(code == 200 and 'Old' in body['content'], 'read a dashboard config')
for kw, why in (({'operation': 'rest', 'method': 'POST', 'path': '/api/services/light/turn_on', 'data': {}}, 'service call'),
                ({'operation': 'save_dashboard', 'dashboard': None, 'config': {'views': []}}, 'dashboard save'),
                ({'operation': 'websocket', 'message': {'type': 'call_service', 'domain': 'light', 'service': 'turn_on'}}, 'WebSocket service call'),
                ({'operation': 'rest', 'method': 'DELETE', 'path': '/api/config/x'}, 'other writes')):
    code, body = hass(**kw)
    check(code == 403 and body['error'] == 'policy_denied', f'level 1 refuses: {why}')
check(hass(operation='rest', method='GET', path='/etc/passwd')[1]['error'] == 'invalid_path', 'only /api/ paths')
check(hass(operation='rest', method='GET', path='/api/../x')[1]['error'] == 'invalid_path', 'no .. in paths')
check(hass(operation='websocket', message={'type': 'lovelace/config/save', 'config': {}})[1]['error'] == 'use_save_dashboard', 'raw dashboard save refused: use save_dashboard')

# Level 4: changes, with backups.
policy({'servers': {'homeassistant': {'level': 4}}})
check(policy({'servers': {'homeassistant': {'compose_projects': ['/opt/x']}}})[0] == 400, 'services have no compose projects')
code, body = hass(operation='rest', method='POST', path='/api/services/light/turn_on', data={'entity_id': 'light.kitchen'})
check(code == 200 and HA['calls'][-1] == ('/api/services/light/turn_on', {'entity_id': 'light.kitchen'}), 'level 4: service call')
check(hass(operation='rest', method='POST', path='/api/config/core/check_config')[0] == 403, 'level 4: other POSTs still refused')
new = {'views': [{'title': 'New', 'type': 'sections', 'sections': []}]}
code, body = hass(operation='save_dashboard', dashboard='dashboard-home', config=new)
check(code == 200 and body['saved'] and HA['dashboards']['dashboard-home'] == new, 'dashboard saved')
first_backup = body['backup_id']
code, body = hass(operation='save_dashboard', dashboard=None, config=new)
check(code == 200 and HA['dashboards'][None] == new, 'default dashboard (auto-generated) saved')
code, body = hass(operation='backups', dashboard='dashboard-home')
check(body['backups'][0]['id'] == first_backup and not body['backups'][0]['auto_generated'], 'backups listed per dashboard')
code, body = hass(operation='restore_dashboard', dashboard='dashboard-home', backup_id=first_backup)
check(code == 200 and HA['dashboards']['dashboard-home'] == {'views': [{'title': 'Old'}]}, 'restore puts the old config back')
default_backup = hass(operation='backups', dashboard=None)[1]['backups'][0]
check(default_backup['auto_generated'], 'an auto-generated dashboard is recorded as such')
code, body = hass(operation='restore_dashboard', dashboard=None, backup_id=default_backup['id'])
check(code == 200 and HA['dashboards'][None] is None and HA['ws'][-1]['type'] == 'lovelace/config/delete', 'restoring it deletes the saved config again')
check(hass(operation='restore_dashboard', dashboard=None, backup_id=first_backup)[1]['error'] == 'unknown_backup', 'a backup only restores its own dashboard')
check(hass(operation='save_dashboard', dashboard='Bad Name', config=new)[1]['error'] == 'invalid_dashboard', 'dashboard url_path validated')
check(hass(operation='save_dashboard', dashboard=None, config={'title': 'x'})[1]['error'] == 'invalid_config', 'config needs views')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    actions = [r[0] for r in db.execute("SELECT action FROM audit WHERE server='homeassistant'")]
check('agent_ha_change' in actions and 'agent_ha_read' in actions, 'requests audited')

# Level 6 / unhinged: anything.
policy({'unhinged': {'enabled': True, 'hours': 1}})
check(hass(operation='rest', method='POST', path='/api/config/core/check_config')[0] == 200, 'unhinged: any request')
policy({'unhinged': {'enabled': False}})

# Pinned URL and token problems.
SECRETS['HA_URL'] = 'http://10.0.0.9:8123'
code, body = hass(operation='entities')
check(code == 502 and 'pinned' in body['detail'], 'HA_URL must match the pinned URL')
SECRETS['HA_URL'] = ha.SERVICES[ha.NAME][0] + '/'
SECRETS['HA_TOKEN'] = 'wrong'
code, body = hass(operation='websocket', message={'type': 'get_config'})
check(code == 502 and body['error'] == 'ha_auth_failed', 'a rejected token is reported')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
httpd.shutdown(); fake.shutdown()
sys.exit(1 if failures else 0)
