"""Local preview for UI work: real app + throwaway Postgres + mocked TypeSafe/OpenRouter, seeded with realistic data.

Login password: pw. Serves http://127.0.0.1:8765/ui/ (DEV_PORT to change).
"""
import asyncio, json, os, re, sys, uuid, random
from datetime import datetime, timedelta, timezone

# Wipes the target database's public schema: never point this at the deployed database.
TEST_DB = os.environ.get('TEST_DATABASE_URL')
if not TEST_DB or os.environ.get('I_UNDERSTAND_THIS_WIPES_TEST_DB') != '1':
    sys.exit('Set TEST_DATABASE_URL to a throwaway Postgres and I_UNDERSTAND_THIS_WIPES_TEST_DB=1')
os.environ.update({
    'DATABASE_URL': TEST_DB,
    'AGENT_PLATFORM_TOKEN': 't' * 64, 'DASHBOARD_PASSWORD': 'pw',
    'TYPESAFE_API_KEY': 'x', 'HLA_RISK_REVIEW': 'true', 'OPENROUTER_API_KEY': 'x',
    'OPENROUTER_WORKER_MODEL': 'qwen/qwen3-235b-a22b', 'AGENT_ALLOWED_MODELS': 'qwen/qwen3-235b-a22b,anthropic/claude-sonnet-5.5',
})
# HLA_ATTACHMENTS_V1: uploaded files go to a throwaway folder (the container uses the /data/attachments volume).
import tempfile as _tempfile
os.environ.setdefault('HLA_ATTACHMENTS_DIR', _tempfile.mkdtemp(prefix='hla-preview-files-'))
# HLA_ENGINES_V1: fake Claude Code / Codex CLIs (tests/fake_engines) for the Subscriptions page: Claude signed in
# (Max plan), ChatGPT signed out. Its sign-in shows a device code and completes by itself after 4 seconds; a Claude
# sign-in (after signing out) accepts the code good#fakestate123.
_FAKES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fake_engines')
os.environ.setdefault('HLA_ENGINES_DIR', _tempfile.mkdtemp(prefix='mme-preview-', dir='/tmp'))
os.environ.setdefault('HLA_CLAUDE_BIN', os.path.join(_FAKES, 'claude'))
os.environ.setdefault('HLA_CODEX_BIN', os.path.join(_FAKES, 'codex'))
for _dir, _file, _text in (('claude/.claude', '.credentials.json', '{"fake": true}'), ('codex', 'auto-approve', '4'),
                           ('claude', 'fake.json', '{"steps": [{"text": "pong"}]}'), ('codex', 'fake.json', '{"steps": [{"text": "pong"}]}')):
    os.makedirs(os.path.join(os.environ['HLA_ENGINES_DIR'], _dir), mode=0o700, exist_ok=True)
    with open(os.path.join(os.environ['HLA_ENGINES_DIR'], _dir, _file), 'w') as _fh:
        _fh.write(_text)
os.environ.update(json.loads(os.environ.get('DEV_EXTRA_ENV', '{}')))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import httpx, psycopg, sqlite3
# The real gateway policy rules, on an in-memory database, so the Policy page behaves like production.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'ssh-gateway'))
from gateway import policy as gw_policy, deploy as gw_deploy, audit as gw_audit, mcp as gw_mcp
import threading as _threading_db
class _Rows:
    """A finished statement: rows read under the lock, so another thread's statement cannot mix into them."""
    def __init__(self, cur):
        self.rows = cur.fetchall() if cur.description else []
        self.rowcount, self.lastrowid, self.description, self._i = cur.rowcount, cur.lastrowid, cur.description, 0
    def fetchone(self):
        if self._i >= len(self.rows): return None
        self._i += 1
        return self.rows[self._i - 1]
    def fetchall(self):
        out, self._i = self.rows[self._i:], len(self.rows)
        return out
    def __iter__(self): return iter(self.fetchall())
class _SharedDB:
    """The preview's one in-memory gateway database, used from the event loop and from worker threads (the real gateway
    opens one connection per request): every statement runs and is read out under one lock."""
    def __init__(self, db): self._db, self._lock = db, _threading_db.RLock()
    def execute(self, *a):
        with self._lock: return _Rows(self._db.execute(*a))
    def executemany(self, *a):
        with self._lock: return _Rows(self._db.executemany(*a))
    def executescript(self, *a):
        with self._lock: return _Rows(self._db.executescript(*a))
    def commit(self):
        with self._lock: return self._db.commit()
    def rollback(self):
        with self._lock: return self._db.rollback()
    def __getattr__(self, name): return getattr(self._db, name)
    def __enter__(self): return self
    def __exit__(self, *a): return False
