"""Managed public routes on Traefik (HLA_ROUTES_V1): domains and subdomains for the websites on public-website.

The gateway owns exactly ONE file on the traefik host: TRAEFIK_MANAGED_FILE (default /opt/traefik/dynamic/hla-managed.yml).
It never writes, moves or deletes any other file there. The desired state lives in the gateway's own database
(managed_routes); the whole file is rendered from it, deterministically, every time something changes:

  render -> check the text locally (parsed back, names unique, allow-lists only 192.168.0.0/16 and 10.0.0.0/8)
  -> read Traefik's API and refuse names or hosts that clash with ANY other router (hand-written ones included)
  -> upload with one fixed script (content on stdin, sha256-checked, previous content kept in the backup folder outside
     the dynamic folder, written under an flock) -> poll the Traefik API until every managed router is enabled without
     an error and no other router got worse -> on any failure the previous content is written back.

Every value that reaches the file passes a strict pattern first (host names, route names, users, resolvers, entry points,
backends) and is written as a JSON-quoted YAML string. Basic-auth passwords are generated in the generated namespace of the secret store
(gateway/agent_secrets.py) and hashed here with apr1 (MD5-crypt, what htpasswd -m writes); only the hash goes into
the file, the password never leaves the secret store and the gateway. Agents can only file a pending request (POST
/v1/routes/request, level >= 4 on the traefik server); the owner approves it on the dashboard. Stdlib only.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import agent_secrets, websites
from .core import SERVERS
from . import core

NAME = 'routes'
# The servers with role traefik and role website in the inventory. Without one, a name that can never be a server (so every
# call to it is refused as an unknown server and the routes feature stays unusable, not wrong).
TRAEFIK_SERVER = core.TRAEFIK_SERVER or '(no traefik server in the inventory)'
WEBSITE_SERVER = core.WEBSITE_SERVER or '(no website server in the inventory)'
AGENT_LEVEL = 4  # an agent files route requests with level >= 4 on the traefik server
PREFIX = 'hla-'
ENVIRONMENTS = ('production', 'staging')
PROTECTIONS = ('public', 'lan_only', 'lan_only+basic_auth', 'basic_auth')
DEFAULT_PROTECTION = {'production': 'public', 'staging': 'lan_only+basic_auth'}
# NEVER 172.16.0.0/12: the Traefik container sits on a Docker bridge in that range, and IPv6 clients arrive through
# docker-proxy with such an address, so allowing it would let the whole internet past the allow-list.
LAN_RANGES = ('192.168.0.0/16', '10.0.0.0/8')
FORBIDDEN_RANGE = re.compile(r'(^|[^0-9])172\.(1[6-9]|2[0-9]|3[01])\.')
DEFAULT_ENTRYPOINT = 'websecure'
HEADER = ('# managed by the Homelab Agents platform — do not edit by hand; changes are overwritten\n'
          '# Source of truth: the SSH gateway database (managed_routes); edit routes on the dashboard (Websites → Domains & routes).\n')
LAN_COMMENT = ('# LAN only: 192.168.0.0/16 and 10.0.0.0/8. Never 172.16.0.0/12: Traefik sits on a Docker bridge in that range and IPv6\n'
               '# clients arrive through docker-proxy with such addresses, so allowing it would open the route to everyone.\n')
ROUTE_NAME = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')
HOSTNAME = websites.HOSTNAME
PATH_PREFIX = re.compile(r'^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]{0,99}$')
BASIC_USER = re.compile(r'^[a-z][a-z0-9_.-]{1,31}$')
TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')  # cert resolvers and entry points
_BACKEND = re.compile(r'^http://([a-z0-9.-]{1,253}):([0-9]{1,5})/?$')
_FILE = re.compile(r'^/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.ya?ml$')
_DIR = re.compile(r'^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+/?$')
_IP = re.compile(r'^[0-9a-fA-F:.]{2,45}$')
_PROJECT = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
MAX_DOMAINS = 20
MAX_ROUTES = 200
MAX_PENDING = 50
VERIFY_SECONDS = 10.0
POLL_SECONDS = 1.0
DNS_TIMEOUT = 3.0
DNS_CACHE_SECONDS = 60
STATE_CACHE_SECONDS = 10
COLUMNS = ('name', 'host', 'environment', 'backend', 'path_prefix', 'protection', 'basic_user', 'basic_secret', 'basic_hash',
           'cert_resolver', 'entrypoints', 'project_id', 'status', 'created_at', 'created_by', 'updated_at', 'updated_by')

_lock = threading.Lock()  # one route change at a time (the remote script also takes an flock)
_dns_cache = {}
_state_cache = {'at': 0.0, 'state': None}
sleep = time.sleep  # replaced in tests
clock = time.monotonic


class RouteError(Exception):
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


# ── settings ─────────────────────────────────────────────────────────────────────────────────────────────────────
def managed_file():
    value = os.getenv('TRAEFIK_MANAGED_FILE', '').strip() or '/opt/traefik/dynamic/hla-managed.yml'
    if not _FILE.fullmatch(value) or '..' in value.split('/') or len(value) > 200:
        raise RouteError(500, 'bad_setting', 'TRAEFIK_MANAGED_FILE must be an absolute path ending in .yml or .yaml')
    return value


def backup_dir():
    value = (os.getenv('TRAEFIK_ROUTE_BACKUP_DIR', '').strip() or '/opt/traefik/managed-backups').rstrip('/')
    if not _DIR.fullmatch(value) or '..' in value.split('/') or len(value) > 200:
        raise RouteError(500, 'bad_setting', 'TRAEFIK_ROUTE_BACKUP_DIR must be an absolute folder')
    folder = managed_file().rsplit('/', 1)[0]
    # Traefik's file provider loads every file in its folder: a backup there would be loaded as configuration.
    if value == folder or value.startswith(folder + '/'):
        raise RouteError(500, 'bad_setting', 'TRAEFIK_ROUTE_BACKUP_DIR must be outside the folder of TRAEFIK_MANAGED_FILE '
                                             '(Traefik loads every file in that folder)')
    return value


def public_ips():
    """The router's public addresses: PUBLIC_IPS (comma-separated) if set, else public_ips in the inventory; [] = unknown."""
    raw = os.getenv('PUBLIC_IPS', '').strip()
    ips = [p.strip() for p in raw.split(',') if p.strip()] if raw else list(core.PUBLIC_IPS)
    return [ip for ip in ips if _IP.fullmatch(ip)][:10]


def traefik_lan_ip():
    return SERVERS.get(TRAEFIK_SERVER, ('',))[0]


def website_ip():
    return SERVERS.get(WEBSITE_SERVER, ('',))[0]


