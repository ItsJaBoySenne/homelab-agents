"""MCP servers through the gateway (HLA_MCP_V1): agents call tools of remote MCP servers; the gateway holds the auth.

Transport: Streamable HTTP only (MCP 2025-03-26 and later): every JSON-RPC message is a POST to the server's endpoint
with `Accept: application/json, text/event-stream`; the answer is JSON or an SSE stream. `initialize` returns the
session (Mcp-Session-Id), then `notifications/initialized`, `tools/list` (paged with cursors) and `tools/call`. A session
is cached per server and initialized again when the server answers 404 (session expired) or after 30 idle minutes.
The legacy HTTP+SSE transport (2024-11-05) and stdio servers are not run here: put a stdio server behind a small
Streamable HTTP proxy on a host (e.g. `npx supergateway --stdio "..." --outputTransport streamableHttp` or
`mcp-proxy --transport streamablehttp`) and register its URL.

Registry: table mcp_servers, edited with the admin token (dashboard, MCP page). Auth is none, a bearer token or a custom
header, the value always a credential name from the credential store (gateway/credentials.py); values are masked in
every reply and audit row. A URL that itself carries a secret (a token in the path) is stored as a credential too
(url_credential instead of url); token-looking path segments of a plain URL are masked in the dashboard view and audit.

Policy: every server is the service mcp:<name> (level 0 = off until the owner raises it). A tool needs level 4, or 6
when the server marks it destructiveHint, unless the owner overrides it (policy_settings mcp_tools:
{"server": {"tool": {"level": 1..6} | {"disabled": true}}}). readOnlyHint is not trusted: a server cannot lower a
tool's level by calling it read-only. Tools that appear on a later refresh (not in the previous snapshot) are marked
new and stay disabled until the owner sets an override for them (the Enable button on the MCP page); description,
hint and schema changes are flagged on the refresh (view and audit row). An agent may call a tool when
min(agent level, service level) >= 1 and reaches that level, or with a free shell there (level 6, or unhinged mode).

SSRF (gateway/netcheck.py, the same rules as HTTP tools): loopback, link-local, metadata (169.254.0.0/16,
fd00:ec2::/32, 100.100.100.200), multicast, reserved and documentation addresses, also wrapped in IPv6 forms
(IPv4-mapped/compatible, NAT64, 6to4, Teredo), the gateway's own addresses and the host names of this stack are always
refused; private ranges (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7) only for servers with allow_private;
http:// only with allow_insecure and only to such private addresses. The name is resolved and every address checked
once per request; the connection goes to a checked address (no DNS rebinding), trying them in DNS order when one
cannot be reached; redirects are never followed.
"""
import http.client
import ipaddress
import itertools
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from . import credentials, masking, netcheck, policy

PREFIX = 'mcp:'
PROTOCOL_VERSION = '2025-06-18'
CONNECT_TIMEOUT = 10
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120
MAX_RESPONSE = 2 * 1024 * 1024     # bytes read from one answer
RESULT_CHARS = 20000               # tool result text handed to the agent
MAX_ARGUMENTS = 100000             # JSON characters of one call's arguments
MAX_TOOLS = 200                    # per server snapshot
MAX_PAGES = 20
MAX_SCHEMA = 30000                 # JSON characters of one tool's input schema
SESSION_IDLE = 1800
SERVER_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
TOOL_NAME = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
HEADER_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,63}$')
RESERVED_HEADERS = {'host', 'content-length', 'content-type', 'accept', 'connection', 'transfer-encoding', 'mcp-session-id',
                    'mcp-protocol-version', 'user-agent', 'te', 'upgrade', 'proxy-authorization', 'cookie'}
AUTH_TYPES = ('none', 'bearer', 'header')
HINTS = ('title', 'readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint')
_SECRET_QUERY = re.compile(r'(^|&)[^=&]*(key|token|secret|password|passwd|auth)[^=&]*=', re.I)
_SECRET_SEGMENT_AFTER = {'s', 'key', 'keys', 'token', 'tokens', 'secret', 'secrets', 'apikey', 'api-key', 'api_key', 'auth'}
CHANGE_FIELDS = ('description', 'annotations', 'input_schema', 'title')
MAX_ATTEMPTS = 4
# Only tests set this: their fake MCP server listens on 127.0.0.1 (then treated like a private address).
_test_allow_loopback = False
_ids = itertools.count(1)
_sessions = {}
_lock = threading.Lock()


class MCPError(Exception):
    """A refusal or failure with a reason safe to show (never a credential value)."""
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


