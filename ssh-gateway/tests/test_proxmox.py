"""Proxmox VE through the gateway (gateway/proxmox.py, HLA_PROXMOX_V1): inventory and levels, power and snapshots,
create_vm validation, the approval digest flow, image download with checksum, import-from vs template clone, onboarding
(key in the credential store, host key pinned, server registered at level 0, execute through it), destroy of managed VMs
only, the read-only audit token stored in Agent Generated, TLS verification, and no secret in any reply or audit row.

The PVE API is tests/fake_pve.py at the HTTP layer; Infisical, SSH and the network probe are stand-ins. One real TLS check
runs against a local HTTPS server with a throwaway CA made by openssl (skipped when openssl is missing).

python3 tests/test_proxmox.py   (needs paramiko importable; no network)
"""
import base64
import inspect
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
CA_DIR = TMP + '/certs'
os.makedirs(CA_DIR)
for name in ('proxmox-ca.pem', 'pve2-ca.pem'):
    Path(CA_DIR, name).write_text('placeholder: the fake HTTP layer does no TLS\n')
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'GATEWAY_CREDENTIAL_KEY': base64.b64encode(os.urandom(32)).decode(), 'PROXMOX_CA_DIR': CA_DIR,
                   'INFISICAL_URL': 'https://infisical.test', 'INFISICAL_CLIENT_ID': 'id', 'INFISICAL_CLIENT_SECRET': 'cs',
                   'INFISICAL_PROJECT_ID': 'servers-project', 'INFISICAL_GENERATED_PROJECT_ID': 'generated-project'})
for k in ('PROXMOX_ALLOWED_NETS', 'PROXMOX_PROTECTED_NAMES', 'GITHUB_TOKEN'):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_pve  # noqa: E402
from gateway import proxmox as P, core, policy, credentials as creds, agent_secrets as sec  # noqa: E402
from gateway.cli import db_open  # noqa: E402
import gateway_api.server as srv  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

# ── stand-ins ────────────────────────────────────────────────────────────────────────────────────────────────────────
PVE = fake_pve.FakePVE()
P._http = PVE.http
P._secrets = fake_pve.secrets
P._sleep = lambda s: None
P.TASK_POLL = 0
SUMS = {'body': fake_pve.sha512sums()}
FETCHED = []
def fake_fetch(url):
    FETCHED.append(url)
    if isinstance(SUMS['body'], Exception): raise SUMS['body']
    return SUMS['body']
P._fetch_url = fake_fetch
ANSWERING = set()
P._ip_answers = lambda ip: ip in ANSWERING
SSH = {'fp': 'SHA256:' + 'Q' * 43, 'refuse': 1, 'stdout': 'HLA_USER=svc-hla-agent\nHLA_SUDO_OK\nHLA_QGA_INSTALLED\nHLA_QGA_RUNNING\n',
       'runs': [], 'fps': []}
def fake_fingerprint(host, port=22, timeout=8):
    SSH['fps'].append(host)
    if SSH['refuse'] > 0:
        SSH['refuse'] -= 1
        raise ConnectionRefusedError('not up yet')
    return SSH['fp']
def fake_run_ssh(host, key, pinned, command, timeout=30, max_output=12000, stdin=None):
    SSH['runs'].append((host, key.get_name(), list(pinned), command))
    if SSH['fp'] not in pinned: raise RuntimeError('SSH host fingerprint mismatch')
    return {'exit_code': 0, 'stdout': SSH['stdout'], 'stderr': '', 'timed_out': False, 'truncated': False}
P._host_fingerprint = fake_fingerprint
P._run_ssh = fake_run_ssh
# Agent Generated Infisical project.
STORE = {}
def fake_call(method, base, path, payload=None, token=None):
    if path.startswith('/api/v1/auth/'): return 200, {'accessToken': 'tok'}
    if method == 'GET' and path.startswith('/api/v4/secrets?'):
        return 200, {'secrets': [{'secretKey': k, 'secretValue': v, 'secretComment': ''} for k, v in STORE.items()]}
    STORE[path.rsplit('/', 1)[1]] = payload['secretValue']
    return 200, {}