GW_DB = _SharedDB(sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None))
gw_policy.migrate(GW_DB)
# HLA_MCP_V1: the real MCP registry and level rules; "Refresh tools" loads a canned tool list instead of connecting.
gw_mcp.migrate(GW_DB)
MCP_CREDENTIALS = ['GITHUB_TOKEN', 'NOTION_TOKEN', 'HA_MCP_KEY']
MCP_TOOLS = {
    'notion': [
        {'name': 'search', 'title': 'Search', 'description': 'Search pages and databases in the workspace by title and content.',
         'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}, 'annotations': {'readOnlyHint': True}},
        {'name': 'fetch_page', 'description': 'Read one page as Markdown.', 'input_schema': {'type': 'object', 'properties': {'id': {'type': 'string'}}},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
        {'name': 'create_page', 'description': 'Create a page under a parent page or database.', 'input_schema': {'type': 'object'}, 'annotations': {}},
        {'name': 'update_page', 'description': 'Change the properties or content of a page.', 'input_schema': {'type': 'object'}, 'annotations': {'idempotentHint': True}},
        {'name': 'delete_block', 'description': 'Delete a block (and everything inside it) from a page.', 'input_schema': {'type': 'object'},
         'annotations': {'destructiveHint': True}}],
    # Seen on a later refresh: a tool the server added (off until reviewed) and one whose description changed.
    'notion-later': [
        {'name': 'export_workspace', 'description': 'Export the whole workspace as a ZIP to a URL.', 'input_schema': {'type': 'object'},
         'annotations': {'readOnlyHint': True}, 'new': True}],
    'files-lan': [
        {'name': 'list_directory', 'description': 'List a folder of the shared drive.', 'input_schema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
        {'name': 'read_file', 'description': 'Read a text file.', 'input_schema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
        {'name': 'write_file', 'description': 'Create or overwrite a text file.', 'input_schema': {'type': 'object'}, 'annotations': {'destructiveHint': True}}],
}
gw_deploy.migrate(GW_DB)
# HLA_AUDIT_V1: the gateway's audit table and its real read code; calls below are recorded like the gateway does.
GW_DB.execute('CREATE TABLE audit (id INTEGER PRIMARY KEY, timestamp INTEGER, server TEXT, action TEXT, digest TEXT, exit_code INTEGER, timed_out INTEGER, truncated INTEGER, actor TEXT)')
gw_audit.migrate(GW_DB)
import hashlib, time as _time
def gw_record(server, action, detail, code=0, actor=None, agent=None, task=None, ts=None):
    GW_DB.execute('INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated,actor,agent_id,detail,task_id) VALUES (?,?,?,?,?,0,0,?,?,?,?)',
                  (int(ts or _time.time()), server, action, hashlib.sha256((server + '\n' + detail).encode()).hexdigest(), code, actor,
                   gw_audit.agent_id(agent), gw_audit.mask(detail), gw_audit.task_id(task)))
os.environ['POLICY_ADMIN_TOKEN'] = 'a' * 40
# Sandbox reset against a mocked pve2 (any valid PEM bundle works: the mock transport does no TLS).
import certifi
os.environ.update({'SANDBOX_PROXMOX_TOKEN_ID': 'Agents@pam!agents', 'SANDBOX_PROXMOX_TOKEN_SECRET': 'preview',
                   'SANDBOX_PROXMOX_CA': certifi.where()})

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as db:
    db.execute('DROP SCHEMA public CASCADE'); db.execute('CREATE SCHEMA public')

LONG_MD = """## Fileserver permissions — findings

The file manager at `192.168.10.108:8080` runs as **uid 1000** inside the container, but the bind mount is owned by `root:root`.

| Path | Owner | Mode |
|---|---|---|
| `/srv/files` | root:root | 0755 |
| `/srv/files/shared` | root:root | 0755 |

### Proposed change
1. `chown -R 1000:1000 /srv/files/shared`
2. Verify with `ls -ld /srv/files/shared`

```bash
sudo chown -R 1000:1000 /srv/files/shared
```

> This change needs separate approval; nothing was modified.
""" + "\nAdditional detail line for length. " * 25

# HLA_CONNECTIONS_V1: the gateway's real credential store and HTTP tool rules on the in-memory database; GitHub and
# the outgoing HTTP request are canned (DNS answers a public address, the "API" echoes what it got, never the key).
import base64 as _b64
os.environ['GATEWAY_CREDENTIAL_KEY'] = _b64.b64encode(os.urandom(32)).decode()
from gateway import credentials as gw_creds, http_tools as gw_http
gw_creds.migrate(GW_DB); gw_http.migrate(GW_DB)
gw_http._resolve = lambda host, port: ['93.184.216.34']
def _preview_send(method, host, port, address, target, headers, data, timeout, limit):
    body = {'host': host, 'path': target.split('?')[0], 'query_names': sorted(k.split('=')[0] for k in target.split('?')[1].split('&')) if '?' in target else [],
            'authorized': any(k.lower() == 'authorization' for k in headers), 'temperature': 11.5, 'summary': 'Light rain'}
    return 200, {'content-type': 'application/json'}, json.dumps(body).encode()
gw_http._send = _preview_send
PREVIEW_REPOS = [{'full_name': n, 'private': p, 'archived': False, 'default_branch': 'main', 'permissions': {'push': True, 'admin': False}}
                 for n, p in (('example/site', False), ('example/agent-platform', True), ('example/homelab', True), ('example/notes', True),
                              ('example/old-blog', False), ('octo-org/shared-ci', False), ('example/dotfiles', False), ('example/bakery-site', True),
                              ('example/ha-dashboards', True))]

def connections_gateway(request):
    path, method = request.url.path, request.method
    stored = lambda: gw_creds.values(lambda: _NoClose(GW_DB))
    def reply(status, payload):  # like the gateway: no stored value ever leaves
        gw_creds._cache['at'] = 0
        return httpx.Response(status, json=gw_creds.redact(payload, stored()))
    try:
        if path == '/v1/credentials' and method == 'GET':
            return reply(200, gw_creds.listing(GW_DB))
        if path.startswith('/v1/credentials/'):
            name = path.rsplit('/', 1)[1]
            if method == 'DELETE':
                gone = gw_creds.delete(GW_DB, name)
                return reply(200 if gone else 404, {'deleted': gone, 'name': name})
            body = json.loads(request.content)
            item = gw_creds.put(GW_DB, name, body.get('value'), kind=body.get('kind') or 'token', description=body.get('description') or '',
                                actor=body.get('actor') or '')
            gw_record('gateway', 'credential_stored', name + ' (' + item['kind'] + ')', None, actor=body.get('actor'))
            return reply(200, item)
        if path == '/v1/github':
            body = json.loads(request.content)
            token = stored().get('GITHUB_TOKEN')
            if body['operation'] == 'status':
                if not token:
                    return reply(200, {'service': 'github', 'configured': False, 'source': None, 'ok': False, 'error': 'github_not_configured',
                                       'detail': 'no GITHUB_TOKEN in .env.gateway or in Infisical /GitHub'})
                return reply(200, {'service': 'github', 'configured': True, 'source': 'store', 'ok': True, 'login': 'hla-bot',
                                   'name': 'Homelab Agents Bot', 'token_type': 'fine-grained', 'scopes': None})
            if not token:
                return reply(503, {'error': 'github_not_configured', 'detail': 'no GITHUB_TOKEN'})
            allow = gw_policy.github_repos(GW_DB)
            return reply(200, {'service': 'github', 'truncated': False, 'repos': [
                {'repo': r['full_name'].lower(), 'full_name': r['full_name'], 'private': r['private'], 'archived': False, 'default_branch': 'main',
                 'push': True, 'admin': False, 'allowed': r['full_name'].lower() in allow, 'merge': bool((allow.get(r['full_name'].lower()) or {}).get('merge'))}
                for r in PREVIEW_REPOS]})
        if path == '/v1/http_tools' and method == 'GET':
            names = {c['name'] for c in gw_creds.listing(GW_DB)['credentials']}
            return reply(200, {'tools': [{**t, 'credential_stored': bool(t.get('auth')) and t['auth']['credential'] in names}
                                         for t in gw_http.listing(GW_DB)], 'credentials_configured': True})
        if path.startswith('/v1/http_tools/'):
            name = path.rsplit('/', 1)[1]
            if method == 'DELETE':
                gone = gw_http.delete(GW_DB, name)
                return reply(200 if gone else 404, {'deleted': gone, 'name': name})
            body = json.loads(request.content)
            item = gw_http.put(GW_DB, name, body, body.get('actor') or '')
            gw_record('http_tools', 'http_tool_saved', f"{name}: {item['method']} {item['base_url']}{item['path']}", None, actor=body.get('actor'))
            return reply(200, item)
        if path == '/v1/http_tool':
            body = json.loads(request.content)
            tool = gw_http.get(GW_DB, body.get('tool'))
            req = gw_http.check(tool, body.get('params'), None, False)
            result = gw_http.run(GW_DB, req, gw_creds.get)
            gw_record('http_tools', 'admin_http_test', gw_http.shown(req) + ' -> ' + str(result['http_status']), 0, actor=body.get('actor'))
            return reply(200, {'service': 'http_tools', **result})
    except gw_creds.CredentialError as exc:
        return reply(exc.status, {'error': exc.error, 'detail': exc.detail})
    except gw_http.HttpToolError as exc:
        return reply(exc.status, {'error': exc.error, 'detail': exc.detail})
    return reply(404, {'error': 'not_found'})


# HLA_WEBSITES_V1: the real inventory code (gateway/websites.py) on canned SSH output: a production site that is up, a
# staging site behind basicAuth (401), an alpha test site, a production shop that is down, a container without a route
# and a route without a container. The Traefik API answer holds a basicAuth hash that must never reach the browser.
from gateway import websites as gw_websites
import time
def _ws_container(name, image, state, status, project, port, health=None, exit_code=0, extra=None, started='2026-11-02T08:00:00Z'):
    labels = {'com.docker.compose.project': project, 'com.docker.compose.service': 'web', 'com.docker.compose.project.working_dir': '/opt/' + project,
              **(extra or {})} if project else dict(extra or {})
    binds = {'8080/tcp': [{'HostIp': '192.168.30.11', 'HostPort': str(port)}]} if port else {}
    return ('P ' + json.dumps({'Names': name, 'Image': image, 'State': state, 'Status': status}),
            'I ' + json.dumps({'name': '/' + name, 'image': image, 'state': state, 'health': health, 'started': started, 'exit': exit_code,
                               'restarts': 0, 'ports': binds if state == 'running' else {}, 'bindings': binds, 'labels': labels}))
WS_CONTAINERS = [
    _ws_container('production-web-1', 'ghcr.io/example/site:1.4.2', 'running', 'Up 7 days (healthy)', 'production', 8081, 'healthy'),
    _ws_container('production-db-1', 'postgres:17', 'running', 'Up 7 days', 'production', None),
    _ws_container('northwind-staging-web-1', 'ghcr.io/northwind/web:staging', 'running', 'Up 2 hours', 'northwind-staging', 8082,
                  started='2026-11-09T06:00:00Z'),
    _ws_container('alpha-web-1', 'ghcr.io/example/site:alpha', 'running', 'Up 3 hours (healthy)', 'alpha', 8083, 'healthy'),
    _ws_container('shop-web-1', 'ghcr.io/example/shop:2.0.1', 'exited', 'Exited (137) 40 minutes ago', 'shop', 8085, exit_code=137,
                  extra={'hla.environment': 'production'}),
    _ws_container('uptime-kuma', 'louislam/uptime-kuma:1', 'running', 'Up 30 days', None, 3001),
]
WS_ROUTERS = [
    {'name': 'production@file', 'provider': 'file', 'rule': 'Host(`example.net`) || Host(`www.example.net`)', 'entryPoints': ['websecure'],
     'service': 'production', 'tls': {'certResolver': 'letsencrypt'}},
    {'name': 'nw-staging@file', 'provider': 'file', 'rule': 'Host(`staging.northwind.example`)', 'entryPoints': ['websecure'],
     'middlewares': ['staging-auth', 'staging-ipallow'], 'service': 'nw-staging', 'tls': {'certResolver': 'letsencrypt'}},
    {'name': 'alpha@file', 'provider': 'file', 'rule': 'Host(`alpha.example.net`)', 'entryPoints': ['websecure'], 'service': 'alpha',
     'tls': {'certResolver': 'letsencrypt'}},
    {'name': 'shop@file', 'provider': 'file', 'rule': 'Host(`shop.example.net`)', 'entryPoints': ['websecure'], 'service': 'shop',
     'tls': {'certResolver': 'letsencrypt'}},
    {'name': 'old-blog@file', 'provider': 'file', 'rule': 'Host(`blog.example.net`)', 'entryPoints': ['websecure'], 'service': 'old-blog', 'tls': {}},
    {'name': 'agents@file', 'provider': 'file', 'rule': 'Host(`agents.example.com`)', 'entryPoints': ['websecure'], 'service': 'agents', 'tls': {}},
]
WS_SERVICES = [{'name': n + '@file', 'provider': 'file', 'loadBalancer': {'servers': [{'url': u}]}} for n, u in (
    ('production', 'http://192.168.30.11:8081'), ('nw-staging', 'http://192.168.30.11:8082'), ('alpha', 'http://192.168.30.11:8083'),
    ('shop', 'http://192.168.30.11:8085'), ('old-blog', 'http://192.168.30.11:8090'), ('agents', 'http://192.168.10.202:8088'))]
WS_MIDDLEWARES = [{'name': 'staging-auth@file', 'type': 'basicauth', 'basicAuth': {'users': ['admin:$apr1$PREVIEWSECRETHASH']}},
                  {'name': 'staging-ipallow@file', 'type': 'ipallowlist', 'ipAllowList': {'sourceRange': ['192.168.0.0/16']}}]
WS_CHECKS = {'example.net': '200 0.184 0', 'www.example.net': '301 0.092 0', 'staging.northwind.example': '401 0.071 0', 'alpha.example.net': '200 0.412 0',
             'shop.example.net': '502 0.033 0', 'blog.example.net': '404 0.051 0'}
def ws_execute(server, command, timeout=30, max_output=12000, stdin=None):
    ok = {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stderr': ''}
    if server == 'public-website':
        return {**ok, 'stdout': '\n'.join([c[0] for c in WS_CONTAINERS] + [c[1] for c in WS_CONTAINERS] + ['HLA_DONE'])}
    b = command.split('B=', 1)[1].split(';', 1)[0].split('\n', 1)[0].strip()
    if 'api/http/routers' in command:
        routers, services, mws = rt_api_view()  # the hand-written routes plus the managed file (HLA_ROUTES_V1)
        return {**ok, 'stdout': ''.join(f'{b} API {k} 0\n{json.dumps(v)}\n{b} END\n' for k, v in
                                        (('routers', routers), ('services', services), ('middlewares', mws))) + f'{b} DONE\n'}
    end = time.strftime('%b %d %H:%M:%S %Y GMT', time.gmtime(time.time() + 61 * 86400))
    lines = [f'{b} {n} {WS_CHECKS.get(line.split()[1]) or ("200 0.090 0" if line.split()[1] in RT_FILE["text"] else "000 0 0")} {end}'
             for n, line in enumerate(stdin.decode().splitlines(), 1)]
    return {**ok, 'stdout': '\n'.join(lines) + f'\n{b} DONE\n'}


# HLA_ROUTES_V1: the real managed-routes code (gateway/routes.py) against a simulated traefik host: the managed file
# lives in memory, the "Traefik API" loads it next to the hand-written routers above, DNS answers from a table and the
# basic-auth password comes from a stand-in for the Agent Generated project (it never reaches the browser).
import base64 as _b64
from gateway import routes as gw_routes
gw_routes.migrate(GW_DB)
gw_routes.POLL_SECONDS = 0.05
RT_FILE = {'text': '# created by root\nhttp: {}\n'}
RT_DNS = {'kuma.example.com': ['192.168.30.103'], 'shop.example.net': ['203.0.113.10'], 'example.net': ['203.0.113.10'],
          'away.example.com': ['203.0.113.7']}
def _rt_resolve(host):
    if host.endswith('.example.net') and host not in RT_DNS:
        return ['203.0.113.10']  # a wildcard record for example.net
    if host not in RT_DNS:
        import socket
        raise socket.gaierror(-2, 'Name or service not known')
    return RT_DNS[host]
gw_routes.resolver = _rt_resolve
gw_routes.ensure_password = lambda name, host, actor: 'PreviewBasicAuthPassword0123456789'
def rt_api_view():
    try:
        http = (gw_websites.parse_yaml(RT_FILE['text']) or {}).get('http') or {}
    except gw_websites.YamlError:
        http = {}
    routers = [{**r, 'status': 'enabled'} for r in WS_ROUTERS]
    services = [{**s, 'status': 'enabled'} for s in WS_SERVICES]
    mws = [{**m, 'provider': 'file', 'status': 'enabled'} for m in WS_MIDDLEWARES]
    for name, r in (http.get('routers') or {}).items():
        routers.append({'name': name + '@file', 'provider': 'file', 'rule': r.get('rule'), 'entryPoints': r.get('entryPoints'),
                        'service': r.get('service'), 'middlewares': [m + '@file' for m in r.get('middlewares') or []], 'tls': r.get('tls'), 'status': 'enabled'})
    for name, s in (http.get('services') or {}).items():
        services.append({'name': name + '@file', 'provider': 'file', 'loadBalancer': s.get('loadBalancer'), 'status': 'enabled'})
    for name, m in (http.get('middlewares') or {}).items():
        mws.append({'name': name + '@file', 'provider': 'file', 'type': next(iter(m)).lower(), **m, 'status': 'enabled'})
    return routers, services, mws
def rt_execute(server, command, timeout=30, max_output=12000, stdin=None):
    ok = {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stderr': ''}
    if 'HLA_PREV' in command:  # the write script
        sha = re.search(r'S=([0-9a-f]{64})', command).group(1)
        if hashlib.sha256(stdin).hexdigest() != sha:
            return {**ok, 'exit_code': 3, 'stdout': 'HLA_ERR the new content arrived damaged\n'}
        prev = RT_FILE['text']
        RT_FILE['text'] = stdin.decode()
        gw_websites.reset()
        return {**ok, 'stdout': f"HLA_PREV {_b64.b64encode(prev.encode()).decode()}\nHLA_OK {sha}\n"}
    b = re.search(r'B=(HLA_[0-9a-f]+)', command).group(1)
    routers, services, mws = rt_api_view()
    parts = (('routers', routers), ('services', services), ('middlewares', mws), ('entrypoints', [{'name': 'web'}, {'name': 'websecure'}, {'name': 'traefik'}]))
    return {**ok, 'stdout': ''.join(f'{b} API {k} 0\n{json.dumps(v)}\n{b} END\n' for k, v in parts) + f'{b} DONE\n'}

async def routes_gateway(request):
    """/v1/routes* like gateway_api/server.py: admin token for the dashboard, tool token for agent requests."""
    path, method = request.url.path, request.method
    admin = request.headers.get('authorization') == 'Bearer ' + 'a' * 40
    body = json.loads(request.content) if request.content else {}
    actor = str(body.get('actor') or request.headers.get('x-hla-actor') or 'dashboard')[:120]
    def audit(action, detail, okay):
        gw_record('routes', action, detail, 0 if okay else 1, actor=actor)
    def run():
        try:
            if path == '/v1/routes/request':
                out = gw_routes.file_request(GW_DB, body, body.get('agent_id'))
                gw_record('routes', 'agent_route_requested', f"#{out['id']} {out['host']} -> {out['backend']}", 0, agent=body.get('agent_id'))
                return 200, out
            if not admin:
                return 401, {'error': 'policy_admin_token_required'}
            if path == '/v1/routes' and method == 'GET':
                return 200, gw_routes.listing(GW_DB, rt_execute)
            if path == '/v1/routes/requests':
                return 200, {'requests': gw_routes.requests(GW_DB, request.url.params.get('status'))}
            if path == '/v1/routes/domains':
                if method == 'GET':
                    return 200, {'domains': gw_routes.domains(GW_DB), 'default_protection': gw_routes.DEFAULT_PROTECTION}
                out = gw_routes.set_domains(GW_DB, body.get('domains'))
                gw_record('routes', 'route_domains_saved', ', '.join(out), 0, actor=actor)
                return 200, {'domains': out}
            if path == '/v1/routes/check-dns':
                gw_routes._dns_cache.pop(str(body.get('host') or ''), None)
                return 200, gw_routes.dns_check(str(body.get('host') or '').strip().lower(), gw_routes.domains(GW_DB), str(body.get('protection') or 'public'))
            m = re.fullmatch(r'/v1/routes/requests/(\d+)/(approve|reject)', path)
            if m:
                if m.group(2) == 'reject':
                    out = gw_routes.reject(GW_DB, m.group(1), body.get('note'), actor)
                    gw_record('routes', 'route_request_rejected', f"#{out['request_id']} {out['host']}", 0, actor=actor)
                    return 200, out
                return 200, gw_routes.approve(GW_DB, rt_execute, m.group(1), body, actor, audit)
            name = path[len('/v1/routes/'):]
            if method == 'DELETE':
                return 200, gw_routes.delete(GW_DB, rt_execute, name, actor, audit)
            if method == 'PUT':
                return 200, gw_routes.save(GW_DB, rt_execute, name, body, actor, audit, create=body.get('create') is True)
            return 404, {'error': 'not_found'}
        except gw_routes.RouteError as exc:
            return exc.status, {'error': exc.error, 'detail': exc.detail}
    status, payload = await asyncio.to_thread(run)
    return httpx.Response(status, json=payload)

def seed_routes(agent):
    # No managed route yet: the inventory checks above count the hand-written routes only; the e2e run creates one.
    gw_routes.set_domains(GW_DB, ['example.com', 'example.net'])
    gw_routes.file_request(GW_DB, {'host': 'alpha-staging.example.net', 'backend_port': 8083, 'environment': 'staging',
                                   'reason': 'Preview of the alpha site for the client'}, str(agent))
    gw_policy.update(GW_DB, {'servers': {'traefik': {'level': 4}}}, 'preview')

class _NoClose:
    """credentials.values() opens and closes a database; the preview has one shared in-memory database."""
    def __init__(self, db): self.db = db
    def __enter__(self): return self.db
    def __exit__(self, *a): return False

PREVIEW_FIX = 'setfacl -m g:files:rwx /srv/files/shared && setfacl -d -m g:files:rwx /srv/files/shared'
def _cat(mid, name, ctx, inp, out, tools=True, desc=''):
    return {'id': mid, 'name': name, 'context_length': ctx, 'description': desc,
            'pricing': {'prompt': str(inp / 1e6), 'completion': str(out / 1e6)},
            'supported_parameters': ['max_tokens', 'temperature'] + (['tools', 'tool_choice'] if tools else [])}
PREVIEW_CATALOG = [
    _cat('anthropic/claude-sonnet-5.5', 'Anthropic: Claude Sonnet 5.5', 1000000, 3, 15, desc='Balanced model for agents and code.'),
    _cat('anthropic/claude-haiku-5', 'Anthropic: Claude Haiku 5', 200000, 1, 5, desc='Fast and cheap.'),
    _cat('qwen/qwen3-235b-a22b', 'Qwen: Qwen3 235B A22B', 131072, 0.13, 0.6),
    _cat('openai/gpt-5.2-mini', 'OpenAI: GPT-5.2 Mini', 400000, 0.25, 2),
    _cat('google/gemini-3-flash', 'Google: Gemini 3 Flash', 1048576, 0.3, 2.5),
    _cat('mistralai/mistral-small-4', 'Mistral: Mistral Small 4', 128000, 0.1, 0.3),
    _cat('acme/storyteller-13b', 'Acme: Storyteller 13B', 8192, 0.05, 0.05, tools=False, desc='Chat only, no tool calls.'),
    _cat('meta-llama/llama-4-scout:free', 'Meta: Llama 4 Scout (free)', 128000, 0, 0, tools=False),
]

PREVIEW_FIX ='setfacl -m g:files:rwx /srv/files/shared && setfacl -d -m g:files:rwx /srv/files/shared'
GRANTED = set()

# HLA_PROXMOX_V1: the real gateway Proxmox code (levels, validation, approvals, create/onboard/destroy) against two fake
# nodes (ssh-gateway/tests/fake_pve.py); SSH onboarding and the image checksum are canned, the create takes ~DEV_DELAY*2 s.
import importlib.util as _ilu, tempfile as _tempfile
_spec = _ilu.spec_from_file_location('fake_pve', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'ssh-gateway', 'tests', 'fake_pve.py'))
fake_pve = _ilu.module_from_spec(_spec); _spec.loader.exec_module(fake_pve)
from gateway import proxmox as gw_px
_px_ca = _tempfile.mkdtemp()
for _n in ('proxmox-ca.pem', 'pve2-ca.pem'):
    open(os.path.join(_px_ca, _n), 'w').write('preview: the fake nodes do no TLS\n')
os.environ['PROXMOX_CA_DIR'] = _px_ca
GW_DB.execute('CREATE TABLE IF NOT EXISTS approvals (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
gw_px.migrate(GW_DB)
PVE = fake_pve.FakePVE()
PVE.add_image('proxmox')
gw_px._http, gw_px._secrets, gw_px._fetch_url = PVE.http, fake_pve.secrets, lambda url: fake_pve.sha512sums()
gw_px._ip_answers = lambda ip: False
gw_px._sleep, gw_px.TASK_POLL = (lambda s: None), 0
gw_px._host_fingerprint = lambda host, port=22, timeout=8: 'SHA256:Xk3v9QpZr8mW2bT6yHc1LnE4sJ7aD0fG5uR2oP8iKqY'
def _px_ssh(host, key, pinned, command, timeout=30, max_output=12000, stdin=None):
    _time.sleep(float(os.environ.get('DEV_DELAY', '1.0')) * 2)  # boot and cloud-init, so the job shows "Creating"
    return {'exit_code': 0, 'stdout': 'HLA_USER=svc-hla-agent\nHLA_SUDO_OK\nHLA_QGA_INSTALLED\nHLA_QGA_RUNNING\n', 'stderr': '',
            'timed_out': False, 'truncated': False}
gw_px._run_ssh = _px_ssh
def _px_audit(server, action, d, result=None, actor=None, agent=None, detail=None, task=None):
    gw_record(server, action, detail or action, (result or {}).get('exit_code'), actor=actor, agent=agent, task=task)
# HLA_OMADA_V1: the real gateway Omada code (levels, approvals, certificate pinning) against a canned controller at
# its HTTP layer; the certificate starts untrusted so the panel shows its Trust button.
from gateway import omada as gw_om
gw_om.migrate(GW_DB)
gw_om._secrets = lambda folder, names: {'APPLICATION_NAME': 'hla-agents', 'CLIENT_ID': 'preview-client', 'CLIENT_SECRET': 'preview-omada-secret-1234'}
_OM_CID = 'c0ffee12preview'
_OM_CLIENTS = [{'mac': 'BC-24-11-00-02-05', 'ip': '192.168.10.205', 'name': 'monitoring', 'hostName': 'monitoring', 'wireless': False,
                'networkName': 'Servers', 'vid': 30, 'switchName': 'SG2210P', 'active': True},
               {'mac': '3C-52-82-11-22-33', 'ip': '192.168.10.104', 'name': 'dev', 'hostName': 'dev', 'wireless': False, 'networkName': 'Servers', 'vid': 30}]
def _om_http(db, method, path, query=None, body=None, token=None, credentials=True, timeout=20):
    gw_om._checker(db, credentials)(b'preview omada controller certificate')
    if path == '/api/info':
        return 200, {'errorCode': 0, 'result': {'omadacId': _OM_CID, 'controllerVer': '5.15.20.18', 'apiVer': '3', 'type': 1}}
    if path == '/openapi/authorize/token':
        return 200, {'errorCode': 0, 'result': {'accessToken': 'AT-preview-0123456789', 'expiresIn': 7200}}
    sub = path[len('/openapi/v1/' + _OM_CID):]
    paged = lambda items: (200, {'errorCode': 0, 'result': {'totalRows': len(items), 'data': items}})  # noqa: E731
    if sub == '/sites':
        return paged([{'siteId': 'site-home', 'name': 'Home', 'region': 'Belgium', 'scenario': 'Home'}])
    if sub == '/sites/site-home/clients':
        return paged(_OM_CLIENTS)
    if sub == '/sites/site-home/devices':
        return paged([{'mac': '50-91-E3-00-00-01', 'name': 'ER605', 'type': 'gateway', 'model': 'ER605', 'ip': '192.168.1.1'}])
    if method in gw_om.WRITE_METHODS and sub.startswith('/sites/site-home/'):
        return 200, {'errorCode': 0, 'result': {'id': 'resv-205'}}
    return 200, {'errorCode': -1001, 'msg': 'Invalid request parameters.'}
gw_om._http = _om_http
# The preview shares one SQLite connection (the gateway opens one per request): one gateway call at a time in threads.
import threading as _threading
_GW_LOCK = _threading.Lock()
def _gw_serial(fn, *args):
    with _GW_LOCK:
        return fn(*args)
async def omada_gateway(request):
    admin = request.headers.get('authorization') == 'Bearer ' + 'a' * 40
    status, payload = await asyncio.to_thread(_gw_serial, gw_om.handle_post, GW_DB, json.loads(request.content), admin,
                                              request.headers.get('x-hla-task'), _px_audit)
    return httpx.Response(status, json=payload)
async def proxmox_gateway(request):
    admin = request.headers.get('authorization') == 'Bearer ' + 'a' * 40
    task = request.headers.get('x-hla-task')
    if request.url.path == '/v1/proxmox/inventory':
        status, payload = await asyncio.to_thread(_gw_serial, gw_px.handle_inventory, GW_DB, dict(request.url.params), admin, task, _px_audit)
    else:
        status, payload = await asyncio.to_thread(_gw_serial, gw_px.handle_post, GW_DB, json.loads(request.content), admin, task, _px_audit)
    return httpx.Response(status, json=payload)

def _text(content):
    """A message's text: a string, or the text parts of a message with images (HLA_ATTACHMENTS_V1)."""
    if isinstance(content, list):
        return '\n'.join(p.get('text') or '' for p in content if isinstance(p, dict))
    return content or ''

async def handler(request):
    if 'typesafe' in request.url.host:
        body = json.loads(request.content)
        risky = any(w in body['state'].lower() for w in ('fix', 'chmod', 'delete', 'permission'))
        return httpx.Response(200, json={'answers': {'expert': {'noul': 0.2}, 'risky': {'noul': 0.71 if risky else 0.05}},
                                         'usage': {'input_tokens': 40, 'output_tokens': 2}})
    if 'openrouter' in request.url.host and request.method == 'GET' and request.url.path == '/api/v1/models':
        return httpx.Response(200, json={'data': PREVIEW_CATALOG})  # the Models page's catalog (HLA_MODEL_REGISTRY_V1)
    if 'openrouter' in request.url.host:
        delay = float(os.environ.get('DEV_DELAY', '1.0'))
        usage = {'prompt_tokens': 812, 'completion_tokens': 455, 'cost': 0.00042}
        body = json.loads(request.content)
        if body.get('max_tokens') == 16:  # the Models page's test call
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {'content': 'pong'}, 'finish_reason': 'stop'}],
                                             'usage': {'prompt_tokens': 12, 'completion_tokens': 2, 'cost': 0.0000084}})
        # Asked to apply a change: propose one shell command, which then waits for approval in the conversation.
        if (body.get('tools') and 'ACL change' in _text(body['messages'][-1]['content']).split('LATEST USER MESSAGE:')[-1]
                and not any(m.get('role') == 'tool' for m in body['messages'])):
            await asyncio.sleep(delay / 2)
            call = {'id': 'c0', 'type': 'function', 'function': {'name': 'server_execute_approved',
                    'arguments': json.dumps({'server': 'dev', 'command': PREVIEW_FIX})}}
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]},
                                                          'finish_reason': 'tool_calls'}], 'usage': usage})
        # Asked for the monitoring VM: request it with the proxmox tool; the exact spec waits for approval in the conversation.
        if (body.get('tools') and 'monitoring VM' in _text(body['messages'][-1]['content']).split('LATEST USER MESSAGE:')[-1]
                and not any(m.get('role') == 'tool' for m in body['messages'])):
            await asyncio.sleep(delay / 2)
            call = {'id': 'p0', 'type': 'function', 'function': {'name': 'proxmox', 'arguments': json.dumps(
                {'operation': 'create_vm', 'node': 'pve2', 'vmid': 205, 'name': 'monitoring', 'cores': 2, 'memory': 4096, 'disk': 32,
                 'storage': 'local-lvm', 'bridge': 'vmbr0', 'ip': '192.168.10.205/24', 'gateway': '192.168.10.1', 'dns': '192.168.10.1',
                 'image': 'debian-13'})}}
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]},
                                                          'finish_reason': 'tool_calls'}], 'usage': usage})
        # Asked for a DHCP reservation: the omada write waits for approval in the conversation (HLA_OMADA_V1).
        if (body.get('tools') and 'DHCP reservation' in _text(body['messages'][-1]['content']).split('LATEST USER MESSAGE:')[-1]
                and not any(m.get('role') == 'tool' for m in body['messages'])):
            await asyncio.sleep(delay / 2)
            call = {'id': 'o0', 'type': 'function', 'function': {'name': 'omada', 'arguments': json.dumps(
                {'operation': 'write', 'method': 'POST', 'path': '/sites/site-home/setting/service/dhcp/reservations',
                 'body': {'mac': 'BC-24-11-00-02-05', 'ip': '192.168.10.205', 'description': 'monitoring'}})}}
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]},
                                                          'finish_reason': 'tool_calls'}], 'usage': usage})
        # Project planner: a three-task plan with a gated deploy.
        if any('You plan a project for a team of AI agents' in (m.get('content') or '') for m in body['messages']):
            await asyncio.sleep(delay)
            plan = {'summary': 'Research the company, build a static site in the sandbox, then deploy it to staging after review.',
                    'tasks': [{'key': 'research', 'title': 'Research bakery.example', 'agent': 'Infra Agent', 'description': 'Collect products, tone and contact details.',
                               'acceptance': 'Notes saved in the project folder'},
                              {'key': 'build', 'title': 'Build the static site', 'agent': 'Infra Agent', 'description': 'HTML/CSS in the project folder, served by nginx in the sandbox.',
                               'acceptance': 'curl http://localhost:8080 returns the new page', 'depends_on': ['research']},
                              {'key': 'deploy', 'title': 'Deploy to staging', 'agent': 'Infra Agent', 'description': 'Use the deploy pipeline.',
                               'acceptance': 'staging serves the new site', 'depends_on': ['build'], 'gate': True}]}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(plan)}, 'finish_reason': 'stop'}], 'usage': usage})
        # Tool-enabled agent: two diagnostics first, then the answer.
        if body.get('tools') and not any(m.get('role') == 'tool' for m in body['messages']):
            await asyncio.sleep(delay / 2)
            calls = [{'id': f'c{i}', 'type': 'function', 'function': {'name': 'server_diagnostic',
                      'arguments': json.dumps({'server': 'fs-01', 'diagnostic': d})}} for i, d in enumerate(('identity', 'system'))]
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': calls},
                                                          'finish_reason': 'tool_calls'}], 'usage': usage})
        # HLA_VOICE_V1: a short spoken-style answer for the car mode's questions.
        latest = _text(body['messages'][-1]['content']).split('LATEST USER MESSAGE:')[-1].lower()
        if body.get('stream') and ('hoe staat het' in latest or 'how are the projects' in latest or 'briefing' in latest):
            spoken = ('Alles draait. Twee projecten lopen, en één ding wacht op jou: de DHCP-reservatie voor de monitoring-VM. '
                      'Open Needs you om het goed te keuren.') if ('hoe staat het' in latest or 'geef me' in latest) else (
                      'Everything is running. Two projects are going, and one thing waits for you: the DHCP reservation for the '
                      'monitoring VM. Open Needs you to approve it.')
            async def sse_voice():
                for i in range(0, len(spoken), 30):
                    await asyncio.sleep(delay / 8)
                    yield ('data: ' + json.dumps({'choices': [{'delta': {'content': spoken[i:i + 30]}}]}) + '\n\n').encode()
                yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage}) + '\n\ndata: [DONE]\n\n').encode()
            return httpx.Response(200, content=sse_voice(), headers={'content-type': 'text/event-stream'})
        task_reply = any('YOUR TASK (' in (m.get('content') or '') for m in body['messages'] if m.get('role') == 'user')
        if body.get('stream') and task_reply:
            last = [m.get('content') or '' for m in body['messages'] if m.get('role') == 'user'][-1]
            title = re.search(r'YOUR TASK \(\d+ of \d+\): (.*)', last)
            done_text = ('Finished: ' + (title.group(1) if title else 'task') + '.\n\nTASK STATUS: done\nHANDOFF: files in the project folder; '
                         'nginx serves them on port 8080 in the sandbox.')
            async def sse_task():
                for i in range(0, len(done_text), 40):
                    await asyncio.sleep(delay / 6)
                    yield ('data: ' + json.dumps({'choices': [{'delta': {'content': done_text[i:i + 40]}}]}) + '\n\n').encode()
                yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage}) + '\n\ndata: [DONE]\n\n').encode()
            return httpx.Response(200, content=sse_task(), headers={'content-type': 'text/event-stream'})
        if body.get('stream'):
            async def sse():
                # Eight chunks spread over DEV_DELAY*2 seconds so the live reply is visible.
                step = len(LONG_MD) // 8 + 1
                for i in range(0, len(LONG_MD), step):
                    await asyncio.sleep(delay / 4)
                    yield ('data: ' + json.dumps({'choices': [{'delta': {'content': LONG_MD[i:i + step]}}]}) + '\n\n').encode()
                yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage}) + '\n\ndata: [DONE]\n\n').encode()
            return httpx.Response(200, content=sse(), headers={'content-type': 'text/event-stream'})
        await asyncio.sleep(delay)
        return httpx.Response(200, json={'choices': [{'message': {'content': LONG_MD}, 'finish_reason': 'stop'}], 'usage': usage})
    if request.url.host == '192.168.20.31':
        await asyncio.sleep(float(os.environ.get('DEV_DELAY', '1.0')))
        if '/tasks/' in request.url.path:
            return httpx.Response(200, json={'data': {'status': 'stopped', 'exitstatus': 'OK'}})
        return httpx.Response(200, json={'data': 'UPID:pve2:preview:'})
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/policy/effective':
        agent_id = request.url.params.get('agent_id')
        return httpx.Response(200, json={'servers': gw_policy.effective(GW_DB, agent_id),  # the Proxmox services only (HLA_PROXMOX_V1)
                                         'services': {k: v for k, v in gw_policy.effective_services(GW_DB, agent_id).items() if k.startswith(('proxmox:', 'omada:'))}})
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/policy':
        if request.method == 'PUT':
            if request.headers.get('authorization') != 'Bearer ' + 'a' * 40:
                return httpx.Response(401, json={'error': 'policy_admin_token_required'})
            body = json.loads(request.content)
            try:
                return httpx.Response(200, json=gw_policy.update(GW_DB, body.get('change'), body.get('actor')))
            except ValueError as exc:
                return httpx.Response(400, json={'error': 'invalid_policy', 'detail': str(exc)})
        return httpx.Response(200, json=gw_policy.snapshot(GW_DB))
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/deployments':
        return httpx.Response(200, json={'deployments': gw_deploy.history(GW_DB)})
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/deploy':
        # Real history bookkeeping, simulated run: staging + deploy take DEV_DELAY*3 seconds and succeed.
        sent = json.loads(request.content)
        app = gw_policy.app(GW_DB, sent.get('app'))
        if not app: return httpx.Response(404, json={'error': 'unknown_app'})
        if not gw_deploy.claim(app['name']): return httpx.Response(409, json={'error': 'deploy_in_progress'})
        try:
            ref = sent.get('sha') or sent.get('ref')
            ident = gw_deploy.start(GW_DB, app, ref, sent.get('actor') or 'agent:' + str(sent.get('agent_id')))
            await asyncio.sleep(float(os.environ.get('DEV_DELAY', '1.0')) * 3)
            last = gw_deploy.history(GW_DB, app['name'])
            prev = next((d['sha'] for d in last if d['status'] == 'deployed'), 'a' * 40)
            sha = sent.get('sha') or (format(random.getrandbits(160), '040x'))
            result = {'app': app['name'], 'server': app['server'], 'ref': ref, 'status': 'deployed', 'verified': True, 'error_class': None,
                      'sha': sha, 'from': prev, 'staging': None if sent.get('sha') else {'state': 'running', 'verified': True, 'log': 'built'},
                      'target': {'state': 'running', 'verified': True, 'log': 'Container site-web-1 Started'}}
            gw_deploy.finish(GW_DB, ident, result)
        finally:
            gw_deploy.release(app['name'])
        return httpx.Response(200, json={'id': ident, **result})
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/approvals':
        sent = json.loads(request.content)
        GRANTED.add((sent['server'], sent['command']))
        if str(sent['server']).startswith(('proxmox:', 'omada:')):  # like the gateway: one use within 5 minutes
            GW_DB.execute('INSERT OR REPLACE INTO approvals(digest,expires,used) VALUES (?,?,0)',
                          (gw_px.digest(sent['server'], sent['command']), int(_time.time()) + 300))
        gw_record(sent['server'], 'chat_approval', sent['command'], None, actor=sent.get('actor'), task=request.headers.get('x-hla-task'))
        return httpx.Response(200, json={'server': sent['server'], 'expires_in': 300})
    if request.url.host == 'ssh-gateway-api' and request.url.path.startswith('/v1/mcp/'):
        path, method = request.url.path, request.method
        if path == '/v1/mcp/tools':
            return httpx.Response(200, json={'tools': gw_mcp.agent_tools(GW_DB, request.url.params.get('agent_id'))})
        if path == '/v1/mcp/servers' and method == 'GET':
            return httpx.Response(200, json={'servers': [gw_mcp.view(GW_DB, c, known=MCP_CREDENTIALS) for c in gw_mcp.servers(GW_DB).values()],
                                             'credentials': MCP_CREDENTIALS, 'credentials_configured': True, 'levels': gw_policy.LEVELS})
        name = path[len('/v1/mcp/servers/'):].removesuffix('/refresh')
        try:
            if method == 'PUT':
                cfg = gw_mcp.save(GW_DB, name, {k: v for k, v in json.loads(request.content).items() if k != 'actor'}, 'preview')
            elif method == 'DELETE':
                return httpx.Response(200, json={'deleted': gw_mcp.delete(GW_DB, name), 'name': name})
            else:  # refresh: a canned tool list, or an error for a server named *down*
                await asyncio.sleep(float(os.environ.get('DEV_DELAY', '1.0')) / 2)
                if not gw_mcp.server(GW_DB, name):
                    return httpx.Response(404, json={'error': 'unknown_server'})
                if 'down' in name:
                    GW_DB.execute("UPDATE mcp_servers SET last_error='mcp_unreachable: ConnectionRefusedError' WHERE name=?", (name,))
                    return httpx.Response(502, json={'error': 'mcp_unreachable', 'detail': 'ConnectionRefusedError'})
                GW_DB.execute("UPDATE mcp_servers SET tools=?,server_info=?,refreshed_at=?,last_error=NULL WHERE name=?",
                              (json.dumps(MCP_TOOLS.get(name, MCP_TOOLS['notion'][:2])), json.dumps({'name': name + '-mcp', 'version': '1.2.0'}), _time.time(), name))
                cfg = gw_mcp.server(GW_DB, name)
        except gw_mcp.MCPError as exc:
            return httpx.Response(exc.status, json={'error': exc.error, 'detail': exc.detail})
        return httpx.Response(200, json=gw_mcp.view(GW_DB, cfg, known=MCP_CREDENTIALS))
    if request.url.host == 'ssh-gateway-api' and request.url.path.startswith('/v1/routes'):
        return await routes_gateway(request)
    if request.url.host == 'ssh-gateway-api' and request.url.path in ('/v1/proxmox', '/v1/proxmox/inventory'):
        return await proxmox_gateway(request)
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/omada':
        return await omada_gateway(request)
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/websites':
        refresh = request.url.params.get('refresh') == '1'
        if refresh and request.headers.get('authorization') != 'Bearer ' + 'a' * 40:
            return httpx.Response(403, json={'error': 'refresh_needs_admin_token'})
        try:
            data, _ = gw_websites.get(ws_execute, refresh=refresh)
        except gw_websites.RateLimited as exc:
            return httpx.Response(429, json={'error': 'refresh_rate_limited', 'retry_after': exc.retry})
        return httpx.Response(200, json=data)
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/audit':
        try:
            return httpx.Response(200, json=gw_audit.query(GW_DB, dict(request.url.params)))
        except ValueError as exc:
            return httpx.Response(400, json={'error': 'invalid_query', 'detail': str(exc)})
    if request.url.host == 'ssh-gateway-api' and (request.url.path.startswith('/v1/credentials') or request.url.path.startswith('/v1/http_tool')
                                                  or (request.url.path == '/v1/github' and json.loads(request.content).get('operation') in ('status', 'list_repos'))):
        return connections_gateway(request)
    if request.url.host == 'ssh-gateway-api':
        sent = json.loads(request.content)
        mode = sent.get('mode')
        shown = {'diagnostic': ' '.join(x for x in (sent.get('diagnostic'), sent.get('target')) if x),
                 'maintenance': f"{sent.get('operation')} {sent.get('target')}"}.get(mode, sent.get('command') or sent.get('path') or '')
        if sent.get('mode') == 'approved_command' and not gw_policy.effective(GW_DB, sent.get('agent_id')).get(sent['server'], {}).get('free_shell'):
            if (sent['server'], sent['command']) not in GRANTED:
                gw_record(sent['server'], 'agent_denied', shown, None, agent=sent.get('agent_id'), task=request.headers.get('x-hla-task'))
                return httpx.Response(403, json={'error': 'approval_required', 'server': sent['server']})
            GRANTED.discard((sent['server'], sent['command']))
        await asyncio.sleep(float(os.environ.get('DEV_DELAY', '1.0')))
        gw_record(sent.get('server'), 'agent_' + str(mode), shown, 0, agent=sent.get('agent_id'), task=request.headers.get('x-hla-task'))
        return httpx.Response(200, json={'server': 'fs-01', 'exit_code': 0, 'stdout': 'ok', 'timed_out': False, 'truncated': False})
    return httpx.Response(503, json={'error': 'offline in preview'})

