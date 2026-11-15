"""The server inventory (HLA_INVENTORY_V1): which servers agents may reach and which integrations exist.

One YAML file, mounted read-only into the gateway and the agent platform at /config/inventory.yaml (HLA_INVENTORY
overrides the path). No file means an empty inventory: the platform runs (chat, agents) without server tools.
See config/inventory.example.yaml for every field. This module is shared: agent-platform-starter/inventory.py is an
identical copy (a test keeps them equal), so keep it free of gateway imports.

  servers:                       SSH targets (name -> how to reach it)
    - name: server1              lowercase letters, digits, '.', '_' and '-' (it is also the policy and audit name)
      host: 192.168.1.10         IP address or DNS name
      user: svc-hla-agent        default
      port: 22                   default
      description: Docker host   shown to agents
      sandbox: false             true for the one disposable sandbox VM (free shell, staging of deploys)
      role: website              optional: website (public web server) or traefik (reverse proxy host)
      default_level: 1           optional: the policy level the server starts with (0-6; sandbox 6, others 1)
      host_fingerprint: SHA256:… optional: pinned ed25519 host key(s) (also spelled host_key_fingerprint); else
                                 servers/<name>/SSH_HOST_FINGERPRINT in the secret store (CLI: secrets pin-host-key)
      vmid: 204                  optional: its Proxmox VMID (never offered for new VMs; the sandbox reset uses it)
      node: pve                  optional: the Proxmox node it runs on (one of services.proxmox.nodes)
  services:                      optional integrations; omit a block to disable it
    proxmox: {url: https://192.168.1.5:8006, nodes: [pve], reserved_vmids: [100], verify_tls: true}
    omada: {url: https://192.168.1.2, site: Default, verify_tls: false}
    homeassistant: {url: http://192.168.1.20:8123}
    github: {}
  public_ips: []                 the router's public IPs (website/DNS checks); empty = unknown
"""
import ipaddress
import os
import re
import urllib.parse

DEFAULT_PATH = '/config/inventory.yaml'
DEFAULT_USER = 'svc-hla-agent'
ROLES = ('website', 'traefik')
SERVICE_KINDS = ('proxmox', 'omada', 'homeassistant', 'github')
# Names the gateway uses for its services and policy rows; a server can never take one.
RESERVED_SERVER_NAMES = ('homeassistant', 'secrets', 'github', 'http_tools', 'http-tools', 'websites', 'routes', 'gateway')
_SERVER_NAME = re.compile(r'^[a-z0-9][a-z0-9._-]{0,62}$')
_HOSTNAME = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
_USER = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
_NODE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$')
_FINGERPRINT = re.compile(r'^SHA256:[A-Za-z0-9+/]{43}$')
_SECRET_PATH = re.compile(r'^(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){1,4}$')
_SITE = re.compile(r'^[^\x00-\x1f\x7f]{1,64}$')


class InventoryError(ValueError):
    """The inventory file is unreadable or invalid; the message says where and why (never a secret: there are none here)."""


def path():
    return os.getenv('HLA_INVENTORY', '').strip() or DEFAULT_PATH


_source = ['inventory']


def _fail(where, message):
    raise InventoryError(f'{_source[0]}: {where}: {message}')


def _keys(where, value, allowed, required=()):
    if not isinstance(value, dict):
        _fail(where, 'must be a mapping (key: value)')
    unknown = sorted(set(map(str, value)) - set(allowed))
    if unknown:
        _fail(where, 'unknown key(s) ' + ', '.join(unknown) + ' (allowed: ' + ', '.join(allowed) + ')')
    missing = [k for k in required if value.get(k) in (None, '')]
    if missing:
        _fail(where, 'missing ' + ', '.join(missing))


