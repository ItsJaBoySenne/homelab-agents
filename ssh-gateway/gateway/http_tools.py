"""HTTP tools (HLA_HTTP_TOOLS_V1): simple HTTP API calls the owner defines on the dashboard, made by the gateway.

A tool is one request shape: a fixed method, a fixed https base URL (the host never comes from the agent), a path
template with {param} placeholders, typed query/body parameters, static headers and an optional credential from the
credential store (gateway/credentials.py) that only the gateway fills in. Agents get each tool they may call as its
own function (http_<name>) with exactly the declared parameters.

Policy: one service `http_tools` with a level like servers (Policy page). An agent may call a tool when
min(agent level, http_tools level) reaches the tool's own level (default 1 for GET, 4 for writes); unhinged mode
works as on servers. A tool marked approval_required is refused below level 6 / unhinged: there is no per-call
approval flow for HTTP tools (the simpler, safe choice).

SSRF (gateway/netcheck.py, the same rules as MCP servers): the host is resolved on every call and refused when any
address is not public (private, loopback, link-local incl. 169.254.169.254, CGNAT, multicast, reserved,
documentation ranges, also when wrapped in IPv6 forms such as ::ffff:a.b.c.d, NAT64, 6to4 or Teredo); host names
of this stack (ssh-gateway-api, agent-router, database, ...) are refused before any lookup. allow_private: true on a
tool lets it reach private LAN/VPN addresses (RFC 1918, ULA, CGNAT) but never the rest. The connection goes to a
checked address (no second lookup, so DNS rebinding cannot swap it), trying the addresses in DNS order when one
cannot be reached, with TLS verified against the hostname. Redirects are never followed (status and target host
are reported).

Static headers are for fixed, non-secret values: names that carry credentials (Authorization, Cookie, anything with
key/token/secret/password/auth) are refused there; credentials go through auth (the credential picker).

Responses are capped (max_response, at most 256 KiB, truncated with a note) and every credential value is masked
(also encoded, gateway/masking.py) before anything is returned or audited.
"""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.parse

from . import masking, netcheck

NAME = 'http_tools'
METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
TYPES = ('string', 'number', 'integer', 'boolean')
PLACES = ('path', 'query', 'body')
MAX_TOOLS = 50
MAX_PARAMS = 30
MAX_TIMEOUT = 30
MAX_RESPONSE = 256 * 1024
DEFAULT_RESPONSE = 64 * 1024
_TOOL = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
_PARAM = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
_HOST = re.compile(r'^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?:\.(?!-)[A-Za-z0-9-]{1,63})*$')
_PATH = re.compile(r'^/[A-Za-z0-9._~/{}%:@!$&\'()*+,;=-]{0,299}$')
_HEADER = re.compile(r'^[A-Za-z0-9-]{1,64}$')
_CREDENTIAL = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
_PLACEHOLDER = re.compile(r'\{([a-z][a-z0-9_]{0,39})\}')
_FORBIDDEN_HEADERS = {'host', 'content-length', 'transfer-encoding', 'connection', 'te', 'upgrade', 'proxy-authorization'}
# Static headers are stored in plain text and shown on the dashboard: names that carry credentials are refused there.
_SECRET_HEADERS = {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}
_SECRET_HEADER_WORDS = ('key', 'token', 'secret', 'password', 'passwd', 'auth', 'session', 'signature')
MAX_ATTEMPTS = 4


class _ConnectFailed(OSError):
    """The TCP connection to one address could not be made (nothing was sent): the next address may be tried."""


