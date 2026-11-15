"""Proxmox VE through the gateway (HLA_PROXMOX_V1): inventory, power and snapshots, and VMs the platform creates,
onboards as SSH servers and may destroy again. Stdlib + paramiko (via gateway/core.py) only.

Nodes and credentials
  Each node in the inventory (services.proxmox.nodes) is a service `proxmox:<node>` (core.SERVICES: the pinned address
  from its URL and the secret-store folder Proxmox/<node>). The folder holds TOKEN_ID and TOKEN_SECRET (and, from older
  setups, NODE_IP / NODE_PORT, which must then equal the inventory); read through core.platform_secrets and cached for
  CONFIG_SECONDS. The token secret never leaves the gateway: it is masked in every reply and audit row.

TLS
  Verified unless the owner sets verify_tls: false in the inventory: the connection goes to the node's address, the
  certificate chain is checked against the node's own PVE CA file (certs/<node>-ca.pem in the image, copy it from
  /etc/pve/pve-root-ca.pem; without that file the system CAs) and its name against the node's TLS name (the node's
  tls_name, else the host name of its URL; sent as SNI / server_hostname), so no DNS is needed and an IP missing from
  the SAN does not matter. verify_tls: false (not recommended) skips both checks for every node.

Levels (min(agent level, proxmox:<node> level); the services start at 0 = off)
  1  inventory, snapshots (list), preview_create
  4  start, shutdown, stop, reboot, snapshot_create
  5  create_vm and create_audit_token, each after the owner approved the exact request (chat approval card)
  6  snapshot_rollback; create_vm/create_audit_token without approval (also unhinged mode);
     destroy_vm: level 6 AND an approval of that exact VM every time, and only VMs this gateway created
  VMs named in PROXMOX_PROTECTED_NAMES (default agents-vm, secrets-vm) are never powered or rolled back by agents.
  VMIDs in the inventory (services.proxmox.reserved_vmids and every server's vmid) are never offered for new VMs.

Created VMs
  Debian 13 generic cloud image (download-url with the SHA512 from Debian's SHA512SUMS, `import` content, PVE >= 8.3)
  or a clone of a template named debian-13*; cloud-init user svc-hla-agent with an ed25519 key generated here and
  stored in the credential store (SSH_KEY_<NAME>), tagged hla-managed. After boot the gateway records the SSH host key
  (first contact), checks the user and `sudo -n`, installs qemu-guest-agent, and registers the VM as a server
  (servers_dynamic) at level 0.
"""
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

from . import core
from . import credentials as store
from . import agent_secrets

PREFIX = 'proxmox:'
SETTINGS = core.INVENTORY['services'].get('proxmox') or {}
VERIFY_TLS = SETTINGS.get('verify_tls', True)


def _tls_name(node):
    if node.get('tls_name'):
        return node['tls_name']
    try:
        ipaddress.ip_address(node['host'])
        return node['host']  # an address: the certificate needs it in its SAN (or set tls_name)
    except ValueError:
        return node['host']


# node -> {label, ca (file name in certs/), tls_name, host, port}, from the inventory; empty without Proxmox.
NODES = {n['name']: {'label': n['label'], 'ca': n['name'] + '-ca.pem', 'tls_name': _tls_name(n), 'host': n['host'], 'port': n['port']}
         for n in SETTINGS.get('nodes', [])}
CONFIG_SECONDS = 60
INVENTORY_SECONDS = 15
MANAGED_TAG = 'hla-managed'
CIUSER = 'svc-hla-agent'
RESERVED_VMIDS = frozenset(list(SETTINGS.get('reserved_vmids', [])) + [s['vmid'] for s in core.INVENTORY['servers'] if s.get('vmid')])
VMID_RANGE = (100, 9999)
LIMITS = {'cores': (1, 8), 'memory': (512, 16384), 'disk': (8, 256)}
IMAGES = {
    'debian-13': {'label': 'Debian 13 (trixie) generic cloud image',
                  'url': 'https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2',
                  'sums': 'https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS',
                  'file': 'debian-13-genericcloud-amd64.qcow2', 'template': re.compile(r'^debian-13', re.I)},
}
# Operation -> level it needs. Approval rules are in needs_approval().
OPERATIONS = {'inventory': 1, 'snapshots': 1, 'preview_create': 1, 'start': 4, 'shutdown': 4, 'stop': 4, 'reboot': 4,
              'snapshot_create': 4, 'snapshot_rollback': 6, 'create_vm': 5, 'create_audit_token': 5, 'destroy_vm': 6}
ADMIN_ONLY = ('onboard',)  # retry onboarding of a managed VM (dashboard)
POWER = ('start', 'shutdown', 'stop', 'reboot')
SSH_WAIT = 300
TASK_POLL = 2.0
MAX_BODY = 4 * 1024 * 1024
AUDIT_USER = 'hla-monitoring@pve'
AUDIT_TOKEN = 'monitoring'

_NAME = re.compile(r'^[a-z][a-z0-9-]{0,30}[a-z0-9]$')
_SNAP = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{1,39}$')
_MAC = re.compile(r'^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$')
_TOKEN_ID = re.compile(r'^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+![A-Za-z][A-Za-z0-9._-]*$')
_SUM_LINE = re.compile(r'^([0-9a-f]{128})  ([A-Za-z0-9._-]+)$')
_RESERVED_NAMES = ('proxmox', 'mcp', 'homeassistant', 'secrets', 'github', 'http-tools', 'http_tools', 'websites', 'routes', 'gateway')

# Replaced in tests: the secret reader, the HTTP layer, the image checksum download, SSH, the IP probe and sleeping.
_secrets = None
_fetch_url = None
_host_fingerprint = core.host_fingerprint
_run_ssh = core.run_ssh
_sleep = time.sleep

_cfg_cache = {}
_name_cache = {}
_inv_cache = {}
_locks = {n: threading.Lock() for n in NODES}
_cache_lock = threading.Lock()