sec._call = fake_call

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT = '11111111-1111-4111-8111-111111111111'
TASK = '33333333-3333-4333-8333-333333333333'
REPLIES = []
def call(method, path, body=None, token='a' * 40, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: code, out = r.status, json.load(r)
    except urllib.error.HTTPError as e: code, out = e.code, json.load(e)
    REPLIES.append(json.dumps(out))
    return code, out
def agent(body, token='t' * 40):
    return call('POST', '/v1/proxmox', {'agent_id': AGENT, **body}, token=token, headers={'X-HLA-Task': TASK})
def admin(body):
    return call('POST', '/v1/proxmox', {'actor': 'jordan', **body})
def levels(agent_level=None, **services):
    change = {'servers': {}}
    if agent_level is not None: change['agents'] = {AGENT: {'name': 'Infra', 'level': agent_level}}
    for k, v in services.items(): change['servers'][k.replace('_', '-') if not k.startswith('proxmox') else 'proxmox:' + k[8:]] = {'level': v}
    with db_open() as db: policy.update(db, change, 'test')
def audits():
    with sqlite3.connect(os.environ['GATEWAY_DB']) as d:
        return d.execute('SELECT action,server,actor,agent_id,detail,task_id,exit_code FROM audit ORDER BY id').fetchall()
def grant(service, text, token='a' * 40):
    return call('POST', '/v1/approvals', {'server': service, 'command': text, 'actor': 'jordan'}, token=token)
SPEC = {'node': 'pve2', 'vmid': 205, 'name': 'monitoring', 'cores': 2, 'memory': 4096, 'disk': 32, 'storage': 'local-lvm',
        'bridge': 'vmbr0', 'ip': '192.168.10.205/24', 'gateway': '192.168.10.1', 'dns': '192.168.10.1', 'image': 'debian-13'}

# ── policy: two services, off until raised ───────────────────────────────────────────────────────────────────────────
with db_open() as db:
    snap = policy.snapshot(db)
check(snap['services'].get('proxmox:proxmox') == {'level': 0} and snap['services'].get('proxmox:pve2') == {'level': 0},
      'policy: proxmox:proxmox and proxmox:pve2 exist and start at level 0')
levels(5)
code, body = call('GET', f'/v1/proxmox/inventory?agent_id={AGENT}', token='t' * 40)
check(code == 403 and body['error'] == 'policy_denied' and audits()[-1][0] == 'proxmox_denied', 'inventory: an agent without a level is refused (audited)')
check(call('GET', '/v1/proxmox/inventory', token='t' * 40)[0] == 400, 'inventory: the tool token must name the agent')
check(call('GET', '/v1/proxmox/inventory', token='x' * 40)[0] == 401, 'inventory: needs a token')

# ── inventory ────────────────────────────────────────────────────────────────────────────────────────────────────────
levels(5, proxmox_pve2=1)
code, body = call('GET', f'/v1/proxmox/inventory?agent_id={AGENT}', token='t' * 40, headers={'X-HLA-Task': TASK})
check(code == 200 and [n['node'] for n in body['nodes']] == ['pve2'] and body['nodes'][0]['level'] == 1,
      'inventory: an agent sees only the nodes it has level 1 on')
row = audits()[-1]
check(row[0] == 'proxmox_inventory' and row[1] == 'proxmox:pve2' and row[3] == AGENT and row[5] == TASK, 'inventory: agent reads are audited with the task')
code, body = call('GET', '/v1/proxmox/inventory')
nodes = {n['node']: n for n in body['nodes']}
check(code == 200 and set(nodes) == {'proxmox', 'pve2'} and 'servers' in body and 'managed' in body, 'inventory: the dashboard sees both nodes and the registry')
n1, n2 = nodes['proxmox'], nodes['pve2']
vm = {v['vmid']: v for v in n1['vms']}
check(len(n1['vms']) == 11 and vm[109]['protected'] and vm[104]['reserved'] and vm[104]['ip'] == '192.168.10.104' and not vm[110]['agent'],
      'inventory: VMs with protected/reserved flags and IPs from the guest agent or cloud-init')
check(n1['version']['version'] == '8.4.1' and n2['version']['version'] == '9.0.10' and n2['nextid'] == 100 and n1['status']['cpus'] == 16,
      'inventory: version, next free VMID and node status')
check(n2['defaults']['gateway'] == '192.168.10.1' and n1['defaults']['gateway'] == '192.168.10.1' and n1['defaults']['bridge'] == 'vmbr0'
      and n1['defaults']['storage'] == 'local-lvm' and n1['defaults']['dns'] == '192.168.10.1', 'inventory: gateway, DNS, bridge and storage detected')
check([b['iface'] for b in n2['bridges']] == ['vmbr0', 'vmbr1'] and all(s['storage'] for s in n2['storages']) and n2['import_supported'],
      'inventory: bridges, storages with content types, import support')
check(n1['permissions']['create_vm']['ok'] and n1['permissions']['audit_token']['ok'], 'inventory: token permissions summarised')
PVE.nodes['proxmox']['perms'] = dict(fake_pve.READ_ONLY)
P.forget()
inv = call('GET', '/v1/proxmox/inventory?refresh=1')[1]
caps = {n['node']: n['permissions'] for n in inv['nodes']}['proxmox']
check(caps['inventory']['ok'] and not caps['power']['ok'] and 'VM.PowerMgmt on /vms' in caps['power']['missing']
      and not caps['audit_token']['ok'], 'permissions: a read-only token shows what is missing')
PVE.nodes['proxmox']['perms'] = {'/': dict(fake_pve.ALL)}
P.forget()
check(P.has({'/': {'VM.Audit': 1}, '/vms/205': {}}, 'VM.Audit', '/vms/205') is False and P.has({'/vms': {'VM.Audit': 1}}, 'VM.Audit', '/vms/205')
      and not P.has({'/vmsx': {'VM.Audit': 1}}, 'VM.Audit', '/vms/205'), 'permissions: the most specific path decides')
check(call('GET', '/v1/proxmox/inventory?node=pve9')[0] == 400, 'inventory: unknown node refused')
check(P.has({'/vms': {'VM.PowerMgmt': 0}}, 'VM.PowerMgmt', '/vms') and not P.has({'/': {'VM.PowerMgmt': 0}}, 'VM.PowerMgmt', '/vms'),
      'permissions: a listed privilege counts at its own path; at a parent only when it propagates (value 1)')
# The Proxmox node name differs from the gateway key: found through /cluster/status, else /nodes; paths use it.
PVE.nodes['pve2']['name'] = 'pve-node2'
P.forget()
n2 = {n['node']: n for n in call('GET', '/v1/proxmox/inventory?refresh=1')[1]['nodes']}['pve2']
check(n2['pve_node'] == 'pve-node2' and [v['vmid'] for v in n2['vms']] == [203, 204] and not n2['errors'] and n2['permissions']['create_vm']['ok'],
      f'node name: the real Proxmox node name is used in API paths ({n2.get("errors")})')
PVE.nodes['pve2']['no_cluster_status'] = True
P.forget()
n2 = {n['node']: n for n in call('GET', '/v1/proxmox/inventory?refresh=1')[1]['nodes']}['pve2']
check(n2['pve_node'] == 'pve-node2' and len(n2['vms']) == 2, 'node name: falls back to the only entry of /nodes')
PVE.nodes['pve2'].pop('name'); PVE.nodes['pve2'].pop('no_cluster_status')
# Privilege separation: shown, because the token then only gets what its user also has.
PVE.nodes['proxmox']['tokens']['root@pam'] = {'hla': {'info': {'privsep': 1}, 'value': 'unused'}}
P.forget()
n1 = {n['node']: n for n in call('GET', '/v1/proxmox/inventory?refresh=1')[1]['nodes']}['proxmox']
check(n1['token'] == {'user': 'root@pam', 'privsep': True}, f'token: user and privilege separation reported ({n1.get("token")})')
PVE.nodes['proxmox']['tokens'].pop('root@pam')
P.forget()

# ── power and snapshots ──────────────────────────────────────────────────────────────────────────────────────────────
code, body = agent({'node': 'pve2', 'operation': 'stop', 'vmid': 204})
check(code == 403 and body['error'] == 'policy_denied' and audits()[-1][0] == 'proxmox_denied', 'power: level 1 cannot stop a VM')
levels(5, proxmox_proxmox=4, proxmox_pve2=4)
code, body = agent({'node': 'proxmox', 'operation': 'start', 'vmid': 110})
check(code == 200 and body['vm_status'] == 'running' and body['upid'].startswith('UPID:proxmox:') and PVE.nodes['proxmox']['vms'][110]['status'] == 'running',
      'power: level 4 starts a VM and waits for its task')
row = audits()[-1]
check(row[0] == 'proxmox_start' and row[1] == 'proxmox:proxmox' and 'vmid 110 (old-test)' in row[4] and 'UPID:' in row[4] and row[6] == 0,
      'power: audited as proxmox_start with the VM and the task UPID')
code, body = agent({'node': 'proxmox', 'operation': 'stop', 'vmid': 109})
check(code == 403 and body['error'] == 'protected_vm' and PVE.nodes['proxmox']['vms'][109]['status'] == 'running', 'power: agents never touch a protected VM')
check(admin({'node': 'proxmox', 'operation': 'reboot', 'vmid': 109})[0] == 200, 'power: the owner can reboot a protected VM from the dashboard')
check(agent({'node': 'proxmox', 'operation': 'stop', 'vmid': 999})[1]['error'] == 'unknown_vm', 'power: unknown VM is a 404')
check(agent({'node': 'proxmox', 'operation': 'stop', 'vmid': 'x'})[0] == 400 and agent({'node': 'proxmox', 'operation': 'format'})[0] == 400,
      'power: invalid vmid or operation refused')
check(agent({'node': 'pve2', 'operation': 'snapshot_create', 'vmid': 204, 'snapname': '1bad name'})[1]['error'] == 'invalid_snapname', 'snapshot: name validated')
code, body = agent({'node': 'pve2', 'operation': 'snapshot_create', 'vmid': 204, 'snapname': 'before-update', 'description': 'agent snapshot'})
check(code == 200 and 'before-update' in PVE.nodes['pve2']['vms'][204]['snapshots'], 'snapshot: level 4 creates a snapshot')
code, body = agent({'node': 'pve2', 'operation': 'snapshots', 'vmid': 204})
check(code == 200 and {s['name'] for s in body['snapshots']} == {'clean', 'before-update'}, 'snapshot: listed (without "current")')
check(agent({'node': 'pve2', 'operation': 'snapshot_rollback', 'vmid': 204, 'snapname': 'clean'})[1]['error'] == 'policy_denied', 'rollback: needs level 6')
levels(6, proxmox_pve2=6)
check(agent({'node': 'pve2', 'operation': 'snapshot_rollback', 'vmid': 204, 'snapname': 'clean'})[0] == 200, 'rollback: level 6 rolls back')
levels(5, proxmox_pve2=5)
with db_open() as db: policy.update(db, {'unhinged': {'enabled': True, 'hours': 1}}, 'test')
check(agent({'node': 'pve2', 'operation': 'snapshot_rollback', 'vmid': 204, 'snapname': 'clean'})[1]['error'] == 'policy_denied',
      'rollback: unhinged mode does not reach level 6 operations')
with db_open() as db: policy.update(db, {'unhinged': {'enabled': False}}, 'test')
PVE.task_fail['qmshutdown'] = 'command timed out'
code, body = agent({'node': 'pve2', 'operation': 'shutdown', 'vmid': 204})
check(code == 502 and body['error'] == 'proxmox_task_failed' and 'command timed out' in body['detail'] and audits()[-1][6] == 1,
      'tasks: a failed Proxmox task is reported and audited as failed')
PVE.task_fail.clear()
PVE.fail[('POST', r'/status/stop$')] = (403, 'Permission check failed (/vms/204, VM.PowerMgmt)')
code, body = agent({'node': 'pve2', 'operation': 'stop', 'vmid': 204})
check(code == 403 and body['error'] == 'proxmox_permission_denied' and 'VM.PowerMgmt' in body['detail'], 'permissions: what Proxmox refused is passed on')

# ── create_vm: validation ────────────────────────────────────────────────────────────────────────────────────────────
def problem(**change):
    code, body = admin({**SPEC, **change, 'operation': 'preview_create'})
    return body.get('error') if code != 200 else None
check(problem() is None, 'create: the owner\'s monitoring spec is valid')
for change, error, label in (
        ({'vmid': 104}, 'invalid_spec', 'VMID 104 (reserved 100-108)'), ({'vmid': 204}, 'invalid_spec', 'VMID 204 (sandbox)'),
        ({'vmid': 110}, 'vmid_taken', 'an existing VMID on the other node'), ({'vmid': 99}, 'invalid_spec', 'VMID below 100'),
        ({'cores': 9}, 'invalid_spec', '9 cores'), ({'memory': 256}, 'invalid_spec', '256 MiB'), ({'disk': 300}, 'invalid_spec', '300 GiB'),
        ({'disk': 'big'}, 'invalid_spec', 'a disk that is not a number'),
        ({'ip': '192.168.20.50/24', 'gateway': '192.168.20.1'}, 'ip_not_allowed', 'an IP outside PROXMOX_ALLOWED_NETS'),
        ({'ip': '192.168.10.150/24'}, 'ip_in_use', 'an IP configured on another VM'), ({'ip': '192.168.10.104/24'}, 'ip_in_use', 'the IP of a static server'),
        ({'ip': '192.168.10.205'}, 'invalid_spec', 'an IP without prefix'), ({'ip': '192.168.10.0/24'}, 'invalid_spec', 'the network address'),
        ({'gateway': '192.168.11.1'}, 'invalid_spec', 'a gateway outside the subnet'), ({'dns': 'dns.google'}, 'invalid_spec', 'a DNS name'),
        ({'name': 'dev'}, 'name_taken', 'the name of a static server'), ({'name': 'Bad_Name'}, 'invalid_spec', 'an invalid host name'),
        ({'name': 'proxmox-x'}, 'invalid_spec', 'a reserved name prefix'), ({'name': 'jellyfin'}, 'name_taken', 'a static server name'),
        ({'name': 'old-test'}, 'name_taken', 'the name of an existing VM'),
        ({'storage': 'local'}, 'invalid_spec', 'a storage without images'), ({'storage': 'nas'}, 'invalid_spec', 'a missing storage'),
        ({'bridge': 'vmbr9'}, 'invalid_spec', 'a missing bridge'), ({'macaddr': '01:00:5E:00:00:01'}, 'invalid_spec', 'a multicast MAC'),
        ({'macaddr': 'BC:24:11:00:00:6E'}, 'mac_in_use', 'a MAC used by another VM'), ({'image': 'windows'}, 'invalid_spec', 'an unknown image'),
        ({'node': 'pve3'}, 'unknown_node', 'an unknown node')):
    got = problem(**change)
    check(got == error, f'create: refuses {label} ({got})')
PVE.nodes['proxmox']['vms'][300] = fake_pve._vm(300, 'elsewhere', ip='192.168.10.160')
check(problem(vmid=300) == 'vmid_taken', 'create: a VMID used on the other node is refused')
del PVE.nodes['proxmox']['vms'][300]
P.forget()
ANSWERING.add('192.168.10.205')
check(problem() == 'ip_in_use', 'create: an address that answers on the network is refused')
ANSWERING.clear()
os.environ['PROXMOX_ALLOWED_NETS'] = '192.168.10.0/25'
check(problem(ip='192.168.10.200/24') == 'ip_not_allowed' and problem(ip='192.168.10.100/24') == 'ip_in_use', 'create: PROXMOX_ALLOWED_NETS narrows the range')
os.environ.pop('PROXMOX_ALLOWED_NETS')
code, body = admin({**SPEC, 'operation': 'preview_create'})
text = body['approval_text']
check(code == 200 and body['valid'] and body['image']['source'] == 'download' and text.startswith('proxmox create_vm on pve2\nvmid: 205\nname: monitoring')
      and 'memory: 4096 MiB' in text and '192.168.10.205/24, gateway 192.168.10.1' in text and 'SSH_KEY_MONITORING' in text and 'level 0' in text,
      'preview: the readable spec the owner approves')
check(not [a for a in audits() if a[0] == 'proxmox_preview_create'], 'preview: dashboard previews are not audited')
_spec = dict(body['spec'])
check(P.approval_text({'op': 'create_vm', 'node': 'pve2', 'spec': {**_spec, 'image_source': 'download'}, 'plan': {}})
      == P.approval_text({'op': 'create_vm', 'node': 'pve2', 'spec': {**_spec, 'image_source': 'import'}, 'plan': {}}),
      'approval: the text does not change when the image arrives on the node in the meantime')

# ── create_vm: approval digest flow ──────────────────────────────────────────────────────────────────────────────────
levels(4, proxmox_pve2=5)
check(agent({**SPEC, 'operation': 'create_vm'})[1]['error'] == 'policy_denied', 'create: needs level 5')
levels(5, proxmox_pve2=5)
n_calls = len(PVE.calls_like('POST', r'/qemu$'))
code, body = agent({**SPEC, 'operation': 'create_vm'})
check(code == 403 and body['error'] == 'approval_required' and body['service'] == 'proxmox:pve2' and body['approval_text'] == text
      and body['request']['vmid'] == 205 and body['request']['operation'] == 'create_vm' and len(PVE.calls_like('POST', r'/qemu$')) == n_calls,
      'approval: level 5 gets approval_required with the exact text and the request to replay; nothing created')
stored_request = json.loads(json.dumps(body['request']))
check(stored_request['dns'] == ['192.168.10.1'], 'approval: the stored request is the normalised spec')
check(audits()[-1][0] == 'proxmox_approval_required' and 'vmid 205 monitoring' in audits()[-1][4], 'approval: the waiting request is audited')
check(grant('proxmox:pve2', text, token='t' * 40)[0] == 401, 'approval: the tool token cannot approve')
check(grant('proxmox:pve2', 'rm -rf /')[0] == 400 and grant('proxmox:pve9', text)[0] == 403, 'approval: only Proxmox request texts for known nodes')
code, _ = grant('proxmox:pve2', text.replace('4096', '8192'))
check(code == 200 and agent({**SPEC, 'operation': 'create_vm'})[1]['error'] == 'approval_required', 'approval: approving another text unlocks nothing')
check(grant('proxmox:proxmox', text)[0] == 200 and agent({**SPEC, 'operation': 'create_vm'})[1]['error'] == 'approval_required',
      'approval: the digest is bound to the node service')
code, _ = grant('proxmox:pve2', text)
check(code == 200 and audits()[-1][0] == 'chat_approval' and audits()[-1][1] == 'proxmox:pve2', 'approval: granted (audited as chat_approval)')
SSH['refuse'] = 2
code, body = agent(stored_request)  # what the dashboard stored with the card is sent again
check(code == 200 and body['status'] == 'created' and body['server']['name'] == 'monitoring' and body['server']['level'] == 0,
      f'create: approved request runs once and registers the server ({body.get("error")} {body.get("detail")})')
again = agent({**SPEC, 'operation': 'create_vm'})
check(again[0] != 200 and len(PVE.calls_like('POST', r'^/nodes/pve2/qemu$')) == n_calls + 1, f'approval: single use ({again[1].get("error")})')
row = [a for a in audits() if a[0] == 'proxmox_create_vm'][-1]
check(row[3] == AGENT and 'approved by jordan' in row[4] and '-> created' in row[4] and row[5] == TASK, 'create: audited with the approver')

# ── image download, VM parameters, onboarding ────────────────────────────────────────────────────────────────────────
dl = PVE.downloaded
check(dl['url'] == P.IMAGES['debian-13']['url'] and dl['checksum'] == 'ab' * 64 and dl['checksum-algorithm'] == 'sha512'
      and dl['content'] == 'import' and dl['filename'] == 'debian-13-genericcloud-amd64.qcow2' and FETCHED[-1].endswith('/trixie/latest/SHA512SUMS'),
      'image: downloaded with the SHA-512 from SHA512SUMS')
created = PVE.calls_like('POST', r'^/nodes/pve2/qemu$')[-1][3]
vm205 = PVE.nodes['pve2']['vms'][205]
check(created['scsi0'] == 'local-lvm:0,import-from=local:import/debian-13-genericcloud-amd64.qcow2,discard=on,iothread=1'
      and created['ide2'] == 'local-lvm:cloudinit' and created['serial0'] == 'socket' and created['vga'] == 'serial0' and created['agent'] == 'enabled=1'
      and created['ciuser'] == 'svc-hla-agent' and created['ipconfig0'] == 'ip=192.168.10.205/24,gw=192.168.10.1' and created['tags'] == 'hla-managed'
      and created['nameserver'] == '192.168.10.1' and created['net0'] == 'virtio,bridge=vmbr0,tag=30' and created['memory'] == 4096 and created['cores'] == 2,
      'create: import-from, cloud-init drive, serial console, guest agent, user, IP, tag')
# VLAN: taken from VMs in the same network on that bridge; explicit values checked; a trunk without a known tag is refused.
vcode, vbody = admin({'operation': 'preview_create', **SPEC, 'vmid': 230, 'name': 'vlan-auto', 'ip': '192.168.10.230/24'})
check(vcode == 200 and 'vmbr0 VLAN 30, 192.168.10.230/24' in vbody['approval_text'] and any('VLAN 30 taken from VMs' in w for w in vbody['warnings']),
      f'vlan: inferred from VMs in the same network on the bridge ({vbody.get("approval_text")})')
vcode, vbody = admin({'operation': 'preview_create', **SPEC, 'vmid': 230, 'name': 'vlan-none', 'ip': '192.168.10.230/24', 'vlan': 0})
check(vcode == 200 and 'vmbr0 untagged' in vbody['approval_text'] and any('use VLAN 30' in w for w in vbody['warnings']), 'vlan: 0 = untagged, with a warning')
check(admin({'operation': 'preview_create', **SPEC, 'vmid': 230, 'name': 'vlan-bad', 'ip': '192.168.10.230/24', 'vlan': 5000})[1].get('error') == 'invalid_spec',
      'vlan: out of range refused')
saved_vms = {k: PVE.nodes['pve2']['vms'].pop(k) for k in [k for k in PVE.nodes['pve2']['vms'] if k != 204]}
P.forget()
vcode, vbody = admin({'operation': 'preview_create', **SPEC, 'vmid': 230, 'name': 'vlan-unknown', 'ip': '192.168.10.230/24'})
check(vcode == 400 and vbody['error'] == 'vlan_required' and '192.168.20.0/24' in vbody['detail'], f'vlan: unknown tag on a bridge of another network is refused ({vbody})')
vcode, vbody = admin({'operation': 'preview_create', **SPEC, 'vmid': 230, 'name': 'vlan-given', 'ip': '192.168.10.230/24', 'vlan': 30})
check(vcode == 200 and 'VLAN 30' in vbody['approval_text'], 'vlan: an explicit tag works without other VMs')
PVE.nodes['pve2']['vms'].update(saved_vms)
P.forget()
check('Approved by jordan' in created['description'] and 'agent:' + AGENT in created['description'], 'create: the description says who and which approval')
pub = urllib.parse.unquote(created['sshkeys'])
check(created['sshkeys'].startswith('ssh-ed25519%20') and '%20' in created['sshkeys'] and pub.startswith('ssh-ed25519 ') and pub.endswith('\n'),
      'create: sshkeys URL-encoded as Proxmox expects')
check('size=32G' in vm205['config']['scsi0'] and vm205['status'] == 'running', 'create: disk resized to 32 GiB and the VM started')
with db_open() as db:
    meta = creds.meta(db, 'SSH_KEY_MONITORING')
    private = creds.get(db, 'SSH_KEY_MONITORING')
    dyn = P.dynamic_servers(db)
    lvl = policy.snapshot(db)['servers'].get('monitoring')
    eff = policy.effective(db, AGENT).get('monitoring')
import io, paramiko  # noqa: E401,E402
key = paramiko.Ed25519Key.from_private_key(io.StringIO(private))
check(meta and meta['kind'] == 'other' and 'BEGIN OPENSSH PRIVATE KEY' in private and [key.get_name(), key.get_base64()] == pub.split()[:2],
      'onboarding: the private key is in the credential store and matches the key given to cloud-init')
check(SSH['runs'][-1][0] == '192.168.10.205' and SSH['runs'][-1][2] == [SSH['fp']] and 'sudo -n true' in SSH['runs'][-1][3] and 'qemu-guest-agent' in SSH['runs'][-1][3]
      and SSH['fps'].count('192.168.10.205') == 3, 'onboarding: waits for SSH, pins the host key from first contact, checks sudo, installs the guest agent')
check(len(dyn) == 1 and dyn[0]['name'] == 'monitoring' and dyn[0]['host'] == '192.168.10.205' and dyn[0]['host_fingerprint'] == SSH['fp']
      and dyn[0]['key_credential'] == 'SSH_KEY_MONITORING' and dyn[0]['vmid'] == 205 and dyn[0]['created_by'] == 'agent:' + AGENT,
      'onboarding: registered in servers_dynamic with fingerprint, node and VMID')
check(lvl == {'level': 0, 'compose_projects': []} and eff['level'] == 0 and 'monitoring' in core.SERVERS and core.SERVERS['monitoring'][0] == '192.168.10.205',
      'onboarding: the new server is in SERVERS at level 0')
check(body['server']['guest_agent'] == 'installed' and any('host key' in s for s in body['steps']), 'create: the reply lists the steps')
# execute() through the dynamic server: key from the credential store, the recorded fingerprint pinned.
RAN = []
real_run = core.run_ssh
core.run_ssh = lambda host, k, pinned, command, timeout=30, max_output=12000, stdin=None: (RAN.append((host, k.get_base64(), pinned, command))
                                                                                         or {'exit_code': 0, 'stdout': 'ok', 'stderr': '', 'timed_out': False, 'truncated': False})
code, body = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'monitoring', 'mode': 'diagnostic', 'diagnostic': 'identity'}, token='t' * 40)
check(code == 403 and not RAN, 'dynamic server: level 0 refuses everything')
levels(5, monitoring=1)
code, body = call('POST', '/v1/execute', {'agent_id': AGENT, 'server': 'monitoring', 'mode': 'diagnostic', 'diagnostic': 'identity'}, token='t' * 40)
check(code == 200 and RAN and RAN[-1][:3] == ('192.168.10.205', key.get_base64(), [SSH['fp']]), 'dynamic server: execute uses its stored key and pinned fingerprint')
core.run_ssh = real_run
check(core.SERVERS['dev'] == ('192.168.10.104', '/servers/dev') and core.credentials.__name__ == 'credentials', 'static servers keep their Infisical profile')
with db_open() as db:
    try:
        P.register(db, 'dev', '192.168.10.250', 'SSH_KEY_DEV', 'SHA256:x', 'pve2', 250, 'x'); took = True
    except P.ProxmoxError as exc:
        took = exc.error != 'name_taken'
