"""A stand-in for the Proxmox VE API of two nodes, at the HTTP layer of gateway/proxmox.py (proxmox._http).

Used by tests/test_proxmox.py and the dashboard preview server (agent-platform-starter/tests/e2e/preview_server.py).
It keeps VMs, storages, image contents, users, tokens and ACLs in memory, answers like PVE 8/9 (data envelopes, UPIDs for
tasks, HTTP 500 for a missing VM config, 403 with the permission check in the reason) and records every call.
"""
import copy
import json
import re
import threading
import urllib.parse

TOKEN_SECRETS = {'proxmox': 'node1-secret-6f1d2c3b-aaaa-bbbb-cccc-0123456789ab', 'pve2': 'node2-secret-9e8d7c6b-dddd-eeee-ffff-ba9876543210'}
INFISICAL = {
    '/Proxmox/Node_1': {'NODE_IP': '192.168.20.30', 'NODE_PORT': '8006', 'TOKEN_ID': 'root@pam!hla', 'TOKEN_SECRET': TOKEN_SECRETS['proxmox']},
    '/Proxmox/Node_2': {'NODE_IP': '192.168.20.31', 'NODE_PORT': '8006', 'TOKEN_ID': 'root@pam!hla', 'TOKEN_SECRET': TOKEN_SECRETS['pve2']},
}
ALL = {p: 1 for p in ('Sys.Audit', 'Sys.Modify', 'Sys.AccessNetwork', 'VM.Audit', 'VM.Allocate', 'VM.PowerMgmt', 'VM.Snapshot',
                      'VM.Snapshot.Rollback', 'VM.Config.Disk', 'VM.Config.CPU', 'VM.Config.Memory', 'VM.Config.Network',
                      'VM.Config.Cloudinit', 'VM.Config.Options', 'VM.Config.HWType', 'Datastore.Audit', 'Datastore.AllocateSpace',
                      'Datastore.AllocateTemplate', 'SDN.Use', 'User.Modify', 'Realm.AllocateUser', 'Permissions.Modify')}
READ_ONLY = {'/': {'Sys.Audit': 1, 'VM.Audit': 1, 'Datastore.Audit': 1}}


def secrets(folder, names):
    values = INFISICAL[folder]
    return {n: values[n] for n in names}


def _vm(vmid, name, status='running', ip=None, tags='', template=False, mem=2 << 30, disk=32 << 30, agent=True, bridge='vmbr0', gw='192.168.10.1',
        mac=None, vlan=None):
    config = {'name': name, 'cores': 2, 'memory': mem >> 20, 'net0': f'virtio={mac or "BC:24:11:00:%02X:%02X" % (vmid // 256, vmid % 256)},bridge={bridge}' + (f',tag={vlan}' if vlan else ''),
              'scsi0': f'local-lvm:vm-{vmid}-disk-0,size={disk >> 30}G', 'agent': '1' if agent else '0'}
    if ip:
        config['ipconfig0'] = f'ip={ip}/24,gw={gw}'
        config['nameserver'] = '192.168.10.1'
    if tags:
        config['tags'] = tags
    if template:
        config['template'] = 1
    return {'vmid': vmid, 'name': name, 'status': status, 'cpu': 0.03, 'cpus': 2, 'mem': mem // 3, 'maxmem': mem, 'maxdisk': disk,
            'uptime': 86400 if status == 'running' else 0, 'template': 1 if template else 0, 'config': config, 'snapshots': {},
            'guest_ips': [ip] if ip and agent and status == 'running' else []}