class _Expired(Exception):
    pass


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mcp_servers (
        name TEXT PRIMARY KEY, url TEXT NOT NULL, auth_type TEXT NOT NULL DEFAULT 'none', auth_header TEXT NOT NULL DEFAULT '',
        credential TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, timeout INTEGER NOT NULL DEFAULT 60,
        allow_private INTEGER NOT NULL DEFAULT 0, allow_insecure INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '',
        tools TEXT, server_info TEXT, refreshed_at REAL, last_error TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '')''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(mcp_servers)')}
    if 'url_credential' not in columns:  # additive: the URL itself kept in the credential store
        db.execute("ALTER TABLE mcp_servers ADD COLUMN url_credential TEXT NOT NULL DEFAULT ''")


# ---------------------------------------------------------------- addresses and URLs

def address_problem(ip, allow_private):
    """None when the gateway may connect to this address, else a short reason (gateway/netcheck.py)."""
    problem = netcheck.address_problem(ip, allow_private, allow_loopback=_test_allow_loopback)
    if problem == netcheck.PRIVATE_REASON:
        return 'private address (allow private addresses for this server)'
    return problem


def _private(ip):
    return netcheck.is_private(ip, allow_loopback=_test_allow_loopback)


def display_url(url):
    """The URL as the dashboard and the audit log show it: token-looking path segments and query values masked
    (long random strings of letters and digits, and the segment after /s/, /key/, /token/ ...)."""
    if not isinstance(url, str):
        return url
    parts = urlsplit(url)

    def secretish(text):
        return len(text) >= 24 and re.search(r'[A-Za-z]', text) and re.search(r'[0-9]', text) and re.fullmatch(r'[A-Za-z0-9._~%+=-]+', text)
    segments, out = parts.path.split('/'), []
    for i, seg in enumerate(segments):
        after = i > 0 and segments[i - 1].lower() in _SECRET_SEGMENT_AFTER
        out.append('[redacted]' if seg and (after or secretish(seg)) else seg)
    query = '&'.join((p.split('=', 1)[0] + '=[redacted]') if '=' in p and secretish(p.split('=', 1)[1]) else p
                     for p in parts.query.split('&')) if parts.query else ''
    return parts.scheme + '://' + parts.netloc + '/'.join(out) + ('?' + query if query else '')


def check_url(url, allow_private=False, allow_insecure=False, secret_ok=False):
    """Validate a server URL as saved; raises MCPError. Literal addresses are checked now, names when connecting.
    secret_ok: the URL comes from the credential store, so keys in its query string are fine."""
    if not isinstance(url, str) or not 8 <= len(url) <= 500 or any(c.isspace() or ord(c) < 32 for c in url):
        raise MCPError(400, 'invalid_url', 'the URL is https://host[:port]/path (at most 500 characters)')
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise MCPError(400, 'invalid_url', 'the URL starts with https:// (or http:// for a private host)')
    if parts.scheme == 'http' and not allow_insecure:
        raise MCPError(400, 'insecure_url', 'http:// needs "allow plain http" (only for a private host); use https://')
    if parts.username or parts.password or '@' in parts.netloc:
        raise MCPError(400, 'invalid_url', 'no user name or password in the URL: use a credential')
    if parts.fragment:
        raise MCPError(400, 'invalid_url', 'no #fragment in the URL')
    if parts.query and _SECRET_QUERY.search(parts.query) and not secret_ok:
        raise MCPError(400, 'secret_in_url', 'keys and tokens do not belong in the URL: store them as a credential and use header auth')
    try:
        parts.port
    except ValueError:
        raise MCPError(400, 'invalid_url', 'invalid port') from None
    host = parts.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
        if netcheck.host_problem(host):
            raise MCPError(400, 'address_refused', 'loopback addresses and the hosts of this stack are refused')
    if literal is not None:
        problem = address_problem(literal, allow_private)
        if problem:
            raise MCPError(400, 'address_refused', problem)
        if parts.scheme == 'http' and not _private(literal):
            raise MCPError(400, 'insecure_url', 'plain http is only allowed to a private address')
    return url


def _resolve(host, port, cfg):
    """Every address of host, all checked, in the order to try (gateway/netcheck.py)."""
    if netcheck.host_problem(host):
        raise MCPError(403, 'address_refused', 'loopback addresses and the hosts of this stack are refused')
    try:
        addresses = netcheck.resolve(host, port)
    except (OSError, UnicodeError) as exc:
        raise MCPError(502, 'mcp_unreachable', 'cannot resolve ' + host + ' (' + type(exc).__name__ + ')') from None
    if not addresses:
        raise MCPError(502, 'mcp_unreachable', 'cannot resolve ' + host)
    for address in addresses:  # every answer must be acceptable, so a mixed answer cannot slip a refused one in
        problem = address_problem(address, cfg['allow_private'])
        if problem:
            raise MCPError(403, 'address_refused', host + ' resolves to a ' + problem)
        if cfg['url'].startswith('http://') and not _private(address):
            raise MCPError(403, 'insecure_url', 'plain http is only allowed to a private address')
    return netcheck.connect_order(addresses)[:MAX_ATTEMPTS]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, **kw):
        super().__init__(host, port, **kw)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, **kw):
        super().__init__(host, port, **kw)
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # certificate checked against the name