check(not took and core.SERVERS['dev'][0] == '192.168.10.104', 'dynamic servers can never take a static name')
code, body = call('GET', '/v1/policy/effective?agent_id=' + AGENT, token='t' * 40)
check(body['servers']['monitoring']['level'] == 1, 'dynamic server: in the effective policy like any server')
check(call('GET', '/v1/proxmox/inventory')[1]['servers'][0]['level'] == 1, 'inventory: dynamic servers listed with their level')

# ── import from an image already there; template fallback; no source ────────────────────────────────────────────────
PVE.add_image('proxmox')
levels(6, proxmox_proxmox=6)
n_dl = len(PVE.calls_like('POST', 'download-url'))
SSH['refuse'] = 0
code, body = agent({**SPEC, 'node': 'proxmox', 'vmid': 111, 'name': 'grafana', 'ip': '192.168.10.211/24', 'operation': 'create_vm'})
check(code == 200 and body['image_source'] == 'import' and len(PVE.calls_like('POST', 'download-url')) == n_dl,
      f'create: level 6 needs no approval; an image already in the import storage is reused ({body.get("detail")})')
PVE.nodes['pve2']['version'] = '8.2.4'
PVE.nodes['pve2']['vms'][9000] = fake_pve._vm(9000, 'debian-13-cloud', status='stopped', template=True, agent=False)
PVE.nodes['pve2']['vms'][9000]['config']['ide2'] = 'local-lvm:vm-9000-cloudinit,media=cdrom'
P.forget()
levels(6, proxmox_pve2=6)
code, body = agent({**SPEC, 'vmid': 206, 'name': 'loki', 'ip': '192.168.10.206/24', 'operation': 'create_vm'})
clone = PVE.calls_like('POST', r'/qemu/9000/clone$')
cfg = PVE.calls_like('POST', r'/qemu/206/config$')
check(code == 200 and body['image_source'] == 'template' and clone and clone[-1][3]['full'] is True and cfg and 'ide2' not in cfg[-1][3]
      and cfg[-1][3]['ciuser'] == 'svc-hla-agent' and 'size=32G' in PVE.nodes['pve2']['vms'][206]['config']['scsi0'],
      'create: without import support it clones a debian-13* template and configures cloud-init')
