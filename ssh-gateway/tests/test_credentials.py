"""Credential store through the gateway API: write-only, encrypted, masked everywhere (HLA_CREDENTIALS_V1).

python3 tests/test_credentials.py   (needs paramiko importable; no network)
"""
import base64
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
KEY = base64.b64encode(os.urandom(32)).decode()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'GATEWAY_CREDENTIAL_KEY': KEY})
os.environ.pop('GITHUB_TOKEN', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import credentials as creds, github  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

VALUE = 'ghp_' + 'Q7x' * 12          # looks like a GitHub token
RAN = []
def fake_execute(server, cmd, timeout=40, max_output=8000, stdin=None):
    RAN.append(cmd)
    return {'exit_code': 0, 'stdout': 'token is ' + VALUE + '\n', 'stderr': '', 'timed_out': False, 'truncated': False}
srv.execute = fake_execute

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % httpd.server_address[1]
AGENT = '11111111-2222-3333-4444-555555555555'

def call(method, path, body=None, token='a' * 40):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

# Store: admin token only, reply without the value.
s, r = call('PUT', '/v1/credentials/GITHUB_TOKEN', {'value': VALUE, 'kind': 'token', 'description': 'PRs', 'actor': 'owner'}, token='t' * 40)
check(s == 401, 'the tool token cannot store credentials')
s, r = call('PUT', '/v1/credentials/GITHUB_TOKEN', {'value': VALUE, 'kind': 'token', 'description': 'PRs', 'actor': 'owner'})
check(s == 200 and r['name'] == 'GITHUB_TOKEN' and VALUE not in json.dumps(r) and r['fingerprint'], 'stored; the reply has metadata only')
s, r = call('PUT', '/v1/credentials/bad-name', {'value': 'x' * 10})
check(s == 400 and r['error'] == 'invalid_name', 'names are UPPER_CASE')
s, r = call('PUT', '/v1/credentials/EMPTY_ONE', {'value': '  '})
check(s == 400 and r['error'] == 'invalid_value', 'empty values are refused')

# Listing: names and metadata, never values; admin only.
s, r = call('GET', '/v1/credentials', token='t' * 40)
check(s == 401, 'the tool token cannot list credentials')
s, r = call('GET', '/v1/credentials')
check(s == 200 and r['configured'] and [c['name'] for c in r['credentials']] == ['GITHUB_TOKEN'] and VALUE not in json.dumps(r),
      'listing shows names, never values')

# At rest: encrypted (the database does not contain the value), bound to its name.
raw = open(os.environ['GATEWAY_DB'], 'rb').read()
check(VALUE.encode() not in raw, 'the value is not stored in plain text')
with srv.db_open() as db:
    check(creds.get(db, 'GITHUB_TOKEN') == VALUE, 'gateway code can decrypt it')
    db.execute("UPDATE credentials SET name='OTHER_NAME' WHERE name='GITHUB_TOKEN'"); db.commit()
    try:
        creds.get(db, 'OTHER_NAME'); moved = False
    except creds.CredentialError as exc:
        moved = exc.error == 'credential_unreadable'
    db.execute("UPDATE credentials SET name='GITHUB_TOKEN' WHERE name='OTHER_NAME'"); db.commit()
check(moved, 'a ciphertext copied to another name does not decrypt')

# Used by the GitHub connection before .env.gateway and Infisical.
creds._cache['at'] = 0
check(github.token() == VALUE, 'the GitHub token comes from the credential store')

# Masked in every reply: a command printing the value shows [credential:NAME].
call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'name': 'Infra', 'level': 6}}, 'servers': {'sandbox': {'level': 6}}}, 'actor': 't'})
s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'command', 'command': 'cat ~/.config/gh'}, token='t' * 40)
check(s == 200 and VALUE not in json.dumps(r) and '[credential:GITHUB_TOKEN]' in r.get('stdout', ''), f'command output masks the credential ({r})')
with srv.db_open() as db:
    srv.audit(db, 'sandbox', 'test', 'd', detail='curl -H "Authorization: token ' + VALUE + '"'); db.commit()
    detail = db.execute("SELECT detail FROM audit WHERE action='test'").fetchone()[0]
check(VALUE not in detail, 'audit rows mask the credential')

# Wrong key: unreadable, never exposed; no key: the store is off.
os.environ['GATEWAY_CREDENTIAL_KEY'] = base64.b64encode(os.urandom(32)).decode()
with srv.db_open() as db:
    try:
        creds.get(db, 'GITHUB_TOKEN'); wrong = False
    except creds.CredentialError as exc:
        wrong = exc.error == 'credential_unreadable'