# ---------------------------------------------------------------- JSON-RPC over Streamable HTTP

def auth_headers(db, cfg):
    if cfg['auth_type'] == 'none':
        return {}
    try:
        value = credentials.get(db, cfg['credential'])
    except credentials.CredentialError as exc:
        raise MCPError(503, exc.error, exc.detail) from None
    if not value:
        raise MCPError(503, 'credential_missing', 'credential ' + cfg['credential'] + ' is not stored (Connections page)')
    if any(c in value for c in '\r\n'):
        raise MCPError(503, 'credential_invalid', 'credential ' + cfg['credential'] + ' contains a line break')
    if cfg['auth_type'] == 'bearer':
        return {'Authorization': value if value.lower().startswith('bearer ') else 'Bearer ' + value}
    return {cfg['auth_header']: value}


def _read(resp, limit):
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise MCPError(502, 'mcp_response_too_large', 'the answer is larger than %d bytes' % limit)
    return data


def _pick(messages, ident):
    for msg in messages if isinstance(messages, list) else [messages]:
        if isinstance(msg, dict) and msg.get('id') == ident and ('result' in msg or 'error' in msg):
            return msg
    return None


def _read_sse(resp, ident, deadline, sock):
    """The JSON-RPC response with our id from an SSE stream; other events (notifications, progress) are skipped."""
    total, data = 0, []
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise MCPError(504, 'mcp_timeout', 'no answer from the MCP server in time')
        if sock is not None:
            sock.settimeout(left)
        line = b''
        while not line.endswith(b'\n'):  # a whole line, however long (one big data: line), within the size cap
            piece = resp.readline(min(65536, MAX_RESPONSE - total + 1))
            total += len(piece)
            if total > MAX_RESPONSE:
                raise MCPError(502, 'mcp_response_too_large', 'the answer is larger than %d bytes' % MAX_RESPONSE)
            if not piece:
                break
            line += piece
            left = deadline - time.monotonic()
            if left <= 0:
                raise MCPError(504, 'mcp_timeout', 'no answer from the MCP server in time')
            if sock is not None:
                sock.settimeout(left)
        if not line:
            break
        line = line.decode('utf-8', 'replace').rstrip('\r\n')
        if line.startswith('data:'):
            data.append(line[5:][1:] if line[5:].startswith(' ') else line[5:])
        elif not line and data:
            try:
                msg = _pick(json.loads('\n'.join(data)), ident)
            except ValueError:
                msg = None
            data = []
            if msg is not None:
                return msg
    if data:
        try:
            msg = _pick(json.loads('\n'.join(data)), ident)
        except ValueError:
            msg = None
        if msg is not None:
            return msg
    raise MCPError(502, 'mcp_no_response', 'the event stream ended without an answer')