del PVE.nodes['pve2']['vms'][9000]
P.forget()
code, body = admin({**SPEC, 'vmid': 207, 'name': 'tempo', 'ip': '192.168.10.207/24', 'operation': 'preview_create'})
check(code == 409 and body['error'] == 'no_image_source' and 'Import' in body['detail'] and 'debian-13' in body['detail'], 'create: no image source says what to set up')
PVE.nodes['pve2']['version'] = '9.0.10'
P.forget()
PVE.nodes['pve2']['content']['local'] = []  # the image is gone again: the next creates download it
P.forget()
SUMS['body'] = b'not a checksum line\n'
code, body = admin({**SPEC, 'vmid': 207, 'name': 'tempo', 'ip': '192.168.10.207/24', 'operation': 'create_vm'})
with db_open() as db: leftover = creds.meta(db, 'SSH_KEY_TEMPO')
check(code == 502 and body['error'] == 'checksum_invalid' and 207 not in PVE.nodes['pve2']['vms'] and not leftover,
      'image: a malformed SHA512SUMS stops before anything is created')
SUMS['body'] = fake_pve.sha512sums(file='other.qcow2')
check(admin({**SPEC, 'vmid': 207, 'name': 'tempo', 'ip': '192.168.10.207/24', 'operation': 'create_vm'})[1]['error'] == 'checksum_invalid',
      'image: SHA512SUMS without the image is refused')
