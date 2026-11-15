"""The inventory loader (gateway/inventory.py): validation with clear messages, the shipped example, the router's identical
copy, and a gateway that boots and answers with an empty inventory.

python3 tests/test_inventory.py   (needs pyyaml; paramiko for the boot check)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
from gateway import inventory as inv  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

TMP = tempfile.mkdtemp()
def load_text(text):
    p = Path(TMP, 'inv.yaml')
    p.write_text(text)
    return inv.load(str(p))

def error(text):
    try:
        load_text(text)
    except inv.InventoryError as exc:
        return str(exc)
    return None

# Valid: the minimal and the full forms.
i = load_text('servers:\n  - {name: web, host: 192.168.1.10}\n')
s = i['servers'][0]
check(s == {'name': 'web', 'host': '192.168.1.10', 'user': 'svc-hla-agent', 'port': 22, 'description': '', 'sandbox': False, 'role': None,
            'default_level': None, 'host_fingerprint': [], 'vmid': None, 'node': None, 'secret_path': '/servers/web'} and i['services'] == {} and i['public_ips'] == [],
      'defaults: user svc-hla-agent, port 22, secret folder servers/<name>, no services, no public IPs')
check(load_text('')['servers'] == [] and load_text('servers: []\nservices: {}\npublic_ips: []\n')['servers'] == [], 'empty file and empty lists are valid')
old = os.environ.pop('HLA_INVENTORY', None)
if not os.path.exists(inv.DEFAULT_PATH):
    check(inv.load()['servers'] == [] and inv.load()['source'] is None, 'no file at the default path: empty inventory')
full = load_text('''
servers:
  - {name: box, host: Box.Lan, user: deploy, port: 2222, description: "Docker host", role: website,
     host_fingerprint: "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", default_level: 3, vmid: 110}
  - {name: sb, host: "10.0.0.5", sandbox: true}
services:
  proxmox: {url: "https://192.168.1.5:8006", nodes: [pve, {name: pve2, url: "https://pve2.lan:8006", tls_name: pve2.lan}], reserved_vmids: [100, 101], verify_tls: true}
  omada: {url: "https://192.168.1.2", site: Default, verify_tls: false}
  homeassistant: {url: "http://192.168.1.20:8123/"}
  github:
public_ips: ["198.51.100.7"]
''')
p = full['services']['proxmox']
check(full['servers'][0]['host'] == 'box.lan' and full['servers'][0]['port'] == 2222 and full['servers'][0]['host_fingerprint'] == ['SHA256:' + 'A' * 43],
      'host names are lowercased; port and fingerprint kept')
check(inv.sandbox(full) == 'sb' and inv.role(full, 'website') == 'box' and inv.role(full, 'traefik') is None, 'sandbox and roles')
check([n['name'] for n in p['nodes']] == ['pve', 'pve2'] and p['nodes'][0]['host'] == '192.168.1.5' and p['nodes'][0]['port'] == 8006
      and p['nodes'][1]['host'] == 'pve2.lan' and p['nodes'][0]['secret_path'] == '/Proxmox/pve' and p['reserved_vmids'] == [100, 101],
      'proxmox: nodes share the url or bring their own; secret folders Proxmox/<node>')
check(full['services']['omada']['host'] == '192.168.1.2' and full['services']['omada']['port'] == 443 and full['services']['omada']['site'] == 'Default',
      'omada: host, default port 443 and site')
check(full['services']['homeassistant']['url'] == 'http://192.168.1.20:8123' and full['services']['github']['api_url'] == 'https://api.github.com'
      and full['public_ips'] == ['198.51.100.7'], 'homeassistant URL normalized; "github:" alone enables it; public IPs')
check('github' not in load_text('services: {github: false}\n')['services'], 'false disables a block')
alias = load_text('servers:\n  - {name: web, host: 1.2.3.4, user: admin, host_key_fingerprint: "SHA256:%s"}\n' % ('B' * 43))['servers'][0]
check(alias['host_fingerprint'] == ['SHA256:' + 'B' * 43] and alias['user'] == 'admin', 'host_key_fingerprint is the same field; per-server user kept')

# Invalid: each names the entry and the problem.
for text, expect, label in (
        ('- a\n', 'top level: must be a mapping', 'a list at the top'),
        ('servers: {}\n', 'servers: must be a list', 'servers as a mapping'),
        ('srvers: []\n', "unknown key(s) srvers", 'a typo at the top level'),
        ('servers:\n  - {host: 1.2.3.4}\n', 'servers[0]: missing name', 'a server without a name'),
        ('servers:\n  - {name: web}\n', 'servers[0]: missing host', 'a server without a host'),
        ('servers:\n  - {name: Web, host: 1.2.3.4}\n', "name 'Web' must be", 'an uppercase name'),
        ('servers:\n  - {name: web, host: 1.2.3.4}\n  - {name: web, host: 1.2.3.5}\n', 'servers[1] (web): the name is used twice', 'a duplicate name'),
        ('servers:\n  - {name: github, host: 1.2.3.4}\n', 'reserved for a gateway service', 'a reserved name'),
        ('servers:\n  - {name: web, host: "1.2.3.999"}\n', 'is not an IP address or host name', 'a bad address'),
        ('servers:\n  - {name: web, host: 1.2.3.4, port: 70000}\n', 'port: must be a whole number from 1 to 65535', 'a bad port'),
        ('servers:\n  - {name: web, host: 1.2.3.4, sandbox: "yes"}\n', 'sandbox: must be true or false', 'sandbox not a boolean'),
        ('servers:\n  - {name: a, host: 1.2.3.4, sandbox: true}\n  - {name: b, host: 1.2.3.5, sandbox: true}\n', 'only one server can be the sandbox', 'two sandboxes'),
        ('servers:\n  - {name: a, host: 1.2.3.4, role: dns}\n', 'role must be one of website, traefik', 'an unknown role'),
        ('servers:\n  - {name: a, host: 1.2.3.4, role: website}\n  - {name: b, host: 1.2.3.5, role: website}\n', 'role website is already taken by a', 'a role twice'),
        ('servers:\n  - {name: a, host: 1.2.3.4, ip: 1.2.3.4}\n', 'unknown key(s) ip', 'a typo in a server'),
        ('servers:\n  - {name: a, host: 1.2.3.4, host_fingerprint: abc}\n', 'ed25519 host key fingerprint', 'a bad fingerprint'),
        ('services: {vault: {}}\n', 'unknown integration(s) vault', 'an unknown integration'),
        ('services: {proxmox: {url: "http://1.2.3.4:8006", nodes: [pve]}}\n', 'must start with https://', 'proxmox over http'),
        ('services: {proxmox: {nodes: [pve]}}\n', 'needs a url', 'a proxmox node without a url'),
        ('services: {proxmox: {url: "https://1.2.3.4:8006", nodes: []}}\n', 'must be a non-empty list', 'proxmox without nodes'),
        ('services: {proxmox: {url: "https://1.2.3.4:8006", nodes: [pve], reserved_vmids: [5]}}\n', 'reserved_vmids', 'a VMID below 100'),
        ('services: {omada: {url: "https://1.2.3.4/x"}}\n', 'scheme://host[:port] only', 'a URL with a path'),
        ('services: {homeassistant: {url: "ftp://ha"}}\n', 'must start with http:// or https://', 'homeassistant with ftp'),
        ('services: {homeassistant: }\n', 'needs its settings', 'an empty homeassistant block'),
        ('public_ips: [nope]\n', "public_ips: 'nope' is not an IP address", 'a bad public IP'),
        ('servers: [\n', 'not valid YAML at line', 'broken YAML'),
        ('servers:\n  - {name: pve, host: 1.2.3.4}\nservices: {proxmox: {url: "https://1.2.3.4:8006", nodes: [pve]}}\n', 'same name as a server', 'a node named like a server'),
        ('servers:\n  - {name: a, host: 1.2.3.4, node: pve9}\nservices: {proxmox: {url: "https://1.2.3.4:8006", nodes: [pve]}}\n', 'pve9 is not one of services.proxmox.nodes', 'a server on an unknown node')):
    msg = error(text)
    check(msg is not None and expect in msg and 'inv.yaml' in msg, f'refused: {label} ({msg})')
os.makedirs(TMP + '/dir.yaml', exist_ok=True)
try:
    inv.load(TMP + '/dir.yaml'); msg = None
except inv.InventoryError as exc:
    msg = str(exc)
check(msg and 'is a folder' in msg, 'a folder where the file should be (Docker made it): explained')
try:
    inv.load(TMP + '/missing.yaml'); msg = None
except inv.InventoryError as exc:
    msg = str(exc)
check(msg and 'not found' in msg, 'HLA_INVENTORY naming a missing file is an error, not an empty inventory')

# The shipped example loads; the router's copy of the loader is the same file.
example = inv.load(str(REPO / 'config' / 'inventory.example.yaml'))
check([s['name'] for s in example['servers']] == ['server1'] and example['services'] == {} and example['public_ips'] == [], 'config/inventory.example.yaml is valid')
router_copy = REPO / 'agent-platform-starter' / 'inventory.py'
check(router_copy.exists() and router_copy.read_bytes() == (ROOT / 'gateway' / 'inventory.py').read_bytes(),
      'agent-platform-starter/inventory.py is an identical copy of gateway/inventory.py')
fixture = inv.load(str(ROOT / 'tests' / 'fixtures' / 'inventory.yaml'))
router_fixture = REPO / 'agent-platform-starter' / 'tests' / 'fixtures' / 'inventory.yaml'
check(router_fixture.exists() and router_fixture.read_bytes() == (ROOT / 'tests' / 'fixtures' / 'inventory.yaml').read_bytes(),
      'the router tests use the same fixture inventory')

# An empty inventory boots: the gateway API starts, answers, and offers no servers or integrations.
Path(TMP, 'empty.yaml').write_text('servers: []\n')
probe = r'''
import json, threading, urllib.request
from http.server import ThreadingHTTPServer
import gateway_api.server as srv
from gateway import core, policy
h = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler); threading.Thread(target=h.serve_forever, daemon=True).start()
def get(path):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (h.server_address[1], path), headers={'Authorization': 'Bearer ' + 't' * 40})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)
s, pol = get('/v1/policy')
w, web = get('/v1/websites')
print(json.dumps({'servers': core.SERVERS, 'services': sorted(core.SERVICES), 'status': s, 'policy_servers': sorted(pol['servers']),
                  'roles': pol['roles'], 'websites': [w, web.get('error')]}))
'''
env = {**os.environ, 'HLA_INVENTORY': TMP + '/empty.yaml', 'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40,
       'POLICY_ADMIN_TOKEN': 'a' * 40, 'HLA_SECRETS_DIR': TMP + '/secrets'}
r = subprocess.run([sys.executable, '-c', probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
try:
    out = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    out = {}
check(r.returncode == 0 and out.get('servers') == {} and out.get('services') == ['http_tools', 'secrets'] and out.get('status') == 200
      and out.get('policy_servers') == [] and out.get('roles') == {'sandbox': None, 'website': None, 'traefik': None}
      and out.get('websites') == [503, 'websites_not_configured'], 'empty inventory: the gateway boots and answers ' + (r.stderr[-300:] if r.returncode else ''))
Path(TMP, 'bad.yaml').write_text('servers:\n  - {name: web}\n')
r = subprocess.run([sys.executable, '-c', 'import gateway.core'], cwd=ROOT, env={**env, 'HLA_INVENTORY': TMP + '/bad.yaml'},
                   capture_output=True, text=True, timeout=60)
check(r.returncode == 2 and 'INVENTORY ERROR' in r.stderr and 'servers[0]: missing host' in r.stderr and 'Traceback' not in r.stderr,
      'a broken inventory stops the gateway with one clear line')
r = subprocess.run([sys.executable, '-m', 'gateway.cli', 'servers'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and json.loads(r.stdout) == {}, 'CLI servers with an empty inventory')
if old is not None:
    os.environ['HLA_INVENTORY'] = old

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED: {failures}'))
sys.exit(1 if failures else 0)