_Real = httpx.AsyncClient
class MockAsync(_Real):
    def __init__(self, *a, **kw):
        kw['transport'] = httpx.MockTransport(handler); super().__init__(*a, **kw)
httpx.AsyncClient = MockAsync

import app as appmod
import uvicorn

# HLA_VOICE_V1: the Chief's car mode talks to /ui/api/chief. Until the backend serves it, a stand-in here: one plain
# conversation titled "Chief" (no agent, so replies stream from the mocked model).
if not any(getattr(r, 'path', '') == '/ui/api/chief' for r in appmod.app.router.routes):
    from fastapi import Request as _Req
    from fastapi.routing import APIRoute as _Route
    _CHIEF = {'model': 'qwen/qwen3-235b-a22b'}
    def _chief_thread():
        with psycopg.connect(os.environ['DATABASE_URL']) as db:
            row = db.execute("SELECT id FROM agent_threads WHERE title='Chief' ORDER BY created_at LIMIT 1").fetchone()
            if row: return row[0]
            ident = uuid.uuid4()
            db.execute("INSERT INTO agent_threads(id,title,workspace) VALUES(%s,'Chief','home')", (ident,)); db.commit()
            return ident
    def _chief_view():
        return {'thread_id': str(_chief_thread()), 'agent_id': None, 'agent_name': 'Chief', 'model': _CHIEF['model'],
                'since': (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()}
    def chief_get(request: _Req):
        appmod.require_dashboard(request); return _chief_view()
    async def chief_patch(request: _Req):
        appmod.require_ui_write(request); _CHIEF['model'] = (await request.json()).get('model') or _CHIEF['model']; return _chief_view()
    appmod.app.router.routes[0:0] = [_Route('/ui/api/chief', chief_get, methods=['GET']), _Route('/ui/api/chief', chief_patch, methods=['PATCH'])]

# HLA_SUBSCRIPTIONS_PREVIEW: two finished runs on the Claude subscription (the real engines run the fake CLIs above).
def subs_seed(db, agent):
    """Two finished runs on the Claude subscription (cost 0) for the Costs page and the task list."""
    now = datetime.now(timezone.utc)
    for i, text in enumerate(['Summarise the open pull requests on example/site.', 'Draft release notes for the dashboard.']):
        db.execute("""INSERT INTO tasks(id,created_at,finished_at,task_text,status,provider,worker_model,result_text,input_tokens,output_tokens,
            openrouter_cost_usd,agent_id,workspace) VALUES(%s,%s,%s,%s,'completed','subscription','claude-sub/sonnet','Done.',900,300,0,%s,'home')""",
                   (uuid.uuid4(), now - timedelta(days=2, hours=i), now - timedelta(days=2, hours=i) + timedelta(seconds=30), text, agent))
# ── end HLA_SUBSCRIPTIONS_PREVIEW ─────────────────────────────────────────────────────────────────────────────────

def seed():
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        agent = uuid.uuid4()
        db.execute("""INSERT INTO agents(id,name,role,instructions,workspace,model) VALUES
            (%s,'Infra Agent','Linux infrastructure specialist','Diagnose first.','home','qwen/qwen3-235b-a22b')""", (agent,))
        now = datetime.now(timezone.utc)
        statuses = ['completed'] * 6 + ['failed', 'incomplete', 'rejected', 'human_approval_required']
        prompts = ['Check disk usage on all servers and summarise which hosts are above 80%.',
                   'Install jq on dev using server_maintenance.',
                   'Why does the file manager at http://192.168.10.108:8080/files/ refuse to delete files? ' + 'Please investigate the container user, the mounts, ACLs, NFS export options and SMB share configuration in detail. ' * 12,
                   'Summarise the Proxmox inventory.',
                   'Explain how to verify a successful Proxmox backup without changing anything.']
        for i in range(42):
            st = statuses[i % len(statuses)]
            db.execute("""INSERT INTO tasks(id,created_at,finished_at,task_text,status,provider,worker_model,result_text,
                input_tokens,output_tokens,openrouter_cost_usd,agent_id,workspace,error_message,risk_probability,expert_probability)
                VALUES(%s,%s,%s,%s,%s,'openrouter','qwen/qwen3-235b-a22b',%s,812,455,%s,%s,%s,%s,%s,%s)""",
                (uuid.uuid4(), now - timedelta(hours=i * 3), now - timedelta(hours=i * 3) + timedelta(seconds=40),
                 prompts[i % len(prompts)], st, LONG_MD if st == 'completed' else None, round(random.random() / 1000, 6),
                 agent if i % 2 else None, 'home' if i % 4 else 'work',
                 {'failed': 'OpenRouter failed: ReadTimeout', 'incomplete': 'Model response truncated at output token limit',
                  'rejected': 'Rejected in dashboard'}.get(st), 0.71 if st == 'human_approval_required' else 0.05, 0.2))
        subs_seed(db, agent)  # HLA_SUBSCRIPTIONS_PREVIEW
        # Facts in every state: fresh, stale (expired), changed (with a previous value), unverified history.
        from memory import store as mstore
        mstore.save(db, 'home', 'diag:fs-01:path-permissions:/srv/files/shared',
                    'path-permissions of /srv/files/shared on fs-01:\ndrwxr-xr-x root root /srv/files/shared', 'ssh-gateway:diagnostic', True,
                    kind='diagnostic', server='fs-01', agent_id=agent, ttl_hours=24)
        mstore.save(db, 'home', 'diag:fs-01:path-permissions:/srv/files/shared',
                    'path-permissions of /srv/files/shared on fs-01:\ndrwxrwxr-x root files /srv/files/shared', 'ssh-gateway:diagnostic', True,
                    kind='diagnostic', server='fs-01', agent_id=agent, ttl_hours=24)
        mstore.save(db, 'home', 'diag:dev:disk', 'disk on dev:\n/dev/sda1  32G  21G  9.6G  69% /', 'ssh-gateway:diagnostic', True,
                    kind='diagnostic', server='dev', agent_id=agent, ttl_hours=6)
        db.execute("UPDATE agent_memories SET verified_at=NOW()-interval '9 hours',expires_at=NOW()-interval '3 hours' WHERE key='diag:dev:disk'")
        mstore.save(db, 'home', 'op:dev:install_package:jq', 'Package jq is installed on dev (version 1.7.1-1), verified by dpkg-query after the install.',
                    'ssh-gateway:maintenance', True, kind='change', server='dev', agent_id=agent, ttl_hours=720)
        mstore.save(db, 'home', 'op:dev:install_package:htop', 'install_package htop on dev was attempted and NOT verified (apt_lock_timeout).',
                    'ssh-gateway:maintenance', False, kind='change', server='dev', agent_id=agent, ttl_hours=24 * 365)
        # Queue: jobs in each state (the worker is a separate process and does not run here).
        done_task = db.execute("SELECT id FROM tasks WHERE status='completed' ORDER BY created_at DESC LIMIT 1").fetchone()['id']
        for status, text, minutes, task_id, result, error in (
                ('queued', 'Summarise last night\'s backup logs.', 5, None, None, None),
                ('completed', 'Explain how to verify a successful Proxmox backup without changing anything.', 90, done_task, LONG_MD[:400], None),
                ('failed', 'Draft a maintenance notice for the file server.', 180, None, None, 'API HTTP 502: OpenRouter failed: ReadTimeout'),
                ('cancelled', 'List the containers on docker.', 300, None, None, None)):
            db.execute("""INSERT INTO queued_jobs(id,created_at,started_at,finished_at,task_text,workspace,status,api_task_id,result_text,error_message)
                VALUES (%s,NOW()-make_interval(mins => %s),CASE WHEN %s<>'queued' THEN NOW()-make_interval(mins => %s) END,
                CASE WHEN %s<>'queued' THEN NOW()-make_interval(mins => %s)+interval '40 seconds' END,%s,'home',%s,%s,%s,%s)""",
                (uuid.uuid4(), minutes, status, minutes, status, minutes, text, status, task_id, result, error))
        # HLA_GITHUB_V1: a finished project whose PR waits to be merged (state fresh, so no gateway refresh).
        gp, gt = uuid.uuid4(), uuid.uuid4()
        db.execute('''INSERT INTO projects(id,title,goal,status,workdir,merge_policy,updated_at) VALUES (%s,'Login form','Add a login form.',
            'completed','/home/svc-hla-agent/projects/login',%s,NOW()-interval '1 day')''', (gp, 'pr'))
        db.execute('''INSERT INTO project_tasks(id,project_id,seq,key,title,status,agent_id,result) VALUES (%s,%s,1,'login','Build the login form',
            'done',%s,'Branch task/login\nPR: https://github.com/example/site/pull/12')''', (gt, gp, agent))
        db.execute('''INSERT INTO project_prs(project_id,task_id,url,repo,number,state,title,checks,checked_at) VALUES
            (%s,%s,'https://github.com/example/site/pull/12','example/site',12,'open','Login form','success',NOW()+interval '1 day')''', (gp, gt))
        db.commit()
        return agent

async def main():
    config = uvicorn.Config(appmod.app, host='127.0.0.1', port=int(os.environ.get('DEV_PORT', '8765')), log_level='warning')
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started: await asyncio.sleep(0.05)
    agent = seed()
    # The seeded Infra Agent gets the (mocked) SSH tools, like the live SSH agent.
    os.environ.update({'SSH_TOOL_ENABLED': 'true', 'SSH_TOOL_AGENT_ID': str(agent), 'TOOL_GATEWAY_TOKEN': 'g' * 40})
    now_seed = _time.time()
    gw_policy.update(GW_DB, {'agents': {str(agent): {'name': 'Infra Agent', 'level': 5}},
                             'apps': {'site': {'server': 'public-website', 'path': '/opt/site', 'repo': 'git@github.com:example/site.git',
                                               'health_url': 'http://127.0.0.1:8080/', 'staging': True}}}, 'preview')
    seed_routes(agent)  # HLA_ROUTES_V1: two domains, a LAN-only route and an agent's pending request
    gw_policy.update(GW_DB, {'servers': {'proxmox:pve2': {'level': 5}, 'proxmox:proxmox': {'level': 1}}}, 'preview')  # HLA_PROXMOX_V1
    gw_policy.update(GW_DB, {'servers': {'omada:controller': {'level': 5}}}, 'preview')  # HLA_OMADA_V1
    # MCP servers: Notion (read-only tools reachable), a LAN file server behind a proxy, one that fails to connect.
    gw_mcp.save(GW_DB, 'notion', {'url': 'https://mcp.notion.com/mcp', 'auth': {'type': 'bearer', 'credential': 'NOTION_TOKEN'},
                                  'description': 'Team wiki: specs, runbooks and meeting notes'}, 'preview')
    gw_mcp.save(GW_DB, 'files-lan', {'url': 'http://192.168.10.50:8000/mcp', 'allow_private': True, 'allow_insecure': True,
                                     'auth': {'type': 'header', 'header': 'X-API-Key', 'credential': 'FILES_MCP_KEY'}}, 'preview')
    gw_mcp.save(GW_DB, 'weather-down', {'url': 'https://weather.example.net/mcp'}, 'preview')
    for name in ('notion', 'files-lan'):
        snapshot = MCP_TOOLS[name] + (MCP_TOOLS['notion-later'] if name == 'notion' else [])
        if name == 'notion':
            snapshot = [dict(t, changed=['description']) if t['name'] == 'create_page' else t for t in snapshot]
        GW_DB.execute("UPDATE mcp_servers SET tools=?,server_info=?,refreshed_at=? WHERE name=?",
                      (json.dumps(snapshot), json.dumps({'name': name + '-mcp', 'version': '1.2.0'}), now_seed - 3600, name))
    GW_DB.execute("UPDATE mcp_servers SET last_error='mcp_unreachable: ConnectionRefusedError' WHERE name='weather-down'")
    gw_policy.update(GW_DB, {'servers': {'mcp:notion': {'level': 1}}, 'mcp': {'tools': {'notion': {
        'update_page': {'level': 6}, 'search': {'level': 1}, 'fetch_page': {'level': 1}}}}}, 'preview')
    # Audit history from the last days (newer entries come from the seeded conversations below).
    now, day = _time.time(), 86400
    for i in range(45):  # older routine checks, so the log has a second page
        gw_record('docker', 'agent_diagnostic', 'containers', 0, agent=agent, ts=now - 30 * day + i * 3600)
    for ts, server, action, detail, code, actor, ag in (
            (now - 6 * day, 'dev', 'agent_diagnostic', 'disk', 0, None, agent),
            (now - 5 * day, 'homeassistant', 'agent_ha_read', 'states:/api/states', 0, None, agent),
            (now - 4 * day, 'dev', 'chat_approval', 'sudo systemctl restart nginx', None, 'jordan', None),
            (now - 4 * day + 20, 'dev', 'agent_approved_command', 'sudo systemctl restart nginx', 0, None, agent),
            (now - 3 * day, 'sandbox', 'agent_write_file', '/home/svc/site/index.html (write, sha256 9f2c41aa07d3)', 0, None, agent),
            (now - 3 * day + 60, 'sandbox', 'agent_free_shell', 'cd /home/svc/site && docker compose up -d --build && curl -fsS http://localhost:8080/ | head -20', 0, None, agent),
            (now - 2 * day, 'secrets', 'agent_secret_created', 'create {{secret:SITE_DB_PASSWORD}} (password)', 0, 'agent:' + str(agent), agent),
            (now - 2 * day + 30, 'sandbox', 'agent_secret_use', 'uses {{secret:SITE_DB_PASSWORD}}', None, 'agent:' + str(agent), agent),
            (now - 2 * day + 40, 'sandbox', 'agent_free_shell', 'mysql -u site --password=hunter2 -e "SELECT 1"', 1, None, agent),
            (now - 1 * day, 'public-website', 'deploy_rolled_back', 'site feature/new-header -> e7f8a9b00000 (unhealthy)', None, 'agent:' + str(agent), agent),
            (now - 1 * day + 90, 'jellyfin', 'agent_denied', 'sudo reboot', None, None, agent)):
        gw_record(server, action, detail, code, actor=actor, agent=ag, ts=ts)
    # Deployment history: a good deploy, then one that failed health checks and was rolled back.
    site = gw_policy.app(GW_DB, 'site')
    for ref, status, sha, prev, err in (('main', 'deployed', 'c3d4e5f6' + '0' * 32, 'a1b2c3d4' + '0' * 32, None),
                                        ('feature/new-header', 'rolled_back', 'e7f8a9b0' + '0' * 32, 'c3d4e5f6' + '0' * 32, 'unhealthy')):
        ident = gw_deploy.start(GW_DB, site, ref, 'agent:' + str(agent))
        gw_deploy.finish(GW_DB, ident, {'status': status, 'sha': sha, 'from': prev, 'error_class': err})
    # Conversations go through the real API so they get real tasks, kinds and statuses.
    async with _Real(base_url=f'http://127.0.0.1:{config.port}') as c:
        await c.post('/ui/api/login', json={'password': 'pw'})
        H = {'x-agent-ui': '1'}
        async def settle(tid):
            for _ in range(200):
                if (await c.get(f'/ui/api/threads/{tid}')).json()['thread']['status'] == 'idle': return
                await asyncio.sleep(0.1)
        t = (await c.post('/ui/api/threads', headers=H, json={'title': 'Fileserver delete permissions', 'workspace': 'home', 'agent_id': str(agent)})).json()['id']
        await c.post(f'/ui/api/threads/{t}/messages', headers=H, timeout=30, json={'content': 'What user does the file manager on fs-01 run as?'})
        await settle(t)
        await c.post(f'/ui/api/threads/{t}/messages', headers=H, timeout=30, json={'content': 'Fix the permissions so I can delete files in /srv/files/shared'})
        await settle(t)
        a = (await c.post('/ui/api/threads', headers=H, json={'title': 'Shared folder ACL', 'workspace': 'home', 'agent_id': str(agent)})).json()['id']
        await c.post(f'/ui/api/threads/{a}/messages', headers=H, timeout=30, json={'content': 'Apply the ACL change you proposed on dev.'})
        await settle(a)
        mv = (await c.post('/ui/api/threads', headers=H, json={'title': 'Monitoring VM', 'workspace': 'home', 'agent_id': str(agent)})).json()['id']
        await c.post(f'/ui/api/threads/{mv}/messages', headers=H, timeout=30, json={'content': 'Create the monitoring VM on pve2: Debian 13, 192.168.10.205.'})
        await settle(mv)
        om = (await c.post('/ui/api/threads', headers=H, json={'title': 'Monitoring DHCP', 'workspace': 'home', 'agent_id': str(agent)})).json()['id']
        await c.post(f'/ui/api/threads/{om}/messages', headers=H, timeout=30, json={'content': 'Create a DHCP reservation for 192.168.10.205 (monitoring).'})
        await settle(om)
        for title in ['Deploy monitoring stack', 'Jellyfin transcoding is slow on the docker host — investigate GPU passthrough', 'Backup verification']:
            x = (await c.post('/ui/api/threads', headers=H, json={'title': title, 'workspace': 'home'})).json()['id']
            await c.post(f'/ui/api/threads/{x}/messages', headers=H, timeout=30, json={'content': 'Give me a status overview.'})
            await settle(x)
        # A project whose plan waits for approval.
        await c.post('/ui/api/projects', headers=H, timeout=30, json={'title': 'Bakery website', 'goal': 'Research bakery.example and build a new static '
                     'website in the sandbox; deploy it to staging when it works.', 'planner_agent_id': str(agent), 'budget_usd': 1})
        for _ in range(100):
            projects_now = (await c.get('/ui/api/projects')).json()['projects']
            if projects_now and projects_now[0]['status'] == 'awaiting_approval': break
            await asyncio.sleep(0.1)
    # The website monitor's first pass (normally 90 s after startup): stores the statuses, so the shop is in "Needs you".
    await appmod.websites.check_now(appmod.database_connection)
    print('READY', flush=True)
    await task

asyncio.run(main())