class FakePVE:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.fail = {}        # (method, path regex) -> (status, reason)  one-shot or permanent ('*' prefix in key[0])
        self.task_fail = {}   # task type -> exit status
        self.tasks = {}
        self.seq = 0
        self.nodes = {
            'proxmox': {'version': '8.4.1', 'perms': {'/': dict(ALL)}, 'vms': {}, 'lxc': [],
                        'storages': [{'storage': 'local', 'type': 'dir', 'content': 'iso,vztmpl,backup,import', 'avail': 80 << 30, 'total': 100 << 30, 'used': 20 << 30, 'active': 1, 'enabled': 1},
                                     {'storage': 'local-lvm', 'type': 'lvmthin', 'content': 'images,rootdir', 'avail': 400 << 30, 'total': 900 << 30, 'used': 500 << 30, 'active': 1, 'enabled': 1}],
                        'content': {'local': [{'volid': 'local:iso/debian-13.1.0-amd64-netinst.iso', 'content': 'iso', 'size': 700 << 20, 'format': 'iso'}]},
                        'network': [{'iface': 'vmbr0', 'type': 'bridge', 'cidr': '192.168.20.30/24', 'gateway': '192.168.20.1', 'active': 1},
                                    {'iface': 'eno1', 'type': 'eth', 'active': 1}],
                        'users': [{'userid': 'root@pam'}], 'tokens': {}, 'acl': []},
            'pve2': {'version': '9.0.10', 'perms': {'/': dict(ALL)}, 'vms': {}, 'lxc': [],
                     'storages': [{'storage': 'local', 'type': 'dir', 'content': 'iso,vztmpl,import,backup', 'avail': 60 << 30, 'total': 90 << 30, 'used': 30 << 30, 'active': 1, 'enabled': 1},
                                  {'storage': 'local-lvm', 'type': 'lvmthin', 'content': 'images,rootdir', 'avail': 300 << 30, 'total': 400 << 30, 'used': 100 << 30, 'active': 1, 'enabled': 1}],
                     'content': {'local': []},
                     'network': [{'iface': 'vmbr0', 'type': 'bridge', 'cidr': '192.168.20.31/24', 'gateway': '192.168.20.1', 'active': 1},
                                 {'iface': 'vmbr1', 'type': 'bridge', 'active': 1, 'comments': 'isolated VLAN 70'}],
                     'users': [{'userid': 'root@pam'}], 'tokens': {}, 'acl': []},
        }
        n1 = self.nodes['proxmox']['vms']
        for vmid, name, ip in ((100, 'homeassistant', '192.168.10.100'), (101, 'jellyfin', '192.168.10.101'), (102, 'docker', '192.168.10.102'),
                               (103, 'traefik', None), (104, 'dev', '192.168.10.104'), (105, 'omada', None), (106, 'public-website', None),
                               (107, 'db-01', '192.168.10.107'), (108, 'fs-01', '192.168.10.108')):
            n1[vmid] = _vm(vmid, name, ip=ip)
        n1[103]['config']['ipconfig0'] = 'ip=192.168.30.103/24,gw=192.168.30.1'
        n1[109] = _vm(109, 'agents-vm', ip='192.168.10.202')
        n1[110] = _vm(110, 'old-test', status='stopped', ip='192.168.10.150', agent=False)
        self.nodes['pve2']['vms'][204] = _vm(204, 'sandbox-vm', ip='192.168.40.204', bridge='vmbr1', gw='192.168.40.1')
        # pve2's vmbr0 is a trunk whose untagged network is VLAN 40: guests in 192.168.10.0/24 carry tag 30.
        self.nodes['pve2']['vms'][203] = _vm(203, 'vault-test', ip='192.168.10.203', vlan=30)
        self.nodes['pve2']['vms'][204]['snapshots']['clean'] = {'name': 'clean', 'description': 'fresh', 'snaptime': 1760000000}

    # ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────
    def task(self, node, kind, vmid=''):
        self.seq += 1
        upid = f'UPID:{node}:0000{self.seq:04X}:0000ABCD:6900{self.seq:04X}:{kind}:{vmid}:root@pam!hla:'
        exit_status = self.task_fail.get(kind, 'OK')
        self.tasks[upid] = {'status': 'stopped', 'exitstatus': exit_status}
        return upid

    def nextid(self, node):
        used = set(self.nodes[node]['vms']) | {c['vmid'] for c in self.nodes[node]['lxc']}
        n = 100
        while n in used:
            n += 1
        return n

    def add_image(self, node, storage='local', file='debian-13-genericcloud-amd64.qcow2'):
        self.nodes[node]['content'].setdefault(storage, []).append(
            {'volid': f'{storage}:import/{file}', 'content': 'import', 'size': 420 << 20, 'format': 'qcow2'})

    def calls_like(self, method, pattern):
        return [c for c in self.calls if c[1] == method and re.search(pattern, c[2])]

    # ── the HTTP layer (replaces proxmox._http) ─────────────────────────────────────────────────────────────────
    def http(self, cfg, method, path, params=None, timeout=20):
        with self.lock:
            node = cfg['node']
            assert cfg['token_secret'] == TOKEN_SECRETS[node], 'wrong token for ' + node
            self.calls.append((node, method, path, copy.deepcopy(params)))
            for (m, pattern), (status, reason) in list(self.fail.items()):
                if m.lstrip('*') == method and re.search(pattern, path):
                    if not m.startswith('*'):
                        del self.fail[(m, pattern)]
                    return status, reason, {'data': None}
            try:
                return self.route(node, method, path, dict(params or {}))
            except KeyError as exc:
                return 500, f'not found {exc}', {'data': None}

    def ok(self, data):
        return 200, 'OK', {'data': data}

    def route(self, node, method, path, p):
        n = self.nodes[node]
        if path == '/version':
            return self.ok({'version': n['version'], 'release': n['version'].rsplit('.', 1)[0], 'repoid': 'abc'})
        if path == '/access/permissions':
            if p.get('path'):   # Proxmox answers {path: privileges at that path} (value = propagate flag)
                q = p['path']
                best = max((k for k in n['perms'] if k == '/' or q == k or q.startswith(k.rstrip('/') + '/')), key=len, default=None)
                return self.ok({q: dict(n['perms'][best]) if best else {}})
            return self.ok(n['perms'])
        name = n.get('name', node)
        if path == '/cluster/status':
            if n.get('no_cluster_status'):
                return 403, 'Permission check failed', {'data': None}
            return self.ok([{'type': 'node', 'name': name, 'local': 1, 'id': 'node/' + name}])
        if path == '/nodes':
            return self.ok([{'node': name, 'status': 'online'}])
        if path == '/cluster/nextid':
            return self.ok(str(self.nextid(node)))
        m = re.fullmatch(r'/nodes/([^/]+)(/.*)?', path)
        if m:
            if m.group(1) != name:
                return 595, 'no such node', {'data': None}
            return self.node_route(node, n, method, m.group(2) or '', p)
        if path == '/access/users':
            if method == 'GET':
                return self.ok(n['users'])
            if not self.allowed(n, 'User.Modify', '/access/groups'):
                return 403, 'Permission check failed (/access/groups, User.Modify)', {'data': None}
            n['users'].append({'userid': p['userid'], 'comment': p.get('comment')})
            return self.ok(None)
        if path == '/access/acl' and method == 'PUT':
            if not self.allowed(n, 'Permissions.Modify', p['path']):
                return 403, f'Permission check failed ({p["path"]}, Permissions.Modify)', {'data': None}
            n['acl'].append(p)
            return self.ok(None)
        m = re.fullmatch(r'/access/users/([^/]+)/token(?:/([^/]+))?', path)
        if m:
            user = urllib.parse.unquote(m.group(1))
            toks = n['tokens'].setdefault(user, {})
            if method == 'GET' and m.group(2):
                if not toks:
                    n['tokens'].pop(user)
                return self.ok(toks[m.group(2)]['info'])
            if method == 'GET':
                return self.ok([{'tokenid': t, **v['info']} for t, v in toks.items()])
            if method == 'DELETE':
                toks.pop(m.group(2))
                return self.ok(None)
            value = 'f00dbabe-%04d-4abc-8def-%012d' % (self.seq, len(toks) + 1)
            self.seq += 1
            toks[m.group(2)] = {'info': {'privsep': int(p.get('privsep', 1)), 'comment': p.get('comment')}, 'value': value}
            return self.ok({'full-tokenid': f'{user}!{m.group(2)}', 'value': value, 'info': toks[m.group(2)]['info']})
        return 501, 'Method not implemented', {'data': None}

    def allowed(self, n, priv, path):
        perms = n['perms']
        best = max((q for q in perms if q == '/' or path == q or path.startswith(q.rstrip('/') + '/')), key=len, default=None)
        return bool(best and perms[best].get(priv))

    def node_route(self, node, n, method, sub, p):
        if sub == '/status':
            return self.ok({'cpu': 0.12, 'cpuinfo': {'cpus': 16}, 'memory': {'used': 40 << 30, 'total': 64 << 30}, 'uptime': 900000,
                            'pveversion': 'pve-manager/' + n['version']})
        if sub == '/qemu' and method == 'GET':
            return self.ok([{k: v for k, v in vm.items() if k not in ('config', 'snapshots', 'guest_ips')}
                            | ({'tags': vm['config']['tags']} if vm['config'].get('tags') else {}) for vm in n['vms'].values()])
        if sub == '/qemu' and method == 'POST':
            return self.create(node, n, p)
        if sub == '/lxc':
            return self.ok(n['lxc'])
        if sub == '/storage':
            return self.ok(n['storages'])
        if sub == '/network':
            return self.ok(n['network'])
        m = re.fullmatch(r'/storage/([^/]+)/content', sub)
        if m:
            return self.ok(n['content'].get(m.group(1), []))
        m = re.fullmatch(r'/storage/([^/]+)/download-url', sub)
        if m:
            st = next(s for s in n['storages'] if s['storage'] == m.group(1))
            if 'import' not in st['content']:
                return 400, 'Parameter verification failed.', {'errors': {'content': "storage does not support content type 'import'"}}
            assert p['checksum-algorithm'] == 'sha512' and re.fullmatch(r'[0-9a-f]{128}', p['checksum'])
            self.downloaded = p
            if not self.task_fail.get('download'):
                self.add_image(node, m.group(1), p['filename'])
            return self.ok(self.task(node, 'download'))
        m = re.fullmatch(r'/tasks/([^/]+)/status', sub)
        if m:
            return self.ok(self.tasks[urllib.parse.unquote(m.group(1))])
        m = re.fullmatch(r'/qemu/(\d+)(/.*)?', sub)
        if m:
            vmid = int(m.group(1))
            if vmid not in n['vms']:
                return 500, f"Configuration file 'nodes/{node}/qemu-server/{vmid}.conf' does not exist", {'data': None}
            return self.vm_route(node, n, n['vms'][vmid], method, m.group(2) or '', p)
        return 501, 'Method not implemented', {'data': None}

    def create(self, node, n, p):
        vmid = int(p['vmid'])
        if vmid in n['vms']:
            return 500, f'unable to create VM {vmid} - VM {vmid} already exists on node \'{node}\'', {'data': None}
        if not self.allowed(n, 'VM.Allocate', f'/vms/{vmid}'):
            return 403, f'Permission check failed (/vms/{vmid}, VM.Allocate)', {'data': None}
        source = re.search(r'import-from=([^,]+)', p.get('scsi0', ''))
        if not source or not any(c['volid'] == source.group(1) for cs in n['content'].values() for c in cs):
            return 400, 'Parameter verification failed.', {'errors': {'scsi0': 'import-from: volume does not exist'}}
        vm = _vm(vmid, p['name'], status='stopped', agent=False)
        vm['config'] = {k: v for k, v in p.items() if k != 'vmid'}
        vm['config']['scsi0'] = f'{p["scsi0"].split(":")[0]}:vm-{vmid}-disk-0,size=3G'
        vm['maxdisk'] = 3 << 30
        n['vms'][vmid] = vm
        return self.ok(self.task(node, 'qmcreate', vmid))

    def vm_route(self, node, n, vm, method, sub, p):
        vmid = vm['vmid']
        if sub == '/config' and method == 'GET':
            return self.ok(vm['config'])
        if sub == '/config' and method in ('POST', 'PUT'):
            vm['config'].update(p)
            if 'name' in p:
                vm['name'] = p['name']
            return self.ok(self.task(node, 'qmconfig', vmid))
        if sub == '/status/current':
            return self.ok({'vmid': vmid, 'name': vm['name'], 'status': vm['status'], 'qmpstatus': vm['status']})
        m = re.fullmatch(r'/status/(start|stop|shutdown|reboot)', sub)
        if m:
            if not self.allowed(n, 'VM.PowerMgmt', f'/vms/{vmid}'):
                return 403, f'Permission check failed (/vms/{vmid}, VM.PowerMgmt)', {'data': None}
            vm['status'] = 'stopped' if m.group(1) in ('stop', 'shutdown') else 'running'
            if vm['status'] == 'running' and 'ipconfig0' in vm['config']:
                ip = re.search(r'ip=([0-9.]+)', vm['config']['ipconfig0']).group(1)
                vm['guest_ips'] = [ip]
            return self.ok(self.task(node, 'qm' + m.group(1), vmid))
        if sub == '/agent/network-get-interfaces':
            if vm['status'] != 'running' or not vm['guest_ips']:
                return 500, 'QEMU guest agent is not running', {'data': None}
            return self.ok({'result': [{'name': 'lo', 'ip-addresses': [{'ip-address': '127.0.0.1', 'ip-address-type': 'ipv4'}]},
                                       {'name': 'eth0', 'ip-addresses': [{'ip-address': ip, 'ip-address-type': 'ipv4'} for ip in vm['guest_ips']]}]})
        if sub == '/snapshot' and method == 'GET':
            return self.ok([*vm['snapshots'].values(), {'name': 'current', 'description': 'You are here!'}])
        if sub == '/snapshot' and method == 'POST':
            vm['snapshots'][p['snapname']] = {'name': p['snapname'], 'description': p.get('description', ''), 'snaptime': 1762700000}
            return self.ok(self.task(node, 'qmsnapshot', vmid))
        m = re.fullmatch(r'/snapshot/([^/]+)/rollback', sub)
        if m:
            if m.group(1) not in vm['snapshots']:
                return 500, f"snapshot '{m.group(1)}' does not exist", {'data': None}
            return self.ok(self.task(node, 'qmrollback', vmid))
        if sub == '/clone':
            newid = int(p['newid'])
            clone = _vm(newid, p['name'], status='stopped', agent=False)
            clone['config'] = {k: v for k, v in vm['config'].items() if k != 'template'}
            clone['config'].update(name=p['name'])
            n['vms'][newid] = clone
            return self.ok(self.task(node, 'qmclone', newid))
        if sub == '/resize':
            vm['config'][p['disk']] = re.sub(r'size=\w+', 'size=' + p['size'], vm['config'].get(p['disk'], 'x,size=3G'))
            vm['maxdisk'] = int(p['size'].rstrip('G')) << 30
            return self.ok(self.task(node, 'resize', vmid))
        if sub == '' and method == 'DELETE':
            if vm['status'] != 'stopped':
                return 500, f'VM {vmid} is running - destroy failed', {'data': None}
            assert str(p.get('purge')) == '1'
            del n['vms'][vmid]
            return self.ok(self.task(node, 'qmdestroy', vmid))
        return 501, 'Method not implemented', {'data': None}


def sha512sums(file='debian-13-genericcloud-amd64.qcow2', digest='ab' * 64):
    return ('\n'.join([f'{"cd" * 64}  debian-13-generic-amd64.qcow2', f'{digest}  {file}', f'{"ef" * 64}  debian-13-nocloud-amd64.qcow2']) + '\n').encode()


def as_json(obj):
    return json.dumps(obj, sort_keys=True)
