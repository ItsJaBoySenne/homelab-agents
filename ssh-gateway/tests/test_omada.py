"""Omada controller through the gateway (gateway/omada.py, HLA_OMADA_V1): certificate pinning (pending, trusted,
changed, nothing with credentials sent before trust), the client-credentials token flow with renewal, levels (service
off by default, reads at 1, writes at 5 with a single-use approval of the exact text, 6 without, unhinged mode never
applies), path/query/body validation, the dashboard (admin token), and the client secret and access token masked in
every reply and audit row.

The controller is a local HTTPS server with a throwaway self-signed certificate (made with cryptography, shipped with
paramiko); the gateway's real TLS code connects to it, only the address is redirected.

python3 tests/test_omada.py   (needs paramiko importable; no network)
"""
import base64
import datetime
import json
import os
import sqlite3
import ssl
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'GATEWAY_CREDENTIAL_KEY': base64.b64encode(os.urandom(32)).decode(), 'OMADA_CA': TMP + '/missing.pem'})
for k in ('OMADA_URL', 'OMADA_TLS_NAME', 'GITHUB_TOKEN'):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import omada as O, policy  # noqa: E402
from gateway.cli import db_open  # noqa: E402
import gateway_api.server as srv  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)


# ── a throwaway certificate ──────────────────────────────────────────────────────────────────────────────────────────
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

def make_cert(tag):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'omada-' + tag)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    cert_path, key_path = f'{TMP}/{tag}.crt', f'{TMP}/{tag}.key'
    Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    Path(key_path).write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    import hashlib
    return cert_path, key_path, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


# ── the fake controller ──────────────────────────────────────────────────────────────────────────────────────────────
CID = 'c0ffee1234567890abcdef'
SECRET = 'omada-client-secret-5f1e2d3c4b5a'
STATE = {'tokens': 0, 'calls': [], 'expire_next': False, 'clients': [], 'writes': []}

def make_clients():
    return [{'mac': f'AA-BB-CC-00-00-{i:02X}', 'ip': f'192.168.10.{i}', 'name': f'host-{i}', 'hostName': f'host{i}', 'wireless': i % 2 == 0,
             'secretish': 'x'} for i in range(1, 151)]
STATE['clients'] = make_clients()

