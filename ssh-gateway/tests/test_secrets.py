"""Agent secrets through the gateway API: real HTTP server and SQLite, Infisical and SSH replaced by stand-ins.

python3 tests/test_secrets.py   (needs paramiko importable; no network)
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'INFISICAL_URL': 'https://infisical.test', 'INFISICAL_CLIENT_ID': 'id', 'INFISICAL_CLIENT_SECRET': 'cs',
                   'INFISICAL_PROJECT_ID': 'servers-project', 'INFISICAL_ENV': 'prod',
                   'INFISICAL_GENERATED_PROJECT_ID': 'generated-project'})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import agent_secrets as sec  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

# --- Infisical stand-in: one project, root folder ---
STORE = {'OLD_APP_KEY': 'pre-existing-value-123'}
CALLS = []
def fake_call(method, base, path, payload=None, token=None):
    CALLS.append((method, path.split('?')[0], payload))
    if path.startswith('/api/v1/auth/'):
        assert payload == LOGIN, payload
        return 200, {'accessToken': 'tok'}
    assert token == 'tok'
    if method == 'GET' and path.startswith('/api/v4/secrets?'):
        assert 'projectId=generated-project' in path and 'environment=prod' in path
        return 200, {'secrets': [{'secretKey': k, 'secretValue': v, 'secretComment': 'c'} for k, v in STORE.items()]}
    name = path.rsplit('/', 1)[1]
    assert payload['projectId'] == 'generated-project' and payload['secretPath'] == '/'
    if method == 'POST':
        if name in STORE: return 400, {'message': 'exists'}
        STORE[name] = payload['secretValue']; return 200, {}
    if method == 'PATCH':
        STORE[name] = payload['secretValue']; return 200, {}
    return 404, {}
LOGIN = {'clientId': 'id', 'clientSecret': 'cs'}
sec._call = fake_call

# --- SSH stand-in: records what would run and echoes it back, like `cat .env` would ---
RAN = []
def fake_execute(server, cmd, timeout=40, max_output=8000, stdin=None):
    RAN.append((server, cmd, stdin))
    out = 'ran: ' + cmd + ('\nfile: ' + stdin.decode() if stdin else '')
    if stdin is not None: out = 'HLA_BYTES=%d\n' % len(stdin) + out
    return {'exit_code': 0, 'stdout': out, 'stderr': '', 'timed_out': False, 'truncated': False}
srv.execute = fake_execute

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % httpd.server_address[1]
AGENT = '11111111-2222-3333-4444-555555555555'

def call(method, path, body=None, token='t' * 40):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def policy(change):
    return call('PUT', '/v1/policy', {'change': change, 'actor': 'test'}, token='a' * 40)

def secrets(**body):
    return call('POST', '/v1/secrets', {'agent_id': AGENT, **body})

def run(command, server='dev'):
    return call('POST', '/v1/execute', {'agent_id': AGENT, 'server': server, 'mode': 'command', 'command': command})

# Policy: the secrets row exists and starts at level 1.
s, snap = call('GET', '/v1/policy')
check(snap['services'].get('secrets') == {'level': 1}, 'secrets listed as a service at level 1')
policy({'agents': {AGENT: {'name': 'Infra', 'level': 1}}})
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
check(eff['services']['secrets']['level'] == 1, 'effective secrets level for the agent')

# Level 1: list names, no values; create refused.
s, r = secrets(operation='list')
check(s == 200 and r['secrets'][0]['name'] == 'OLD_APP_KEY' and r['secrets'][0]['reference'] == '{{secret:OLD_APP_KEY}}'
      and 'pre-existing' not in json.dumps(r), 'list returns names and references, never values')
s, r = secrets(operation='create', name='VP_STAGING_DB_PASSWORD', kind='password')
check(s == 403 and r['error'] == 'policy_denied', 'create needs level 4')

# Level 4: create; value stored, never returned.
policy({'agents': {AGENT: {'name': 'Infra', 'level': 4}}, 'servers': {'secrets': {'level': 4}}})
s, r = secrets(operation='create', name='VP_STAGING_DB_PASSWORD', kind='password', description='staging app role')
value = STORE.get('VP_STAGING_DB_PASSWORD', '')
check(s == 200 and r['status'] == 'created' and r['reference'] == '{{secret:VP_STAGING_DB_PASSWORD}}' and r['value_returned'] is False,
      'create returns the reference')
check(len(value) == 32 and value.isalnum() and value not in json.dumps(r), 'value is 32 letters/digits and not in the reply')
check(any(c[0] == 'POST' and 'staging app role' in c[2].get('secretComment', '') and 'agent:' + AGENT in c[2]['secretComment'] for c in CALLS if c[2]),
      'Infisical comment says which agent created it and why')
s, r = secrets(operation='create', name='VP_STAGING_DB_PASSWORD', kind='password')
check(s == 409 and r['error'] == 'secret_exists' and STORE['VP_STAGING_DB_PASSWORD'] == value, 'existing secret is not overwritten at level 4')
s, r = secrets(operation='create', name='VP_STAGING_DB_PASSWORD', kind='password', replace=True)
check(s == 403, 'replace needs level 6')
for bad, err in (('ssh_key', 'invalid_name'), ('SSH_PRIVATE_KEY', 'reserved_name'), ('INFISICAL_X', 'reserved_name'), ('HA_TOKEN', 'reserved_name')):
    s, r = secrets(operation='create', name=bad, kind='password')
    check(s == 400 and r['error'] == err, 'name %s refused (%s)' % (bad, err))
s, r = secrets(operation='create', name='VP_HEX', kind='hex')
check(s == 200 and len(STORE['VP_HEX']) == 64 and set(STORE['VP_HEX']) <= set('0123456789abcdef'), 'hex kind: 64 hex characters')

# Use in a command: level 5 approval sees the reference; the server gets the value; output is redacted.
policy({'agents': {AGENT: {'name': 'Infra', 'level': 5}}, 'servers': {'dev': {'level': 5}}})
cmd = "psql -c \"ALTER ROLE app PASSWORD '{{secret:VP_STAGING_DB_PASSWORD}}'\""
s, r = run(cmd)
check(s == 403 and r['error'] == 'approval_required' and not RAN, 'level 5 still needs approval')
s, g = call('POST', '/v1/approvals', {'server': 'dev', 'command': cmd, 'actor': 'admin'}, token='a' * 40)
check(s == 200, 'approval granted on the command with the reference')
s, r = run(cmd)
check(s == 200 and RAN[-1][1] == cmd.replace('{{secret:VP_STAGING_DB_PASSWORD}}', value), 'server receives the real value')
check(value not in json.dumps(r) and '[secret:VP_STAGING_DB_PASSWORD]' in r['stdout'], 'output shows [secret:NAME], never the value')

# Unknown reference: nothing runs.
before = len(RAN)
s, g = call('POST', '/v1/approvals', {'server': 'dev', 'command': 'echo {{secret:NOPE_NOT_THERE}}', 'actor': 'admin'}, token='a' * 40)
s, r = run('echo {{secret:NOPE_NOT_THERE}}')
check(s == 404 and r['error'] == 'unknown_secret' and len(RAN) == before, 'unknown secret: refused, nothing runs')

# File write with a reference (free shell on the sandbox), then reading it back is redacted.
policy({'agents': {AGENT: {'name': 'Infra', 'level': 6}}})
env_file = 'DATABASE_URL=postgres://app:{{secret:VP_STAGING_DB_PASSWORD}}@db/vp_staging\nKEY={{secret:VP_HEX}}\n'
s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'write_file', 'path': '/srv/app/.env', 'content': env_file})
written = RAN[-1][2].decode()
check(s == 200 and value in written and STORE['VP_HEX'] in written and '{{secret' not in written, 'file on the server holds the values')
check(value not in json.dumps(r) and r['sha256'] == hashlib.sha256(env_file.encode()).hexdigest(),
      'reply: sha256 of the content as sent (with references), no values')
s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'command', 'command': 'cat /srv/app/.env; docker inspect x'})
RAN[-1] = (RAN[-1][0], RAN[-1][1], None)
check(s == 200 and value not in json.dumps(r), 'free-shell output redacted too')
# A secret the agent did not reference this time is still redacted (e.g. `cat` of an older file).
s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'command', 'command': 'echo pre-existing-value-123'})
check('pre-existing-value-123' not in json.dumps(r) and '[secret:OLD_APP_KEY]' in r['stdout'], 'every value in the project is redacted')

# Level 6: replace (rotate).
policy({'servers': {'secrets': {'level': 6}}})
old = STORE['VP_STAGING_DB_PASSWORD']
s, r = secrets(operation='create', name='VP_STAGING_DB_PASSWORD', kind='password', replace=True)
check(s == 200 and r['status'] == 'replaced' and STORE['VP_STAGING_DB_PASSWORD'] != old, 'level 6 replaces the value')
s, r = run('echo ' + old, server='sandbox')
s, r2 = run('echo {{secret:VP_STAGING_DB_PASSWORD}}', server='sandbox')
check(STORE['VP_STAGING_DB_PASSWORD'] in RAN[-1][1] and STORE['VP_STAGING_DB_PASSWORD'] not in json.dumps(r2), 'the new value is used and redacted')

# Secrets level 0 for this agent: references refused.
policy({'servers': {'secrets': {'level': 0}}})
before = len(RAN)
s, r = run('echo {{secret:VP_HEX}}', server='sandbox')
check(s == 403 and r['error'] == 'secrets_not_allowed' and len(RAN) == before, 'secrets level 0: references refused, nothing runs')
s, r = secrets(operation='list')
check(s == 403, 'secrets level 0: list refused')

# Audit: uses and creations recorded without values.
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    rows = db.execute("SELECT action, actor FROM audit WHERE action LIKE 'agent_secret_%'").fetchall()
    dump = json.dumps(db.execute('SELECT * FROM audit').fetchall())
check({a for a, _ in rows} >= {'agent_secret_created', 'agent_secret_replaced', 'agent_secret_use'}, 'audit has create, replace and use')
check(all(v not in dump for v in STORE.values()), 'no value in the audit log')

# Not configured: clear error, plain commands unaffected.
os.environ.pop('INFISICAL_GENERATED_PROJECT_ID')
policy({'servers': {'secrets': {'level': 6}}})
s, r = secrets(operation='list')
check(s == 503 and r['error'] == 'secrets_not_configured', 'without INFISICAL_GENERATED_PROJECT_ID: secrets_not_configured')
s, r = run('echo hello', server='sandbox')
check(s == 200 and 'hello' in r['stdout'], 'commands without references still run')
os.environ['INFISICAL_GENERATED_PROJECT_ID'] = 'servers-project'
sec._cache['at'] = 0
s, r = secrets(operation='list')
check(s == 502 and 'differ' in r['detail'], 'the servers project can never be used for agent secrets')

# A separate identity for Agent Generated: used when both settings are set; half-set is an error.
os.environ['INFISICAL_GENERATED_PROJECT_ID'] = 'generated-project'
os.environ.update({'INFISICAL_GENERATED_CLIENT_ID': 'gen-id', 'INFISICAL_GENERATED_CLIENT_SECRET': 'gen-cs'})
LOGIN = {'clientId': 'gen-id', 'clientSecret': 'gen-cs'}
sec._cache['at'] = 0
s, r = secrets(operation='list')
check(s == 200, 'separate Agent Generated identity is used for login')
os.environ.pop('INFISICAL_GENERATED_CLIENT_SECRET')
sec._cache['at'] = 0
s, r = secrets(operation='list')
check(s == 502 and 'both' in r['detail'], 'only the client id set: clear error')

httpd.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