SUMS['body'] = fake_pve.sha512sums()
PVE.task_fail['download'] = 'checksum mismatch'
code, body = admin({**SPEC, 'vmid': 207, 'name': 'tempo', 'ip': '192.168.10.207/24', 'operation': 'create_vm'})
check(code == 502 and body['error'] == 'proxmox_task_failed' and 'checksum mismatch' in body['detail'], 'image: Proxmox\'s checksum failure is reported')
PVE.task_fail.clear()

# ── onboarding failures and retry ────────────────────────────────────────────────────────────────────────────────────
SSH['stdout'] = 'HLA_USER=svc-hla-agent\nHLA_SUDO_NO\n'
code, body = admin({**SPEC, 'vmid': 207, 'name': 'tempo', 'ip': '192.168.10.207/24', 'operation': 'create_vm'})
with db_open() as db:
    status207 = db.execute('SELECT status FROM managed_vms WHERE vmid=207').fetchone()
check(code == 502 and body['error'] == 'onboarding_failed' and 'sudo' in body['detail'] and 'created and started' in body['detail']
      and status207 == ('onboarding_failed',) and 'tempo' not in core.SERVERS, 'onboarding: no sudo -> not registered, VM kept for a retry')
check(agent({'node': 'pve2', 'operation': 'onboard', 'vmid': 207})[1]['error'] == 'policy_denied', 'onboarding: retry is for the dashboard only')
SSH['stdout'] = 'HLA_USER=svc-hla-agent\nHLA_SUDO_OK\nHLA_QGA_PRESENT\nHLA_QGA_RUNNING\n'
code, body = admin({'node': 'pve2', 'operation': 'onboard', 'vmid': 207})
check(code == 200 and body['status'] == 'onboarded' and 'tempo' in core.SERVERS and body['server']['guest_agent'] == 'present', 'onboarding: retry registers it')
check(admin({'node': 'pve2', 'operation': 'onboard', 'vmid': 207})[1]['error'] == 'already_onboarded', 'onboarding: not twice')
fps = iter(['SHA256:' + 'A' * 43, 'SHA256:' + 'B' * 43])
P._host_fingerprint = lambda host, port=22, timeout=8: next(fps)
P._run_ssh = lambda *a, **k: (_ for _ in ()).throw(paramiko.AuthenticationException('key not there yet'))
with db_open() as db:
    try: P.onboard(db, 'pve2', 207, 'tempo', '192.168.10.207', 'SSH_KEY_TEMPO', 'test', wait=60); err = None
    except P.ProxmoxError as exc: err = exc.error