def _bool(where, value, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(where, 'must be true or false')
    return value


def _host(where, value):
    if not isinstance(value, str) or not value.strip():
        _fail(where, 'must be an IP address or host name')
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not _HOSTNAME.match(value) or re.fullmatch(r'[0-9.]+', value):
        _fail(where, f'{value!r} is not an IP address or host name')
    return value.lower()


def _int(where, value, low, high, default=None):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail(where, f'must be a whole number from {low} to {high}')
    return value


def _url(where, value, schemes):
    """(normalized url without trailing slash, host, port) of an http(s) base URL without path, query or credentials."""
    if not isinstance(value, str) or not value.strip():
        _fail(where, 'must be a URL like ' + schemes[0] + '://192.168.1.5')
    raw = value.strip().rstrip('/')
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in schemes:
        _fail(where, f'{raw!r} must start with ' + ' or '.join(s + '://' for s in schemes))
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('',):
        _fail(where, f'{raw!r} must be scheme://host[:port] only (no path, query or user)')
    try:
        port = parts.port
    except ValueError:
        _fail(where, f'{raw!r} has an invalid port')
    host = _host(where, parts.hostname or '')
    port = port or (443 if parts.scheme == 'https' else 80)
    shown = f'[{host}]' if ':' in host else host
    default = (parts.scheme == 'https' and port == 443) or (parts.scheme == 'http' and port == 80)
    return f'{parts.scheme}://{shown}' + ('' if default and parts.port is None else f':{port}'), host, port


def _fingerprints(where, value):
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(f, str) and _FINGERPRINT.match(f.strip()) for f in items):
        _fail(where, 'must be an ed25519 host key fingerprint SHA256:<43 base64 characters> (or a list of them)')
    return [f.strip() for f in items]


def _secret_path(where, value, default):
    if value is None:
        return default
    if not isinstance(value, str) or not _SECRET_PATH.match(value):
        _fail(where, 'must be a folder like /servers/name')
    return value


def _servers(raw):
    if raw is None:
        return []
    if not isinstance(raw, list):
        _fail('servers', 'must be a list (use servers: [] for none)')
    out, seen, roles, sandbox = [], set(), {}, None
    for i, item in enumerate(raw):
        where = f'servers[{i}]'
        _keys(where, item, ('name', 'host', 'user', 'port', 'description', 'sandbox', 'role', 'default_level',
                            'host_fingerprint', 'host_key_fingerprint', 'vmid', 'node', 'secret_path'), ('name', 'host'))
        name = item['name']
        if not isinstance(name, str) or not _SERVER_NAME.match(name):
            _fail(where, f'name {name!r} must be 1-63 lowercase letters, digits, ".", "_" or "-", starting with a letter or digit')
        where = f'servers[{i}] ({name})'
        if name in seen:
            _fail(where, 'the name is used twice')
        if name in RESERVED_SERVER_NAMES:
            _fail(where, f'{name!r} is reserved for a gateway service; pick another name')
        seen.add(name)
        user = item.get('user') if item.get('user') is not None else DEFAULT_USER
        if not isinstance(user, str) or not _USER.match(user):
            _fail(where, 'user must be a Unix user name')
        description = item.get('description') or ''
        if not isinstance(description, str) or len(description) > 200 or re.search(r'[\x00-\x08\x0b-\x1f\x7f]', description):
            _fail(where, 'description must be text of at most 200 characters')
        is_sandbox = _bool(where + ' sandbox', item.get('sandbox'), False)
        if is_sandbox:
            if sandbox:
                _fail(where, f'only one server can be the sandbox ({sandbox} is already)')
            sandbox = name
        role = item.get('role')
        if role is not None:
            if role not in ROLES:
                _fail(where, 'role must be one of ' + ', '.join(ROLES))
            if role in roles:
                _fail(where, f'role {role} is already taken by {roles[role]}')
            if is_sandbox:
                _fail(where, 'the sandbox cannot also have a role')
            roles[role] = name
        out.append({'name': name, 'host': _host(where + ' host', item['host']), 'user': user,
                    'port': _int(where + ' port', item.get('port'), 1, 65535, 22), 'description': description.strip(),
                    'sandbox': is_sandbox, 'role': role,
                    'default_level': _int(where + ' default_level', item.get('default_level'), 0, 6),
                    # host_key_fingerprint is accepted as another spelling of host_fingerprint
                    'host_fingerprint': _fingerprints(where + ' host_fingerprint', item.get('host_fingerprint') if item.get('host_fingerprint') is not None
                                                      else item.get('host_key_fingerprint')),
                    'vmid': _int(where + ' vmid', item.get('vmid'), 100, 999999999),
                    'node': item.get('node') if item.get('node') is None or (isinstance(item.get('node'), str) and _NODE.match(item['node']))
                    else _fail(where + ' node', 'must be a Proxmox node name'),
                    'secret_path': _secret_path(where + ' secret_path', item.get('secret_path'), '/servers/' + name)})
    return out


