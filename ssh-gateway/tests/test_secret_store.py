"""The local secret store (gateway/secret_store.py, HLA_SECRETS_BACKEND=local) and the owner's CLI: round trip, encryption
at rest, the platform/generated separation through the agent API, atomic and concurrent writes, a wrong key, and a CLI
that never prints a value.

python3 tests/test_secret_store.py   (needs paramiko and cryptography importable; no network)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import base64
import io
import json
import multiprocessing
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
for k in [k for k in os.environ if k.startswith('INFISICAL_')] + ['GATEWAY_CREDENTIAL_KEY', 'GITHUB_TOKEN']:
    os.environ.pop(k, None)
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'HLA_SECRETS_BACKEND': 'local', 'HLA_SECRETS_DIR': TMP + '/secrets', 'HLA_SECRETS_KEY': KEY})
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateway import secret_store as ss, agent_secrets as sec, core, credentials as creds  # noqa: E402
import gateway_api.server as srv  # noqa: E402
import paramiko  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

def raises(fn, code=None):
    try:
        fn()
    except ss.SecretStoreError as exc:
        return exc if code is None or exc.code == code else None
    return None

store = ss.get()
FILE = Path(TMP, 'secrets', 'secrets.enc')

# ── round trip ───────────────────────────────────────────────────────────────────────────────────────────────────────
check(store.backend == 'local' and store.configured() and ss.describe() == {'backend': 'local', 'configured': True}, 'local is the backend')
check(store.names(ss.PLATFORM) == [] and store.names(ss.GENERATED) == [] and not FILE.exists(), 'a missing file is an empty store')
PLAT = 'plat-value-' + secrets.token_hex(8)
GEN = 'gen-value-' + secrets.token_hex(8)
check(store.put(ss.PLATFORM, 'GitHub/GITHUB_TOKEN', PLAT) is False, 'platform put (new)')
check(store.put(ss.GENERATED, 'APP_DB_PASSWORD', GEN, 'for the app') is False, 'generated put (new)')
check(store.read_platform('/GitHub', ('GITHUB_TOKEN',)) == {'GITHUB_TOKEN': PLAT}, 'platform read back')
check(store.generated()[0] == {'APP_DB_PASSWORD': GEN} and store.generated()[1]['APP_DB_PASSWORD']['comment'] == 'for the app', 'generated read back')
check(store.put(ss.PLATFORM, 'GitHub/GITHUB_TOKEN', PLAT + 'x') is True and core.platform_secrets('/GitHub', ('GITHUB_TOKEN',))['GITHUB_TOKEN'] == PLAT + 'x',
      'replace reports it and core.platform_secrets reads it')
check(store.read_platform('/GitHub', (), ('NOPE',)) == {} and raises(lambda: store.read_platform('/GitHub', ('NOPE',)), 'not_found'),
      'optional names may be missing; required ones are not_found')
check(store.names(ss.PLATFORM) == ['GitHub/GITHUB_TOKEN'] and store.names(ss.GENERATED) == ['APP_DB_PASSWORD'], 'names per namespace')
check(store.delete(ss.PLATFORM, 'GitHub/GITHUB_TOKEN') and not store.delete(ss.PLATFORM, 'GitHub/GITHUB_TOKEN')
      and store.names(ss.PLATFORM) == [], 'delete, then delete again is False')
for bad in ('', 'lower', 'a/b/c/d/e/f/KEY', '../x/KEY', 'servers/a b/KEY'):
    check(raises(lambda: store.put(ss.PLATFORM, bad, 'v'), 'invalid') is not None, f'platform name {bad!r} refused')
check(raises(lambda: store.put(ss.GENERATED, 'servers/dev/SSH_PRIVATE_KEY', 'v'), 'invalid') is not None, 'a generated name has no folder')
check(raises(lambda: store.put(ss.GENERATED, 'OK_NAME', ''), 'invalid') is not None, 'empty values refused')

# ── encryption at rest ───────────────────────────────────────────────────────────────────────────────────────────────
store.put(ss.PLATFORM, 'servers/dev/SSH_KEY_PASSPHRASE', PLAT)
raw = FILE.read_bytes()
check(PLAT.encode() not in raw and GEN.encode() not in raw and b'APP_DB_PASSWORD' not in raw and b'servers/dev' not in raw
      and raw.startswith(ss.MAGIC), 'the file holds neither values nor names in plain text')
check(stat.S_IMODE(FILE.stat().st_mode) == 0o600, 'secrets.enc is mode 0600')
check(not [p for p in FILE.parent.iterdir() if p.name.endswith('.tmp')], 'no temporary files left behind')

# ── wrong or bad key ─────────────────────────────────────────────────────────────────────────────────────────────────
wrong = ss.LocalStore(TMP + '/secrets', base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
exc = raises(lambda: wrong.names(ss.GENERATED), 'wrong_key')
check(exc is not None and 'HLA_SECRETS_KEY' in str(exc) and GEN not in str(exc), 'a wrong key: wrong_key with a clear message')
check(raises(lambda: wrong.put(ss.GENERATED, 'OTHER_NAME', 'x'), 'wrong_key') is not None and FILE.read_bytes() == raw,
      'a wrong key never overwrites the file')
for bad, why in (('', 'not set'), ('not base64 !!', 'base64'), (base64.b64encode(b'short').decode(), '32 random bytes')):
    e = raises(lambda: ss.LocalStore(TMP + '/x', bad).names(ss.PLATFORM), 'not_configured')
    check(e is not None and why in str(e) and not ss.LocalStore(TMP + '/x', bad).configured(), f'key {bad!r}: {why}')
std = base64.b64encode(base64.urlsafe_b64decode(KEY)).decode().rstrip('=')
check(ss.LocalStore(TMP + '/secrets', std).generated()[0] == {'APP_DB_PASSWORD': GEN}, 'standard base64 without padding is the same key')
FILE.write_bytes(raw[:-20] + b'garbage\n')
check(raises(lambda: ss.LocalStore(TMP + '/secrets', KEY).names(ss.PLATFORM), 'wrong_key') is not None, 'a damaged file is reported, not guessed')
FILE.write_bytes(raw)

# ── atomic writes and concurrency ────────────────────────────────────────────────────────────────────────────────────
real_replace = os.replace
def boom(*a):
    raise OSError('disk full')
os.replace = boom
try:
    store.put(ss.GENERATED, 'NEVER_SAVED', 'x')
except OSError:
    pass
os.replace = real_replace
check(FILE.read_bytes() == raw and 'NEVER_SAVED' not in store.names(ss.GENERATED)
      and not [p for p in FILE.parent.iterdir() if p.name.endswith('.tmp')], 'a failed write leaves the old file and no temp file')

def writer(i):
    s = ss.LocalStore(TMP + '/secrets', KEY)
    for j in range(5):
        s.put(ss.GENERATED, f'P{i}_N{j}', f'value-{i}-{j}')

procs = [multiprocessing.get_context('fork').Process(target=writer, args=(i,)) for i in range(4)]
threads = [threading.Thread(target=writer, args=(10 + i,)) for i in range(3)]
for w in procs + threads: w.start()
for w in procs + threads: w.join()
names = set(store.names(ss.GENERATED))
want = {f'P{i}_N{j}' for i in list(range(4)) + [10, 11, 12] for j in range(5)}
check(want <= names and 'APP_DB_PASSWORD' in names and all(p.exitcode == 0 for p in procs), f'7 concurrent writers x 5: no change lost ({len(want & names)}/35)')
check(store.generated()[0]['P3_N4'] == 'value-3-4', 'values written by other processes are read (cache follows the file)')

# ── namespace separation through the agent API ───────────────────────────────────────────────────────────────────────
store.put(ss.PLATFORM, 'servers/dev/SSH_PRIVATE_KEY', 'PLATFORM-ONLY-' + PLAT)
store.put(ss.PLATFORM, 'SSH_PRIVATE_KEY', 'ROOT-PLATFORM-' + PLAT)
sec._cache['at'] = 0
RAN = []
def fake_execute(server, cmd, timeout=40, max_output=8000, stdin=None):
    RAN.append(cmd)
    return {'exit_code': 0, 'stdout': 'ran: ' + cmd, 'stderr': '', 'timed_out': False, 'truncated': False}
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
call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'name': 'A', 'level': 6}}, 'servers': {'secrets': {'level': 6}, 'sandbox': {'level': 6}}},
                           'actor': 't'}, token='a' * 40)
code, pol = call('GET', '/v1/policy')
check(pol['secret_store'] == {'backend': 'local', 'configured': True} and pol['roles'] == {'sandbox': 'sandbox', 'website': 'public-website', 'traefik': 'traefik'},
      '/v1/policy tells the dashboard the backend and the inventory roles')
s, r = call('POST', '/v1/secrets', {'agent_id': AGENT, 'operation': 'list'})
listed = {m['name'] for m in r.get('secrets', [])}
check(s == 200 and 'APP_DB_PASSWORD' in listed and not any('/' in n or n.startswith('SSH_') for n in listed), 'agents list only generated names')
s, r = call('POST', '/v1/secrets', {'agent_id': AGENT, 'operation': 'create', 'name': 'WEB_SECRET'})
check(s == 200 and r['reference'] == '{{secret:WEB_SECRET}}' and 'WEB_SECRET' in store.names(ss.GENERATED)
      and 'WEB_SECRET' not in store.names(ss.PLATFORM), 'an agent-created secret lands in the generated namespace')
for name, err in (('SSH_PRIVATE_KEY', 'reserved_name'), ('servers/dev/SSH_PRIVATE_KEY', 'invalid_name'), ('HLA_SECRETS_KEY', 'reserved_name')):
    s, r = call('POST', '/v1/secrets', {'agent_id': AGENT, 'operation': 'create', 'name': name, 'replace': True})
    check(s == 400 and r['error'] == err, f'agents cannot write {name} ({r.get("error")})')
for ref in ('{{secret:SSH_PRIVATE_KEY}}', '{{secret:servers/dev/SSH_PRIVATE_KEY}}'):
    s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'command', 'command': 'echo ' + ref})
    leaked = any(PLAT in c for c in RAN)
    check(not leaked and (s != 200 or ref in r.get('stdout', '')), f'{ref} never reaches a platform secret ({s} {r.get("error")})')
s, r = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'sandbox', 'mode': 'command', 'command': 'echo {{secret:APP_DB_PASSWORD}}'})
check(s == 200 and RAN[-1] == 'echo ' + GEN and GEN not in json.dumps(r) and '[secret:APP_DB_PASSWORD]' in r['stdout'],
      'a generated reference is filled in and redacted from the reply')
check(sec.store('PVE_X_TOKEN', 'v' * 20, 'test')['status'] == 'created' and 'PVE_X_TOKEN' in store.names(ss.GENERATED), 'gateway-internal store() writes generated')

# ── SSH credentials from the local store ─────────────────────────────────────────────────────────────────────────────
key = paramiko.Ed25519Key.generate() if hasattr(paramiko.Ed25519Key, 'generate') else None
if key is None:
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', TMP + '/id_test'], check=True)
    key_text = Path(TMP + '/id_test').read_text()
else:
    buf = io.StringIO(); key.write_private_key(buf); key_text = buf.getvalue()
Path(TMP + '/id_test').write_text(key_text)
FP = 'SHA256:' + 'A' * 43

# ── the CLI never prints a value ─────────────────────────────────────────────────────────────────────────────────────
env = {**os.environ}
def cli(*args, stdin=''):
    r = subprocess.run([sys.executable, '-m', 'gateway.cli', 'secrets', *args], cwd=ROOT, env=env, input=stdin, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr
VALUE = 'cli-value-' + secrets.token_hex(12)
outputs = []
for args, stdin, rc_want, label in (
        (('set', 'HomeAssistant/HA_TOKEN'), VALUE + '\n', 0, 'set reads stdin'),
        (('set', 'CLI_GENERATED', '--generated'), VALUE, 0, 'set --generated'),
        (('set', 'SSH_SOMETHING', '--generated'), VALUE, 2, 'set --generated refuses reserved names'),
        (('list',), '', 0, 'list'), (('list', '--generated'), '', 0, 'list --generated'), (('status',), '', 0, 'status'),
        (('import-ssh-key', 'dev', TMP + '/id_test', '--fingerprint', FP), '', 0, 'import-ssh-key'),
        (('import-ssh-key', 'jellyfin', '/dev/stdin'), key_text, 0, 'import-ssh-key from /dev/stdin (docker compose exec -T ... < key)'),
        (('import-ssh-key', 'nosuch', TMP + '/id_test'), '', 2, 'import-ssh-key: unknown server'),
        (('import-ssh-key', 'dev', TMP + '/missing'), '', 2, 'import-ssh-key: unreadable file'),
        (('export', 'HomeAssistant/HA_TOKEN', '--out', TMP + '/exported'), '', 0, 'export to a new file'),
        (('export', 'HomeAssistant/HA_TOKEN', '--out', TMP + '/exported'), '', 2, 'export never overwrites'),
        (('delete', 'CLI_GENERATED', '--generated'), '', 0, 'delete'), (('delete', 'CLI_GENERATED', '--generated'), '', 1, 'delete again')):
    rc, out = cli(*args, stdin=stdin)
    outputs.append(out)
    check(rc == rc_want, f'CLI {label} (exit {rc})')
everything = '\n'.join(outputs)
check(VALUE not in everything and GEN not in everything and PLAT not in everything and key_text.split('\n')[1] not in everything,
      'no CLI output holds a value or key material')
check('HomeAssistant/HA_TOKEN' in outputs[3] and 'CLI_GENERATED' in outputs[4] and '"backend": "local"' in outputs[5], 'list and status show names only')
check(store.read_platform('/HomeAssistant', ('HA_TOKEN',))['HA_TOKEN'] == VALUE, 'the stored value is exactly stdin minus the newline')
check(Path(TMP + '/exported').read_text() == VALUE and stat.S_IMODE(Path(TMP + '/exported').stat().st_mode) == 0o600, 'export writes 0600')
c = core.credentials('dev')
check(c['SSH_PRIVATE_KEY'].strip() == key_text.strip() and c['SSH_HOST_FINGERPRINT'] == FP and c['SSH_USER'] == 'svc-hla-agent',
      'core.credentials reads the imported key and pinned fingerprint')
store.put(ss.PLATFORM, 'servers/dev/SSH_HOST', '10.9.9.9')
try:
    core.credentials('dev'); mismatch = False
except RuntimeError as e:
    mismatch = 'pinned' in str(e)
check(store.read_platform('/servers/jellyfin', ('SSH_PRIVATE_KEY',))['SSH_PRIVATE_KEY'].strip() == key_text.strip(), 'the piped key is stored as servers/jellyfin/SSH_PRIVATE_KEY')
check(mismatch, 'an SSH_HOST in the store that differs from the inventory is refused')
store.delete(ss.PLATFORM, 'servers/dev/SSH_HOST')
store.delete(ss.PLATFORM, 'servers/dev/SSH_HOST_FINGERPRINT')
try:
    core.credentials('dev'); unpinned = False
except RuntimeError as e:
    unpinned = 'pin-host-key' in str(e)
check(unpinned, 'without a pinned host key: a clear error naming pin-host-key')
# Per-server user and port from the inventory reach the SSH connection; the pinned fingerprint comes from the store.
store.put(ss.PLATFORM, 'servers/jellyfin/SSH_HOST_FINGERPRINT', FP)
core.SERVER_INFO['jellyfin'] = {**core.SERVER_INFO['jellyfin'], 'user': 'mediaops', 'port': 2222}
SEEN = []
real_run = core.run_ssh
core.run_ssh = lambda host, k, pinned, command, timeout=30, max_output=12000, stdin=None, **kw: SEEN.append((host, pinned, kw)) or {'exit_code': 0}
core.execute('jellyfin', 'true')
core.run_ssh = real_run
check(SEEN == [('192.168.10.101', [FP], {'user': 'mediaops', 'port': 2222})], f'execute uses the inventory user, port and pinned key {SEEN}')
env['HLA_SECRETS_KEY'] = ''
rc, out = cli('list')
check(rc == 2 and 'HLA_SECRETS_KEY' in out, 'CLI without a key: clear error')
env['HLA_SECRETS_KEY'] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
rc, out = cli('list')
check(rc == 2 and 'wrong_key' in out and VALUE not in out, 'CLI with a wrong key: clear error')

# ── the credential store can live on HLA_SECRETS_KEY alone ───────────────────────────────────────────────────────────
check(creds.configured() and len(creds._key()) == 32 and creds._key() != base64.urlsafe_b64decode(KEY),
      'no GATEWAY_CREDENTIAL_KEY: a key derived from HLA_SECRETS_KEY (never the same bytes)')

# ── not configured ───────────────────────────────────────────────────────────────────────────────────────────────────
os.environ['HLA_SECRETS_KEY'] = ''
sec._cache['at'] = 0
s, r = call('POST', '/v1/secrets', {'agent_id': AGENT, 'operation': 'list'})
check(s == 503 and r['error'] == 'secrets_not_configured' and 'HLA_SECRETS_KEY' in r['detail'], 'no key: secrets_not_configured naming HLA_SECRETS_KEY')
os.environ['HLA_SECRETS_BACKEND'] = 'vault'
check(ss.describe()['configured'] is False and 'local or infisical' in ss.describe()['error'], 'an unknown backend is reported')
os.environ['HLA_SECRETS_BACKEND'] = 'local'
os.environ['HLA_SECRETS_KEY'] = KEY

httpd.shutdown()
print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED: {failures}'))
sys.exit(1 if failures else 0)