check(err == 'host_key_changed', 'onboarding: a host key that changes between attempts is not trusted')
P._host_fingerprint = lambda host, port=22, timeout=8: (_ for _ in ()).throw(socket.timeout())
with db_open() as db:
    try: P.onboard(db, 'pve2', 207, 'tempo', '192.168.10.207', 'SSH_KEY_TEMPO', 'test', wait=0); err = None
    except P.ProxmoxError as exc: err = exc.error
check(err == 'ssh_timeout', 'onboarding: gives up after the wait')
P._host_fingerprint, P._run_ssh = fake_fingerprint, fake_run_ssh

# ── destroy: only managed VMs, level 6 and an approval every time ───────────────────────────────────────────────────
levels(6, proxmox_proxmox=6, proxmox_pve2=5)
check(agent({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 205})[1]['error'] == 'policy_denied', 'destroy: level 5 cannot')
code, body = agent({'node': 'proxmox', 'operation': 'destroy_vm', 'vmid': 110})
check(code == 403 and body['error'] == 'not_managed' and 110 in PVE.nodes['proxmox']['vms'], 'destroy: a VM the gateway did not create is never destroyed')
check(admin({'node': 'proxmox', 'operation': 'destroy_vm', 'vmid': 104})[1]['error'] == 'not_managed', 'destroy: not even from the dashboard')
levels(6, proxmox_pve2=6)
code, body = agent({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 205})
dtext = body.get('approval_text', '')
check(code == 403 and body['error'] == 'approval_required' and 'vmid: 205' in dtext and 'name: monitoring' in dtext and 'SSH_KEY_MONITORING' in dtext,
      'destroy: level 6 still needs an approval of that VM')