def _rpc(db, cfg, method, params, session, timeout, notify=False):
    """One JSON-RPC message: (result, session id from the answer). Raises MCPError, or _Expired on 404 within a session."""
    parts = urlsplit(cfg['url'])
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    addresses = _resolve(parts.hostname, port, cfg)
    ident = None if notify else next(_ids)
    body = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        body['params'] = params
    if ident is not None:
        body['id'] = ident
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'User-Agent': 'hla-gateway'}
    if session.get('id'):
        headers['Mcp-Session-Id'] = session['id']
    if session.get('version'):
        headers['MCP-Protocol-Version'] = session['version']
    headers.update(auth_headers(db, cfg))
    deadline = time.monotonic() + timeout
    conn = None
    try:
        try:
            for attempt, address in enumerate(addresses):  # the next checked address only when nothing was sent
                if parts.scheme == 'https':
                    conn = _PinnedHTTPS(parts.hostname, port, address, timeout=CONNECT_TIMEOUT, context=ssl.create_default_context())
                else:
                    conn = _PinnedHTTP(parts.hostname, port, address, timeout=CONNECT_TIMEOUT)
                try:
                    conn.connect()
                    break
                except ssl.SSLError:
                    raise
                except OSError:
                    conn.close()
                    if attempt + 1 >= len(addresses):
                        raise
            conn.sock.settimeout(timeout)
            conn.request('POST', (parts.path or '/') + ('?' + parts.query if parts.query else ''), body=json.dumps(body).encode(), headers=headers)
            resp = conn.getresponse()
        except ssl.SSLError as exc:
            raise MCPError(502, 'mcp_tls_error', type(exc).__name__ + ': ' + str(exc.reason or '')[:120]) from None
        except socket.timeout:
            raise MCPError(504, 'mcp_timeout', 'the MCP server did not answer in time') from None
        except OSError as exc:
            raise MCPError(502, 'mcp_unreachable', type(exc).__name__) from None
        status = resp.status
        if 300 <= status < 400:
            raise MCPError(502, 'mcp_redirect', 'the MCP server answered with a redirect (HTTP %d); redirects are not followed: register the final URL' % status)
        if status == 404 and session.get('id'):
            raise _Expired()
        if status in (401, 403):
            raise MCPError(502, 'mcp_unauthorized', 'the MCP server refused the credentials (HTTP %d)' % status)
        if status >= 400:
            raise MCPError(502, 'mcp_http_error', 'the MCP server answered HTTP %d' % status)
        sid = resp.getheader('Mcp-Session-Id')
        if notify:
            return None, sid
        ctype = (resp.getheader('Content-Type') or '').split(';')[0].strip().lower()
        try:
            if ctype == 'text/event-stream':
                msg = _read_sse(resp, ident, deadline, conn.sock)
            elif ctype == 'application/json':
                msg = _pick(json.loads(_read(resp, MAX_RESPONSE)), ident)
            else:
                raise MCPError(502, 'mcp_bad_response', 'unexpected content type ' + ctype[:60])
        except socket.timeout:
            raise MCPError(504, 'mcp_timeout', 'the MCP server did not answer in time') from None
        except ValueError:
            raise MCPError(502, 'mcp_bad_response', 'the answer is not JSON-RPC') from None
        if msg is None:
            raise MCPError(502, 'mcp_bad_response', 'the answer has no response to the request')
        if 'error' in msg:
            err = msg['error'] if isinstance(msg['error'], dict) else {}
            raise MCPError(502, 'mcp_error', ('%s (code %s)' % (str(err.get('message') or 'error')[:300], err.get('code'))))
        result = msg.get('result')
        if not isinstance(result, dict):
            raise MCPError(502, 'mcp_bad_response', 'the result is not an object')
        return result, sid
    finally:
        if conn is not None:
            conn.close()


def _key(cfg):
    return (cfg['url'], cfg['auth_type'], cfg['auth_header'], cfg['credential'])


def forget(name):
    with _lock:
        _sessions.pop(name, None)


def endpoint(db, cfg):
    """The URL to connect to: the saved one, or the value of the server's URL credential (checked like a saved URL)."""
    name = cfg.get('url_credential')
    if not name:
        return cfg['url']
    try:
        value = credentials.get(db, name)
    except credentials.CredentialError as exc:
        raise MCPError(503, exc.error, exc.detail) from None
    if not value:
        raise MCPError(503, 'credential_missing', 'credential ' + name + ' (the server URL) is not stored (Connections page)')
    value = value.strip()
    try:
        check_url(value, cfg['allow_private'], cfg['allow_insecure'], secret_ok=True)
    except MCPError as exc:
        raise MCPError(exc.status, exc.error, 'the URL in credential ' + name + ': ' + exc.detail) from None
    return value


def _initialize(db, cfg, timeout):
    try:
        return _start(db, cfg, timeout)
    except _Expired:  # a 404 to the initialized notification: the server dropped the session it just made
        raise MCPError(502, 'mcp_session_lost', 'the MCP server does not keep the session') from None


def _start(db, cfg, timeout):
    result, sid = _rpc(db, cfg, 'initialize', {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                                               'clientInfo': {'name': 'hla-gateway', 'version': '1.0'}}, {}, timeout)
    version = result.get('protocolVersion')
    if not isinstance(version, str) or not re.match(r'^\d{4}-\d{2}-\d{2}$', version):
        version = PROTOCOL_VERSION
    info = result.get('serverInfo') if isinstance(result.get('serverInfo'), dict) else {}
    session = {'id': sid if isinstance(sid, str) and 0 < len(sid) <= 200 and sid.isprintable() else None, 'version': version,
               'key': _key(cfg), 'at': time.time(),
               'info': {k: str(info.get(k))[:100] for k in ('name', 'version', 'title') if info.get(k)}}
    _rpc(db, cfg, 'notifications/initialized', None, session, timeout, notify=True)
    with _lock:
        _sessions[cfg['name']] = session
    return session