check(wrong, 'another key cannot read stored values')
os.environ['GATEWAY_CREDENTIAL_KEY'] = ''
s, r = call('PUT', '/v1/credentials/NEW_TOKEN', {'value': 'x' * 20})
check(s == 503 and r['error'] == 'credentials_not_configured', 'without GATEWAY_CREDENTIAL_KEY the store is off')
os.environ['GATEWAY_CREDENTIAL_KEY'] = KEY

# Delete.
s, r = call('DELETE', '/v1/credentials/GITHUB_TOKEN', token='t' * 40)
check(s == 401, 'the tool token cannot delete')
s, r = call('DELETE', '/v1/credentials/GITHUB_TOKEN')
check(s == 200 and r['deleted'], 'deleted')
s, r = call('GET', '/v1/credentials')
check(r['credentials'] == [], 'gone from the listing')
with srv.db_open() as db:
    actions = [a for (a,) in db.execute("SELECT action FROM audit WHERE action LIKE 'credential_%' ORDER BY id").fetchall()]
check(actions == ['credential_stored', 'credential_deleted'], f'store and delete are audited ({actions})')

# Fingerprints: keyed (HMAC with a subkey of GATEWAY_CREDENTIAL_KEY), 10 hex characters; old rows recomputed when listed.
import hashlib, hmac, html as html_mod, re, urllib.parse  # noqa: E402
with srv.db_open() as db:
    creds.put(db, 'FP_TEST', 'fingerprint-value-1', actor='t')
    fp = creds.meta(db, 'FP_TEST')['fingerprint']
    subkey = hmac.new(base64.b64decode(KEY), b'fingerprint', hashlib.sha256).digest()
    check(fp == hmac.new(subkey, b'fingerprint-value-1', hashlib.sha256).hexdigest()[:10]
          and fp != hashlib.sha256(b'hla-credential:fingerprint-value-1').hexdigest()[:10], 'the fingerprint is an HMAC with a key-derived subkey')
    db.execute("UPDATE credentials SET fingerprint=? WHERE name='FP_TEST'", (hashlib.sha256(b'hla-credential:fingerprint-value-1').hexdigest()[:10],))
    db.commit()
    listed = {c['name']: c['fingerprint'] for c in creds.listing(db)['credentials']}
    check(listed['FP_TEST'] == fp and db.execute("SELECT fingerprint FROM credentials WHERE name='FP_TEST'").fetchone()[0] == fp,
          'an old unkeyed fingerprint is recomputed when listed')
    creds.delete(db, 'FP_TEST')

# redact: one masking helper (gateway/masking.py) for every encoded form.
V = 'pa/ss+w0rd&key=Zx9 "q"'
known = {'ODD_ONE': V, 'SHORT': 'abc12'}
forms = {'raw': V, 'json': json.dumps(V)[1:-1], 'json_slash': json.dumps(V)[1:-1].replace('/', '\\/'),
         'pct': urllib.parse.quote(V, safe=''),
         'pct_lower': re.sub(r'%[0-9A-F]{2}', lambda m: m.group(0).lower(), urllib.parse.quote(V, safe='')),
         'plus': urllib.parse.quote_plus(V), 'html': html_mod.escape(V), 'html_num': V.replace('&', '&#38;').replace('/', '&#x2F;'),
         'b64': base64.b64encode(V.encode()).decode(), 'b64url': base64.urlsafe_b64encode(V.encode()).decode()}
out = creds.redact({'items': ['before ' + f + ' after' for f in forms.values()], V: 'key'}, known)
check(all(x == 'before [credential:ODD_ONE] after' for x in out['items']), f'every encoded form is masked ({out["items"]})')
check(list(out)[1] == '[credential:ODD_ONE]', 'a value used as a dict key is masked')
check(creds.redact('cut: ' + V[:9], known) == 'cut: [credential:ODD_ONE]' and creds.redact('cut: ' + V[:7], known) == 'cut: ' + V[:7],
      'a value cut off at the end of a text is masked from 8 characters on')
check(creds.redact('abc12 stays', known) == 'abc12 stays' and creds.MIN_REDACT == 6, 'values shorter than 6 characters are not masked (one minimum everywhere)')
from gateway import masking  # noqa: E402
basic = base64.b64encode(('robot:' + V).encode()).decode()
check(masking.mask_text('Basic ' + basic, masking.prepare([(V, '[X]', ['robot'])])) == 'Basic [X]', 'base64 of user:value is masked when the user is named')

httpd.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