class Controller(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def handle_any(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        auth = self.headers.get('Authorization')
        STATE['calls'].append((self.command, url.path, q, body, auth))
        if url.path == '/api/info':
            return self.send({'errorCode': 0, 'msg': 'Success.', 'result': {'omadacId': CID, 'controllerVer': '5.15.20.18', 'apiVer': '3', 'type': 1}})
        if url.path == '/openapi/authorize/token':
            if q.get('grant_type') != 'client_credentials' or body != {'omadacId': CID, 'client_id': 'omada-client-id', 'client_secret': SECRET}:
                return self.send({'errorCode': -44106, 'msg': 'The client id or client secret is invalid.'})
            STATE['tokens'] += 1
            return self.send({'errorCode': 0, 'msg': 'Open API Get Access Token successfully.',
                              'result': {'accessToken': f'AT-{STATE["tokens"]}-0123456789abcdef', 'tokenType': 'bearer', 'expiresIn': 7200,
                                         'refreshToken': f'RT-{STATE["tokens"]}-fedcba9876543210'}})
        prefix = f'/openapi/v1/{CID}'
        if not url.path.startswith(prefix):
            return self.send({'errorCode': -1, 'msg': 'not found'}, 404)
        if auth != f'AccessToken=AT-{STATE["tokens"]}-0123456789abcdef':
            return self.send({'errorCode': -44113, 'msg': 'The access token is invalid.'}, 401)
        if STATE['expire_next']:
            STATE['expire_next'] = False
            return self.send({'errorCode': -44112, 'msg': 'The access token has expired. Please re-initiate the refreshToken process to obtain the access token.'})
        path = url.path[len(prefix):]
        page, size = int(q.get('page', 1)), int(q.get('pageSize', 10))
        def paged(items):
            return self.send({'errorCode': 0, 'msg': 'Success.', 'result': {'totalRows': len(items), 'currentPage': page, 'currentSize': size,
                                                                            'data': items[(page - 1) * size:page * size]}})
        if path == '/sites' and self.command == 'GET':
            return paged([{'siteId': 'site-home-01', 'name': 'Home', 'region': 'Belgium', 'scenario': 'Home'}])
        if path == '/sites/site-home-01/clients':
            return paged(STATE['clients'])
        if path == '/sites/site-home-01/devices':
            return paged([{'mac': '50-91-E3-00-00-01', 'name': 'ER605', 'type': 'gateway', 'model': 'ER605', 'ip': '192.168.1.1', 'status': 14}])
        if path == '/sites/site-home-01/setting/lan/networks' and self.command == 'GET':
            return self.send({'errorCode': 0, 'msg': 'Success.', 'result': {'data': [{'id': 'net30', 'name': 'Servers', 'vlan': 30}],
                                                                             'echo': SECRET}})
        if path.startswith('/sites/site-home-01/') and self.command in ('POST', 'PUT', 'PATCH', 'DELETE'):
            STATE['writes'].append((self.command, path, body))
            return self.send({'errorCode': 0, 'msg': 'Success.', 'result': {'ok': True}})
        return self.send({'errorCode': -1001, 'msg': 'Invalid request parameters.'})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # the gateway hangs up right after refusing a certificate: expected

def serve(cert, key):
    httpd = TLSServer(('127.0.0.1', 0), Controller)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd

CERT1, KEY1, FP1 = make_cert('one')
CERT2, KEY2, FP2 = make_cert('two')
ctrl = serve(CERT1, KEY1)
TARGET = {'port': ctrl.server_address[1]}
O._address = lambda host, port: (('127.0.0.1', TARGET['port']) if host == O.HOST and port == 8043 else (host, port))
O._secrets = lambda folder, names: {'APPLICATION_NAME': 'hla-agents', 'CLIENT_ID': 'omada-client-id', 'CLIENT_SECRET': SECRET} if folder == '/Omada' else {}

# ── the gateway API ──────────────────────────────────────────────────────────────────────────────────────────────────
api_server = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=api_server.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % api_server.server_address[1]
AGENT = '11111111-1111-4111-8111-111111111111'
TASK = '33333333-3333-4333-8333-333333333333'
REPLIES = []

def call(method, path, body=None, token='a' * 40, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: code, out = r.status, json.load(r)
    except urllib.error.HTTPError as e: code, out = e.code, json.load(e)
    REPLIES.append(json.dumps(out))
    return code, out
def agent(body):
    return call('POST', '/v1/omada', {'agent_id': AGENT, **body}, token='t' * 40, headers={'X-HLA-Task': TASK})
def admin(body):
    return call('POST', '/v1/omada', {'actor': 'jordan', **body})
def levels(agent_level=None, service=None):
    change = {'servers': {}}
    if agent_level is not None: change['agents'] = {AGENT: {'name': 'Net', 'level': agent_level}}
    if service is not None: change['servers'][O.NAME] = {'level': service}
    with db_open() as db: policy.update(db, change, 'test')
def audits():
    with sqlite3.connect(os.environ['GATEWAY_DB']) as d:
        return d.execute('SELECT action,server,actor,agent_id,detail,task_id,exit_code FROM audit ORDER BY id').fetchall()
def grant(text):
    return call('POST', '/v1/approvals', {'server': O.NAME, 'command': text, 'actor': 'jordan'})
def authed_calls():
    return [c for c in STATE['calls'] if c[1] != '/api/info']

# ── policy: off by default ───────────────────────────────────────────────────────────────────────────────────────────
with db_open() as db:
    snap = policy.snapshot(db)
check(snap['services'].get(O.NAME) == {'level': 0}, 'policy: omada:controller exists and starts at level 0')
levels(5)
code, body = agent({'operation': 'status'})
check(code == 403 and body['error'] == 'policy_denied' and audits()[-1][0] == 'omada_denied' and audits()[-1][5] == TASK,
      'levels: an agent is refused while the service is at 0 (audited with the task)')
check(call('POST', '/v1/omada', {'operation': 'status'}, token='x' * 40)[0] == 401, 'needs a token')

# ── certificate: pending until trusted, nothing with credentials before ──────────────────────────────────────────────
levels(5, 1)
code, body = admin({'operation': 'status'})
check(code == 200 and body['controller']['omadacId'] == CID and body['controller']['controllerVer'] == '5.15.20.18'
      and body['certificate']['state'] == 'pending' and body['certificate']['seen'].replace(':', '').lower() == FP1
      and body['error'] == 'certificate_not_trusted' and not body['connected'], f'status: version and omadacId shown, certificate pending ({body.get("error")})')
check(authed_calls() == [], 'certificate: no token request (no credentials) before the owner trusts it')
code, body = agent({'operation': 'sites'})
check(code == 409 and body['error'] == 'certificate_not_trusted' and authed_calls() == [], 'certificate: agent calls blocked while pending')
check(admin({'operation': 'trust_certificate', 'fingerprint': FP2})[1]['error'] == 'fingerprint_mismatch', 'trust: only the fingerprint the controller presents')
check(agent({'operation': 'trust_certificate', 'fingerprint': FP1})[0] == 403, 'trust: agents cannot trust a certificate')
code, body = admin({'operation': 'trust_certificate', 'fingerprint': ':'.join(FP1[i:i + 2] for i in range(0, 64, 2)).upper()})
check(code == 200 and body['status'] == 'trusted' and audits()[-1][0] == 'omada_trust_certificate' and audits()[-1][2] == 'jordan',
      'trust: the owner trusts the presented fingerprint (audited)')

# ── token flow and reads ─────────────────────────────────────────────────────────────────────────────────────────────
code, body = admin({'operation': 'status'})
check(code == 200 and body['connected'] and body['sites'] == 1 and body['certificate']['state'] == 'trusted' and body['error'] is None
      and STATE['tokens'] == 1, f'status: connected after trust, one token ({body.get("error")}: {body.get("detail")})')
code, body = agent({'operation': 'sites'})
check(code == 200 and body['sites'] == [{'siteId': 'site-home-01', 'name': 'Home', 'region': 'Belgium', 'scenario': 'Home'}] and STATE['tokens'] == 1,
      'sites: listed, the token is reused')
code, body = agent({'operation': 'clients', 'mac': 'aa:bb:cc:00:00:96'})
check(code == 200 and body['count'] == 1 and body['clients'][0]['ip'] == '192.168.10.150' and 'secretish' not in body['clients'][0]
      and body['scanned'] == 150 and body['site'] == 'site-home-01', f'clients: found by MAC over two pages, compact fields ({body.get("count")})')
code, body = agent({'operation': 'clients', 'site': 'home', 'ip': '192.168.10.7'})
check(code == 200 and [c['name'] for c in body['clients']] == ['host-7'], 'clients: site by name, filter by IP')
code, body = agent({'operation': 'devices', 'site': 'site-home-01'})
check(code == 200 and body['devices'][0]['model'] == 'ER605', 'devices: listed')
check(agent({'operation': 'clients', 'site': 'office'})[1]['error'] == 'unknown_site', 'clients: unknown site named with the choices')
check(agent({'operation': 'clients', 'mac': 'nope'})[1]['error'] == 'invalid_filter', 'clients: invalid MAC refused')
row = audits()[-3]
check(row[0] == 'omada_clients' and row[3] == AGENT and 'ip=192.168.10.7' in row[4] and row[5] == TASK, 'reads: audited with filter and task')

# generic GET, paged text
code, body = agent({'operation': 'get', 'path': '/sites/site-home-01/setting/lan/networks', 'query': {'page': 1}})
check(code == 200 and '"vlan": 30' in body['content'] and body['eof'] and SECRET not in json.dumps(body) and '[secret:OMADA_CLIENT_SECRET]' in body['content'],
      'get: any path below the controller, the client secret masked in the reply')
for bad in ('/../api/info', 'sites', '/sites//x', '/sites/<x>', '/' + 'a' * 400):
    check(agent({'operation': 'get', 'path': bad})[1].get('error') == 'invalid_path', f'get: path {bad[:20]!r} refused')
check(agent({'operation': 'get', 'path': '/sites', 'query': {'a b': 1}})[1]['error'] == 'invalid_query', 'get: query keys checked')

# token renewal: expired once -> renewed and retried
STATE['expire_next'] = True
code, body = agent({'operation': 'sites'})
check(code == 200 and STATE['tokens'] == 2, 'token: an expired token is renewed once and the call retried')
O._token['expires'] = 0
agent({'operation': 'sites'})
check(STATE['tokens'] == 3, 'token: renewed before it expires')

# ── writes: level 5 with a single-use approval of the exact text, 6 without, unhinged never ──────────────────────────
WRITE = {'operation': 'write', 'method': 'POST', 'path': '/sites/site-home-01/setting/service/dhcp/reservations',
         'body': {'mac': 'BC-24-11-00-02-05', 'ip': '192.168.10.205', 'netId': 'net30', 'description': 'monitoring'}}
code, body = agent(WRITE)
check(code == 403 and body['error'] == 'policy_denied', 'write: level 1 cannot write')
levels(5, 5)
code, body = agent(WRITE)
text = body.get('approval_text') or ''
check(code == 403 and body['error'] == 'approval_required' and text.startswith('omada write on controller\nmethod: POST\npath: /sites/site-home-01/')
      and '"ip": "192.168.10.205"' in text and body['request']['body']['mac'] == 'BC-24-11-00-02-05' and STATE['writes'] == []
      and audits()[-1][0] == 'omada_approval_required', 'write: below 6 the exact text waits for approval; nothing written')
check(grant('proxmox create_vm on pve2')[1]['error'] == 'invalid_command', 'approval: only omada texts for this service')
check(grant(text)[0] == 200, 'approval: the owner grants that exact text')
check(agent({**WRITE, 'body': {**WRITE['body'], 'ip': '192.168.10.206'}})[1]['error'] == 'approval_required' and STATE['writes'] == [],
      'approval: a different body is not covered')
code, body = agent(WRITE)
check(code == 200 and body['status'] == 'done' and STATE['writes'] == [('POST', '/sites/site-home-01/setting/service/dhcp/reservations', WRITE['body'])],
      'write: runs once after the approval')
row = audits()[-1]
check(row[0] == 'omada_write' and '192.168.10.205' in row[4] and 'approved by jordan' in row[4], 'write: audited with the body and the approver')
check(agent(WRITE)[1]['error'] == 'approval_required' and len(STATE['writes']) == 1, 'approval: single use')
for bad, err in (({'method': 'GET'}, 'invalid_method'), ({'path': '/sites'}, 'invalid_path'), ({'path': '/openapi/x'}, 'invalid_path'),
                 ({'body': 'text'}, 'invalid_body'), ({'body': {'x': 'y' * 4000}}, 'body_too_large')):
    check(agent({**WRITE, **bad})[1].get('error') == err, f'write: {err} refused')
with db_open() as db: policy.update(db, {'unhinged': {'enabled': True, 'hours': 1}}, 'test')
levels(5, 1)
code, body = agent(WRITE)
check(code == 403 and body['error'] == 'policy_denied', 'unhinged: does not lift the controller to writes')
with db_open() as db:
    eff = policy.effective_services(db, AGENT)[O.NAME]
check(eff == {'level': 1, 'unhinged': False, 'free_shell': False}, f'unhinged: the effective level the router sees stays 1 ({eff})')
with db_open() as db: policy.update(db, {'unhinged': {'enabled': False}}, 'test')
levels(6, 6)
code, body = agent({**WRITE, 'method': 'DELETE', 'path': '/sites/site-home-01/setting/service/dhcp/reservations/r1', 'body': None})
check(code == 200 and STATE['writes'][-1][:2] == ('DELETE', '/sites/site-home-01/setting/service/dhcp/reservations/r1'), 'write: level 6 writes without approval')

# ── certificate changed: blocked again, nothing sent ─────────────────────────────────────────────────────────────────
ctrl.shutdown(); ctrl.server_close()
ctrl = serve(CERT2, KEY2)
TARGET['port'] = ctrl.server_address[1]
O._info['at'] = 0
before = len(authed_calls())
code, body = agent({'operation': 'sites'})
check(code == 502 and body['error'] == 'certificate_changed' and len(authed_calls()) == before, 'certificate: a changed certificate blocks every call')
code, body = admin({'operation': 'status'})
check(body['certificate']['state'] == 'changed' and body['certificate']['seen'].replace(':', '').lower() == FP2 and not body['connected'],
      'status: the dashboard shows the new fingerprint to compare')
check(admin({'operation': 'trust_certificate', 'fingerprint': FP2})[0] == 200 and agent({'operation': 'sites'})[0] == 200, 'certificate: the owner trusts the new one')

# ── CA mode and URL pinning ──────────────────────────────────────────────────────────────────────────────────────────
Path(TMP + '/ca.pem').write_text(Path(CERT2).read_text())
os.environ['OMADA_CA'] = TMP + '/ca.pem'
code, body = admin({'operation': 'status'})
check(code == 200 and body['certificate']['mode'] == 'ca' and body['connected'], 'CA mode: the self-signed certificate verifies against omada-ca.pem')
Path(TMP + '/ca.pem').write_text(Path(CERT1).read_text())
O._info['at'] = 0
check(agent({'operation': 'sites'})[1]['error'] == 'omada_tls_failed', 'CA mode: another certificate is refused')
os.environ['OMADA_CA'] = TMP + '/missing.pem'
os.environ['OMADA_URL'] = 'https://192.168.20.99:8043'
check(agent({'operation': 'sites'})[1]['error'] == 'omada_target_mismatch', 'OMADA_URL cannot move the controller to another host')
os.environ['OMADA_URL'] = 'https://192.168.20.101:8043/'
check(agent({'operation': 'sites'})[0] == 200, 'OMADA_URL may restate the pinned host')
os.environ.pop('OMADA_URL')

# ── secrets ──────────────────────────────────────────────────────────────────────────────────────────────────────────
O._secrets = lambda folder, names: (_ for _ in ()).throw(RuntimeError('Infisical down'))
O.forget()
code, body = admin({'operation': 'status'})
check(body['error'] == 'omada_not_configured' and 'RuntimeError' in body['detail'] and not body['configured'], 'status: Infisical unreadable is reported')
tokens = [f'AT-{i}-0123456789abcdef' for i in range(1, STATE['tokens'] + 1)] + [f'RT-{i}-fedcba9876543210' for i in range(1, STATE['tokens'] + 1)]
leaks = [r for r in REPLIES if SECRET in r or any(t in r for t in tokens)]
with sqlite3.connect(os.environ['GATEWAY_DB']) as d:
    rows = json.dumps(d.execute('SELECT * FROM audit').fetchall()) + json.dumps(d.execute('SELECT * FROM omada_state').fetchall())
check(not leaks and SECRET not in rows and not any(t in rows for t in tokens), 'no client secret or token in any reply, audit row or state')

api_server.shutdown(); ctrl.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