def request(db, cfg, method, params, timeout=None):
    """A request within the cached session; initialized (again) when needed."""
    timeout = min(MAX_TIMEOUT, max(1, int(timeout or cfg['timeout'])))
    cfg = dict(cfg, url=endpoint(db, cfg))
    with _lock:
        session = _sessions.get(cfg['name'])
    if not session or session['key'] != _key(cfg) or time.time() - session['at'] > SESSION_IDLE:
        session = _initialize(db, cfg, timeout)
    try:
        result, _ = _rpc(db, cfg, method, params, session, timeout)
    except _Expired:  # the server forgot the session: start a new one, once
        session = _initialize(db, cfg, timeout)
        try:
            result, _ = _rpc(db, cfg, method, params, session, timeout)
        except _Expired:
            raise MCPError(502, 'mcp_session_lost', 'the MCP server does not keep the session') from None
    session['at'] = time.time()
    return result, session


# ---------------------------------------------------------------- tools

def _clean_tool(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('name'), str) or not TOOL_NAME.match(raw['name']):
        return None
    schema = raw.get('inputSchema') if isinstance(raw.get('inputSchema'), dict) else {'type': 'object'}
    truncated = len(json.dumps(schema)) > MAX_SCHEMA
    ann = raw.get('annotations') if isinstance(raw.get('annotations'), dict) else {}
    hints = {k: ann[k] for k in HINTS if isinstance(ann.get(k), bool if k != 'title' else str)}
    if 'title' in hints:
        hints['title'] = hints['title'][:200]
    tool = {'name': raw['name'], 'description': str(raw.get('description') or '')[:2000],
            'input_schema': {'type': 'object'} if truncated else schema, 'annotations': hints}
    if isinstance(raw.get('title'), str):
        tool['title'] = raw['title'][:200]
    if truncated:
        tool['schema_truncated'] = True
    return tool


def list_tools(db, cfg):
    """Every tool of the server (cursor pages), cleaned; plus the server info from initialize."""
    tools, cursor, session = [], None, None
    for _ in range(MAX_PAGES):
        result, session = request(db, cfg, 'tools/list', {'cursor': cursor} if cursor else {})
        for raw in result.get('tools') or []:
            tool = _clean_tool(raw)
            if tool and len(tools) < MAX_TOOLS and all(t['name'] != tool['name'] for t in tools):
                tools.append(tool)
        cursor = result.get('nextCursor')
        if not isinstance(cursor, str) or not cursor or len(tools) >= MAX_TOOLS:
            break
    return tools, (session or {}).get('info') or {}


def default_level(tool):
    """4, or 6 for a tool the server marks destructive. readOnlyHint is ignored: the server cannot lower a level."""
    ann = tool.get('annotations') or {}
    if ann.get('destructiveHint') is True:
        return 6
    return 4


def required_level(tool, override):
    """The level a call needs, or None when the owner disabled the tool or has not reviewed a new one yet."""
    if isinstance(override, dict):
        if override.get('disabled'):
            return None
        if override.get('level') in range(1, 7):
            return override['level']
    if tool.get('new'):
        return None
    return default_level(tool)


def compare(previous, tools, overrides):
    """Mark the tools of a fresh snapshot against the previous one: 'new' for tools added since (kept until the owner
    sets an override), 'changed' for the fields that differ. The first snapshot of a server marks nothing (the server
    itself starts at level 0). Returns (new names, {name: changed fields})."""
    if previous is None:
        return [], {}
    old = {t['name']: t for t in previous}
    new, changed = [], {}
    for tool in tools:
        before = old.get(tool['name'])
        if before is None or (before.get('new') and tool['name'] not in overrides):
            tool['new'] = True
            new.append(tool['name'])
        if before is not None:
            diff = [k for k in CHANGE_FIELDS if before.get(k) != tool.get(k)]
            if diff:
                tool['changed'] = diff
                changed[tool['name']] = diff
    return new, changed