class ProxmoxError(Exception):
    def __init__(self, status, error, detail='', **extra):
        super().__init__(error)
        self.status, self.error, self.detail, self.extra = status, error, detail, extra

    def payload(self):
        return {'error': self.error, 'detail': self.detail, **self.extra}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS servers_dynamic (name TEXT PRIMARY KEY, host TEXT NOT NULL, user TEXT NOT NULL,
        key_credential TEXT NOT NULL, host_fingerprint TEXT NOT NULL, node TEXT NOT NULL, vmid INTEGER NOT NULL,
        created_by TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS managed_vms (node TEXT NOT NULL, vmid INTEGER NOT NULL, name TEXT NOT NULL,
        ip TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, key_credential TEXT NOT NULL DEFAULT '', image_source TEXT NOT NULL DEFAULT '',
        approval TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '', PRIMARY KEY (node, vmid))''')


def service(node):
    return PREFIX + node


def node_of(service_name):
    if isinstance(service_name, str) and service_name.startswith(PREFIX) and service_name[len(PREFIX):] in NODES:
        return service_name[len(PREFIX):]
    return None


# ── settings ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def allowed_nets():
    raw = os.getenv('PROXMOX_ALLOWED_NETS', '').strip() or '192.168.10.0/24'
    nets = []
    for part in re.split(r'[\s,]+', raw):
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                continue
    return [n for n in nets if n.version == 4] or [ipaddress.ip_network('192.168.10.0/24')]


def protected_names():
    raw = os.getenv('PROXMOX_PROTECTED_NAMES')
    raw = 'agents-vm,secrets-vm' if raw is None else raw
    return {p.strip().lower() for p in raw.split(',') if p.strip()}


def ca_dir():
    return os.getenv('PROXMOX_CA_DIR', '').strip() or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'certs')


def _env_key(node):
    return re.sub(r'[^A-Z0-9]', '_', node.upper())


def node_config(node):
    """{ip, port, tls_name, ca, token_id, token_secret} of one node: address from the inventory, token from the secret
    store (cached CONFIG_SECONDS)."""
    if node not in NODES:
        raise ProxmoxError(404, 'unknown_node', 'nodes are ' + ', '.join(NODES))
    with _cache_lock:
        hit = _cfg_cache.get(node)
        if hit and time.monotonic() - hit[0] < CONFIG_SECONDS:
            return hit[1]
    pinned, folder = core.SERVICES[service(node)]
    shown = folder.strip('/')
    reader = _secrets or core.platform_secrets
    try:
        values = reader(folder, ('TOKEN_ID', 'TOKEN_SECRET'), ('NODE_IP', 'NODE_PORT'))
    except Exception as exc:  # noqa: BLE001 - only the type is shown, never a value
        raise ProxmoxError(503, 'proxmox_not_configured',
                           f'cannot read TOKEN_ID and TOKEN_SECRET from the secret store ({shown}/...) ({type(exc).__name__})') from None
    host = str(values.get('NODE_IP') or pinned).strip()
    if '://' in host:
        host = urllib.parse.urlsplit(host).hostname or ''
    host = host.split(':')[0] if host.count(':') == 1 else host
    if host != pinned:
        raise ProxmoxError(503, 'proxmox_target_mismatch', f'NODE_IP in {shown} does not match the pinned address of {node} ({pinned})')
    try:
        port = int(str(values.get('NODE_PORT') or NODES[node]['port']).strip())
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ProxmoxError(503, 'proxmox_not_configured', f'NODE_PORT in {shown} is not a port number') from None
    if port != NODES[node]['port']:
        raise ProxmoxError(503, 'proxmox_target_mismatch', f'NODE_PORT in {shown} does not match the inventory URL of {node}')
    token_id = str(values.get('TOKEN_ID') or '').strip()
    secret = str(values.get('TOKEN_SECRET') or '').strip()
    if not _TOKEN_ID.match(token_id) or not secret or any(c in secret for c in '\r\n= '):
        raise ProxmoxError(503, 'proxmox_not_configured', f'TOKEN_ID (user@realm!name) or TOKEN_SECRET in {shown} is not valid')
    explicit = os.getenv('PROXMOX_CA_' + _env_key(node), '').strip()
    ca = explicit or os.path.join(ca_dir(), NODES[node]['ca'])
    if not os.path.isfile(ca) or not os.access(ca, os.R_OK):
        if explicit or not VERIFY_TLS:
            if VERIFY_TLS:
                raise ProxmoxError(503, 'proxmox_ca_missing', f'the CA file of {node} is missing in the gateway image ({os.path.basename(ca)}); '
                                   'copy it into ssh-gateway/certs/ and rebuild')
        ca = None  # no node CA file: the system CAs (a publicly trusted certificate), or no checks with verify_tls: false
    cfg = {'node': node, 'ip': host, 'port': port, 'ca': ca, 'token_id': token_id, 'token_secret': secret,
           'tls_name': os.getenv('PROXMOX_TLS_NAME_' + _env_key(node), '').strip() or NODES[node]['tls_name']}
    with _cache_lock:
        _cfg_cache[node] = (time.monotonic(), cfg)
    return cfg


def known():
    """{name: value} of the node token secrets seen so far (for masking; never fetched for this)."""
    with _cache_lock:
        return {'PVE_' + _env_key(n) + '_TOKEN_SECRET': c[1]['token_secret'] for n, c in _cfg_cache.items()}


def forget():
    with _cache_lock:
        _cfg_cache.clear()
        _inv_cache.clear()
        _name_cache.clear()


# ── HTTP layer ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _unverified_context():
    """Only with verify_tls: false in the inventory (the owner's explicit choice): encrypted but not authenticated."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def tls_context(cafile):
    """Verification on: chain against this node's CA (or the system CAs without one), and the host name against the
    certificate. The only exception is verify_tls: false in the inventory (_unverified_context)."""
    if not VERIFY_TLS:
        return _unverified_context()
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a fixed IP and checks the certificate against `tls_name` (sent as SNI), not against the IP."""

    def __init__(self, ip, port, tls_name, context, timeout):
        super().__init__(ip, port, timeout=timeout, context=context)
        self.tls_name = tls_name

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.tls_name)
        except BaseException:
            sock.close()
            raise


def _encode(params):
    out = []
    for k, v in (params or {}).items():
        if v is None:
            continue
        out.append((k, '1' if v is True else '0' if v is False else str(v)))
    return urllib.parse.urlencode(out)


def _real_http(cfg, method, path, params=None, timeout=20):
    """(status, reason, body json) of one API call."""
    conn = PinnedHTTPSConnection(cfg['ip'], cfg['port'], cfg['tls_name'], tls_context(cfg['ca']), timeout)
    url = '/api2/json' + path
    headers = {'Authorization': 'PVEAPIToken=' + cfg['token_id'] + '=' + cfg['token_secret'], 'Accept': 'application/json'}
    data = None
    if params and method in ('GET', 'DELETE'):
        url += '?' + _encode(params)
    elif params is not None:
        data = _encode(params).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        conn.request(method, url, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_BODY + 1)
        status, reason = resp.status, resp.reason
    finally:
        conn.close()
    try:
        body = json.loads(raw[:MAX_BODY] or b'{}')
    except ValueError:
        body = {}
    return status, reason, body if isinstance(body, dict) else {}


_http = _real_http


def _clean(text, limit=300):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(text or ''))[:limit]


def api(node, method, path, params=None, timeout=20):
    """data of one call, or ProxmoxError with what Proxmox said (never the token)."""
    cfg = node_config(node)
    prefix = '/nodes/' + node
    if path == prefix or path.startswith(prefix + '/'):
        path = '/nodes/' + pve_name(node) + path[len(prefix):]
    try:
        status, reason, body = _http(cfg, method, path, params, timeout)
    except ssl.SSLCertVerificationError as exc:
        raise ProxmoxError(502, 'proxmox_tls_failed', f'the certificate of {node} is not trusted for {cfg["tls_name"]} '
                           f'(checked against {os.path.basename(cfg["ca"]) if cfg["ca"] else "the system CAs"}): {_clean(exc.verify_message, 120)}') from None
    except ssl.SSLError as exc:
        raise ProxmoxError(502, 'proxmox_tls_failed', f'TLS with {node} failed ({type(exc).__name__})') from None
    except (OSError, http.client.HTTPException) as exc:
        raise ProxmoxError(502, 'proxmox_unreachable', f'{node} ({cfg["ip"]}:{cfg["port"]}) did not answer: {type(exc).__name__}') from None
    if status == 401:
        raise ProxmoxError(502, 'proxmox_auth_failed', f'{node} refused the API token (check TOKEN_ID/TOKEN_SECRET in the secret store)')
    if status == 403:
        raise ProxmoxError(403, 'proxmox_permission_denied', f'{node}: {_clean(reason)}')
    if status >= 400:
        errors = body.get('errors') if isinstance(body.get('errors'), dict) else {}
        detail = _clean(reason) + (': ' + '; '.join(f'{_clean(k, 40)}: {_clean(v, 160)}' for k, v in list(errors.items())[:6]) if errors else '')
        raise ProxmoxError(502 if status >= 500 else 400, 'proxmox_error', f'{node} answered HTTP {status} {detail}'.strip(), http_status=status)
    return body.get('data')


def pve_name(node):
    """The node's own name in Proxmox (what /nodes/<name> needs), which need not be the gateway's key for it:
    PROXMOX_PVE_NODE_<KEY>, else the local node in /cluster/status, else the only entry of /nodes, else the key."""
    override = os.getenv('PROXMOX_PVE_NODE_' + _env_key(node), '').strip()
    if override:
        return override
    with _cache_lock:
        if node in _name_cache:
            return _name_cache[node]
    name = None
    try:
        for item in api(node, 'GET', '/cluster/status') or []:
            if item.get('type') == 'node' and item.get('local') and item.get('name'):
                name = str(item['name'])
    except ProxmoxError:
        pass
    if not name:
        try:
            names = [str(n.get('node')) for n in api(node, 'GET', '/nodes') or [] if n.get('node')]
        except ProxmoxError:
            names = []
        name = names[0] if len(names) == 1 else (node if node in names or not names else
                                                  next((n for n in names if n == NODES[node]['tls_name'].split('.')[0]), node))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', name):
        name = node
    with _cache_lock:
        _name_cache[node] = name
    return name


def wait_task(node, upid, timeout, what='task'):
    """Wait until a Proxmox task stopped; its exit status must be OK (or WARNINGS)."""
    if not isinstance(upid, str) or not upid.startswith('UPID:'):
        return None  # a synchronous call
    deadline = time.monotonic() + timeout
    path = '/nodes/' + node + '/tasks/' + urllib.parse.quote(upid, safe='') + '/status'
    while True:
        st = api(node, 'GET', path) or {}
        if st.get('status') == 'stopped':
            exit_status = str(st.get('exitstatus') or '')
            if exit_status == 'OK' or exit_status.startswith('WARNINGS'):
                return exit_status
            raise ProxmoxError(502, 'proxmox_task_failed', f'{what} on {node} failed: {_clean(exit_status, 300)}', upid=upid)
        if time.monotonic() >= deadline:
            raise ProxmoxError(504, 'proxmox_task_timeout', f'{what} on {node} did not finish within {int(timeout)} s', upid=upid)
        _sleep(TASK_POLL)


# ── permissions of the node token ────────────────────────────────────────────────────────────────────────────────────

CAPABILITIES = {
    'inventory': [('Sys.Audit', '/nodes/{node}'), ('VM.Audit', '/vms'), ('Datastore.Audit', '/storage')],
    'power': [('VM.PowerMgmt', '/vms')],
    'snapshot': [('VM.Snapshot', '/vms')],
    'rollback': [('VM.Snapshot.Rollback', '/vms')],
    'create_vm': [('VM.Allocate', '/vms'), ('VM.Config.Disk', '/vms'), ('VM.Config.CPU', '/vms'), ('VM.Config.Memory', '/vms'),
                  ('VM.Config.Network', '/vms'), ('VM.Config.Cloudinit', '/vms'), ('VM.Config.Options', '/vms'),
                  ('VM.Config.HWType', '/vms'), ('VM.PowerMgmt', '/vms'), ('Datastore.AllocateSpace', '/storage'),
                  ('Datastore.Audit', '/storage'), ('SDN.Use', '/sdn/zones/localnetwork')],
    'download_image': [('Datastore.AllocateTemplate', '/storage'), ('Sys.AccessNetwork', '/nodes/{node}')],
    'destroy_vm': [('VM.Allocate', '/vms'), ('VM.PowerMgmt', '/vms')],
    'audit_token': [('User.Modify', '/access/groups'), ('Realm.AllocateUser', '/access/realm/pve'), ('Permissions.Modify', '/')],
}


def has(perms, priv, path):
    """Whether the token has priv at path, using the most specific path /access/permissions lists for it."""
    best = None
    for p in perms or {}:
        if p == path or p == '/' or path.startswith(p.rstrip('/') + '/'):
            if best is None or len(p) > len(best):
                best = p
    if best is None or not isinstance(perms[best], dict) or priv not in perms[best]:
        return False
    return best == path or bool(perms[best][priv])


def token_permissions(node, real=None):
    """{path: {priv: propagate}} for each path CAPABILITIES checks, asked per path (Proxmox resolves inheritance,
    privilege separation and pools itself), with the overall map as fallback for paths it does not answer."""
    real = real or pve_name(node)
    paths = sorted({path.replace('{node}', real) for needs in CAPABILITIES.values() for _, path in needs})
    out = {}
    try:
        overall = api(node, 'GET', '/access/permissions')
        if isinstance(overall, dict):
            out.update({k: v for k, v in overall.items() if isinstance(v, dict)})
    except ProxmoxError:
        pass
    for path in paths:
        try:
            got = api(node, 'GET', '/access/permissions', {'path': path})
        except ProxmoxError:
            continue
        if isinstance(got, dict):
            out[path] = got.get(path) if isinstance(got.get(path), dict) else {}
    return out


def token_info(node):
    """{'user', 'privsep'} of the node token (privsep None when the token may not read its own entry)."""
    token_id = node_config(node)['token_id']
    user, _, name = token_id.partition('!')
    info = {'user': user, 'privsep': None}
    try:
        got = api(node, 'GET', f"/access/users/{urllib.parse.quote(user, safe='')}/token/{urllib.parse.quote(name, safe='')}") or {}
        if isinstance(got, dict) and 'privsep' in got:
            info['privsep'] = bool(int(got['privsep']))
    except (ProxmoxError, ValueError, TypeError):
        pass
    return info


def capabilities(perms, node, real=None):
    out = {}
    for cap, needs in CAPABILITIES.items():
        missing = []
        for priv, path in needs:
            path = path.replace('{node}', real or node)
            if priv == 'Sys.AccessNetwork' and (has(perms, 'Sys.Modify', '/') and has(perms, 'Sys.Audit', '/')):
                continue  # the older way to allow download-url
            if not has(perms, priv, path):
                missing.append(priv + ' on ' + path)
        out[cap] = {'ok': not missing, 'missing': missing}
    return out


# ── inventory ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _tags(value):
    return [t for t in re.split(r'[;, ]+', str(value or '')) if t]


def _ipconfig_ips(cfg):
    ips = []
    for key, value in (cfg or {}).items():
        if re.fullmatch(r'ipconfig\d+', key):
            m = re.search(r'(?:^|,)ip=([0-9.]+)(?:/\d+)?', str(value))
            if m:
                ips.append(m.group(1))
    return ips


def _ipconfig_gw(cfg):
    m = re.search(r'(?:^|,)gw=([0-9.]+)', str((cfg or {}).get('ipconfig0') or ''))
    return m.group(1) if m else None


def _macs(cfg):
    return [m.group(1).upper() for key, value in (cfg or {}).items() if re.fullmatch(r'net\d+', key)
            for m in [re.search(r'(?:^|,)(?:virtio|e1000|e1000e|vmxnet3|rtl8139|macaddr)=([0-9A-Fa-f:]{17})', str(value))] if m]


def _bridges_of(cfg):
    return [m.group(1) for key, value in (cfg or {}).items() if re.fullmatch(r'net\d+', key)
            for m in [re.search(r'(?:^|,)bridge=([A-Za-z0-9._-]+)', str(value))] if m]


def _agent_ips(node, vmid):
    try:
        data = api(node, 'GET', f'/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces', timeout=8) or {}
    except ProxmoxError:
        return []
    ips = []
    for iface in (data.get('result') or []) if isinstance(data, dict) else []:
        for addr in iface.get('ip-addresses') or []:
            ip = str(addr.get('ip-address') or '')
            if addr.get('ip-address-type') == 'ipv4' and not ip.startswith('127.'):
                ips.append(ip)
    return ips


def _vlans_of(cfg):
    """The VLAN tag of each NIC (None when untagged), in net0, net1, ... order."""
    out = []
    for key in sorted((k for k in (cfg or {}) if re.fullmatch(r'net\d+', k)), key=lambda k: int(k[3:])):
        m = re.search(r'(?:^|,)tag=(\d+)', str(cfg[key]))
        out.append(int(m.group(1)) if m else None)
    return out


def _version_tuple(text):
    m = re.match(r'(\d+)\.(\d+)', str(text or ''))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def node_inventory(node, db=None):
    """Everything the dashboard and the create form need about one node. Errors of single parts are listed, not raised."""
    out = {'node': node, 'service': service(node), 'label': NODES[node]['label'], 'ok': True, 'errors': [],
           'vms': [], 'containers': [], 'storages': [], 'images': [], 'bridges': [], 'templates': []}
    try:
        version = api(node, 'GET', '/version') or {}
    except ProxmoxError as exc:
        out.update(ok=False, error=exc.error, detail=exc.detail)
        return out
    out['version'] = {'version': _clean(version.get('version'), 40), 'release': _clean(version.get('release'), 20)}

    def part(what, fn):
        try:
            return fn()
        except ProxmoxError as exc:
            out['errors'].append({'part': what, 'error': exc.error, 'detail': exc.detail})
            return None

    real = pve_name(node)
    out['pve_node'] = real
    perms = part('permissions', lambda: token_permissions(node, real)) or {}
    out['permissions'] = capabilities(perms if isinstance(perms, dict) else {}, node, real)
    out['token'] = part('token', lambda: token_info(node)) or {}
    status = part('status', lambda: api(node, 'GET', f'/nodes/{node}/status'))
    if isinstance(status, dict):
        mem = status.get('memory') or {}
        out['status'] = {'cpu': status.get('cpu'), 'cpus': (status.get('cpuinfo') or {}).get('cpus'), 'mem': mem.get('used'),
                         'maxmem': mem.get('total'), 'uptime': status.get('uptime'), 'pveversion': _clean(status.get('pveversion'), 60)}
    nextid = part('nextid', lambda: api(node, 'GET', '/cluster/nextid'))
    try:
        out['nextid'] = int(nextid) if nextid is not None else None
    except (TypeError, ValueError):
        out['nextid'] = None
    managed = {}
    if db is not None:
        managed = {vmid: status for vmid, status in db.execute('SELECT vmid,status FROM managed_vms WHERE node=? AND status!=?', (node, 'destroyed'))}
    protected = protected_names()
    for vm in sorted(part('vms', lambda: api(node, 'GET', f'/nodes/{node}/qemu')) or [], key=lambda v: int(v.get('vmid') or 0)):
        vmid = int(vm.get('vmid') or 0)
        cfg = part(f'config {vmid}', lambda: api(node, 'GET', f'/nodes/{node}/qemu/{vmid}/config')) or {}
        tags = _tags(vm.get('tags') if vm.get('tags') is not None else cfg.get('tags'))
        agent_on = str(cfg.get('agent') or '').split(',')[0] in ('1', 'enabled=1')
        ips = _ipconfig_ips(cfg)
        guest_ips = _agent_ips(node, vmid) if agent_on and vm.get('status') == 'running' else []
        name = _clean(vm.get('name') or cfg.get('name'), 63)
        out['vms'].append({
            'vmid': vmid, 'name': name, 'status': _clean(vm.get('status'), 20), 'cpu': vm.get('cpu'), 'cpus': vm.get('cpus'),
            'mem': vm.get('mem'), 'maxmem': vm.get('maxmem'), 'maxdisk': vm.get('maxdisk'), 'uptime': vm.get('uptime'),
            'tags': tags[:20], 'template': bool(vm.get('template')), 'ip_config': ips, 'ip_guest': guest_ips,
            'ip': (guest_ips or ips or [None])[0], 'gateway': _ipconfig_gw(cfg), 'nameserver': _clean(cfg.get('nameserver'), 100) or None,
            'macs': _macs(cfg), 'bridges': _bridges_of(cfg), 'vlans': _vlans_of(cfg), 'agent': agent_on, 'managed': MANAGED_TAG in tags and vmid in managed,
            'managed_tag': MANAGED_TAG in tags, 'managed_status': managed.get(vmid), 'protected': name.lower() in protected,
            'reserved': vmid in RESERVED_VMIDS})
        if vm.get('template'):
            out['templates'].append({'vmid': vmid, 'name': name})
    for ct in part('containers', lambda: api(node, 'GET', f'/nodes/{node}/lxc')) or []:
        out['containers'].append({'vmid': int(ct.get('vmid') or 0), 'name': _clean(ct.get('name'), 63), 'status': _clean(ct.get('status'), 20),
                                  'mem': ct.get('mem'), 'maxmem': ct.get('maxmem'), 'tags': _tags(ct.get('tags'))})
    for st in part('storages', lambda: api(node, 'GET', f'/nodes/{node}/storage')) or []:
        content = sorted(c for c in str(st.get('content') or '').split(',') if c)
        out['storages'].append({'storage': _clean(st.get('storage'), 60), 'type': _clean(st.get('type'), 30), 'content': content,
                                'avail': st.get('avail'), 'total': st.get('total'), 'used': st.get('used'),
                                'active': bool(st.get('active', 1)), 'enabled': bool(st.get('enabled', 1)), 'shared': bool(st.get('shared'))})
    for st in out['storages']:
        if st['active'] and set(st['content']) & {'import', 'iso', 'vztmpl'}:
            items = part(f'content {st["storage"]}', lambda: api(node, 'GET', f'/nodes/{node}/storage/{st["storage"]}/content')) or []
            for item in items[:200]:
                if item.get('content') in ('import', 'iso', 'vztmpl'):
                    out['images'].append({'volid': _clean(item.get('volid'), 200), 'content': item.get('content'), 'size': item.get('size'),
                                          'format': _clean(item.get('format'), 20), 'storage': st['storage']})
    for net in part('network', lambda: api(node, 'GET', f'/nodes/{node}/network')) or []:
        if net.get('type') in ('bridge', 'OVSBridge'):
            out['bridges'].append({'iface': _clean(net.get('iface'), 30), 'type': net.get('type'), 'cidr': _clean(net.get('cidr'), 50) or None,
                                   'gateway': _clean(net.get('gateway'), 50) or None, 'active': bool(net.get('active')),
                                   'comments': _clean(net.get('comments'), 100) or None})
    out['bridges'].sort(key=lambda b: b['iface'])
    out['import_supported'] = _version_tuple(out['version']['version']) >= (8, 3) and any('import' in s['content'] for s in out['storages'])
    out['defaults'] = _defaults(out)
    return out


def _defaults(inv):
    """Suggestions for the create form: a gateway and DNS seen on VMs in an allowed network, the usual bridge and storage."""
    nets = allowed_nets()
    inside = lambda ip: any(_in(ip, n) for n in nets)
    gws = [v['gateway'] for v in inv['vms'] if v.get('gateway') and inside(v['gateway'])]
    dns = [v['nameserver'] for v in inv['vms'] if v.get('nameserver')]
    common = lambda xs: max(set(xs), key=xs.count) if xs else None
    bridges = [b for v in inv['vms'] if any(inside(ip) for ip in v.get('ip_config') or []) for b in v.get('bridges') or []]
    images_storages = [s['storage'] for s in inv['storages'] if 'images' in s['content'] and s['active'] and s['enabled']]
    gateway = common(gws) or next((str(ipaddress.ip_interface(b['cidr']).network.network_address + 1) for b in inv['bridges']
                                   if b.get('cidr') and inside(b['cidr'].split('/')[0])), None) or str(nets[0].network_address + 1)
    return {'gateway': gateway, 'dns': common(dns) or gateway, 'bridge': common(bridges) or ('vmbr0' if any(b['iface'] == 'vmbr0' for b in inv['bridges'])
            else (inv['bridges'][0]['iface'] if inv['bridges'] else 'vmbr0')),
            'storage': ('local-lvm' if 'local-lvm' in images_storages else (images_storages[0] if images_storages else None)),
            'cores': 2, 'memory': 2048, 'disk': 32, 'image': 'debian-13'}


def _in(ip, net):
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def inventory(nodes, db=None, refresh=False):
    out = []
    for node in nodes:
        with _cache_lock:
            hit = _inv_cache.get(node)
        if hit and not refresh and time.monotonic() - hit[0] < INVENTORY_SECONDS:
            out.append(hit[1])
            continue
        try:
            inv = node_inventory(node, db)
        except ProxmoxError as exc:
            inv = {'node': node, 'service': service(node), 'label': NODES[node]['label'], 'ok': False, 'error': exc.error, 'detail': exc.detail,
                   'vms': [], 'containers': [], 'storages': [], 'images': [], 'bridges': [], 'templates': [], 'errors': []}
        if inv.get('ok'):
            with _cache_lock:
                _inv_cache[node] = (time.monotonic(), inv)
        out.append(inv)
    return {'nodes': out, 'allowed_nets': [str(n) for n in allowed_nets()], 'reserved_vmids': sorted(RESERVED_VMIDS),
            'images': {k: v['label'] for k, v in IMAGES.items()}, 'managed_tag': MANAGED_TAG, 'limits': LIMITS,
            'protected_names': sorted(protected_names()), 'checked_at': int(time.time())}


def _invalidate(node):
    with _cache_lock:
        _inv_cache.pop(node, None)


def dynamic_servers(db):
    rows = db.execute('SELECT name,host,user,key_credential,host_fingerprint,node,vmid,created_by,created_at FROM servers_dynamic ORDER BY name')
    return [{'name': r[0], 'host': r[1], 'user': r[2], 'key_credential': r[3], 'host_fingerprint': r[4], 'node': r[5], 'vmid': r[6],
             'created_by': r[7], 'created_at': r[8]} for r in rows]


def managed_vms(db):
    rows = db.execute('''SELECT node,vmid,name,ip,status,key_credential,image_source,approval,created_by,created_at,updated_at,note
        FROM managed_vms ORDER BY created_at DESC LIMIT 200''')
    keys = ('node', 'vmid', 'name', 'ip', 'status', 'key_credential', 'image_source', 'approval', 'created_by', 'created_at', 'updated_at', 'note')
    return [dict(zip(keys, r)) for r in rows]


# ── request validation ───────────────────────────────────────────────────────────────────────────────────────────────

def _int(body, key, low, high, label):
    value = body.get(key)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProxmoxError(400, 'invalid_spec', f'{label} must be a whole number from {low} to {high}')
    return value


def _vmid(body):
    return _int(body, 'vmid', VMID_RANGE[0], 999999999, 'vmid')


def _vm(node, vmid):
    """status/current of a qemu VM on node, or 404."""
    try:
        return api(node, 'GET', f'/nodes/{node}/qemu/{vmid}/status/current') or {}
    except ProxmoxError as exc:
        if exc.error == 'proxmox_error' and exc.extra.get('http_status') in (500, 400, 404):
            raise ProxmoxError(404, 'unknown_vm', f'no VM {vmid} on {node}') from None
        raise


def server_names():
    return set(core.SERVERS) | set(core.SERVICES)


def credential_name(name):
    return 'SSH_KEY_' + name.upper().replace('-', '_')


def _probe_ip(ip, ports=(22, 80, 443), timeout=0.6):
    """True when something at ip answers (a connection or a refusal) on a common port."""
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
            return True
        except ConnectionRefusedError:
            return True
        except OSError:
            continue
        finally:
            sock.close()
    return False


_ip_answers = _probe_ip


def normalize_spec(db, body):
    """Validated create_vm spec (with live checks against both nodes) -> (spec, warnings). ProxmoxError on the first problem."""
    node = body.get('node')
    if node not in NODES:
        raise ProxmoxError(400, 'invalid_spec', 'node is one of ' + ', '.join(NODES))
    vmid = _int(body, 'vmid', *VMID_RANGE, 'vmid')
    if vmid in RESERVED_VMIDS:
        raise ProxmoxError(400, 'invalid_spec', f'VMID {vmid} is reserved (the inventory lists it as an existing server)')
    name = str(body.get('name') or '').strip().lower()
    if not _NAME.match(name) or any(name == r or name.startswith(r + '-') for r in _RESERVED_NAMES):
        raise ProxmoxError(400, 'invalid_spec', 'name is 2-32 lowercase letters, digits and -, starting with a letter (it becomes the '
                           'host name and the server name)')
    if name in server_names() or name in core.STATIC_SERVERS:
        raise ProxmoxError(409, 'name_taken', f'{name} is already a server or service name of the gateway')
    spec = {'node': node, 'vmid': vmid, 'name': name,
            'cores': _int(body, 'cores', *LIMITS['cores'], 'cores'),
            'memory': _int(body, 'memory', *LIMITS['memory'], 'memory (MiB)'),
            'disk': _int(body, 'disk', *LIMITS['disk'], 'disk (GiB)')}
    image = body.get('image') or 'debian-13'
    if image not in IMAGES:
        raise ProxmoxError(400, 'invalid_spec', 'image is one of ' + ', '.join(IMAGES))
    spec['image'] = image
    try:
        iface = ipaddress.ip_interface(str(body.get('ip') or '').strip())
    except ValueError:
        raise ProxmoxError(400, 'invalid_spec', 'ip is an address with prefix, e.g. 192.168.10.205/24') from None
    if iface.version != 4 or '/' not in str(body.get('ip')):
        raise ProxmoxError(400, 'invalid_spec', 'ip is an IPv4 address with prefix, e.g. 192.168.10.205/24')
    nets = allowed_nets()
    if not any(iface.network.subnet_of(n) or n.subnet_of(iface.network) for n in nets) or not any(iface.ip in n for n in nets):
        raise ProxmoxError(400, 'ip_not_allowed', f'{iface.ip} is outside the allowed networks ({", ".join(map(str, nets))}; PROXMOX_ALLOWED_NETS)')
    if iface.network.prefixlen < 31 and iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        raise ProxmoxError(400, 'invalid_spec', f'{iface.ip} is the network or broadcast address of {iface.network}')
    try:
        gateway = ipaddress.ip_address(str(body.get('gateway') or '').strip())
    except ValueError:
        raise ProxmoxError(400, 'invalid_spec', 'gateway is an IPv4 address, e.g. 192.168.10.1') from None
    if gateway not in iface.network or gateway == iface.ip:
        raise ProxmoxError(400, 'invalid_spec', f'gateway {gateway} must be another address in {iface.network}')
    dns = []
    raw_dns = body.get('dns')
    raw_dns = ' '.join(map(str, raw_dns)) if isinstance(raw_dns, list) else str(raw_dns or '')
    for part in re.split(r'[\s,]+', raw_dns.strip()):
        if part:
            try:
                dns.append(str(ipaddress.IPv4Address(part)))
            except ValueError:
                raise ProxmoxError(400, 'invalid_spec', f'dns: {_clean(part, 40)} is not an IPv4 address') from None
    if not 1 <= len(dns) <= 3:
        raise ProxmoxError(400, 'invalid_spec', 'dns is one to three IPv4 addresses')
    mac = str(body.get('macaddr') or '').strip().upper()
    if mac and (not _MAC.match(mac) or int(mac[:2], 16) & 1):
        raise ProxmoxError(400, 'invalid_spec', 'macaddr is a unicast MAC like BC:24:11:12:34:56, or empty for automatic')
    raw_vlan = body.get('vlan')
    if raw_vlan in (None, ''):
        vlan = 'auto'
    else:
        try:
            vlan = int(raw_vlan)
        except (TypeError, ValueError):
            vlan = -1
        if isinstance(raw_vlan, bool) or not 0 <= vlan <= 4094:
            raise ProxmoxError(400, 'invalid_spec', 'vlan is a VLAN tag 1-4094, 0 for untagged, or empty to take it from VMs in the same network')
    spec.update(ip=str(iface), gateway=str(gateway), dns=dns, macaddr=mac or None, onboot=body.get('onboot', True) is not False)
    # Live checks: the node's storage and bridge, and collisions on both nodes.
    warnings = []
    inv = inventory([node], db, refresh=True)['nodes'][0]
    if not inv.get('ok'):
        raise ProxmoxError(502, inv.get('error') or 'proxmox_unreachable', inv.get('detail') or f'{node} is not reachable')
    storage = str(body.get('storage') or '').strip()
    st = next((s for s in inv['storages'] if s['storage'] == storage), None)
    if not st:
        raise ProxmoxError(400, 'invalid_spec', f'storage {_clean(storage, 60)} does not exist on {node} (' +
                           ', '.join(s['storage'] for s in inv['storages'] if 'images' in s['content']) + ')')
    if 'images' not in st['content'] or not st['active'] or not st['enabled']:
        raise ProxmoxError(400, 'invalid_spec', f'storage {storage} on {node} does not hold VM disks (content images) or is not active')
    if isinstance(st.get('avail'), (int, float)) and st['avail'] < spec['disk'] * 1024 ** 3:
        raise ProxmoxError(400, 'storage_full', f'storage {storage} has {round(st["avail"] / 1024 ** 3, 1)} GiB free, the disk needs {spec["disk"]} GiB')
    bridge = str(body.get('bridge') or '').strip()
    if not any(b['iface'] == bridge for b in inv['bridges']):
        raise ProxmoxError(400, 'invalid_spec', f'bridge {_clean(bridge, 30)} does not exist on {node} (' + ', '.join(b['iface'] for b in inv['bridges']) + ')')
    spec.update(storage=storage, bridge=bridge)
    everything = [inv]
    for other in NODES:
        if other != node:
            o = inventory([other], db, refresh=True)["nodes"][0]
            if o.get('ok'):
                everything.append(o)
            else:
                warnings.append(f'{other} is not reachable: VMID, name and IP were only checked on {node}')
    used_ips, used_macs = {}, {}
    for i in everything:
        for vm in i['vms'] + i['containers']:
            if vm['vmid'] == vmid:
                raise ProxmoxError(409, 'vmid_taken', f'VMID {vmid} already exists on {i["node"]} ({vm.get("name") or "no name"})')
            if (vm.get('name') or '').lower() == name:
                raise ProxmoxError(409, 'name_taken', f'a guest named {name} already exists on {i["node"]} (VMID {vm["vmid"]})')
            for ip in (vm.get('ip_config') or []) + (vm.get('ip_guest') or []):
                used_ips[ip] = f'VM {vm["vmid"]} {vm.get("name") or ""} on {i["node"]}'.strip()
            for m in vm.get('macs') or []:
                used_macs[m] = f'VM {vm["vmid"]} on {i["node"]}'
    for server, (host, _) in core.SERVERS.items():
        used_ips.setdefault(host, 'server ' + server)
    for svc, (host, _) in core.SERVICES.items():
        if host and re.fullmatch(r'[0-9.]+', str(host)):
            used_ips.setdefault(host, 'service ' + svc)
    # VLAN: the bridge is often a trunk whose untagged traffic is another network (pve2's vmbr0 is VLAN 40). Without a tag the
    # VM lands there and nothing in its own network reaches it, so take the tag VMs in the same network use on this bridge.
    seen = {}
    for i in (inv,):  # bridge names are per node
        for vm in i['vms']:
            ips = (vm.get('ip_config') or []) + (vm.get('ip_guest') or [])
            if any(_in(x, iface.network) for x in ips):
                for b, t in zip(vm.get('bridges') or [], vm.get('vlans') or []):
                    if b == bridge:
                        seen.setdefault(t, []).append(f'{vm["vmid"]} {vm.get("name") or ""}'.strip())
    if vlan == 'auto':
        tags = sorted((t for t in seen if t is not None), key=lambda t: -len(seen[t]))
        br = next((b for b in inv['bridges'] if b['iface'] == bridge), {})
        bridge_net = None
        try:
            bridge_net = ipaddress.ip_interface(br['cidr']).network if br.get('cidr') else None
        except ValueError:
            pass
        if tags:
            vlan = tags[0]
            warnings.append(f'VLAN {vlan} taken from VMs in {iface.network} on {bridge} (' + ', '.join(seen[vlan][:4]) + ')')
        elif None in seen or (bridge_net is not None and iface.ip in bridge_net):
            vlan = 0
        elif bridge_net is not None:
            raise ProxmoxError(400, 'vlan_required', f'{bridge} on {node} is in {bridge_net}, but {iface.ip} is in {iface.network} and no VM '
                               f'there shows its VLAN tag: give vlan (the tag of {iface.network}), or 0 for untagged')
        else:
            vlan = 0
            warnings.append(f'no VLAN tag: nothing shows which tag {iface.network} uses on {bridge}; give vlan if it is a tagged network')
    elif seen and (vlan or None) not in seen:
        warnings.append(f'VMs in {iface.network} on {bridge} use ' + ', '.join('untagged' if t is None else f'VLAN {t}' for t in seen))
    spec['vlan'] = vlan or None
    ip = str(iface.ip)
    if ip in used_ips:
        raise ProxmoxError(409, 'ip_in_use', f'{ip} is already used by {used_ips[ip]}')
    if ip == str(gateway) or ip in dns:
        raise ProxmoxError(409, 'ip_in_use', f'{ip} is the gateway or a DNS server')
    if mac and mac in used_macs:
        raise ProxmoxError(409, 'mac_in_use', f'{mac} is already used by {used_macs[mac]}')
    if _ip_answers(ip):
        raise ProxmoxError(409, 'ip_in_use', f'something at {ip} answers on the network (SSH, HTTP or HTTPS port); pick a free address')
    if db is not None and store.configured() and store.meta(db, credential_name(name)):
        raise ProxmoxError(409, 'name_taken', f'the credential {credential_name(name)} already exists (an earlier server with this name?)')
    plan = image_plan(inv, spec)
    spec['image_source'] = plan['source']
    if plan['source'] is None:
        raise ProxmoxError(409, 'no_image_source', plan['detail'])
    for need in ('create_vm',) + (('download_image',) if plan['source'] == 'download' else ()):
        cap = (inv.get('permissions') or {}).get(need) or {}
        if cap and not cap.get('ok'):
            warnings.append(f'the {node} token seems to lack ' + ', '.join(cap['missing']) + ' (Proxmox decides)')
    return spec, warnings, plan


def image_plan(inv, spec):
    """Where the disk comes from: an image already in an import storage, a download into one, or a debian-13* template."""
    image = IMAGES[spec['image']]
    imports = [s for s in inv['storages'] if 'import' in s['content'] and s['active'] and s['enabled']]
    if inv.get('import_supported') and imports:
        imports.sort(key=lambda s: (s['storage'] != spec.get('storage'), s['storage'] != 'local', s['storage']))
        for s in imports:
            volid = f'{s["storage"]}:import/{image["file"]}'
            if any(i['volid'] == volid for i in inv['images']):
                return {'source': 'import', 'storage': s['storage'], 'volid': volid, 'present': True}
        s = imports[0]
        return {'source': 'download', 'storage': s['storage'], 'volid': f'{s["storage"]}:import/{image["file"]}', 'present': False}
    template = next((t for t in inv['templates'] if image['template'].match(t['name'] or '')), None)
    if template:
        return {'source': 'template', 'template': template['vmid'], 'template_name': template['name']}
    version = (inv.get('version') or {}).get('version') or '?'
    return {'source': None, 'detail': (
        f'{inv["node"]} (PVE {version}) has no storage with the "import" content type and no template named debian-13*. '
        'Either enable Import on a storage (Datacenter > Storage > local > Content: Import; needs PVE 8.3 or later), or create a '
        f'Debian 13 cloud-init template on {inv["node"]} named debian-13-cloud.')}


def approval_text(req):
    """The exact text the owner approves; its digest (with the service name) is what the approval unlocks."""
    op, node = req['op'], req['node']
    if op == 'create_vm':
        s = req['spec']
        # Stable between approval and run: whether the image is already there (or another create fetched it) does not matter.
        source = ('clone of template ' + str(req['plan'].get('template_name')) if s['image_source'] == 'template'
                  else 'Debian cloud image, downloaded with its SHA-512 check unless already on the node')
        return '\n'.join([
            f'proxmox create_vm on {node}',
            f'vmid: {s["vmid"]}', f'name: {s["name"]}',
            f'cpu: {s["cores"]} cores', f'memory: {s["memory"]} MiB', f'disk: {s["disk"]} GiB on {s["storage"]}',
            f'image: {IMAGES[s["image"]]["file"]} ({source})',
            f'network: {s["bridge"]}' + (f' VLAN {s["vlan"]}' if s.get('vlan') else ' untagged') + f', {s["ip"]}, gateway {s["gateway"]}, dns {" ".join(s["dns"])}, mac {s["macaddr"] or "automatic"}',
            f'start on boot: {"yes" if s["onboot"] else "no"}',
            f'login: {CIUSER} with a new gateway-held SSH key ({credential_name(s["name"])}), passwordless sudo',
            f'afterwards: server {s["name"]} registered at level 0, tag {MANAGED_TAG}'])
    if op == 'destroy_vm':
        t = req['target']
        return '\n'.join([f'proxmox destroy_vm on {node}', f'vmid: {t["vmid"]}', f'name: {t["name"]}',
                          'stop it, destroy it and purge its disks (cannot be undone)',
                          f'unregister server {t["name"]} and delete credential {t["key_credential"] or credential_name(t["name"])}'])
    if op == 'create_audit_token':
        names = audit_secret_names(node)
        return '\n'.join([f'proxmox create_audit_token on {node}',
                          f'user: {AUDIT_USER} with role PVEAuditor on / (read-only)',
                          f'token: {AUDIT_USER}!{AUDIT_TOKEN} (privilege separated, PVEAuditor on /)' + (' — replaces the existing token' if req.get('rotate') else ''),
                          f'stored in the generated secrets as {names[0]} and {names[1]}'])
    raise ValueError(op)


def check(db, op, body, level, free, admin):
    """Validate one POST /v1/proxmox request at this level -> req {op, node, need, approval, ...}. ProxmoxError otherwise."""
    node = body.get('node')
    if node not in NODES:
        raise ProxmoxError(400, 'unknown_node', 'node is one of ' + ', '.join(NODES))
    if op in ADMIN_ONLY and not admin:
        raise ProxmoxError(403, 'policy_denied', op + ' is for the dashboard')
    if op not in OPERATIONS and op not in ADMIN_ONLY:
        raise ProxmoxError(400, 'invalid_operation', 'operation is one of ' + ', '.join(list(OPERATIONS) + list(ADMIN_ONLY)))
    need = OPERATIONS.get(op, 6)
    if not admin and not (level >= need or (free and need <= 5)):
        raise ProxmoxError(403, 'policy_denied', f'{op} needs level {need} on {service(node)}; this agent has {level}', level=level)
    req = {'op': op, 'node': node, 'need': need, 'admin': admin, 'request': {'node': node, 'operation': op}}
    if op in POWER or op in ('snapshots', 'snapshot_create', 'snapshot_rollback', 'destroy_vm', 'onboard'):
        req['vmid'] = _vmid(body)
        req['request']['vmid'] = req['vmid']
    if op in ('snapshot_create', 'snapshot_rollback'):
        snap = str(body.get('snapname') or '')
        if not _SNAP.match(snap) or snap == 'current':
            raise ProxmoxError(400, 'invalid_snapname', 'snapname is 2-40 letters, digits, - and _, starting with a letter')
        req['snapname'] = req['request']['snapname'] = snap
        if op == 'snapshot_create':
            req['description'] = _clean(body.get('description'), 200)
            if req['description']:
                req['request']['description'] = req['description']
    if op in POWER or op == 'snapshot_rollback':
        vm = _vm(node, req['vmid'])
        if not admin and str(vm.get('name') or '').lower() in protected_names():
            raise ProxmoxError(403, 'protected_vm', f'VM {req["vmid"]} ({vm.get("name")}) is protected (PROXMOX_PROTECTED_NAMES): only the owner can change it')
        req['vm_name'] = _clean(vm.get('name'), 63)
    if op in ('create_vm', 'preview_create'):
        req['spec'], req['warnings'], req['plan'] = normalize_spec(db, body)
        req['request'].update({k: v for k, v in req['spec'].items() if k not in ('node', 'image_source')})
        req['request']['operation'] = 'create_vm'
    if op in ('destroy_vm', 'onboard'):
        row = db.execute('SELECT node,vmid,name,ip,status,key_credential FROM managed_vms WHERE node=? AND vmid=?', (node, req['vmid'])).fetchone()
        if not row or row[4] == 'destroyed':
            raise ProxmoxError(403, 'not_managed', f'VM {req["vmid"]} on {node} was not created by this gateway; it is never '
                               + ('destroyed' if op == 'destroy_vm' else 'onboarded') + ' here')
        req['target'] = {'node': row[0], 'vmid': row[1], 'name': row[2], 'ip': row[3], 'status': row[4], 'key_credential': row[5]}
    if op == 'create_audit_token':
        req['rotate'] = body.get('rotate') is True
        if req['rotate']:
            req['request']['rotate'] = True
    req['approval'] = needs_approval(op, free, admin)
    if req['approval'] or op == 'preview_create':
        req['text'] = approval_text({**req, 'op': 'create_vm' if op == 'preview_create' else op})
    return req


def needs_approval(op, free, admin):
    if admin:
        return False  # the dashboard approver is the human acting
    if op == 'destroy_vm':
        return True  # every time, also at level 6
    return op in ('create_vm', 'create_audit_token') and not free


# ── operations ───────────────────────────────────────────────────────────────────────────────────────────────────────

def run(db, req, who, approval=None):
    op, node = req['op'], req['node']
    if op == 'inventory':
        return inventory([node], db)
    if op == 'preview_create':
        return {'valid': True, 'spec': req['spec'], 'approval_text': req['text'], 'warnings': req['warnings'],
                'image': {k: v for k, v in req['plan'].items() if k != 'detail'}, 'request': req['request']}
    if op == 'snapshots':
        items = api(node, 'GET', f'/nodes/{node}/qemu/{req["vmid"]}/snapshot') or []
        return {'node': node, 'vmid': req['vmid'], 'snapshots': [
            {'name': _clean(s.get('name'), 40), 'description': _clean(s.get('description'), 200), 'snaptime': s.get('snaptime'),
             'parent': _clean(s.get('parent'), 40)} for s in items if s.get('name') != 'current']}
    if op in POWER:
        upid = api(node, 'POST', f'/nodes/{node}/qemu/{req["vmid"]}/status/{op}', {})
        wait_task(node, upid, 180 if op == 'shutdown' else 120, op)
        _invalidate(node)
        state = (_vm(node, req['vmid']) or {}).get('status')
        return {'node': node, 'vmid': req['vmid'], 'name': req.get('vm_name'), 'operation': op, 'status': 'done', 'vm_status': state, 'upid': upid}
    if op == 'snapshot_create':
        upid = api(node, 'POST', f'/nodes/{node}/qemu/{req["vmid"]}/snapshot',
                   {'snapname': req['snapname'], 'description': req.get('description') or f'by {who}'})
        wait_task(node, upid, 300, 'snapshot')
        return {'node': node, 'vmid': req['vmid'], 'snapname': req['snapname'], 'status': 'created', 'upid': upid}
    if op == 'snapshot_rollback':
        upid = api(node, 'POST', f'/nodes/{node}/qemu/{req["vmid"]}/snapshot/{req["snapname"]}/rollback', {})
        wait_task(node, upid, 300, 'rollback')
        _invalidate(node)
        return {'node': node, 'vmid': req['vmid'], 'snapname': req['snapname'], 'status': 'rolled_back', 'upid': upid}
    if op == 'create_vm':
        return create_vm(db, req, who, approval)
    if op == 'destroy_vm':
        return destroy_vm(db, req, who)
    if op == 'onboard':
        return onboard_again(db, req, who)
    if op == 'create_audit_token':
        return create_audit_token(req, who)
    raise ProxmoxError(400, 'invalid_operation')


def _claim(node):
    if not _locks[node].acquire(blocking=False):
        raise ProxmoxError(409, 'node_busy', f'another create or destroy is running on {node}; try again when it is done')


def fetch_checksum(image):
    """The SHA-512 of the image from Debian's SHA512SUMS next to it (fetched by the gateway with normal TLS checks)."""
    fetch = _fetch_url or (lambda url: urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'hla-gateway'}), timeout=20).read(1024 * 1024))
    try:
        text = fetch(image['sums']).decode('utf-8', 'replace')
    except Exception as exc:  # noqa: BLE001
        raise ProxmoxError(502, 'checksum_unavailable', f'cannot read {image["sums"]} ({type(exc).__name__})') from None
    found = None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _SUM_LINE.match(line.strip())
        if not m:
            raise ProxmoxError(502, 'checksum_invalid', 'SHA512SUMS has a line that is not "<128 hex>  <file name>"')
        if m.group(2) == image['file']:
            if found and found != m.group(1):
                raise ProxmoxError(502, 'checksum_invalid', 'SHA512SUMS lists ' + image['file'] + ' twice with different sums')
            found = m.group(1)
    if not found:
        raise ProxmoxError(502, 'checksum_invalid', 'SHA512SUMS does not list ' + image['file'])
    return found


def new_keypair():
    """(OpenSSH private key text, 'ssh-ed25519 AAAA... comment') generated in the gateway."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return private, public


def _record(db, node, vmid, **fields):
    now = int(time.time())
    row = db.execute('SELECT 1 FROM managed_vms WHERE node=? AND vmid=?', (node, vmid)).fetchone()
    if row:
        sets = ','.join(k + '=?' for k in fields) + ',updated_at=?'
        db.execute(f'UPDATE managed_vms SET {sets} WHERE node=? AND vmid=?', (*fields.values(), now, node, vmid))
    else:
        cols = ['node', 'vmid', 'created_at', 'updated_at'] + list(fields)
        db.execute(f'INSERT INTO managed_vms({",".join(cols)}) VALUES ({",".join("?" * len(cols))})', (node, vmid, now, now, *fields.values()))


def create_vm(db, req, who, approval=None):
    spec, plan, node = req['spec'], req['plan'], req['node']
    if not store.configured():
        raise ProxmoxError(503, 'credentials_not_configured', 'the SSH key of a new VM goes into the credential store: set GATEWAY_CREDENTIAL_KEY first')
    _claim(node)
    steps, vmid, name = [], spec['vmid'], spec['name']
    key_name = credential_name(name)
    try:
        image = IMAGES[spec['image']]
        if plan['source'] == 'download':
            checksum = fetch_checksum(image)
            upid = api(node, 'POST', f'/nodes/{node}/storage/{plan["storage"]}/download-url',
                       {'url': image['url'], 'content': 'import', 'filename': image['file'], 'checksum': checksum,
                        'checksum-algorithm': 'sha512', 'verify-certificates': True})
            wait_task(node, upid, 1800, 'image download')
            steps.append(f'downloaded {image["file"]} to {plan["storage"]} (sha512 {checksum[:12]}…)')
        private, public = new_keypair()
        store.put(db, key_name, private, kind='other', description=f'SSH key of {name} (VM {vmid} on {node}), made by the gateway', actor=who)
        private = None
        steps.append(f'stored a new SSH key as {key_name}')
        stamp = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
        description = (f'Created by the Homelab Agents platform for {who} on {stamp}. '
                       + (f'Approved by {approval["by"]} (approval {approval["digest"]}). ' if approval else '')
                       + f'Managed by the gateway (tag {MANAGED_TAG}); server {name}; SSH user {CIUSER}.')
        net = (f'virtio,bridge={spec["bridge"]}' + (f',macaddr={spec["macaddr"]}' if spec['macaddr'] else '')
               + (f',tag={spec["vlan"]}' if spec.get('vlan') else ''))
        cloud = {'ciuser': CIUSER, 'sshkeys': urllib.parse.quote(public + '\n', safe=''),  # PVE wants it URL-encoded (then form-encoded)
                 'ipconfig0': f'ip={spec["ip"]},gw={spec["gateway"]}', 'nameserver': ' '.join(spec['dns']),
                 'serial0': 'socket', 'vga': 'serial0', 'agent': 'enabled=1', 'tags': MANAGED_TAG, 'description': description,
                 'onboot': spec['onboot'], 'cores': spec['cores'], 'memory': spec['memory'], 'net0': net}
        _record(db, node, vmid, name=name, ip=spec['ip'].split('/')[0], status='creating', key_credential=key_name,
                image_source=plan['source'], approval=(approval or {}).get('digest', ''), created_by=who)
        if plan['source'] in ('import', 'download'):
            params = {'vmid': vmid, 'name': name, 'ostype': 'l26', 'sockets': 1, 'cpu': 'x86-64-v2-AES', 'scsihw': 'virtio-scsi-single',
                      'scsi0': f'{spec["storage"]}:0,import-from={plan["volid"]},discard=on,iothread=1',
                      'ide2': f'{spec["storage"]}:cloudinit', 'boot': 'order=scsi0', **cloud}
            upid = api(node, 'POST', f'/nodes/{node}/qemu', params)
            wait_task(node, upid, 900, 'VM creation')
            disk = 'scsi0'
            steps.append(f'created VM {vmid} from {plan["volid"]}')
        else:
            upid = api(node, 'POST', f'/nodes/{node}/qemu/{plan["template"]}/clone',
                       {'newid': vmid, 'name': name, 'full': True, 'storage': spec['storage'], 'description': description})
            wait_task(node, upid, 1200, 'template clone')
            config = api(node, 'GET', f'/nodes/{node}/qemu/{vmid}/config') or {}
            has_ci = any('cloudinit' in str(v) for k, v in config.items() if re.fullmatch(r'(ide|scsi|sata)\d+', k))
            update = dict(cloud, **({} if has_ci else {'ide2': f'{spec["storage"]}:cloudinit'}))
            wait_task(node, api(node, 'POST', f'/nodes/{node}/qemu/{vmid}/config', update), 120, 'configuration')
            disk = next((k for k in ('scsi0', 'virtio0', 'sata0') if config.get(k)), 'scsi0')
            steps.append(f'cloned template {plan["template_name"]} ({plan["template"]}) to VM {vmid}')
        wait_task(node, api(node, 'PUT', f'/nodes/{node}/qemu/{vmid}/resize', {'disk': disk, 'size': f'{spec["disk"]}G'}), 300, 'disk resize')
        steps.append(f'disk {disk} resized to {spec["disk"]} GiB')
        wait_task(node, api(node, 'POST', f'/nodes/{node}/qemu/{vmid}/status/start', {}), 180, 'start')
        steps.append('started')
        _invalidate(node)
        _record(db, node, vmid, status='onboarding')
        try:
            server = onboard(db, node, vmid, name, spec['ip'].split('/')[0], key_name, who)
        except ProxmoxError as exc:
            _record(db, node, vmid, status='onboarding_failed', note=_clean(exc.detail, 300))
            raise ProxmoxError(exc.status, exc.error, f'VM {vmid} ({name}) was created and started, but onboarding failed: {exc.detail} '
                               '(retry with Onboard on the dashboard, or destroy it)', steps=steps, vmid=vmid) from None
        steps.extend(server.pop('steps'))
        _record(db, node, vmid, status='ready', note='')
        return {'node': node, 'vmid': vmid, 'name': name, 'status': 'created', 'ip': spec['ip'].split('/')[0],
                'image_source': plan['source'], 'server': server, 'steps': steps}
    except ProxmoxError as exc:
        if 'steps' not in exc.extra:
            exc.extra['steps'] = steps
        if db.execute('SELECT status FROM managed_vms WHERE node=? AND vmid=?', (node, vmid)).fetchone() == ('creating',):
            try:
                _vm(node, vmid)
                exists = True
            except ProxmoxError as probe:
                exists = probe.error != 'unknown_vm'
            if exists:  # destroyable later: it is in managed_vms and tagged
                _record(db, node, vmid, status='create_failed', note=_clean(exc.detail, 300))
            else:  # nothing was created: forget it, and its key
                db.execute('DELETE FROM managed_vms WHERE node=? AND vmid=?', (node, vmid))
                store.delete(db, key_name)
        elif not db.execute('SELECT 1 FROM managed_vms WHERE node=? AND vmid=?', (node, vmid)).fetchone() and store.configured():
            store.delete(db, key_name)  # failed before the VM was asked for (e.g. the image download)
        raise
    finally:
        _locks[node].release()


ONBOARD_SCRIPT = ("cloud-init status --wait >/dev/null 2>&1; echo HLA_USER=$(id -un); "
                  "if sudo -n true 2>/dev/null; then echo HLA_SUDO_OK; else echo HLA_SUDO_NO; exit 3; fi; "
                  "if dpkg-query -W -f='${Status}' qemu-guest-agent 2>/dev/null | grep -q 'ok installed'; then echo HLA_QGA_PRESENT; "
                  "else sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1; "
                  "sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qemu-guest-agent >/dev/null 2>&1 "
                  "&& echo HLA_QGA_INSTALLED || echo HLA_QGA_FAILED; fi; "
                  "sudo -n systemctl enable --now qemu-guest-agent >/dev/null 2>&1 && echo HLA_QGA_RUNNING; true")


def onboard(db, node, vmid, name, ip, key_name, who, wait=None):
    """First contact with a VM the gateway created: record its ed25519 host key, check the user and sudo, install the guest
    agent, register it as a server at level 0. Returns the server record (with steps)."""
    import io
    import paramiko
    deadline = time.monotonic() + (SSH_WAIT if wait is None else wait)
    private = store.get(db, key_name)
    if not private:
        raise ProxmoxError(500, 'key_missing', f'the credential {key_name} is gone')
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private))
    private = None
    fingerprint, result, last = None, None, ''
    while True:
        try:
            seen = _host_fingerprint(ip)
            if fingerprint and seen != fingerprint:
                raise ProxmoxError(502, 'host_key_changed', f'the SSH host key of {ip} changed during onboarding ({fingerprint} -> {seen}); not trusted')
            fingerprint = seen
            result = _run_ssh(ip, key, [fingerprint], ONBOARD_SCRIPT, timeout=280, max_output=4000)
            break
        except ProxmoxError:
            raise
        except Exception as exc:  # noqa: BLE001 - not up yet, key not installed yet: try again until the deadline
            last = type(exc).__name__ + (': ' + _clean(exc, 80) if isinstance(exc, RuntimeError) else '')
        if time.monotonic() >= deadline:
            raise ProxmoxError(504, 'ssh_timeout', f'{ip} did not accept the gateway key over SSH within {SSH_WAIT} s ({last or "no answer"})')
        _sleep(5)
    out = result.get('stdout') or ''
    user = (re.search(r'HLA_USER=(\S+)', out) or [None, None])[1]
    if user != CIUSER:
        raise ProxmoxError(502, 'onboarding_failed', f'logged in as {user!r}, expected {CIUSER}')
    if 'HLA_SUDO_OK' not in out:
        raise ProxmoxError(502, 'onboarding_failed', f'{CIUSER} has no passwordless sudo on {ip}')
    steps = [f'first SSH contact: host key {fingerprint} recorded', f'{CIUSER} logs in with the gateway key and has sudo -n']
    qga = 'present' if 'HLA_QGA_PRESENT' in out else 'installed' if 'HLA_QGA_INSTALLED' in out else 'failed'
    steps.append('qemu-guest-agent ' + qga + (' and running' if 'HLA_QGA_RUNNING' in out else ' (not running)'))
    register(db, name, ip, key_name, fingerprint, node, vmid, who)
    steps.append(f'registered as server {name} at level 0')
    return {'name': name, 'host': ip, 'host_fingerprint': fingerprint, 'level': 0, 'guest_agent': qga, 'steps': steps}


def register(db, name, ip, key_name, fingerprint, node, vmid, who):
    if name in core.STATIC_SERVERS or name in core.SERVICES:
        raise ProxmoxError(409, 'name_taken', f'{name} is a static server or service name')
    db.execute('''INSERT INTO servers_dynamic(name,host,user,key_credential,host_fingerprint,node,vmid,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET host=excluded.host,key_credential=excluded.key_credential,
        host_fingerprint=excluded.host_fingerprint,node=excluded.node,vmid=excluded.vmid''',
               (name, ip, CIUSER, key_name, fingerprint, node, vmid, str(who)[:120], int(time.time())))
    db.execute("INSERT INTO server_policy(server,level) VALUES (?,0) ON CONFLICT(server) DO UPDATE SET level=0, compose_projects='[]'", (name,))
    core.load_dynamic(db)


def unregister(db, name):
    db.execute('DELETE FROM servers_dynamic WHERE name=?', (name,))
    if name not in core.STATIC_SERVERS:
        db.execute('DELETE FROM server_policy WHERE server=?', (name,))
        db.execute('DELETE FROM deploy_apps WHERE server=?', (name,))
    core.load_dynamic(db)


def onboard_again(db, req, who):
    t = req['target']
    if t['status'] == 'ready' and t['name'] in core.DYNAMIC:
        raise ProxmoxError(409, 'already_onboarded', f'{t["name"]} is already a registered server')
    _claim(req['node'])
    try:
        server = onboard(db, req['node'], t['vmid'], t['name'], t['ip'], t['key_credential'] or credential_name(t['name']), who)
        _record(db, req['node'], t['vmid'], status='ready', note='')
        return {'node': req['node'], 'vmid': t['vmid'], 'name': t['name'], 'status': 'onboarded', 'server': server, 'steps': server.pop('steps')}
    except ProxmoxError as exc:
        _record(db, req['node'], t['vmid'], status='onboarding_failed', note=_clean(exc.detail, 300))
        raise
    finally:
        _locks[req['node']].release()


def destroy_vm(db, req, who):
    node, t = req['node'], req['target']
    vmid = t['vmid']
    _claim(node)
    steps = []
    try:
        try:
            config = api(node, 'GET', f'/nodes/{node}/qemu/{vmid}/config') or {}
        except ProxmoxError as exc:
            if exc.extra.get('http_status') in (400, 404, 500):
                raise ProxmoxError(404, 'unknown_vm', f'VM {vmid} no longer exists on {node}') from None
            raise
        if MANAGED_TAG not in _tags(config.get('tags')):
            raise ProxmoxError(403, 'not_managed', f'VM {vmid} on {node} is not tagged {MANAGED_TAG}; refusing to destroy it')
        if str(config.get('name') or '') != t['name']:
            raise ProxmoxError(409, 'vm_changed', f'VM {vmid} is now named {_clean(config.get("name"), 40)!r}, not {t["name"]}; refusing')
        state = (_vm(node, vmid) or {}).get('status')
        if state != 'stopped':
            wait_task(node, api(node, 'POST', f'/nodes/{node}/qemu/{vmid}/status/stop', {}), 120, 'stop')
            steps.append('stopped')
        upid = api(node, 'DELETE', f'/nodes/{node}/qemu/{vmid}', {'purge': 1, 'destroy-unreferenced-disks': 1})
        wait_task(node, upid, 600, 'destroy')
        steps.append(f'destroyed VM {vmid} and purged its disks')
        unregister(db, t['name'])
        steps.append(f'server {t["name"]} unregistered')
        key_name = t['key_credential'] or credential_name(t['name'])
        if store.delete(db, key_name):
            steps.append(f'credential {key_name} deleted')
        _record(db, node, vmid, status='destroyed', note=f'destroyed by {who}')
        _invalidate(node)
        return {'node': node, 'vmid': vmid, 'name': t['name'], 'status': 'destroyed', 'steps': steps}
    finally:
        _locks[node].release()


def audit_secret_names(node):
    return ('PVE_' + _env_key(node) + '_AUDIT_TOKEN_ID', 'PVE_' + _env_key(node) + '_AUDIT_TOKEN_SECRET')


def pveum_commands(node):
    full = f'{AUDIT_USER}!{AUDIT_TOKEN}'
    return [f'pveum user add {AUDIT_USER} --comment "Read-only monitoring (Homelab Agents)"',
            f'pveum acl modify / --users {AUDIT_USER} --roles PVEAuditor',
            f'pveum user token add {AUDIT_USER} {AUDIT_TOKEN} --privsep 1 --comment "Homelab Agents monitoring"',
            f"pveum acl modify / --tokens '{full}' --roles PVEAuditor",
            f'# then store the token id ({full}) and the secret Proxmox printed in the generated secrets (gateway CLI: secrets set --generated NAME) as '
            + ' and '.join(audit_secret_names(node))]


def create_audit_token(req, who):
    node = req['node']
    id_name, secret_name = audit_secret_names(node)
    if not agent_secrets.configured():
        raise ProxmoxError(503, 'secrets_not_configured', 'the token goes into the generated secrets: ' + agent_secrets.not_configured_detail())
    real = pve_name(node)
    cap = capabilities(token_permissions(node, real), node, real)['audit_token']
    if not cap['ok']:
        raise ProxmoxError(403, 'insufficient_privileges', f'the {node} token lacks ' + ', '.join(cap['missing'])
                           + '. Run these on the node as root instead:', commands=pveum_commands(node))
    users = api(node, 'GET', '/access/users') or []
    steps = []
    if not any(u.get('userid') == AUDIT_USER for u in users):
        api(node, 'POST', '/access/users', {'userid': AUDIT_USER, 'comment': 'Read-only monitoring (Homelab Agents)'})
        steps.append(f'created user {AUDIT_USER}')
    api(node, 'PUT', '/access/acl', {'path': '/', 'roles': 'PVEAuditor', 'users': AUDIT_USER})
    steps.append('PVEAuditor on / for the user')
    quoted = urllib.parse.quote(AUDIT_USER, safe='')
    tokens = api(node, 'GET', f'/access/users/{quoted}/token') or []
    try:
        stored = agent_secrets.load(force=True)
    except RuntimeError as exc:
        raise ProxmoxError(502, 'secrets_unavailable', str(exc)[:200]) from None
    exists = any(t.get('tokenid') == AUDIT_TOKEN for t in tokens)
    if exists and not req.get('rotate'):
        if id_name in stored and secret_name in stored:
            return {'node': node, 'status': 'exists', 'user': AUDIT_USER, 'token_id_secret': '{{secret:' + id_name + '}}',
                    'token_secret': '{{secret:' + secret_name + '}}', 'steps': steps + ['the token and its secrets already exist']}
        raise ProxmoxError(409, 'token_exists', f'{AUDIT_USER}!{AUDIT_TOKEN} exists but its secret is not in the generated secrets; '
                           'ask again with rotate: true to replace it (anything using the old token stops working)')
    if exists:
        api(node, 'DELETE', f'/access/users/{quoted}/token/{AUDIT_TOKEN}')
        steps.append('removed the old token')
    made = api(node, 'POST', f'/access/users/{quoted}/token/{AUDIT_TOKEN}', {'privsep': 1, 'comment': f'Homelab Agents monitoring, by {who}'[:120]}) or {}
    full, value = made.get('full-tokenid') or f'{AUDIT_USER}!{AUDIT_TOKEN}', made.get('value')
    if not value:
        raise ProxmoxError(502, 'proxmox_error', 'Proxmox did not return the token secret')
    api(node, 'PUT', '/access/acl', {'path': '/', 'roles': 'PVEAuditor', 'tokens': full})
    steps.append(f'token {full} (privilege separated) with PVEAuditor on /')
    try:
        agent_secrets.store(id_name, full, who, f'Proxmox {node} read-only token id', replace=True)
        agent_secrets.store(secret_name, value, who, f'Proxmox {node} read-only token secret ({full})', replace=True)
    except agent_secrets.SecretError as exc:
        raise ProxmoxError(exc.status, exc.error, f'the token was created but could not be stored: {exc.detail}') from None
    finally:
        value = None
    steps.append(f'stored as {id_name} and {secret_name} in the generated secrets')
    return {'node': node, 'status': 'created', 'user': AUDIT_USER, 'token_id_secret': '{{secret:' + id_name + '}}',
            'token_secret': '{{secret:' + secret_name + '}}', 'steps': steps,
            'usage': f'e.g. prometheus-pve-exporter: user: {AUDIT_USER}, token_name: {AUDIT_TOKEN}, '
                     'token_value: {{secret:' + secret_name + '}}, verify_ssl: true with the node CA'}


def summary(req, result=None, error=None):
    """One line for the audit log (never a secret)."""
    op, node = req['op'], req['node']
    parts = [op, node]
    if req.get('vmid'):
        parts.append(f'vmid {req["vmid"]}' + (f' ({req["vm_name"]})' if req.get('vm_name') else ''))
    if req.get('snapname'):
        parts.append('snapshot ' + req['snapname'])
    if req.get('spec'):
        s = req['spec']
        parts.append(f'vmid {s["vmid"]} {s["name"]} {s["cores"]}c/{s["memory"]}MiB/{s["disk"]}GiB {s["storage"]} {s["bridge"]} {s["ip"]} '
                     f'image {s["image_source"]}')
    if req.get('target'):
        parts.append(f'vmid {req["target"]["vmid"]} ({req["target"]["name"]})')
    if result:
        if result.get('upid'):
            parts.append(result['upid'])
        parts.append('-> ' + str(result.get('status') or 'ok'))
    if error:
        parts.append('-> ' + error)
    return ' '.join(str(p) for p in parts)[:1000]


# ── API handlers (gateway_api/server.py; the dashboard preview server uses them too) ─────────────────────────────────

def digest(name, text):
    return hashlib.sha256((name + '\n' + text).encode()).hexdigest()  # the same as cli.digest


def handle_inventory(db, params, admin, task, audit):
    """GET /v1/proxmox/inventory -> (status, payload). Agents (tool token + agent_id) see the nodes they have level 1 on."""
    from . import policy
    agent = params.get('agent_id') or None
    node = params.get('node') or None
    if node is not None and node not in NODES:
        return 400, {'error': 'unknown_node', 'detail': 'node is one of ' + ', '.join(NODES)}
    nodes = [n for n in NODES if node in (None, n)]
    if not admin or agent:
        if not agent:
            return 400, {'error': 'agent_id_required'}
        levels = policy.effective_services(db, agent)
        readable = [n for n in nodes if levels.get(service(n), {}).get('level', 0) >= 1]
        if not readable:
            audit(service(node or 'proxmox'), 'proxmox_denied', digest('proxmox', 'inventory'), actor='agent:' + agent, agent=agent, task=task,
                  detail='inventory ' + (node or 'all nodes') + ' (needs level 1 on proxmox:<node>)')
            return 403, {'error': 'policy_denied', 'detail': 'reading Proxmox needs level 1 on proxmox:<node>'}
        nodes = readable
    data = inventory(nodes, db, refresh=params.get('refresh') in ('1', 'true') and admin)
    snap = policy.snapshot(db)
    for n in data['nodes']:
        n['level'] = snap['services'].get(service(n['node']), {}).get('level', 0)
    if admin and not agent:
        data['servers'] = [{**s, 'level': snap['servers'].get(s['name'], {}).get('level', 0)} for s in dynamic_servers(db)]
        data['managed'] = managed_vms(db)
    else:
        for n in data['nodes']:
            audit(service(n['node']), 'proxmox_inventory', digest(service(n['node']), 'inventory'),
                  {'exit_code': 0 if n.get('ok') else 1, 'timed_out': False, 'truncated': False}, actor='agent:' + agent, agent=agent, task=task,
                  detail='inventory ' + n['node'] + (f": {len(n['vms'])} VMs" if n.get('ok') else ' -> ' + str(n.get('error'))))
    return 200, data


def handle_post(db, body, admin, task, audit):
    """POST /v1/proxmox -> (status, payload). Agents act at min(agent, proxmox:<node>) level; create_vm and create_audit_token
    below level 6 and destroy_vm always answer 403 approval_required with the exact text until the owner approved it
    (POST /v1/approvals {server: proxmox:<node>, command: text}); the approval is used once. The admin token is the owner."""
    from . import policy
    op, node = body.get('operation'), body.get('node')
    if node not in NODES:
        return 400, {'error': 'unknown_node', 'detail': 'node is one of ' + ', '.join(NODES)}
    svc = service(node)
    if admin:
        who = body.get('actor')
        if not isinstance(who, str) or not who.strip() or len(who) > 128:
            return 400, {'error': 'actor_required'}
        who, agent, level, free = who.strip(), None, 6, True
    else:
        agent = body.get('agent_id')
        allowed = policy.effective_services(db, agent).get(svc, {'level': 0})
        who, level, free = 'agent:' + str(agent), allowed['level'], bool(allowed.get('free_shell'))
    shown = str(op)[:40] + ' ' + node
    try:
        req = check(db, op, body, level, free, admin)
    except ProxmoxError as exc:
        if exc.status == 403:
            audit(svc, 'proxmox_denied', digest(svc, shown), actor=who, agent=agent, task=task, detail=shown + ' -> ' + exc.error + ': ' + str(exc.detail)[:300])
        return exc.status, exc.payload()
    approval = None
    if req['approval']:
        d = digest(svc, req['text'])
        db.execute('BEGIN IMMEDIATE')
        used = db.execute('UPDATE approvals SET used=1 WHERE digest=? AND used=0 AND expires>?', (d, int(time.time()))).rowcount
        db.execute('COMMIT')
        if used != 1:
            audit(svc, 'proxmox_approval_required', d, actor=who, agent=agent, task=task, detail=summary(req))
            return 403, {'error': 'approval_required', 'service': svc, 'approval_text': req['text'], 'digest': d[:16], 'request': req['request'],
                         'detail': 'the owner approves this exact request on the dashboard; then it runs once'}
        row = db.execute("SELECT actor FROM audit WHERE action='chat_approval' AND digest=? ORDER BY id DESC LIMIT 1", (d,)).fetchone()
        approval = {'digest': d[:16], 'by': (row[0] if row else None) or 'the owner'}
    d = digest(svc, json.dumps(req['request'], sort_keys=True))
    action = 'proxmox_' + op
    quiet = op in ('preview_create', 'inventory', 'snapshots') and admin  # dashboard reads are not audited
    try:
        result = run(db, req, who, approval)
    except ProxmoxError as exc:
        if not quiet:
            audit(svc, action, d, {'exit_code': 1, 'timed_out': exc.status == 504, 'truncated': False}, actor=who, agent=agent, task=task,
                  detail=summary(req, error=exc.error + ': ' + str(exc.detail)[:400]))
        return exc.status, exc.payload()
    except Exception as exc:  # noqa: BLE001 - audited and answered with the type only
        audit(svc, action, d, actor=who, agent=agent, task=task, detail=summary(req, error=type(exc).__name__))
        return 502, {'error': 'proxmox_failed', 'exception_type': type(exc).__name__}
    if not quiet:
        audit(svc, action, d, {'exit_code': 0, 'timed_out': False, 'truncated': False}, actor=who, agent=agent, task=task,
              detail=summary(req, result) + (f" (approved by {approval['by']})" if approval else ''))
    return 200, {'service': svc, 'operation': op, **result}