def _proxmox(raw):
    where = 'services.proxmox'
    _keys(where, raw, ('url', 'nodes', 'reserved_vmids', 'verify_tls'), ('nodes',))
    default_url = _url(where + '.url', raw['url'], ('https',)) if raw.get('url') else None
    nodes_raw = raw['nodes']
    if not isinstance(nodes_raw, list) or not nodes_raw:
        _fail(where + '.nodes', 'must be a non-empty list of node names')
    nodes, seen = [], set()
    for i, node in enumerate(nodes_raw):
        nw = f'{where}.nodes[{i}]'
        if isinstance(node, str):
            node = {'name': node}
        _keys(nw, node, ('name', 'url', 'tls_name', 'label', 'secret_path'), ('name',))
        name = node['name']
        if not isinstance(name, str) or not _NODE.match(name):
            _fail(nw, f'node name {name!r} must be letters, digits and "-"')
        if name in seen:
            _fail(nw, f'node {name} is listed twice')
        seen.add(name)
        url = _url(nw + '.url', node['url'], ('https',)) if node.get('url') else default_url
        if not url:
            _fail(nw, 'needs a url (or set services.proxmox.url for all nodes)')
        tls_name = node.get('tls_name')
        if tls_name is not None and (not isinstance(tls_name, str) or not _HOSTNAME.match(tls_name)):
            _fail(nw, 'tls_name must be a host name')
        label = node.get('label') or name
        if not isinstance(label, str) or len(label) > 40:
            _fail(nw, 'label must be text of at most 40 characters')
        nodes.append({'name': name, 'url': url[0], 'host': url[1], 'port': url[2], 'tls_name': tls_name, 'label': label,
                      'secret_path': _secret_path(nw + '.secret_path', node.get('secret_path'), '/Proxmox/' + name)})
    vmids = raw.get('reserved_vmids') or []
    if not isinstance(vmids, list) or not all(isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 999999999 for v in vmids):
        _fail(where + '.reserved_vmids', 'must be a list of VMIDs (whole numbers from 100)')
    return {'url': default_url[0] if default_url else None, 'nodes': nodes, 'reserved_vmids': sorted(set(vmids)),
            'verify_tls': _bool(where + '.verify_tls', raw.get('verify_tls'), True)}