def format_result(result, prepared=None):
    """tools/call result -> {is_error, content (text, capped), truncated}."""
    parts = []
    for item in (result.get('content') if isinstance(result.get('content'), list) else [])[:500]:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind == 'text':
            parts.append(str(item.get('text') or ''))
        elif kind in ('image', 'audio'):
            parts.append('[%s %s, about %d bytes, not shown]' % (kind, str(item.get('mimeType') or '')[:60], len(str(item.get('data') or '')) * 3 // 4))
        elif kind == 'resource':
            res = item.get('resource') if isinstance(item.get('resource'), dict) else {}
            parts.append(str(res['text']) if isinstance(res.get('text'), str) else '[resource %s not shown]' % str(res.get('uri') or '')[:300])
        elif kind == 'resource_link':
            parts.append('[resource link %s %s]' % (str(item.get('uri') or '')[:300], str(item.get('name') or '')[:100]))
        else:
            parts.append('[%s content not shown]' % str(kind)[:40])
    text = '\n'.join(parts)
    if not text.strip() and result.get('structuredContent') is not None:
        text = json.dumps(result['structuredContent'], ensure_ascii=False, default=str)
    text = masking.mask_text(text, prepared)  # before the cut, so no part of a value survives it
    truncated = len(text) > RESULT_CHARS
    if truncated:
        text = text[:RESULT_CHARS] + '\n…[%d more characters cut by the gateway]' % (len(text) - RESULT_CHARS)
    return {'is_error': result.get('isError') is True, 'content': text, 'truncated': truncated}


# ---------------------------------------------------------------- registry

_COLUMNS = ('name', 'url', 'auth_type', 'auth_header', 'credential', 'enabled', 'timeout', 'allow_private', 'allow_insecure',
            'description', 'tools', 'server_info', 'refreshed_at', 'last_error', 'created_at', 'updated_at', 'updated_by',
            'url_credential')


def _row(row):
    cfg = dict(zip(_COLUMNS, row))
    for k in ('enabled', 'allow_private', 'allow_insecure'):
        cfg[k] = bool(cfg[k])
    for k, empty in (('tools', None), ('server_info', {})):
        try:
            cfg[k] = json.loads(cfg[k]) if cfg[k] else empty
        except ValueError:
            cfg[k] = empty
    return cfg


def servers(db):
    migrate(db)
    return {r[0]: _row(r) for r in db.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM mcp_servers ORDER BY name').fetchall()}


def server(db, name):
    if not isinstance(name, str) or not SERVER_NAME.match(name):
        return None
    migrate(db)
    row = db.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM mcp_servers WHERE name=?', (name,)).fetchone()
    return _row(row) if row else None


def view(db, cfg, schemas=False, known=None):
    """What the dashboard sees of a server: settings, status and tools with their levels; never a credential value."""
    overrides = policy.mcp_tools(db).get(cfg['name'], {})
    level = db.execute('SELECT level FROM server_policy WHERE server=?', (PREFIX + cfg['name'],)).fetchone()
    tools = []
    for tool in cfg['tools'] or []:
        item = {k: v for k, v in tool.items() if schemas or k != 'input_schema'}
        override = overrides.get(tool['name'])
        item.update(default_level=default_level(tool), override=override, required_level=required_level(tool, override),
                    new=bool(tool.get('new')), review=bool(tool.get('new')) and not isinstance(override, dict),
                    changed=tool.get('changed') or [])
        tools.append(item)
    names = set(known) if known is not None else None
    url_credential = cfg.get('url_credential') or None
    return {'name': cfg['name'], 'service': PREFIX + cfg['name'], 'url': None if url_credential else display_url(cfg['url']),
            'url_credential': url_credential,
            'url_credential_stored': None if not url_credential or names is None else url_credential in names,
            'description': cfg['description'],
            'auth': {'type': cfg['auth_type'], 'credential': cfg['credential'] or None, 'header': cfg['auth_header'] or None},
            'credential_stored': None if cfg['auth_type'] == 'none' or names is None else cfg['credential'] in names,
            'review_count': sum(1 for t in tools if t['review']),
            'enabled': cfg['enabled'], 'timeout': cfg['timeout'], 'allow_private': cfg['allow_private'],
            'allow_insecure': cfg['allow_insecure'], 'level': level[0] if level else 0, 'refreshed_at': cfg['refreshed_at'],
            'last_error': cfg['last_error'], 'server_info': cfg['server_info'], 'tool_count': len(cfg['tools'] or []),
            'refreshed': cfg['tools'] is not None, 'tools': tools, 'stale_overrides': sorted(set(overrides) - {t['name'] for t in tools}),
            'created_at': cfg['created_at'], 'updated_at': cfg['updated_at'], 'updated_by': cfg['updated_by']}


def save(db, name, body, actor):
    """Create or update a server from the dashboard; raises MCPError(400) with a message safe to show."""
    if not isinstance(name, str) or not SERVER_NAME.match(name):
        raise MCPError(400, 'invalid_name', 'names are 1-32 lowercase letters, digits, - and _ (e.g. notion)')
    if not isinstance(body, dict):
        raise MCPError(400, 'invalid_payload')
    current = server(db, name) or {}
    def get(key, default):
        return body[key] if key in body else current.get(key, default)
    auth = body.get('auth') if isinstance(body.get('auth'), dict) else None
    auth_type = (auth or {}).get('type', current.get('auth_type', 'none'))
    credential = (auth or {}).get('credential', current.get('credential', '')) or ''
    header = (auth or {}).get('header', current.get('auth_header', '')) or ''
    allow_private, allow_insecure, enabled = get('allow_private', False), get('allow_insecure', False), get('enabled', True)
    timeout, description, url = get('timeout', DEFAULT_TIMEOUT), get('description', ''), get('url', None)
    url_credential = get('url_credential', '') or ''
    if body.get('url') and not body.get('url_credential'):  # a plain URL replaces a URL credential
        url_credential = ''
    if not all(isinstance(v, bool) for v in (allow_private, allow_insecure, enabled)):
        raise MCPError(400, 'invalid_payload', 'allow_private, allow_insecure and enabled are true or false')
    if allow_insecure and not allow_private:
        raise MCPError(400, 'insecure_url', 'plain http is only for private hosts: allow private addresses too')
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 5 <= timeout <= MAX_TIMEOUT:
        raise MCPError(400, 'invalid_timeout', 'timeout is 5-%d seconds' % MAX_TIMEOUT)
    if not isinstance(description, str) or len(description) > 300:
        raise MCPError(400, 'invalid_description', 'description is at most 300 characters')
    if url_credential:
        if not isinstance(url_credential, str) or not credentials.NAME.match(url_credential):
            raise MCPError(400, 'invalid_url', 'pick a stored credential (UPPER_CASE name) that holds the server URL')
        url = ''  # the URL lives only in the credential store
    else:
        check_url(url, allow_private, allow_insecure)
    if auth_type not in AUTH_TYPES:
        raise MCPError(400, 'invalid_auth', 'auth type is none, bearer or header')
    if auth_type == 'none':
        credential, header = '', ''
    elif not isinstance(credential, str) or not credentials.NAME.match(credential):
        raise MCPError(400, 'invalid_auth', 'pick a stored credential (UPPER_CASE name) for the token')
    if auth_type == 'bearer':
        header = ''
    elif auth_type == 'header' and (not isinstance(header, str) or not HEADER_NAME.match(header) or header.lower() in RESERVED_HEADERS):
        raise MCPError(400, 'invalid_auth', 'the header name is letters, digits and - (e.g. X-API-Key), not a reserved header')
    now = time.time()
    db.execute('BEGIN IMMEDIATE')
    db.execute('''INSERT INTO mcp_servers(name,url,auth_type,auth_header,credential,enabled,timeout,allow_private,allow_insecure,
        description,created_at,updated_at,updated_by,url_credential) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
        url=excluded.url,auth_type=excluded.auth_type,auth_header=excluded.auth_header,credential=excluded.credential,
        enabled=excluded.enabled,timeout=excluded.timeout,allow_private=excluded.allow_private,allow_insecure=excluded.allow_insecure,
        description=excluded.description,updated_at=excluded.updated_at,updated_by=excluded.updated_by,url_credential=excluded.url_credential''',
               (name, url, auth_type, header, credential, int(enabled), timeout, int(allow_private), int(allow_insecure),
                description.strip(), now, now, str(actor)[:120], url_credential))
    if current and (current.get('url') != url or (current.get('url_credential') or '') != url_credential):
        # another server behind the name: its old tool list no longer applies
        db.execute('UPDATE mcp_servers SET tools=NULL,server_info=NULL,refreshed_at=NULL,last_error=NULL WHERE name=?', (name,))
    db.execute('INSERT OR IGNORE INTO server_policy(server,level) VALUES (?,0)', (PREFIX + name,))  # off until raised
    db.execute('COMMIT')
    forget(name)
    return server(db, name)


def delete(db, name):
    if not server(db, name):
        return False
    db.execute('BEGIN IMMEDIATE')
    db.execute('DELETE FROM mcp_servers WHERE name=?', (name,))
    db.execute('DELETE FROM server_policy WHERE server=?', (PREFIX + name,))
    overrides = policy.mcp_tools(db)
    if overrides.pop(name, None) is not None:
        db.execute("UPDATE policy_settings SET value=? WHERE key='mcp_tools'", (json.dumps(overrides, sort_keys=True),))
    db.execute('COMMIT')
    forget(name)
    return True


def refresh(db, name):
    """Connect, list the tools and store the snapshot (or the error). Returns the server."""
    cfg = server(db, name)
    if not cfg:
        raise MCPError(404, 'unknown_server')
    try:
        tools, info = list_tools(db, cfg)
    except MCPError as exc:
        db.execute('UPDATE mcp_servers SET last_error=? WHERE name=?', ((exc.error + ': ' + str(exc.detail))[:300], name))
        raise
    new, changed = compare(cfg['tools'], tools, policy.mcp_tools(db).get(name, {}))
    db.execute('UPDATE mcp_servers SET tools=?,server_info=?,refreshed_at=?,last_error=NULL WHERE name=?',
               (json.dumps(tools), json.dumps(info), time.time(), name))
    out = server(db, name)
    out['refresh_changes'] = {'new': new, 'changed': changed}
    return out


def refresh_summary(cfg):
    """The audit detail of a refresh: tool count, tools added (disabled until reviewed) and tools whose description,
    hints or schema changed."""
    changes = cfg.get('refresh_changes') or {}
    text = cfg['name'] + ' · %d tools' % len(cfg['tools'] or [])
    if changes.get('new'):
        text += ' · new, disabled until reviewed: ' + ', '.join(changes['new'][:20])
    if changes.get('changed'):
        text += ' · changed: ' + ', '.join('%s (%s)' % (n, ', '.join(f)) for n, f in list(changes['changed'].items())[:20])
    return text


# ---------------------------------------------------------------- what agents may use

def _service(db, agent_id, name):
    return policy.effective_services(db, agent_id).get(PREFIX + name, {'level': 0})


def may_call(allowed, need):
    if need is None or allowed.get('level', 0) < 1:
        return False
    return allowed['level'] >= need or bool(allowed.get('free_shell'))


def agent_tools(db, agent_id):
    """The tools this agent may call now (enabled, refreshed servers whose service level and tool level allow it)."""
    services = policy.effective_services(db, agent_id)
    overrides = policy.mcp_tools(db)
    out = []
    for name, cfg in servers(db).items():
        allowed = services.get(PREFIX + name, {'level': 0})
        if not cfg['enabled'] or not cfg['tools'] or allowed.get('level', 0) < 1:
            continue
        for tool in cfg['tools']:
            need = required_level(tool, overrides.get(name, {}).get(tool['name']))
            if may_call(allowed, need):
                out.append({'server': name, 'tool': tool['name'], 'title': tool.get('title') or tool['annotations'].get('title') or '',
                            'description': tool['description'], 'input_schema': tool['input_schema'],
                            'annotations': tool['annotations'], 'level': need})
    return out


def check_call(db, body):
    """Validate an agent's call: (cfg, tool, arguments, need). Raises MCPError."""
    name, tool_name, arguments = body.get('server'), body.get('tool'), body.get('arguments', {})
    cfg = server(db, name)
    if not cfg:
        raise MCPError(404, 'unknown_server', 'no MCP server named %r' % str(name)[:40])
    if not cfg['enabled']:
        raise MCPError(403, 'server_disabled', 'the MCP server is switched off')
    tool = next((t for t in cfg['tools'] or [] if t['name'] == tool_name), None)
    if tool is None:
        raise MCPError(404, 'unknown_tool', 'the server has no tool %r in its last refreshed list' % str(tool_name)[:80])
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict) or len(json.dumps(arguments)) > MAX_ARGUMENTS:
        raise MCPError(400, 'invalid_arguments', 'arguments are a JSON object of at most %d characters' % MAX_ARGUMENTS)
    need = required_level(tool, policy.mcp_tools(db).get(name, {}).get(tool_name))
    allowed = _service(db, body.get('agent_id'), name)
    if need is None and tool.get('new') and not isinstance(policy.mcp_tools(db).get(name, {}).get(tool_name), dict):
        raise MCPError(403, 'tool_not_reviewed', 'this tool is new on the server and stays off until the owner reviews it')
    if need is None:
        raise MCPError(403, 'tool_disabled', 'the owner switched this tool off')
    if not may_call(allowed, need):
        raise MCPError(403, 'policy_denied', 'this tool needs level %d on %s%s; this agent has %d' % (need, PREFIX, name, allowed.get('level', 0)))
    return cfg, tool, arguments, need


def call(db, cfg, tool, arguments, prepared=None):
    """One tools/call; prepared: the masking list of known values (credentials), applied before the result is cut."""
    result, _ = request(db, cfg, 'tools/call', {'name': tool['name'], 'arguments': arguments})
    return format_result(result, prepared)


def audit_detail(tool_name, arguments, result=None, error=None, prepared=None):
    """Tool, argument names and a short preview (masked before the cut, and again by the caller's audit), and the outcome."""
    preview = masking.mask_text(json.dumps(arguments, ensure_ascii=False, default=str), prepared)
    preview = preview if len(preview) <= 160 else preview[:160] + '…'
    text = tool_name + ' · args: ' + (', '.join(sorted(str(k) for k in arguments)[:20]) or '-') + ' · ' + preview
    if error:
        text += ' · ' + error
    elif result is not None:
        text += ' · isError' if result['is_error'] else ' · ok'
    return text