grant('proxmox:pve2', dtext)
PVE.nodes['pve2']['vms'][205]['config']['tags'] = 'other'
code, body = agent({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 205})
check(code == 403 and body['error'] == 'not_managed' and 205 in PVE.nodes['pve2']['vms'], 'destroy: a VM whose hla-managed tag is gone is refused')
PVE.nodes['pve2']['vms'][205]['config']['tags'] = 'hla-managed'
grant('proxmox:pve2', dtext)
code, body = agent({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 205})
with db_open() as db:
    gone_key = creds.meta(db, 'SSH_KEY_MONITORING')
    gone_pol = db.execute("SELECT 1 FROM server_policy WHERE server='monitoring'").fetchone()
    status205 = db.execute('SELECT status FROM managed_vms WHERE node=? AND vmid=205', ('pve2',)).fetchone()
check(code == 200 and body['status'] == 'destroyed' and 205 not in PVE.nodes['pve2']['vms'] and 'monitoring' not in core.SERVERS
      and not gone_key and not gone_pol and status205 == ('destroyed',), 'destroy: stopped, purged, server and key removed')
check(PVE.calls_like('DELETE', r'/qemu/205$')[-1][3] == {'purge': 1, 'destroy-unreferenced-disks': 1}, 'destroy: with purge')
check(agent({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 205})[1]['error'] == 'not_managed', 'destroy: once')
check(admin({'node': 'pve2', 'operation': 'destroy_vm', 'vmid': 207})[0] == 200 and 'tempo' not in core.SERVERS, 'destroy: the dashboard destroys a managed VM')

# ── read-only audit token in Agent Generated ────────────────────────────────────────────────────────────────────────
PVE.nodes['pve2']['perms'] = {'/': dict(fake_pve.READ_ONLY['/'], **{'VM.PowerMgmt': 1})}
code, body = admin({'node': 'pve2', 'operation': 'create_audit_token'})
check(code == 403 and body['error'] == 'insufficient_privileges' and any('pveum acl modify / --tokens' in c for c in body['commands'])
      and 'Permissions.Modify on /' in body['detail'] and not PVE.nodes['pve2']['tokens'], 'audit token: without the privileges, the exact pveum commands')
PVE.nodes['pve2']['perms'] = {'/': dict(fake_pve.ALL)}
levels(5, proxmox_pve2=5)
code, body = agent({'node': 'pve2', 'operation': 'create_audit_token'})
check(code == 403 and body['error'] == 'approval_required' and 'PVEAuditor' in body['approval_text'] and 'PVE_PVE2_AUDIT_TOKEN_SECRET' in body['approval_text'],
      'audit token: level 5 needs an approval')
grant('proxmox:pve2', body['approval_text'])
code, body = agent({'node': 'pve2', 'operation': 'create_audit_token'})
token_value = PVE.nodes['pve2']['tokens']['hla-monitoring@pve']['monitoring']['value']
check(code == 200 and body['status'] == 'created' and body['token_secret'] == '{{secret:PVE_PVE2_AUDIT_TOKEN_SECRET}}'
      and STORE.get('PVE_PVE2_AUDIT_TOKEN_SECRET') == token_value and STORE.get('PVE_PVE2_AUDIT_TOKEN_ID') == 'hla-monitoring@pve!monitoring',
      'audit token: created and stored in Agent Generated; the reply holds references only')
acl = PVE.nodes['pve2']['acl']
check({'path': '/', 'roles': 'PVEAuditor', 'users': 'hla-monitoring@pve'} in acl and {'path': '/', 'roles': 'PVEAuditor', 'tokens': 'hla-monitoring@pve!monitoring'} in acl
      and PVE.nodes['pve2']['tokens']['hla-monitoring@pve']['monitoring']['info']['privsep'] == 1, 'audit token: PVEAuditor on / for user and privilege-separated token')
levels(6, proxmox_pve2=6)
code, body = agent({'node': 'pve2', 'operation': 'create_audit_token'})
check(code == 200 and body['status'] == 'exists', 'audit token: a second call keeps the stored token')
del STORE['PVE_PVE2_AUDIT_TOKEN_SECRET']; sec._cache['at'] = 0
check(agent({'node': 'pve2', 'operation': 'create_audit_token'})[1]['error'] == 'token_exists', 'audit token: an unknown existing token is not silently replaced')
code, body = agent({'node': 'pve2', 'operation': 'create_audit_token', 'rotate': True})
check(code == 200 and STORE['PVE_PVE2_AUDIT_TOKEN_SECRET'] == PVE.nodes['pve2']['tokens']['hla-monitoring@pve']['monitoring']['value'] != token_value,
      'audit token: rotate replaces it')