def backend_hosts():
    if WEBSITE_SERVER not in SERVERS:
        return []
    hosts = [website_ip(), WEBSITE_SERVER]
    hosts += [h.strip().lower() for h in os.getenv('WEBSITE_BACKEND_HOSTS', '').split(',') if h.strip()]
    return list(dict.fromkeys(h for h in hosts if re.fullmatch(r'[a-z0-9.-]{1,253}', h)))


def env_cert_resolver():
    value = os.getenv('TRAEFIK_CERT_RESOLVER', '').strip()
    return value if value and TOKEN.fullmatch(value) else None


def _quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


# ── database ─────────────────────────────────────────────────────────────────────────────────────────────────────
def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS managed_routes (name TEXT PRIMARY KEY, host TEXT NOT NULL, environment TEXT NOT NULL,
        backend TEXT NOT NULL, path_prefix TEXT NOT NULL DEFAULT '', protection TEXT NOT NULL, basic_user TEXT, basic_secret TEXT,
        basic_hash TEXT, cert_resolver TEXT, entrypoints TEXT NOT NULL DEFAULT '[]', project_id TEXT,
        status TEXT NOT NULL DEFAULT 'active', created_at INTEGER, created_by TEXT, updated_at INTEGER, updated_by TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS route_requests (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, agent_id TEXT,
        host TEXT NOT NULL, backend_port INTEGER NOT NULL, environment TEXT NOT NULL, protection TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', project_id TEXT, task_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
        decided_at INTEGER, decided_by TEXT, decision_note TEXT, route_name TEXT)''')
    # The last rendered files (what was written, with its sha256): a second copy of the history next to the backups.
    db.execute('''CREATE TABLE IF NOT EXISTS route_files (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, sha256 TEXT NOT NULL,
        content TEXT NOT NULL, actor TEXT, note TEXT)''')
    db.execute('CREATE TABLE IF NOT EXISTS policy_settings (key TEXT PRIMARY KEY, value TEXT)')


def _row(values):
    row = dict(zip(COLUMNS, values))
    try:
        row['entrypoints'] = [e for e in json.loads(row['entrypoints'] or '[]') if isinstance(e, str)]
    except ValueError:
        row['entrypoints'] = []
    row['path_prefix'] = row['path_prefix'] or ''
    return row


def load_rows(db):
    return {r[0]: _row(r) for r in db.execute('SELECT ' + ','.join(COLUMNS) + ' FROM managed_routes ORDER BY name')}


def _store(db, rows_before, rows_after):
    db.execute('BEGIN IMMEDIATE')
    try:
        for name in rows_before:
            if name not in rows_after:
                db.execute('DELETE FROM managed_routes WHERE name=?', (name,))
        for name, row in rows_after.items():
            if rows_before.get(name) == row:
                continue
            values = [json.dumps(row[c]) if c == 'entrypoints' else row.get(c) for c in COLUMNS]
            db.execute('INSERT OR REPLACE INTO managed_routes(' + ','.join(COLUMNS) + ') VALUES (' + ','.join('?' * len(COLUMNS)) + ')', values)
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def domains(db):
    row = db.execute("SELECT value FROM policy_settings WHERE key='route_domains'").fetchone()
    try:
        value = json.loads(row[0]) if row and row[0] else []
    except ValueError:
        return []
    return [d for d in value if isinstance(d, str) and HOSTNAME.fullmatch(d)] if isinstance(value, list) else []


def set_domains(db, value):
    if not isinstance(value, list) or len(value) > MAX_DOMAINS:
        raise RouteError(400, 'invalid_domains', f'domains is a list of at most {MAX_DOMAINS} domain names')
    out = []
    for d in value:
        d = str(d or '').strip().lower().rstrip('.')
        if not HOSTNAME.fullmatch(d) or d.count('.') < 1:
            raise RouteError(400, 'invalid_domain', f'{str(d)[:80]!r} is not a domain name like example.com')
        if d not in out:
            out.append(d)
    db.execute("INSERT INTO policy_settings(key,value) VALUES ('route_domains',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (json.dumps(out),))
    return out


def domain_of(host, known):
    """The configured domain a host belongs to (longest match), or None."""
    best = None
    for d in known:
        if (host == d or host.endswith('.' + d)) and (best is None or len(d) > len(best)):
            best = d
    return best


def registrable(host):
    parts = host.split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


# ── apr1 (MD5-crypt with the Apache magic, as `htpasswd -m` / `openssl passwd -apr1`) ─────────────────────────────
_ITOA64 = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def apr1(password, salt=None):
    pw = password.encode('utf-8')
    salt = salt if salt is not None else ''.join(secrets.choice(_ITOA64) for _ in range(8))
    salt_b = salt.encode('ascii')[:8]
    magic = b'$apr1$'
    ctx = pw + magic + salt_b
    final = hashlib.md5(pw + salt_b + pw).digest()
    for i in range(len(pw), 0, -16):
        ctx += final[:min(16, i)]
    i = len(pw)
    while i:
        ctx += b'\x00' if i & 1 else pw[:1]
        i >>= 1
    final = hashlib.md5(ctx).digest()
    for i in range(1000):
        c = pw if i & 1 else final
        if i % 3:
            c += salt_b
        if i % 7:
            c += pw
        c += final if i & 1 else pw
        final = hashlib.md5(c).digest()

    def to64(v, n):
        out = ''
        for _ in range(n):
            out += _ITOA64[v & 0x3f]
            v >>= 6
        return out
    f = final
    text = (to64((f[0] << 16) | (f[6] << 8) | f[12], 4) + to64((f[1] << 16) | (f[7] << 8) | f[13], 4)
            + to64((f[2] << 16) | (f[8] << 8) | f[14], 4) + to64((f[3] << 16) | (f[9] << 8) | f[15], 4)
            + to64((f[4] << 16) | (f[10] << 8) | f[5], 4) + to64(f[11], 2))
    return '$apr1$' + salt_b.decode('ascii') + '$' + text


def secret_name(route):
    return ('TRAEFIK_BASIC_' + route.upper().replace('-', '_'))[:64]


def ensure_password(name, host, actor):
    """The basic-auth password from the generated secrets, created there when missing. Replaced in tests."""
    if not agent_secrets.configured():
        raise RouteError(503, 'secrets_not_configured', 'basic auth needs the generated secrets: '
                                                        + agent_secrets.not_configured_detail())
    try:
        values = agent_secrets.load(force=True)
    except RuntimeError as exc:
        raise RouteError(502, 'secrets_unavailable', str(exc)[:200]) from None
    if name not in values:
        try:
            agent_secrets.run({'op': 'create', 'need': 4, 'name': name, 'kind': 'password', 'replace': False,
                               'description': 'Traefik basic auth for ' + host}, actor)
        except agent_secrets.SecretError as exc:
            raise RouteError(exc.status, exc.error, exc.detail) from None
        values = agent_secrets.load()
    value = values.get(name)
    if not value:
        raise RouteError(502, 'secrets_unavailable', 'the password ' + name + ' could not be read back from the secret store')
    return value


# ── validation ───────────────────────────────────────────────────────────────────────────────────────────────────
def name_for_host(host):
    name = re.sub(r'[^a-z0-9-]+', '-', host.lower()).strip('-')[:40].strip('-')
    return name or 'route'


def parse_backend(body):
    """http://<website host>:<port> from {backend} or {backend_port[, backend_host]}."""
    allowed = backend_hosts()
    if body.get('backend') not in (None, ''):
        m = _BACKEND.fullmatch(str(body['backend']).strip().lower())
        if not m:
            raise RouteError(400, 'invalid_backend', 'backend is http://' + (allowed[0] if allowed else '<website server>') + ':<port>')
        host, port = m.group(1), int(m.group(2))
    else:
        port = body.get('backend_port')
        if isinstance(port, str) and port.isdigit():
            port = int(port)
        if isinstance(port, bool) or not isinstance(port, int):
            raise RouteError(400, 'invalid_backend', 'backend_port is the port the website publishes on ' + (allowed[0] if allowed else 'the website server'))
        host = str(body.get('backend_host') or allowed[0]).strip().lower()
    if host not in allowed:
        raise RouteError(400, 'backend_not_allowed', 'backends are on the website VM only: ' + ', '.join(allowed))
    if not 1 <= port <= 65535:
        raise RouteError(400, 'invalid_backend', 'the port is 1..65535')
    return f'http://{host}:{port}'


def backend_port(backend):
    m = _BACKEND.fullmatch(backend or '')
    return int(m.group(2)) if m else None


def clean_host(value, known):
    host = str(value or '').strip().lower().rstrip('.')
    if not HOSTNAME.fullmatch(host):
        raise RouteError(400, 'invalid_host', 'the host is a full name like shop.example.com (letters, digits, - and .)')
    if not known:
        raise RouteError(400, 'no_domains', 'add your domains first (Domains on the routes panel)')
    if not domain_of(host, known):
        raise RouteError(400, 'unknown_domain', host + ' is not under a configured domain (' + ', '.join(known) + ')')
    return host


def build_row(name, body, current, known, actor, now):
    """A validated managed_routes row from a dashboard body (current: the stored row when editing)."""
    if not isinstance(name, str) or not ROUTE_NAME.fullmatch(name):
        raise RouteError(400, 'invalid_name', 'route names are 1-40 lowercase letters, digits and -')
    cur = current or {}
    pick = lambda key, default=None: body[key] if key in body else cur.get(key, default)
    host = clean_host(pick('host'), known)
    env = pick('environment') or 'production'
    if env not in ENVIRONMENTS:
        raise RouteError(400, 'invalid_environment', 'environment is production or staging')
    protection = body.get('protection') or (cur.get('protection') if current else None) or DEFAULT_PROTECTION[env]
    if protection not in PROTECTIONS:
        raise RouteError(400, 'invalid_protection', 'protection is ' + ', '.join(PROTECTIONS))
    if any(k in body for k in ('backend', 'backend_port', 'backend_host')) or not current:
        backend = parse_backend(body)
    else:
        backend = cur['backend']
    path = str(pick('path_prefix', '') or '').strip()
    if path in ('/',):
        path = ''
    if path and not PATH_PREFIX.fullmatch(path):
        raise RouteError(400, 'invalid_path', 'the path prefix looks like /app (letters, digits, . _ ~ - /)')
    path = path.rstrip('/') if path != '/' else ''
    basic = 'basic_auth' in protection
    user = str(pick('basic_user') or 'hla').strip()
    if basic and not BASIC_USER.fullmatch(user):
        raise RouteError(400, 'invalid_user', 'the basic-auth user is 2-32 lowercase letters, digits, _ . -, starting with a letter')
    resolver = pick('cert_resolver')
    if resolver not in (None, '') and not TOKEN.fullmatch(str(resolver)):
        raise RouteError(400, 'invalid_cert_resolver', 'a certificate resolver name is letters, digits, _ and -')
    eps = pick('entrypoints', []) or []
    if not isinstance(eps, list) or len(eps) > 5 or not all(isinstance(e, str) and TOKEN.fullmatch(e) for e in eps):
        raise RouteError(400, 'invalid_entrypoints', 'extra entry points are up to 5 names (letters, digits, _ and -)')
    eps = [e for e in dict.fromkeys(eps) if e != DEFAULT_ENTRYPOINT]
    if 'traefik' in eps:
        raise RouteError(400, 'invalid_entrypoints', 'the traefik entry point (API and dashboard) is never used for a route')
    project = pick('project_id')
    if project not in (None, '') and not _PROJECT.fullmatch(str(project)):
        raise RouteError(400, 'invalid_project', 'project_id is a project UUID')
    enabled = body.get('enabled')
    status = cur.get('status', 'active') if enabled is None else ('active' if enabled else 'disabled')
    return {'name': name, 'host': host, 'environment': env, 'backend': backend, 'path_prefix': path, 'protection': protection,
            'basic_user': user if basic else None, 'basic_secret': secret_name(name) if basic else None,
            'basic_hash': cur.get('basic_hash') if basic and cur.get('basic_user') == user and cur.get('basic_secret') == secret_name(name) else None,
            'cert_resolver': str(resolver) if resolver else None, 'entrypoints': eps, 'project_id': str(project) if project else None,
            'status': status, 'created_at': cur.get('created_at') or now, 'created_by': cur.get('created_by') or actor,
            'updated_at': now, 'updated_by': actor}


# ── rendering ────────────────────────────────────────────────────────────────────────────────────────────────────
def names(row):
    base = PREFIX + row['name']
    mws = []
    if row['protection'] in ('lan_only', 'lan_only+basic_auth'):
        mws.append(base + '-lan')
    if 'basic_auth' in row['protection']:
        mws.append(base + '-auth')
    return {'router': base, 'service': base, 'middlewares': mws}


def rule(row):
    return f"Host(`{row['host']}`)" + (f" && PathPrefix(`{row['path_prefix']}`)" if row['path_prefix'] else '')


def _q(value):
    """A YAML double-quoted scalar (JSON strings are valid YAML); values are pattern-checked before they get here."""
    text = json.dumps(str(value), ensure_ascii=True)
    if '\n' in text or '\r' in text:
        raise RouteError(500, 'render_failed', 'a value with a line break reached the renderer')
    return text


def entrypoints(row):
    return [DEFAULT_ENTRYPOINT] + [e for e in row['entrypoints'] if e != DEFAULT_ENTRYPOINT]


def render(rows, resolver):
    """The whole managed file for these rows (active ones), byte-for-byte the same for the same input."""
    active = [rows[k] for k in sorted(rows) if rows[k]['status'] == 'active']
    out = [HEADER]
    if not active:
        return ''.join(out) + '# No managed routes.\nhttp: {}\n'
    routers, services, middlewares = [], [], []
    for row in active:
        n = names(row)
        routers += [f'    {_q(n["router"])}:\n', f'      rule: {_q(rule(row))}\n', '      entryPoints:\n']
        routers += [f'        - {_q(e)}\n' for e in entrypoints(row)]
        routers.append(f'      service: {_q(n["service"])}\n')
        if n['middlewares']:
            routers.append('      middlewares:\n')
            routers += [f'        - {_q(m)}\n' for m in n['middlewares']]
        routers += ['      tls:\n', f'        certResolver: {_q(row.get("cert_resolver") or resolver)}\n']
        services += [f'    {_q(n["service"])}:\n', '      loadBalancer:\n', '        passHostHeader: true\n', '        servers:\n',
                     f'          - url: {_q(row["backend"])}\n']
        for m in n['middlewares']:
            if m.endswith('-lan'):
                middlewares += [LAN_COMMENT.replace('# ', '    # '), f'    {_q(m)}:\n', '      ipAllowList:\n', '        sourceRange:\n']
                middlewares += [f'          - {_q(r)}\n' for r in LAN_RANGES]
            else:
                if not row.get('basic_hash') or not row['basic_hash'].startswith('$apr1$'):
                    raise RouteError(500, 'render_failed', 'route ' + row['name'] + ' has basic auth without a hash')
                middlewares += [f'    {_q(m)}:\n', '      basicAuth:\n', '        removeHeader: true\n', '        users:\n',
                                f'          - {_q(row["basic_user"] + ":" + row["basic_hash"])}\n']
    out += ['http:\n', '  routers:\n'] + routers + ['  services:\n'] + services
    if middlewares:
        out += ['  middlewares:\n'] + middlewares
    return ''.join(out)


def _duplicate_keys(text):
    """Line numbers of a key repeated under the same parent (a duplicate key crashed Traefik's config once)."""
    stack, dupes = [(-1, set())], []
    for number, raw in enumerate(text.split('\n'), 1):
        line = raw.split(' #', 1)[0].rstrip() if not raw.lstrip().startswith('#') else ''
        if not line.strip() or line.lstrip().startswith('- '):
            continue
        indent = len(line) - len(line.lstrip(' '))
        m = re.match(r'^\s*("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.-]+):(\s|$)', line)
        if not m:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if m.group(1) in stack[-1][1]:
            dupes.append(number)
        stack[-1][1].add(m.group(1))
        stack.append((indent, set()))
    return dupes


def check_rendered(text, rows, resolver):
    """Parse the rendered text back and compare it with what it must say; RouteError when anything is off."""
    problems = []
    if _duplicate_keys(text):
        problems.append('duplicate keys on lines ' + ', '.join(map(str, _duplicate_keys(text)[:5])))
    for number, raw in enumerate(text.split('\n'), 1):
        if not raw.lstrip().startswith('#') and FORBIDDEN_RANGE.search(raw):
            problems.append(f'a 172.16.0.0/12 address on line {number}')
    try:
        cfg = websites.parse_yaml(text)
    except websites.YamlError as exc:
        raise RouteError(500, 'render_failed', 'the rendered file does not parse: ' + str(exc)) from None
    http = cfg.get('http') or {}
    active = {k: r for k, r in rows.items() if r['status'] == 'active'}
    got_routers = http.get('routers') or {}
    want = {names(r)['router'] for r in active.values()}
    if set(got_routers) != want:
        problems.append('routers differ from the database')
    for r in active.values():
        n = names(r)
        got = got_routers.get(n['router']) or {}
        if got.get('rule') != rule(r) or got.get('service') != n['service'] or (got.get('middlewares') or []) != n['middlewares']:
            problems.append('router ' + n['router'] + ' differs from the database')
        if (got.get('tls') or {}).get('certResolver') != (r.get('cert_resolver') or resolver):
            problems.append('router ' + n['router'] + ' has the wrong certificate resolver')
        servers = (((http.get('services') or {}).get(n['service']) or {}).get('loadBalancer') or {}).get('servers') or []
        if [s.get('url') for s in servers if isinstance(s, dict)] != [r['backend']]:
            problems.append('service ' + n['service'] + ' differs from the database')
    for name, mw in (http.get('middlewares') or {}).items():
        allow = (mw or {}).get('ipAllowList')
        if allow is not None and (allow.get('sourceRange') != list(LAN_RANGES) or 'ipStrategy' in allow):
            problems.append('allow-list ' + name + ' is not exactly 192.168.0.0/16 and 10.0.0.0/8')
    if not all(k.startswith(PREFIX) for part in ('routers', 'services', 'middlewares') for k in (http.get(part) or {})):
        problems.append('a name without the hla- prefix')
    if problems:
        raise RouteError(500, 'render_failed', '; '.join(problems)[:400])
    return True


# ── Traefik API (read through SSH on the traefik host) ───────────────────────────────────────────────────────────
_STATE = """A={api}; B={boundary}
for k in http/routers http/services http/middlewares entrypoints; do
 r=$(curl -fsS --max-time 5 "$A/api/$k?per_page=1000" 2>&1); c=$?
 echo "$B API ${{k#http/}} $c"; printf '%s\\n' "$r" | head -c 3000000; echo; echo "$B END"
 [ $c -eq 0 ] || break
done
echo "$B DONE"
"""


def traefik_state(execute):
    """{ok, error, routers, services, middlewares, entrypoints} from the Traefik API (raw objects stay in the gateway)."""
    boundary = 'HLA_' + secrets.token_hex(8)
    try:
        script = _STATE.format(api=_quote(websites.api_url()), boundary=boundary)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    try:
        res = execute(TRAEFIK_SERVER, script, timeout=40, max_output=6_000_000)
    except Exception as exc:
        detail = str(exc)[:160] if isinstance(exc, (RuntimeError, ValueError)) else ''
        return {'ok': False, 'error': f'could not reach the traefik host: {type(exc).__name__}' + (f' ({detail})' if detail else '')}
    if res.get('timed_out'):
        return {'ok': False, 'error': 'the traefik host did not answer in time'}
    found = {}
    for words, body in websites._sections(res.get('stdout'), boundary):
        if words[0] != 'API' or len(words) < 3:
            continue
        if words[2] != '0':
            first = (body.strip().splitlines() or [''])[0]
            return {'ok': False, 'error': 'Traefik API not reachable' + (' (' + first[:160] + ')' if first else '')}
        try:
            found[words[1]] = json.loads(body)
        except ValueError:
            return {'ok': False, 'error': 'the Traefik API answered something that is not JSON'}
    if not isinstance(found.get('routers'), list):
        return {'ok': False, 'error': 'the Traefik API gave no router list'}
    lst = lambda v: [x for x in v if isinstance(x, dict) and isinstance(x.get('name'), str)] if isinstance(v, list) else []
    return {'ok': True, 'error': None, 'routers': lst(found.get('routers')), 'services': lst(found.get('services')),
            'middlewares': lst(found.get('middlewares')), 'entrypoints': [e.get('name') for e in lst(found.get('entrypoints'))]}


def cached_state(execute):
    if _state_cache['state'] is not None and clock() - _state_cache['at'] < STATE_CACHE_SECONDS:
        return _state_cache['state']
    state = traefik_state(execute)
    _state_cache.update(at=clock(), state=state)
    return state


def reset():
    _state_cache.update(at=0.0, state=None)
    _dns_cache.clear()


def _short(name):
    return str(name).split('@', 1)[0]


def _provider(item):
    return item.get('provider') or (item['name'].split('@', 1)[1] if '@' in item['name'] else '')


def _errors(item):
    err = item.get('error')
    if isinstance(err, list):
        return '; '.join(str(e) for e in err)[:300]
    return str(err)[:300] if err else ''


def resolver_in_use(state):
    """(resolver, source): TRAEFIK_CERT_RESOLVER, else the one most routers use, else letsencrypt."""
    if env_cert_resolver():
        return env_cert_resolver(), 'setting'
    counts = {}
    for r in state.get('routers') or [] if state and state.get('ok') else []:
        tls = r.get('tls') if isinstance(r.get('tls'), dict) else {}
        res = tls.get('certResolver')
        if isinstance(res, str) and TOKEN.fullmatch(res) and not _short(r['name']).startswith(PREFIX):
            counts[res] = counts.get(res, 0) + 1
    if counts:
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0], 'routers'
    return 'letsencrypt', 'default'


def ours(item, managed_names):
    """A Traefik object written by the managed file: file provider and one of the names the current rows render."""
    return _provider(item) == 'file' and _short(item['name']) in managed_names


def rendered_names(rows):
    out = set()
    for r in rows.values():
        if r['status'] == 'active':
            n = names(r)
            out |= {n['router'], n['service'], *n['middlewares']}
    return out


def other_routers(state, managed_names):
    """Routers not from the managed file: name, provider, hosts, rule (read-only on the dashboard)."""
    out = []
    for r in state.get('routers') or []:
        if ours(r, managed_names) or _provider(r) == 'internal':
            continue
        hosts, paths, _ = websites.rule_hosts(r.get('rule'))
        out.append({'name': _short(r['name']), 'provider': _provider(r), 'hosts': hosts, 'paths': paths,
                    'rule': str(r.get('rule') or '')[:300], 'status': r.get('status')})
    return sorted(out, key=lambda r: (r['hosts'][:1], r['name']))


def conflicts(row, rows, state):
    """Reasons this row cannot be saved: a name used by another Traefik object, or a host another router serves."""
    managed_names = rendered_names(rows)
    problems = []
    n = names(row)
    for kind, key in (('router', 'routers'), ('service', 'services'), ('middleware', 'middlewares')):
        wanted = {n['router']} if kind == 'router' else {n['service']} if kind == 'service' else set(n['middlewares'])
        for item in state.get(key) or []:
            if _short(item['name']) in wanted and not ours(item, managed_names):
                problems.append(f'a {kind} named {_short(item["name"])} already exists ({_provider(item) or "unknown"} provider); '
                                'names are global across Traefik\'s files')
    for other in other_routers(state, managed_names):
        if row['host'] in other['hosts']:
            problems.append(f'{row["host"]} is already served by router {other["name"]} ({other["provider"]}'
                            + (', hand-written' if other['provider'] == 'file' else '') + '); change or remove that route first')
    for name, r in rows.items():
        if name != row['name'] and r['host'] == row['host'] and r['path_prefix'] == row['path_prefix']:
            problems.append(f'managed route {name} already serves {row["host"]}{row["path_prefix"]}')
    return problems


# ── DNS ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _resolve(host):
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


resolver = _resolve  # replaced in tests


def dns_check(host, known=(), protection='public'):
    """{status: ok|lan|elsewhere|missing|error, addresses, expected, message, record}."""
    if not HOSTNAME.fullmatch(host or ''):
        raise RouteError(400, 'invalid_host', 'the host is a full name like shop.example.com')
    hit = _dns_cache.get(host)
    if hit and clock() - hit[0] < DNS_CACHE_SECONDS:
        addresses, failure = hit[1], hit[2]
    else:
        failure = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(resolver, host)
            try:
                addresses = list(fut.result(timeout=DNS_TIMEOUT))
            except FutureTimeout:
                addresses, failure = [], 'timeout'
            except (socket.gaierror, UnicodeError):
                addresses = []
            except OSError:
                addresses, failure = [], 'error'
        _dns_cache[host] = (clock(), addresses, failure)
    expected = public_ips()
    domain = domain_of(host, known)
    rel = '@' if host == domain else (host[:-len(domain) - 1] if domain else host)
    v4 = next((ip for ip in expected if ':' not in ip), expected[0] if expected else '<your public IP>')
    record = f'{rel} A {v4}' + (f'   (in the DNS zone of {domain})' if domain else '')
    out = {'host': host, 'addresses': addresses, 'expected': expected, 'record': record}
    if failure:
        return {**out, 'status': 'error', 'message': 'DNS lookup ' + ('timed out' if failure == 'timeout' else 'failed') + ' from the gateway'}
    if not addresses:
        return {**out, 'status': 'missing', 'message': f'{host} has no DNS record. Add {rel} A {v4}; the certificate cannot be issued '
                                                       'until it exists (the route still works on the LAN with a hosts override).'}
    lan = traefik_lan_ip()
    if not expected:
        return {**out, 'status': 'unknown', 'message': 'resolves to ' + ', '.join(addresses) + '; the public IP is unknown '
                                                       '(set public_ips in the inventory) so it cannot be checked'}
    if all(a in expected for a in addresses):
        return {**out, 'status': 'ok', 'message': 'points at ' + ', '.join(addresses)}
    if all(a in expected or a == lan for a in addresses):
        return {**out, 'status': 'lan', 'message': f'resolves to Traefik\'s LAN address {lan} here (local DNS override); '
                                                   f'public DNS must still point at {v4} for the certificate'
                                                   + ('' if 'lan_only' in protection else ' and for visitors')}
    others = [a for a in addresses if a not in expected and a != lan]
    return {**out, 'status': 'elsewhere', 'message': f'{host} points at {", ".join(others)}, not at {", ".join(expected)}. '
                                                     f'Change the record to {rel} A {v4}' + (' and remove the AAAA record' if any(':' in a for a in others) else '')
                                                     + '; the certificate cannot be issued until it does.'}


# ── writing the file ─────────────────────────────────────────────────────────────────────────────────────────────
_WRITE = """set -u
F={file}; D={backup}; S={sha}; M={mode}
fail() {{ echo "HLA_ERR $*"; exit 3; }}
command -v flock >/dev/null 2>&1 || fail 'flock is not installed on the traefik host'
[ -d "$D" ] && [ -w "$D" ] || fail "the backup folder $D is missing or not writable for $(id -un)"
[ -f "$F" ] || fail "$F does not exist: create it once as root (see the gateway README)"
[ -w "$F" ] || fail "$F is not writable for $(id -un) (see the gateway README)"
exec 9>>"$D/.hla-routes.lock" || fail 'cannot open the lock file'
flock -w 15 9 || fail 'another route change is still running'
umask 077; T="$D/.incoming.$$"
cat > "$T" || fail 'could not store the new content'
[ "$(sha256sum < "$T" | cut -d' ' -f1)" = "$S" ] || {{ rm -f -- "$T"; fail 'the new content arrived damaged'; }}
echo "HLA_PREV $(base64 -w0 < "$F")"
if [ "$M" = write ]; then
 b=$(basename -- "$F")
 cp -- "$F" "$D/$b.prev" && cp -- "$F" "$D/$b.$(date -u +%Y%m%dT%H%M%SZ)" || {{ rm -f -- "$T"; fail 'could not back up the current file'; }}
 ls -1t -- "$D/$b".2* 2>/dev/null | tail -n +31 | while IFS= read -r old; do rm -f -- "$old"; done
fi
cat -- "$T" > "$F" || {{ rm -f -- "$T"; fail 'could not write the managed file'; }}
rm -f -- "$T"
[ "$(sha256sum < "$F" | cut -d' ' -f1)" = "$S" ] || fail 'the managed file does not hold the new content after writing'
echo "HLA_OK $S"
"""


def write_script(sha, mode):
    if not re.fullmatch(r'[0-9a-f]{64}', sha) or mode not in ('write', 'restore'):
        raise RouteError(500, 'bad_write')
    return _WRITE.format(file=_quote(managed_file()), backup=_quote(backup_dir()), sha=sha, mode=mode)


def upload(execute, content, mode='write'):
    """Write the managed file; returns the previous content (bytes). RouteError when it was not written."""
    data = content.encode('utf-8')
    sha = hashlib.sha256(data).hexdigest()
    try:
        res = execute(TRAEFIK_SERVER, write_script(sha, mode), timeout=40, max_output=2_000_000, stdin=data)
    except RouteError:
        raise
    except Exception as exc:
        raise RouteError(502, 'traefik_unreachable', f'could not reach the traefik host: {type(exc).__name__}') from None
    out = res.get('stdout') or ''
    found = re.search(r'^HLA_PREV ([A-Za-z0-9+/=]*)$', out, re.M)
    try:
        previous = base64.b64decode(found.group(1)) if found else None
    except ValueError:
        previous = None
    err = re.search(r'^HLA_ERR (.*)$', out, re.M)
    if err or res.get('timed_out') or not re.search(r'^HLA_OK ' + sha + r'$', out, re.M):
        exc = RouteError(502, 'write_failed', err.group(1).strip()[:300] if err else 'the traefik host did not confirm the write'
                         + (' (timed out)' if res.get('timed_out') else '') + (': ' + (res.get('stderr') or '').strip()[:200] if res.get('stderr') else ''))
        exc.previous = previous  # set once the script got past reading the old content: the file may have changed
        raise exc
    return previous


def _restore(execute, previous):
    """(restored, error): write the previous content back."""
    if previous is None:
        return False, 'no previous content'
    try:
        upload(execute, previous.decode('utf-8', 'replace'), 'restore')
        return True, None
    except RouteError as exc:
        return False, exc.detail or exc.error


def _enabled(state):
    return {r['name'] for r in state.get('routers') or [] if r.get('status') == 'enabled' and not r.get('error')}


def verify_once(state, rows, removed, before):
    """(done, error): done when Traefik shows exactly the rows; error when it shows a definite problem."""
    if not state.get('ok'):
        return False, state.get('error') or 'Traefik API not reachable'
    by_name = {r['name']: r for r in state['routers']}
    svc = {s['name']: s for s in state.get('services') or []}
    mws = {m['name']: m for m in state.get('middlewares') or []}
    pending = False
    for row in rows.values():
        if row['status'] != 'active':
            continue
        n = names(row)
        r = by_name.get(n['router'] + '@file')
        if not r or r.get('rule') != rule(row) or [_short(m) for m in r.get('middlewares') or []] != n['middlewares'] \
                or _short(r.get('service') or '') != n['service']:
            pending = True
            continue
        if _errors(r) or r.get('status') not in (None, 'enabled'):
            return False, f'router {n["router"]}: ' + (_errors(r) or 'status ' + str(r.get('status')))
        s = svc.get(n['service'] + '@file')
        if s and (_errors(s) or s.get('status') not in (None, 'enabled')):
            return False, f'service {n["service"]}: ' + (_errors(s) or 'status ' + str(s.get('status')))
        for m in n['middlewares']:
            item = mws.get(m + '@file')
            if item and (_errors(item) or item.get('status') not in (None, 'enabled')):
                return False, f'middleware {m}: ' + (_errors(item) or 'status ' + str(item.get('status')))
    for name in removed:
        if name + '@file' in by_name:
            pending = True
    if pending:
        return False, None
    now_enabled = _enabled(state)
    broken = sorted(n for n in before if n not in now_enabled and not (_short(n) in removed and n.endswith('@file')))
    if broken:
        return False, 'other routers stopped working: ' + ', '.join(broken[:5])
    return True, None


def verify(execute, rows, removed, before, seconds=None):
    """Poll the API until the rows are live (True, None) or a problem shows / time runs out (False, reason)."""
    deadline = clock() + (VERIFY_SECONDS if seconds is None else seconds)
    last = None
    while True:
        sleep(POLL_SECONDS)
        state = traefik_state(execute)
        done, error = verify_once(state, rows, removed, before)
        if done:
            return True, None
        last = error or last
        definite = error and state.get('ok')
        if definite or clock() >= deadline:
            return False, error or last or 'Traefik did not load the new routes within %d s (is the file provider watching %s?)' % (
                VERIFY_SECONDS if seconds is None else seconds, managed_file().rsplit('/', 1)[0])


def apply(db, execute, before_rows, after_rows, actor, note, audit):
    """Render, check, upload, verify; roll back on failure. Stores after_rows only when Traefik took them."""
    with _lock:
        state = traefik_state(execute)
        if not state['ok']:
            raise RouteError(502, 'traefik_api_unreachable', (state['error'] or 'Traefik API not reachable')
                             + '; routes are only changed while the API can confirm them')
        resolver_name, _ = resolver_in_use(state)
        for name, row in after_rows.items():
            if row['status'] == 'active' and before_rows.get(name) != row:
                problems = conflicts(row, {k: v for k, v in after_rows.items()}, state)
                if problems:
                    raise RouteError(409, 'route_conflict', '; '.join(problems)[:600])
                for e in row['entrypoints']:
                    if state.get('entrypoints') and e not in state['entrypoints']:
                        raise RouteError(400, 'unknown_entrypoint', f'Traefik has no entry point {e} ({", ".join(state["entrypoints"])})')
        content = render(after_rows, resolver_name)
        check_rendered(content, after_rows, resolver_name)
        managed_before = rendered_names(before_rows)
        removed = sorted({names(r)['router'] for r in before_rows.values() if r['status'] == 'active'}
                         - {names(r)['router'] for r in after_rows.values() if r['status'] == 'active'})
        before_ok = {n for n in _enabled(state) if not (n.endswith('@file') and _short(n) in managed_before)}
        try:
            previous = upload(execute, content, 'write')
        except RouteError as exc:
            if getattr(exc, 'previous', None) is not None:  # failed after the old content was read: put it back
                restored, restore_error = _restore(execute, exc.previous)
                audit('route_rollback', f'{note}: write failed ({exc.detail}); ' + ('previous file restored' if restored
                                                                                   else 'RESTORE FAILED: ' + str(restore_error)), False)
                reset()
            raise
        ok, error = verify(execute, after_rows, removed, before_ok)
        if not ok:
            restored, restore_error = _restore(execute, previous)
            restored_ok = False
            if restored:
                back, _ = verify(execute, before_rows, [], set(), seconds=min(VERIFY_SECONDS, 6))
                restored_ok = back
            audit('route_rollback', f'{note}: {error}; ' + ('previous file restored' + ('' if restored_ok else ' (Traefik not confirmed yet)')
                                                            if restored else 'RESTORE FAILED: ' + str(restore_error or 'no previous content')), False)
            reset()
            websites.reset()
            raise RouteError(502, 'traefik_rejected', f'Traefik did not accept the change: {error}. '
                             + ('The previous file was restored.' if restored else 'Restoring the previous file FAILED: '
                                + str(restore_error or 'no previous content') + '; check ' + managed_file() + ' on the traefik host.'))
        _store(db, before_rows, after_rows)
        sha = hashlib.sha256(content.encode()).hexdigest()
        db.execute('INSERT INTO route_files(created_at,sha256,content,actor,note) VALUES (?,?,?,?,?)', (int(time.time()), sha, content, actor, note[:300]))
        db.execute('DELETE FROM route_files WHERE id NOT IN (SELECT id FROM route_files ORDER BY id DESC LIMIT 30)')
        reset()
        websites.reset()  # the inventory shows the new route at once
        return {'sha256': sha, 'cert_resolver': resolver_name}


def describe(row):
    return (f"{row['host']}{row['path_prefix']} -> {row['backend']} ({row['environment']}, {row['protection']}"
            + (f", user {row['basic_user']}, password {{{{secret:{row['basic_secret']}}}}}" if row.get('basic_user') else '')
            + (', disabled' if row['status'] != 'active' else '') + ')')


def save(db, execute, name, body, actor, audit, create=False, note=None):
    if not isinstance(body, dict):
        raise RouteError(400, 'invalid_payload')
    before = load_rows(db)
    current = before.get(name)
    if create and current:
        raise RouteError(409, 'route_exists', f'a managed route named {name} exists already; edit it instead')
    if not current and len(before) >= MAX_ROUTES:
        raise RouteError(400, 'too_many_routes', f'at most {MAX_ROUTES} managed routes')
    now = int(time.time())
    row = build_row(name, body, current, domains(db), actor, now)
    if 'basic_auth' in row['protection'] and (not row['basic_hash'] or body.get('refresh_password') is True):
        password = ensure_password(row['basic_secret'], row['host'], actor)
        row['basic_hash'] = apr1(password)
        password = None
    after = {**before, name: row}
    action = 'route_updated' if current else 'route_created'
    result = apply(db, execute, before, after, actor, (note + ': ' if note else '') + f'{action[6:]} {name}', audit)
    audit(action, f'{name}: ' + describe(row) + (f' [{note}]' if note else ''), True)
    return {'route': view(row), **result, 'created': not current}


def delete(db, execute, name, actor, audit):
    before = load_rows(db)
    if name not in before:
        raise RouteError(404, 'unknown_route', 'no managed route named ' + str(name)[:40])
    after = {k: v for k, v in before.items() if k != name}
    result = apply(db, execute, before, after, actor, f'delete {name}', audit)
    audit('route_deleted', f'{name}: ' + describe(before[name]), True)
    return {'deleted': True, 'name': name, **result}


# ── agent requests ───────────────────────────────────────────────────────────────────────────────────────────────
def file_request(db, body, agent):
    """Store a pending request from an agent (never touches Traefik)."""
    known = domains(db)
    host = clean_host(body.get('host'), known)
    env = body.get('environment') or 'staging'
    if env not in ENVIRONMENTS:
        raise RouteError(400, 'invalid_environment', 'environment is production or staging')
    protection = body.get('protection') or DEFAULT_PROTECTION[env]
    if protection not in PROTECTIONS:
        raise RouteError(400, 'invalid_protection', 'protection is ' + ', '.join(PROTECTIONS))
    port = parse_backend({'backend_port': body.get('backend_port')})
    reason = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(body.get('reason') or '')).strip()[:500]
    if not reason:
        raise RouteError(400, 'reason_required', 'say why the route is needed (reason)')
    project = body.get('project_id')
    project = str(project) if project and _PROJECT.fullmatch(str(project)) else None
    for r in load_rows(db).values():
        if r['host'] == host:
            raise RouteError(409, 'route_exists', f'{host} already has a managed route ({r["name"]} -> {r["backend"]})')
    if db.execute("SELECT count(*) FROM route_requests WHERE status='pending'").fetchone()[0] >= MAX_PENDING:
        raise RouteError(429, 'too_many_requests', 'too many route requests are waiting for the owner')
    row = db.execute("SELECT id FROM route_requests WHERE status='pending' AND host=?", (host,)).fetchone()
    values = (backend_port(port), env, protection, reason, project, agent)
    if row:
        db.execute('UPDATE route_requests SET backend_port=?,environment=?,protection=?,reason=?,project_id=?,agent_id=?,created_at=? WHERE id=?',
                   (*values, int(time.time()), row[0]))
        ident, again = row[0], True
    else:
        ident = db.execute('INSERT INTO route_requests(created_at,host,backend_port,environment,protection,reason,project_id,agent_id) '
                           'VALUES (?,?,?,?,?,?,?,?)', (int(time.time()), host, *values)).lastrowid
        again = False
    return {'id': ident, 'status': 'pending', 'updated': again, 'host': host, 'backend': port, 'environment': env, 'protection': protection,
            'message': 'Request stored. The owner approves or rejects it on the dashboard (Needs you); nothing changes on Traefik until then.'}


REQUEST_COLUMNS = ('id', 'created_at', 'agent_id', 'host', 'backend_port', 'environment', 'protection', 'reason', 'project_id', 'task_id',
                   'status', 'decided_at', 'decided_by', 'decision_note', 'route_name')


def requests(db, status=None, limit=50):
    q = 'SELECT ' + ','.join(REQUEST_COLUMNS) + ' FROM route_requests'
    args = []
    if status:
        q += ' WHERE status=?'
        args.append(status)
    q += ' ORDER BY id DESC LIMIT ?'
    args.append(max(1, min(int(limit), 200)))
    out = []
    for values in db.execute(q, args):
        r = dict(zip(REQUEST_COLUMNS, values))
        r['suggested_name'] = name_for_host(r['host'])
        r['backend'] = f'http://{website_ip()}:{r["backend_port"]}'
        out.append(r)
    return out


def request_row(db, ident):
    try:
        ident = int(ident)
    except (TypeError, ValueError):
        raise RouteError(404, 'unknown_request') from None
    values = db.execute('SELECT ' + ','.join(REQUEST_COLUMNS) + ' FROM route_requests WHERE id=?', (ident,)).fetchone()
    if not values:
        raise RouteError(404, 'unknown_request', 'no route request #' + str(ident))
    return dict(zip(REQUEST_COLUMNS, values))


def approve(db, execute, ident, body, actor, audit):
    req = request_row(db, ident)
    if req['status'] != 'pending':
        raise RouteError(409, 'request_decided', f'request #{req["id"]} is {req["status"]} already')
    name = (body or {}).get('name') or name_for_host(req['host'])
    data = {'host': req['host'], 'environment': req['environment'], 'protection': req['protection'], 'backend_port': req['backend_port'],
            'project_id': req['project_id']}
    for key in ('protection', 'environment', 'backend_port', 'path_prefix', 'basic_user'):
        if (body or {}).get(key) not in (None, ''):
            data[key] = body[key]
    result = save(db, execute, name, data, actor, audit, create=True,
                  note=f'request #{req["id"]} from agent {str(req["agent_id"] or "?")[:36]}')
    db.execute("UPDATE route_requests SET status='approved',decided_at=?,decided_by=?,route_name=? WHERE id=?",
               (int(time.time()), actor, name, req['id']))
    return {**result, 'request_id': req['id']}


def reject(db, ident, note, actor):
    req = request_row(db, ident)
    if req['status'] != 'pending':
        raise RouteError(409, 'request_decided', f'request #{req["id"]} is {req["status"]} already')
    note = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(note or '')).strip()[:300]
    db.execute("UPDATE route_requests SET status='rejected',decided_at=?,decided_by=?,decision_note=? WHERE id=?",
               (int(time.time()), actor, note, req['id']))
    return {'request_id': req['id'], 'status': 'rejected', 'host': req['host']}


# ── the listing ──────────────────────────────────────────────────────────────────────────────────────────────────
def view(row):
    """A row for the dashboard: never the hash; the password by its secret name only."""
    return {**{k: row[k] for k in COLUMNS if k != 'basic_hash'}, 'url': 'https://' + row['host'] + (row['path_prefix'] or '/'),
            'backend_port': backend_port(row['backend']), 'router': names(row)['router'], 'rule': rule(row),
            'middlewares': names(row)['middlewares'], 'secret_reference': '{{secret:' + row['basic_secret'] + '}}' if row.get('basic_secret') else None}


def listing(db, execute, dns=True):
    rows = load_rows(db)
    known = domains(db)
    state = cached_state(execute)
    managed_names = rendered_names(rows)
    resolver_name, source = resolver_in_use(state)
    by_name = {r['name']: r for r in state.get('routers') or []} if state.get('ok') else {}
    out = []
    checks = {}
    if dns and rows:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {name: pool.submit(dns_check, r['host'], known, r['protection']) for name, r in rows.items()}
            for name, fut in futures.items():
                try:
                    checks[name] = fut.result(timeout=DNS_TIMEOUT + 2)
                except Exception:
                    checks[name] = {'status': 'error', 'message': 'DNS lookup failed'}
    for name, r in rows.items():
        item = view(r)
        live = by_name.get(names(r)['router'] + '@file')
        if not state.get('ok'):
            item['traefik'] = {'status': 'unknown', 'error': None}
        elif r['status'] != 'active':
            item['traefik'] = {'status': 'disabled', 'error': None}
        elif not live:
            item['traefik'] = {'status': 'missing', 'error': 'Traefik has no router ' + names(r)['router'] + '@file'}
        else:
            item['traefik'] = {'status': live.get('status') or 'enabled', 'error': _errors(live) or None}
        item['dns'] = checks.get(name)
        item['domain'] = domain_of(r['host'], known)
        item['subdomain'] = r['host'][:-len(item['domain']) - 1] if item['domain'] and r['host'] != item['domain'] else ''
        out.append(item)
    others = other_routers(state, managed_names) if state.get('ok') else []
    suggestions = sorted({registrable(h) for o in others for h in o['hosts']} - set(known))
    try:
        files = {'managed_file': managed_file(), 'backup_dir': backup_dir(), 'settings_error': None}
    except RouteError as exc:
        files = {'managed_file': None, 'backup_dir': None, 'settings_error': exc.detail}
    return {'routes': out, 'domains': known, 'domain_suggestions': suggestions, 'other_routers': others,
            'requests': requests(db, limit=30), 'traefik_ok': bool(state.get('ok')), 'traefik_error': state.get('error'),
            'cert_resolver': resolver_name, 'cert_resolver_source': source, 'public_ips': public_ips(), 'traefik_lan_ip': traefik_lan_ip(),
            'backend_hosts': backend_hosts(), 'website_ip': website_ip(), 'environments': list(ENVIRONMENTS),
            'protections': list(PROTECTIONS), 'default_protection': DEFAULT_PROTECTION, 'lan_ranges': list(LAN_RANGES),
            'agent_level': AGENT_LEVEL, **files}