class HttpToolError(Exception):
    """A refusal or failure with a reason safe to show (never a credential value)."""
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS http_tools (name TEXT PRIMARY KEY, definition TEXT NOT NULL,
        updated_at REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '')''')


# ---- definitions -------------------------------------------------------------------------------------------------

def _bad(detail):
    raise HttpToolError(400, 'invalid_tool', detail)


def _typed(value, kind):
    """Is value of this parameter type (bool is not a number)."""
    if kind == 'string': return isinstance(value, str)
    if kind == 'boolean': return isinstance(value, bool)
    if kind == 'integer': return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(name, body):
    """A clean tool definition from a PUT body, or HttpToolError invalid_tool."""
    if not isinstance(name, str) or not _TOOL.match(name):
        _bad('names are snake_case: 2-40 lowercase letters, digits and _, starting with a letter (e.g. weather_today)')
    if not isinstance(body, dict):
        _bad('the definition is an object')
    description = body.get('description')
    if not isinstance(description, str) or not description.strip() or len(description) > 500:
        _bad('description is 1-500 characters: it tells the model what the tool does and when to use it')
    method = str(body.get('method') or 'GET').upper()
    if method not in METHODS:
        _bad('method is one of ' + ', '.join(METHODS))
    base = body.get('base_url')
    if not isinstance(base, str) or len(base) > 200:
        _bad('base_url is an https:// URL, e.g. https://api.example.com/v1')
    parts = urllib.parse.urlsplit(base.strip())
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment \
            or port == -1 or '@' in parts.netloc or not (_HOST.match(parts.hostname) or _is_ip(parts.hostname)):
        _bad('base_url must be https://host[:port][/base path], without user, query or fragment')
    if netcheck.host_problem(parts.hostname):
        _bad('base_url may not point at ' + parts.hostname + ' (' + netcheck.host_problem(parts.hostname) + ')')
    if '..' in parts.path.split('/') or not re.match(r'^[A-Za-z0-9._~/%-]*$', parts.path):
        _bad('base_url path may not contain .. or special characters')
    path = body.get('path') or '/'
    if not isinstance(path, str) or not _PATH.match(path) or '..' in path.split('/') or '//' in path:
        _bad('path starts with / and may contain {param} placeholders, e.g. /repos/{owner}/{name}; no .. or //')
    if re.search(r'[{}]', _PLACEHOLDER.sub('', path)):
        _bad('placeholders are {name} with a declared path parameter')
    params, seen = [], set()
    raw_params = body.get('params') or []
    if not isinstance(raw_params, list) or len(raw_params) > MAX_PARAMS:
        _bad(f'params is a list of at most {MAX_PARAMS} parameters')
    for p in raw_params:
        if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not _PARAM.match(p['name']):
            _bad('parameter names are snake_case (1-40 lowercase letters, digits and _)')
        pname = p['name']
        if pname in seen:
            _bad('parameter ' + pname + ' is declared twice')
        seen.add(pname)
        where = p.get('in') or ('path' if '{' + pname + '}' in path else 'query' if method in ('GET', 'DELETE') else 'body')
        if where not in PLACES:
            _bad('a parameter is in path, query or body')
        if where == 'body' and method in ('GET', 'DELETE'):
            _bad(method + ' tools take path and query parameters only')
        kind = p.get('type') or 'string'
        if kind not in TYPES:
            _bad('parameter type is one of ' + ', '.join(TYPES))
        desc = p.get('description') or ''
        if not isinstance(desc, str) or len(desc) > 200:
            _bad('a parameter description is at most 200 characters')
        enum = p.get('enum')
        if enum is not None and (not isinstance(enum, list) or not 1 <= len(enum) <= 50 or not all(_typed(v, kind) for v in enum)
                                 or any(isinstance(v, str) and len(v) > 100 for v in enum)):
            _bad('enum of ' + pname + ' is a list of 1-50 values of its type')
        item = {'name': pname, 'in': where, 'type': kind, 'required': where == 'path' or p.get('required') is True,
                'description': desc.strip()}
        if enum is not None:
            item['enum'] = enum
        params.append(item)
    declared = {p['name'] for p in params if p['in'] == 'path'}
    used = set(_PLACEHOLDER.findall(path))
    if used != declared:
        _bad('every {placeholder} in the path needs a path parameter and the other way round ('
             + ', '.join(sorted(used ^ declared)) + ')')
    headers = body.get('headers') or {}
    if not isinstance(headers, dict) or len(headers) > 10:
        _bad('headers is an object of at most 10 static headers')
    clean_headers = {}
    for key, value in headers.items():
        if not _HEADER.match(str(key)) or str(key).lower() in _FORBIDDEN_HEADERS:
            _bad('header ' + str(key)[:64] + ' is not allowed')
        if not isinstance(value, str) or len(value) > 500 or any(c in value for c in '\r\n\x00'):
            _bad('header values are text of at most 500 characters on one line')
        if '{credential:' in value:
            _bad('put credentials in auth (the credential picker), not in static headers')
        lower = str(key).lower()
        if lower in _SECRET_HEADERS or any(w in lower for w in _SECRET_HEADER_WORDS):
            _bad('header ' + str(key)[:64] + ' looks like it carries a credential: store the value in the credential store '
                 'and pick it under auth (header name ' + str(key)[:64] + '); static headers are for fixed, non-secret values')
        clean_headers[str(key)] = value
    auth = body.get('auth')
    clean_auth = None
    if auth:
        if not isinstance(auth, dict) or not isinstance(auth.get('credential'), str) or not _CREDENTIAL.match(auth['credential']):
            _bad('auth names a stored credential (UPPER_CASE), e.g. {"credential": "WEATHER_API_KEY"}')
        where = auth.get('in') or 'header'
        aname = auth.get('name') or ('Authorization' if where == 'header' else '')
        prefix = auth.get('prefix') if auth.get('prefix') is not None else ('Bearer ' if aname == 'Authorization' else '')
        if where not in ('header', 'query'):
            _bad('auth goes in a header or a query parameter')
        if where == 'header' and (not _HEADER.match(str(aname)) or str(aname).lower() in _FORBIDDEN_HEADERS):
            _bad('auth header name is not allowed')
        if where == 'query' and not re.match(r'^[A-Za-z0-9_.-]{1,64}$', str(aname)):
            _bad('auth query parameter name is 1-64 letters, digits, _ . -')
        if not isinstance(prefix, str) or len(prefix) > 40 or any(c in prefix for c in '\r\n\x00'):
            _bad('auth prefix is a short text such as "Bearer "')
        if where == 'query' and aname in {p['name'] for p in params}:
            _bad('auth query parameter clashes with a declared parameter')
        clean_auth = {'in': where, 'name': aname, 'prefix': prefix, 'credential': auth['credential']}
    write = method != 'GET'
    level = body.get('level', 4 if write else 1)
    if type(level) is not int or not 1 <= level <= 6:
        _bad('level is 1..6 (default 1 for GET, 4 for writes)')
    timeout = body.get('timeout', 15)
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
        _bad(f'timeout is 1..{MAX_TIMEOUT} seconds')
    max_response = body.get('max_response', DEFAULT_RESPONSE)
    if type(max_response) is not int or not 1024 <= max_response <= MAX_RESPONSE:
        _bad(f'max_response is 1024..{MAX_RESPONSE} bytes')
    for flag in ('approval_required', 'allow_private', 'enabled'):
        if flag in body and not isinstance(body[flag], bool):
            _bad(flag + ' is true or false')
    return {'name': name, 'description': description.strip(), 'method': method,
            'base_url': base.strip().rstrip('/'), 'path': path, 'params': params, 'headers': clean_headers, 'auth': clean_auth,
            'level': level, 'timeout': timeout, 'max_response': max_response,
            'approval_required': body.get('approval_required') is True, 'allow_private': body.get('allow_private') is True,
            'enabled': body.get('enabled', True) is not False}


def _is_ip(text):
    try:
        ipaddress.ip_address(text.strip('[]'))
        return True
    except ValueError:
        return False


def put(db, name, body, actor):
    migrate(db)
    tool = validate(name, body)
    exists = db.execute('SELECT 1 FROM http_tools WHERE name=?', (name,)).fetchone()
    if not exists and db.execute('SELECT COUNT(*) FROM http_tools').fetchone()[0] >= MAX_TOOLS:
        raise HttpToolError(400, 'too_many_tools', f'at most {MAX_TOOLS} HTTP tools')
    db.execute('''INSERT INTO http_tools(name,definition,updated_at,updated_by) VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE
        SET definition=excluded.definition,updated_at=excluded.updated_at,updated_by=excluded.updated_by''',
               (name, json.dumps(tool, sort_keys=True), time.time(), str(actor)[:120]))
    return get(db, name)


def delete(db, name):
    migrate(db)
    return bool(db.execute('DELETE FROM http_tools WHERE name=?', (str(name),)).rowcount)


def get(db, name):
    migrate(db)
    row = db.execute('SELECT definition,updated_at,updated_by FROM http_tools WHERE name=?', (str(name),)).fetchone()
    if not row:
        return None
    try:
        tool = json.loads(row[0])
    except ValueError:
        return None
    return {**tool, 'updated_at': row[1], 'updated_by': row[2]}


def listing(db):
    migrate(db)
    out = []
    for (name,) in db.execute('SELECT name FROM http_tools ORDER BY name').fetchall():
        tool = get(db, name)
        if tool:
            out.append(tool)
    return out


def allowed(tool, level, free):
    """May an agent at this http_tools level (free: level 6 or unhinged) call this tool."""
    if not tool.get('enabled', True) or level < 1:
        return False
    if tool.get('approval_required'):
        return free or level >= 6
    return free or level >= tool['level']


def for_agent(db, level, free):
    """What the agent tool needs (no URLs, headers or credentials): the tools this agent may call."""
    return [{'name': t['name'], 'description': t['description'], 'method': t['method'], 'params': t['params'],
             'level': t['level']} for t in listing(db) if allowed(t, level, free)]


# ---- calls -------------------------------------------------------------------------------------------------------

def check(tool, params, level, free):
    """Validate a call against the tool's parameters and the caller's level: the request to send, or HttpToolError."""
    if tool is None or not tool.get('enabled', True):
        raise HttpToolError(404, 'unknown_tool', 'no such HTTP tool (Connections page)')
    if level is not None and not allowed(tool, level, free):
        if tool.get('approval_required'):
            raise HttpToolError(403, 'policy_denied', tool['name'] + ' needs level 6 or unhinged mode on http_tools '
                                '(approval required: HTTP tools have no per-call approval)')
        raise HttpToolError(403, 'policy_denied', f"{tool['name']} needs level {tool['level']} on http_tools (you have {level})")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise HttpToolError(400, 'invalid_params', 'params is an object')
    spec = {p['name']: p for p in tool['params']}
    extra = sorted(set(params) - set(spec))
    if extra:
        raise HttpToolError(400, 'invalid_params', 'unknown parameter(s): ' + ', '.join(extra[:5]))
    path, query, body = tool['path'], [], {}
    for p in tool['params']:
        name = p['name']
        if name not in params or params[name] is None:
            if p['required']:
                raise HttpToolError(400, 'invalid_params', name + ' is required')
            continue
        value = params[name]
        if not _typed(value, p['type']):
            raise HttpToolError(400, 'invalid_params', name + ' must be a ' + p['type'])
        if isinstance(value, str) and (len(value) > 2000 or '\x00' in value):
            raise HttpToolError(400, 'invalid_params', name + ' is at most 2000 characters')
        if p.get('enum') is not None and value not in p['enum']:
            raise HttpToolError(400, 'invalid_params', name + ' must be one of ' + ', '.join(str(v) for v in p['enum'][:20]))
        text = ('true' if value else 'false') if isinstance(value, bool) else str(value)
        if p['in'] == 'path':
            if text in ('', '.', '..'):
                raise HttpToolError(400, 'invalid_params', name + ' cannot be empty, . or ..')
            path = path.replace('{' + name + '}', urllib.parse.quote(text, safe=''))  # '/' encoded: stays one segment
        elif p['in'] == 'query':
            query.append((name, text))
        else:
            body[name] = value
    return {'tool': tool, 'path': path, 'query': query, 'body': body if tool['method'] in ('POST', 'PUT', 'PATCH') else None}


def _resolve(host, port):
    """All addresses of host in DNS order, without duplicates (tests replace this)."""
    return netcheck.resolve(host, port)


def check_address(text, allow_private):
    """None when this address may be reached, else the reason it may not (gateway/netcheck.py)."""
    reason = netcheck.address_problem(text, allow_private)
    if reason == netcheck.PRIVATE_REASON:
        return 'private address (set allow_private on the tool to reach internal APIs)'
    return reason


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to an already checked address, TLS verified against the hostname (no second DNS lookup)."""
    def __init__(self, host, port, address, timeout):
        self._pinned, self._tls = address, ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._tls)

    def connect(self):
        try:
            sock = socket.create_connection((self._pinned, self.port), self.timeout)
        except OSError as exc:
            raise _ConnectFailed(type(exc).__name__ + ': ' + str(exc)) from None
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


def _send(method, host, port, address, target, headers, data, timeout, limit):
    """One HTTPS request to address: (status, {header: value} lowercase, body bytes up to limit+1). Tests replace this."""
    conn = _PinnedHTTPS(host, port, address, timeout)
    try:
        conn.request(method, target, body=data, headers=headers)
        resp = conn.getresponse()
        body = resp.read(limit + 1)
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


def _mask(text, secrets):
    """Every credential value used for this call masked, also in encoded forms (gateway/masking.py)."""
    return masking.mask_text(text, masking.prepare([(value, '[credential:' + name + ']') for name, value in secrets]))


def shown(req):
    """What the audit log and the dashboard see: method, host, path, and query parameter names (never values)."""
    tool = req['tool']
    host = urllib.parse.urlsplit(tool['base_url']).netloc
    names = ','.join(n for n, _ in req['query'])
    return f"{tool['name']} {tool['method']} {host}{urllib.parse.urlsplit(tool['base_url']).path}{req['path']}" + (f' ?{names}' if names else '')


def run(db, req, read_credential):
    """Carry out a checked request. read_credential(db, name) -> value or None. Returns the reply payload."""
    tool = req['tool']
    secrets = []
    headers = {'User-Agent': 'hla-gateway', 'Accept': 'application/json, text/plain;q=0.9, */*;q=0.5', **tool['headers']}
    query = list(req['query'])
    auth = tool.get('auth')
    if auth:
        try:
            value = read_credential(db, auth['credential'])
        except Exception as exc:  # e.g. the store is off or the key changed
            raise HttpToolError(503, 'credential_unavailable', auth['credential'] + ': ' + str(getattr(exc, 'detail', '') or type(exc).__name__)[:200]) from None
        if not value:
            raise HttpToolError(503, 'credential_missing', auth['credential'] + ' is not in the credential store (Connections page)')
        if any(c in value for c in '\r\n\x00'):
            raise HttpToolError(503, 'credential_invalid', auth['credential'] + ' contains line breaks')
        secrets.append((auth['credential'], value))
        if auth['in'] == 'header':
            headers[auth['name']] = auth['prefix'] + value
        else:
            query.append((auth['name'], auth['prefix'] + value))
    base = urllib.parse.urlsplit(tool['base_url'])
    host, port = base.hostname, base.port or 443
    if netcheck.host_problem(host):
        raise HttpToolError(403, 'address_refused', f'{host}: {netcheck.host_problem(host)}')
    try:
        addresses = _resolve(host, port)
    except (OSError, UnicodeError):
        raise HttpToolError(502, 'dns_failed', 'cannot resolve ' + host) from None
    if not addresses:
        raise HttpToolError(502, 'dns_failed', 'cannot resolve ' + host)
    for address in addresses:
        reason = check_address(address, tool.get('allow_private'))
        if reason:
            raise HttpToolError(403, 'address_refused', f'{host} resolves to {address}: {reason}')
    target = base.path.rstrip('/') + req['path'] + ('?' + urllib.parse.urlencode(query) if query else '')
    data = None
    if req['body'] is not None:
        data = json.dumps(req['body']).encode()
        headers['Content-Type'] = 'application/json'
    limit = tool['max_response']
    started = time.monotonic()
    try:
        order = netcheck.connect_order(addresses)[:MAX_ATTEMPTS]
        for attempt, address in enumerate(order):
            try:
                status, rheaders, raw = _send(tool['method'], host, port, address, target, headers, data, tool['timeout'], limit)
                break
            except _ConnectFailed:  # nothing was sent: the next checked address may answer
                if attempt + 1 >= len(order):
                    raise
    except socket.timeout:
        raise HttpToolError(504, 'timeout', f"no answer from {host} within {tool['timeout']} s") from None
    except ssl.SSLError as exc:
        raise HttpToolError(502, 'tls_failed', _mask(str(exc), secrets)[:200]) from None
    except (OSError, http.client.HTTPException) as exc:
        raise HttpToolError(502, 'request_failed', type(exc).__name__ + ': ' + _mask(str(exc), secrets)[:200]) from None
    truncated = len(raw) > limit
    raw = raw[:limit]
    content_type = str(rheaders.get('content-type') or '')[:100]
    text = _mask(raw.decode('utf-8', errors='replace'), secrets)
    result = {'tool': tool['name'], 'http_status': status, 'ok': 200 <= status < 300, 'content_type': content_type,
              'seconds': round(time.monotonic() - started, 2), 'truncated': truncated}
    if 300 <= status < 400:
        location = urllib.parse.urlsplit(str(rheaders.get('location') or ''))
        result['note'] = 'redirects are not followed' + (f' (to {location.netloc or host})' if rheaders.get('location') else '')
    if truncated:
        result['note'] = f'response truncated at {limit} bytes (max_response of this tool)'
    parsed = None
    if not truncated and 'json' in content_type.lower():
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    if parsed is not None:
        result['json'] = parsed
    else:
        result['text'] = text
    return result