# ── locks ────────────────────────────────────────────────────────────────────────────────────────────────────────────
P._locks['pve2'].acquire()
code, body = admin({**SPEC, 'vmid': 208, 'name': 'busy', 'ip': '192.168.10.208/24', 'operation': 'create_vm'})
P._locks['pve2'].release()
check(code == 409 and body['error'] == 'node_busy', 'create: one create or destroy at a time per node')

# ── node configuration ──────────────────────────────────────────────────────────────────────────────────────────────
P.forget()
fake_pve.INFISICAL['/Proxmox/Node_2']['NODE_IP'] = '192.168.20.99'
code, body = admin({'node': 'pve2', 'operation': 'inventory'})
check(code == 200 and body['nodes'][0]['error'] == 'proxmox_target_mismatch', 'config: NODE_IP must match the pinned node address')
fake_pve.INFISICAL['/Proxmox/Node_2']['NODE_IP'] = 'https://192.168.20.31:8006'
P.forget()
check(admin({'node': 'pve2', 'operation': 'inventory'})[0] == 200, 'config: NODE_IP may be written as a URL')
fake_pve.INFISICAL['/Proxmox/Node_2']['NODE_IP'] = '192.168.20.31'
P.forget()
os.environ['PROXMOX_CA_PVE2'] = TMP + '/missing.pem'
check(admin({'node': 'pve2', 'operation': 'inventory'})[1]['nodes'][0]['error'] == 'proxmox_ca_missing', 'config: a missing CA file is a clear error')
os.environ.pop('PROXMOX_CA_PVE2')
P.forget()
P._secrets = lambda folder, names: (_ for _ in ()).throw(RuntimeError('Missing environment variable INFISICAL_URL'))
check(admin({'node': 'pve2', 'operation': 'inventory'})[1]['nodes'][0]['error'] == 'proxmox_not_configured', 'config: Infisical unreadable -> not configured')
P._secrets = fake_pve.secrets
P.forget()

# ── TLS: verification is never off ───────────────────────────────────────────────────────────────────────────────────
src = inspect.getsource(P)
check(not re.search(r'CERT_NONE|_create_unverified_context|check_hostname\s*=\s*False|verify\s*=\s*False', src), 'TLS: no code path turns verification off')
if shutil.which('openssl'):
    d = TMP + '/tls'
    os.makedirs(d)
    run = lambda *a: subprocess.run(a, cwd=d, capture_output=True, check=True)
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem', '-days', '2', '-subj', '/CN=Test PVE CA',
        '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'other.key', '-out', 'other.pem', '-days', '2', '-subj', '/CN=Other CA',
        '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=pve-test.home.arpa')
    Path(d, 'ext').write_text('subjectAltName=DNS:pve-test.home.arpa,DNS:localhost,IP:192.168.99.23\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
                              'extendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n')
    run('openssl', 'x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'leaf.pem', '-days', '2', '-extfile', 'ext')
    check(P.tls_context(d + '/ca.pem').verify_mode == ssl.CERT_REQUIRED and P.tls_context(d + '/ca.pem').check_hostname, 'TLS: CERT_REQUIRED and host name checks')
    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.headers.get('Authorization') == 'PVEAPIToken=root@pam!t=tls-secret-123456'
            data = json.dumps({'data': {'version': '9.0.10'}} if ok else {'data': None}).encode()
            self.send_response(200 if ok else 401); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *a): pass
    tls = HTTPServer(('127.0.0.1', 0), Api)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(d + '/leaf.pem', d + '/leaf.key')
    tls.socket = sctx.wrap_socket(tls.socket, server_side=True)
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    cfg = {'node': 'pve2', 'ip': '127.0.0.1', 'port': tls.server_port, 'ca': d + '/ca.pem', 'tls_name': 'pve-test.home.arpa',
           'token_id': 'root@pam!t', 'token_secret': 'tls-secret-123456'}
    status, reason, out = P._real_http(cfg, 'GET', '/version')
    check(status == 200 and out['data']['version'] == '9.0.10', 'TLS: an IP missing from the SAN works through the pinned name (SNI) and the node CA')
    for change, label in (({'tls_name': 'evil.example'}, 'a name the certificate does not carry'), ({'ca': d + '/other.pem'}, 'another CA'),
                          ({'tls_name': '127.0.0.1'}, 'the bare IP')):
        try:
            P._real_http({**cfg, **change}, 'GET', '/version'); refused = False
        except ssl.SSLCertVerificationError:
            refused = True
        check(refused, 'TLS: refused with ' + label)
    P._http = P._real_http
    P._cfg_cache['pve2'] = (1e18, {**cfg, 'tls_name': 'evil.example'})
    code, body = admin({'node': 'pve2', 'operation': 'inventory'})
    check(code == 200 and body['nodes'][0]['error'] == 'proxmox_tls_failed' and 'not trusted' in body['nodes'][0]['detail'], 'TLS: a failed check is reported as such')
    P._http = PVE.http
    P.forget()
    tls.shutdown()
else:
    print('SKIP TLS handshake checks (no openssl)')

# ── nothing secret anywhere ─────────────────────────────────────────────────────────────────────────────────────────
secret_values = list(fake_pve.TOKEN_SECRETS.values()) + [token_value, STORE['PVE_PVE2_AUDIT_TOKEN_SECRET']] + ([private.split('\n')[1]] if private else [])
everything = '\n'.join(REPLIES) + '\n'.join(str(a) for a in audits())
check(not any(v in everything for v in secret_values), 'no token secret or private key in any reply or audit row')
with db_open() as db:
    stored_private = [r[0] for r in db.execute('SELECT ciphertext FROM credentials')]
check(all(b'OPENSSH' not in (c or b'') for c in stored_private), 'the private keys are stored encrypted')
srv_reply = srv.Handler.reply.__code__.co_names
check('known' in srv_reply and 'mask' in srv_reply, 'replies mask the node token secrets')
PVE.fail[('GET', r'/status/current$')] = (502, 'odd answer ' + fake_pve.TOKEN_SECRETS['pve2'])
code, body = admin({'node': 'pve2', 'operation': 'stop', 'vmid': 204})
check(code == 502 and fake_pve.TOKEN_SECRETS['pve2'] not in json.dumps(body) and '[secret:PVE_PVE2_TOKEN_SECRET]' in json.dumps(body),
      'a node token secret that shows up in a reply is masked')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED: {failures}'))
sys.exit(1 if failures else 0)