def _services(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _fail('services', 'must be a mapping of integrations (proxmox, omada, homeassistant, github)')
    unknown = sorted(set(map(str, raw)) - set(SERVICE_KINDS))
    if unknown:
        _fail('services', 'unknown integration(s) ' + ', '.join(unknown) + ' (known: ' + ', '.join(SERVICE_KINDS) + ')')
    out = {}
    for kind, value in raw.items():
        where = 'services.' + kind
        if value is False:
            continue  # explicitly off, same as leaving the block out
        if value is None and kind != 'github':  # "github:" alone (null) enables GitHub with its defaults
            _fail(where, 'needs its settings (or leave the block out to disable it)')
        if kind == 'proxmox':
            out[kind] = _proxmox(value)
        elif kind == 'omada':
            _keys(where, value, ('url', 'site', 'verify_tls', 'secret_path'), ('url',))
            url, host, port = _url(where + '.url', value['url'], ('https',))
            site = value.get('site') if value.get('site') is not None else ''
            if not isinstance(site, str) or (site and not _SITE.match(site)):
                _fail(where + '.site', 'must be the site name (text)')
            out[kind] = {'url': url, 'host': host, 'port': port, 'site': site,
                         'verify_tls': _bool(where + '.verify_tls', value.get('verify_tls'), False),
                         'secret_path': _secret_path(where + '.secret_path', value.get('secret_path'), '/Omada')}
        elif kind == 'homeassistant':
            _keys(where, value, ('url', 'secret_path'), ('url',))
            out[kind] = {'url': _url(where + '.url', value['url'], ('http', 'https'))[0],
                         'secret_path': _secret_path(where + '.secret_path', value.get('secret_path'), '/HomeAssistant')}
        elif kind == 'github':
            if value is None or value is True:
                value = {}
            _keys(where, value, ('api_url', 'secret_path'))
            out[kind] = {'api_url': _url(where + '.api_url', value['api_url'], ('https',))[0] if value.get('api_url') else 'https://api.github.com',
                         'secret_path': _secret_path(where + '.secret_path', value.get('secret_path'), '/GitHub')}
    return out


def _public_ips(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p for p in re.split(r'[\s,]+', raw) if p]
    if not isinstance(raw, list):
        _fail('public_ips', 'must be a list of IP addresses')
    out = []
    for ip in raw:
        try:
            out.append(str(ipaddress.ip_address(str(ip).strip())))
        except ValueError:
            _fail('public_ips', f'{ip!r} is not an IP address')
    return out[:10]


def parse(data, source='inventory'):
    """A validated inventory from already-parsed YAML data (None = empty)."""
    _source[0] = source
    if data is None:
        data = {}
    _keys('top level', data, ('servers', 'services', 'public_ips'))
    servers = _servers(data.get('servers'))
    services = _services(data.get('services'))
    inv = {'servers': servers, 'services': services, 'public_ips': _public_ips(data.get('public_ips'))}
    names = {s['name'] for s in servers}
    nodes = [n['name'] for n in (services.get('proxmox') or {}).get('nodes', [])]
    for s in servers:
        if s['node'] and s['node'] not in nodes:
            _fail(f'servers ({s["name"]}) node', f'{s["node"]} is not one of services.proxmox.nodes ({", ".join(nodes) or "none"})')
    for node in (services.get('proxmox') or {}).get('nodes', []):
        if node['name'] in names:
            _fail('services.proxmox.nodes', f'node {node["name"]} has the same name as a server')
    return inv


def empty():
    return parse({})


def load(file=None):
    """The inventory at `file` (default: path()). A missing file is an empty inventory; anything invalid raises
    InventoryError with a message that names the file, the entry and the problem."""
    file = file or path()
    if not os.path.exists(file):
        if file != DEFAULT_PATH:
            raise InventoryError(f'{file}: not found (HLA_INVENTORY points here)')
        inv = empty()
        inv['source'] = None
        return inv
    if os.path.isdir(file):
        raise InventoryError(f'{file}: is a folder, not a file (Docker creates one when the mounted file is missing on the host: '
                             'create config/inventory.yaml from config/inventory.example.yaml)')
    try:
        import yaml
    except ImportError:
        raise InventoryError('reading the inventory needs PyYAML (pip install pyyaml)') from None
    try:
        with open(file, encoding='utf-8') as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise InventoryError(f'{file}: cannot be read ({exc.strerror or type(exc).__name__})') from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise InventoryError(f'{file}: not valid YAML{where}: {getattr(exc, "problem", None) or exc}') from None
    inv = parse(data, file)
    inv['source'] = file
    return inv


def sandbox(inv):
    """The sandbox server's name, or None."""
    return next((s['name'] for s in inv['servers'] if s['sandbox']), None)


def role(inv, name):
    """The name of the server with this role (website, traefik), or None."""
    return next((s['name'] for s in inv['servers'] if s['role'] == name), None)


def server(inv, name):
    return next((s for s in inv['servers'] if s['name'] == name), None)


def load_lenient(file=None):
    """(inventory, error text or None): an invalid file gives an empty inventory and the reason, so the agent platform can
    keep running and show the problem (the gateway itself refuses to start on one)."""
    try:
        return load(file), None
    except InventoryError as exc:
        inv = empty()
        inv['source'] = None
        return inv, str(exc)


_current = []


def current():
    """load_lenient() once per process: (inventory, error)."""
    if not _current:
        _current.append(load_lenient())
    return _current[0]
