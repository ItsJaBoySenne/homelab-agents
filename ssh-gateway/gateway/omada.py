"""Omada Software Controller through the gateway (HLA_OMADA_V1): its Open API with a client-credentials app.

Connection
  Service `omada:controller`, present when the inventory has services.omada {url, site, verify_tls}: the pinned base URL
  and the secret-store folder Omada (Omada/CLIENT_ID, Omada/CLIENT_SECRET and optionally Omada/APPLICATION_NAME of an
  Open API app in Client mode). OMADA_URL may change the port, never the host. `site` is the site used when a request
  names none and the controller has several. The client secret and the access tokens never leave the gateway: they are masked in
  every reply and audit row ([secret:OMADA_CLIENT_SECRET], [secret:OMADA_ACCESS_TOKEN]).
  Flow: GET /api/info (no auth) gives the omadacId; POST /openapi/authorize/token?grant_type=client_credentials
  {omadacId, client_id, client_secret} gives an access token (expiresIn seconds, renewed a minute early, and once more when
  the controller says it expired); calls go to /openapi/v1/{omadacId}/... with `Authorization: AccessToken=<token>`.
  Replies are {errorCode, msg, result}; errorCode != 0 is an error.

TLS (the controller's certificate is self-signed)
  verify_tls: true in the inventory: the chain is always checked (against certs/omada-ca.pem / OMADA_CA when present,
  else the system CAs) together with the host name. Otherwise:
  certs/omada-ca.pem (or OMADA_CA) present: the chain is checked against that file only (and the name against
  OMADA_TLS_NAME when set). Otherwise the leaf certificate's SHA-256 is pinned: the first contact records it as pending
  and nothing with credentials is sent until the owner trusts that fingerprint on the dashboard; a different certificate
  later blocks everything again (certificate_changed) until the owner trusts the new one. Only GET /api/info (no
  credentials) is made to an untrusted certificate, so the dashboard can show what it is about to trust.

Levels (min(agent level, omada:controller level); the service starts at 0 = off; unhinged mode does not apply)
  1  status, sites, clients (filter by mac, ip or name), devices, and GET of any path below /openapi/v1/{omadacId}/
  5  write: POST/PUT/PATCH/DELETE below /openapi/v1/{omadacId}/sites/{siteId}/..., each after the owner approved the
     exact request text (chat approval card, single use)
  6  write without approval
"""
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse

from . import core

NAME = 'omada:controller'
SETTINGS = core.INVENTORY['services'].get('omada') or {}
HOST = SETTINGS.get('host')  # None: no controller in the inventory (the service does not exist)
CONFIG_SECONDS = 60
INFO_SECONDS = 300
PAGE = 10000                   # characters of a generic GET reply per call (offset / eof)
MAX_BODY = 4 * 1024 * 1024
MAX_WRITE_BODY = 3000          # characters of a write body (the approval text holds it in full)
TIMEOUT = 20
PAGE_SIZE = 100
MAX_PAGES = 5                  # clients/devices: at most 500 entries scanned per call
TOKEN_MARGIN = 60
TOKEN_EXPIRED = (-44112, -44113)
OPERATIONS = {'status': 1, 'sites': 1, 'clients': 1, 'devices': 1, 'get': 1, 'write': 5}
ADMIN_ONLY = ('trust_certificate',)
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
_PATH = re.compile(r'^/[A-Za-z0-9._~/-]{0,300}$')
_SITE_PATH = re.compile(r'^/sites/[A-Za-z0-9_-]{1,64}(/[A-Za-z0-9._~-]{1,100}){1,8}$')
_QKEY = re.compile(r'^[A-Za-z][A-Za-z0-9_.]{0,40}$')
_MAC = re.compile(r'^[0-9A-Fa-f]{2}([-:]?[0-9A-Fa-f]{2}){5}$')
_FP = re.compile(r'^[0-9a-f]{64}$')
CLIENT_KEYS = ('mac', 'ip', 'name', 'hostName', 'vendor', 'deviceType', 'wireless', 'ssid', 'networkName', 'vid', 'apName',
               'switchName', 'port', 'active', 'lastSeen', 'uptime', 'ipv6List', 'guest', 'blocked')
DEVICE_KEYS = ('mac', 'name', 'type', 'model', 'ip', 'status', 'firmwareVersion', 'version', 'uptime', 'site', 'cpuUtil',
               'memUtil', 'clientNum', 'needUpgrade')

# Replaced in tests: the secret reader and where (host, port) really connects.
_secrets = None
_address = lambda host, port: (host, port)  # noqa: E731

_lock = threading.Lock()
_cfg = {'at': 0.0, 'value': None}
_info = {'at': 0.0, 'value': None}
_token = {'value': None, 'expires': 0.0, 'refresh': None}


class OmadaError(Exception):
    def __init__(self, status, error, detail='', **extra):
        super().__init__(error)
        self.status, self.error, self.detail, self.extra = status, error, detail, extra

    def payload(self):
        return {'error': self.error, 'detail': self.detail, **self.extra}


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS omada_state (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER)')


def _state(db, key):
    row = db.execute('SELECT value FROM omada_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def _set(db, key, value):
    db.execute('INSERT INTO omada_state(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET '
               'value=excluded.value,updated_at=excluded.updated_at', (key, value, int(time.time())))


def forget():
    with _lock:
        _cfg.update(at=0.0, value=None)
        _info.update(at=0.0, value=None)
        _token.update(value=None, expires=0.0, refresh=None)


def known():
    """{name: value} of the client secret and tokens seen so far (for masking; never fetched for this)."""
    with _lock:
        out = {}
        if _cfg['value']:
            out['OMADA_CLIENT_SECRET'] = _cfg['value']['client_secret']
        if _token['value']:
            out['OMADA_ACCESS_TOKEN'] = _token['value']
        if _token['refresh']:
            out['OMADA_REFRESH_TOKEN'] = _token['refresh']
        return out


def _clean(text, limit=300):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(text or ''))[:limit]


# ── configuration ────────────────────────────────────────────────────────────────────────────────────────────────────

def base_url():
    """https://<inventory host>:<port>: the pinned URL, or OMADA_URL when it keeps the pinned host."""
    if NAME not in core.SERVICES:
        raise OmadaError(503, 'omada_not_configured', 'the Omada controller is not in the inventory (services.omada)')
    pinned = core.SERVICES[NAME][0]
    raw = os.getenv('OMADA_URL', '').strip().rstrip('/')
    if not raw:
        return pinned
    parts = urllib.parse.urlsplit(raw)
    try:
        port = parts.port or 443
    except ValueError:
        port = None
    if parts.scheme != 'https' or (parts.hostname or '').lower() != HOST or not port or parts.path not in ('', '/') or parts.query:
        raise OmadaError(503, 'omada_target_mismatch', f'OMADA_URL must be https://{HOST}[:port] (the pinned controller)')
    return f'https://{HOST}:{port}'


def ca_file():
    path = os.getenv('OMADA_CA', '').strip() or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'certs', 'omada-ca.pem')
    return path if os.path.isfile(path) and os.access(path, os.R_OK) else None


def config():
    """{app, client_id, client_secret} from the secret store, folder Omada (cached CONFIG_SECONDS)."""
    with _lock:
        if _cfg['value'] and time.monotonic() - _cfg['at'] < CONFIG_SECONDS:
            return _cfg['value']
    if NAME not in core.SERVICES:
        raise OmadaError(503, 'omada_not_configured', 'the Omada controller is not in the inventory (services.omada)')
    folder = core.SERVICES[NAME][1]
    reader = _secrets or core.platform_secrets
    try:
        values = reader(folder, ('CLIENT_ID', 'CLIENT_SECRET'), ('APPLICATION_NAME',))
    except Exception as exc:  # noqa: BLE001 - only the type is shown, never a value
        raise OmadaError(503, 'omada_not_configured',
                         f'cannot read CLIENT_ID and CLIENT_SECRET from the secret store ({folder.strip("/")}/...) ({type(exc).__name__})') from None
    cid, secret = str(values.get('CLIENT_ID') or '').strip(), str(values.get('CLIENT_SECRET') or '').strip()
    bad = lambda v: not v or len(v) > 200 or any(c.isspace() or ord(c) < 32 for c in v)  # noqa: E731
    if bad(cid) or bad(secret):
        raise OmadaError(503, 'omada_not_configured', f'CLIENT_ID or CLIENT_SECRET in {folder} is empty or not valid')
    cfg = {'app': _clean(values.get('APPLICATION_NAME'), 80), 'client_id': cid, 'client_secret': secret}
    with _lock:
        if _cfg['value'] and _cfg['value']['client_secret'] != secret:
            _token.update(value=None, expires=0.0, refresh=None)
        _cfg.update(at=time.monotonic(), value=cfg)
    return cfg


# ── HTTP with certificate checks ─────────────────────────────────────────────────────────────────────────────────────

class _Connection(http.client.HTTPSConnection):
    """Connects to the pinned host; `check(der)` runs on the peer certificate before any byte of the request is sent."""

    def __init__(self, host, port, context, timeout, tls_name, check):
        super().__init__(host, port, timeout=timeout, context=context)
        self.tls_name, self.check = tls_name, check

    def connect(self):
        sock = socket.create_connection(_address(self.host, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.tls_name or self.host)
            self.check(self.sock.getpeercert(binary_form=True) or b'')
        except BaseException:
            sock.close()
            raise


def _context():
    ca = ca_file()
    if SETTINGS.get('verify_tls'):
        ctx = ssl.create_default_context(cafile=ca)  # chain and host name always checked; no fingerprint pinning needed
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
        ctx.check_hostname = bool(os.getenv('OMADA_TLS_NAME', '').strip())
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the leaf certificate is pinned by its SHA-256 instead (_checker)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def fingerprint_text(fp):
    return ':'.join(fp[i:i + 2] for i in range(0, len(fp), 2)).upper() if fp else None


def _ca_mode():
    """True when the chain is checked (verify_tls in the inventory, or a CA file): no fingerprint pinning then."""
    return bool(SETTINGS.get('verify_tls')) or bool(ca_file())


def _checker(db, credentials):
    """The certificate check for one connection: with a CA file the chain was verified already; otherwise compare the
    leaf's SHA-256 with the trusted one. An unknown certificate is recorded as pending."""
    if _ca_mode():
        return lambda der: None

    def check(der):
        fp = hashlib.sha256(der).hexdigest()
        trusted = _state(db, 'cert_trusted')
        if _state(db, 'cert_seen') != fp:
            _set(db, 'cert_seen', fp)
        if fp == trusted:
            return
        if not credentials:
            return  # GET /api/info carries nothing secret
        if trusted:
            raise OmadaError(502, 'certificate_changed', 'the controller certificate changed (now ' + fingerprint_text(fp) + '); '
                             'nothing was sent. If you replaced it, trust the new fingerprint on the dashboard (Infrastructure → Omada)',
                             fingerprint=fp)
        raise OmadaError(409, 'certificate_not_trusted', 'the controller certificate (' + fingerprint_text(fp) + ') is not trusted yet; '
                         'the owner confirms it on the dashboard (Infrastructure → Omada). Nothing with credentials was sent', fingerprint=fp)
    return check


def _http(db, method, path, query=None, body=None, token=None, credentials=True, timeout=TIMEOUT):
    """(status, json) of one call to the controller."""
    parts = urllib.parse.urlsplit(base_url())
    conn = _Connection(parts.hostname, parts.port or 443, _context(), timeout, os.getenv('OMADA_TLS_NAME', '').strip() or None,
                       _checker(db, credentials))
    url = path + ('?' + urllib.parse.urlencode(query) if query else '')
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'AccessToken=' + token
    try:
        conn.request(method, url, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_BODY + 1)
        status = resp.status
    except OmadaError:
        raise
    except ssl.SSLCertVerificationError as exc:
        raise OmadaError(502, 'omada_tls_failed', 'the controller certificate is not trusted by omada-ca.pem: ' + _clean(exc.verify_message, 120)) from None
    except ssl.SSLError as exc:
        raise OmadaError(502, 'omada_tls_failed', f'TLS with the controller failed ({type(exc).__name__})') from None
    except (OSError, http.client.HTTPException) as exc:
        raise OmadaError(502, 'omada_unreachable', f'the controller ({parts.hostname}:{parts.port}) did not answer: {type(exc).__name__}') from None
    finally:
        conn.close()
    if len(raw) > MAX_BODY:
        raise OmadaError(502, 'omada_reply_too_large', 'the controller reply is larger than 4 MiB; ask for a smaller page')
    try:
        out = json.loads(raw or b'{}')
    except ValueError:
        out = {}
    return status, out if isinstance(out, dict) else {'result': out}


def _result(status, out, what):
    if status >= 400 and not isinstance(out.get('errorCode'), int):
        raise OmadaError(502, 'omada_error', f'{what}: the controller answered HTTP {status}', http_status=status)
    code = out.get('errorCode', 0)
    if code != 0:
        raise OmadaError(502 if status < 400 or status >= 500 else 400, 'omada_error',
                         f'{what}: Omada error {code}: {_clean(out.get("msg"), 200)}', omada_code=code)
    return out.get('result')


def info(db, refresh=False):
    """GET /api/info (no credentials): omadacId, controllerVer, apiVer; cached INFO_SECONDS."""
    with _lock:
        if _info['value'] and not refresh and time.monotonic() - _info['at'] < INFO_SECONDS:
            return _info['value']
    status, out = _http(db, 'GET', '/api/info', credentials=False)
    result = _result(status, out, 'controller info') or {}
    cid = str(result.get('omadacId') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,64}', cid):
        raise OmadaError(502, 'omada_error', 'the controller did not report an omadacId (is this an Omada controller with the Open API?)')
    value = {'omadacId': cid, 'controllerVer': _clean(result.get('controllerVer'), 40), 'apiVer': _clean(result.get('apiVer'), 20),
             'type': result.get('type')}
    with _lock:
        _info.update(at=time.monotonic(), value=value)
    return value


def access_token(db, renew=False):
    with _lock:
        if _token['value'] and not renew and time.monotonic() < _token['expires'] - TOKEN_MARGIN:
            return _token['value']
    cfg, cid = config(), info(db)['omadacId']
    status, out = _http(db, 'POST', '/openapi/authorize/token', {'grant_type': 'client_credentials'},
                        {'omadacId': cid, 'client_id': cfg['client_id'], 'client_secret': cfg['client_secret']})
    try:
        result = _result(status, out, 'token request') or {}
    except OmadaError as exc:
        raise OmadaError(502, 'omada_auth_failed', exc.detail + ' (check Omada/CLIENT_ID and Omada/CLIENT_SECRET in the secret store and that '
                         'the Open API app is in Client mode)', omada_code=exc.extra.get('omada_code')) from None
    token = str(result.get('accessToken') or '')
    if not token or any(c.isspace() for c in token):
        raise OmadaError(502, 'omada_auth_failed', 'the controller did not return an access token')
    try:
        lifetime = max(60, min(int(result.get('expiresIn') or 3600), 86400))
    except (TypeError, ValueError):
        lifetime = 3600
    with _lock:
        _token.update(value=token, expires=time.monotonic() + lifetime, refresh=str(result.get('refreshToken') or '') or None)
    return token


def api(db, method, path, query=None, body=None):
    """result of one Open API call below /openapi/v1/{omadacId}; the token is renewed once if it expired."""
    cid = info(db)['omadacId']
    full = f'/openapi/v1/{cid}{path}'
    for attempt in (0, 1):
        status, out = _http(db, method, full, query, body, token=access_token(db, renew=attempt == 1))
        expired = status == 401 or out.get('errorCode') in TOKEN_EXPIRED
        if expired and attempt == 0:
            continue
        return _result(status, out, method + ' ' + path)


def _pages(db, path, limit_pages=MAX_PAGES):
    """Items of a paged list (result.data) and whether more were left."""
    items, total = [], None
    for page in range(1, limit_pages + 1):
        result = api(db, 'GET', path, {'page': page, 'pageSize': PAGE_SIZE}) or {}
        data = result.get('data') if isinstance(result, dict) else result
        data = data if isinstance(data, list) else []
        items.extend(data)
        try:
            total = int(result.get('totalRows')) if isinstance(result, dict) and result.get('totalRows') is not None else None
        except (TypeError, ValueError):
            total = None
        if len(data) < PAGE_SIZE or (total is not None and len(items) >= total):
            return items, False
    return items, True


# ── operations ───────────────────────────────────────────────────────────────────────────────────────────────────────

def certificate(db):
    if _ca_mode():
        return {'mode': 'ca', 'state': 'ca', 'trusted': None, 'seen': None}
    trusted, seen = _state(db, 'cert_trusted'), _state(db, 'cert_seen')
    state = 'unknown' if not seen and not trusted else 'trusted' if seen in (None, trusted) and trusted else 'changed' if trusted else 'pending'
    return {'mode': 'pin', 'state': state, 'trusted': fingerprint_text(trusted), 'seen': fingerprint_text(seen),
            'seen_raw': seen, 'trusted_by': _state(db, 'cert_trusted_by')}


def sites(db):
    items, more = _pages(db, '/sites', 2)
    return [{'siteId': _clean(s.get('siteId') or s.get('id'), 64), 'name': _clean(s.get('name'), 80), 'region': _clean(s.get('region'), 40),
             'scenario': _clean(s.get('scenario'), 40)} for s in items if isinstance(s, dict)], more


def site_id(db, value):
    value = str(value or '').strip()
    found, _ = sites(db)
    if not value and len(found) > 1 and SETTINGS.get('site'):
        value = SETTINGS['site']  # the inventory's default site
    if not value:
        if len(found) == 1:
            return found[0]['siteId'], found
        raise OmadaError(400, 'site_required', 'name the site (siteId or name): ' + ', '.join(s['name'] or s['siteId'] for s in found))
    for s in found:
        if value in (s['siteId'], s['name']) or value.lower() == s['name'].lower():
            return s['siteId'], found
    raise OmadaError(404, 'unknown_site', 'no site ' + _clean(value, 64) + '; sites: ' + ', '.join(s['name'] or s['siteId'] for s in found))


def _pick(item, keys):
    picked = {k: item[k] for k in keys if k in item}
    return picked or item


def _norm_mac(value):
    return re.sub(r'[^0-9a-f]', '', str(value or '').lower())


def status(db, refresh=False):
    """What the dashboard and an agent see about the connection; never raises."""
    out = {'service': NAME, 'base_url': None, 'configured': False, 'connected': False, 'controller': None, 'sites': None,
           'certificate': None, 'error': None, 'detail': None}
    try:
        out['base_url'] = base_url()
        try:
            cfg = config()
            out['configured'], out['app'] = True, cfg['app']
        except OmadaError as exc:
            out.update(error=exc.error, detail=exc.detail)
        out['controller'] = info(db, refresh=refresh)
        out['certificate'] = certificate(db)
        if out['configured']:
            found, more = sites(db)
            out.update(connected=True, sites=len(found), site_names=[s['name'] for s in found][:20], more_sites=more)
    except OmadaError as exc:
        out.update(error=exc.error, detail=exc.detail)
    if out['certificate'] is None:
        out['certificate'] = certificate(db)
    out['certificate'].pop('seen_raw', None)
    _set(db, 'last_error', json.dumps({'error': out['error'], 'detail': out['detail'], 'at': int(time.time())}) if out['error'] else '')
    return out


def check(db, op, body, level, admin):
    """Validate one request at this level -> req {op, need, approval, text?, ...}; OmadaError otherwise."""
    if op in ADMIN_ONLY and not admin:
        raise OmadaError(403, 'policy_denied', op + ' is for the dashboard')
    if op not in OPERATIONS and op not in ADMIN_ONLY:
        raise OmadaError(400, 'invalid_operation', 'operation is one of ' + ', '.join(OPERATIONS))
    need = OPERATIONS.get(op, 6)
    if not admin and level < need:
        raise OmadaError(403, 'policy_denied', f'{op} needs level {need} on {NAME}; this agent has {level}', level=level)
    req = {'op': op, 'need': need, 'request': {'operation': op}}
    if op == 'status':
        req['refresh'] = admin and body.get('refresh') is True
    if op in ('clients', 'devices'):
        req['site'] = _clean(body.get('site'), 64).strip()
        for key in ('mac', 'ip', 'name'):
            value = body.get(key)
            if value not in (None, ''):
                if not isinstance(value, str) or len(value) > 64:
                    raise OmadaError(400, 'invalid_filter', key + ' is text of at most 64 characters')
                req[key] = value.strip()
        if req.get('mac') and not _MAC.match(req['mac']):
            raise OmadaError(400, 'invalid_filter', 'mac is like AA:BB:CC:DD:EE:FF')
    if op in ('get', 'write'):
        path = body.get('path')
        if not isinstance(path, str) or not _PATH.match(path) or '..' in path.split('/') or '//' in path:
            raise OmadaError(400, 'invalid_path', 'path is below /openapi/v1/{omadacId}, e.g. /sites or /sites/<siteId>/clients '
                             '(letters, digits, . _ ~ - and /)')
        req['path'] = path.rstrip('/') or '/'
        req['request']['path'] = req['path']
    if op == 'get':
        query = body.get('query') or {}
        if not isinstance(query, dict) or len(query) > 12 or not all(
                isinstance(k, str) and _QKEY.match(k) and isinstance(v, (str, int)) and not isinstance(v, bool) and len(str(v)) <= 100
                for k, v in query.items()):
            raise OmadaError(400, 'invalid_query', 'query is up to 12 simple key: value pairs (text or numbers)')
        req['query'] = {k: str(v) for k, v in sorted(query.items())}
        offset = body.get('offset', 0) or 0
        if type(offset) is not int or offset < 0:
            raise OmadaError(400, 'invalid_offset')
        req['offset'] = offset
    if op == 'write':
        method = str(body.get('method') or '').upper()
        if method not in WRITE_METHODS:
            raise OmadaError(400, 'invalid_method', 'method is one of ' + ', '.join(WRITE_METHODS))
        if not _SITE_PATH.match(req['path']):
            raise OmadaError(400, 'invalid_path', 'writes go below /sites/<siteId>/... only')
        payload = body.get('body')
        if payload is not None and not isinstance(payload, (dict, list)):
            raise OmadaError(400, 'invalid_body', 'body is a JSON object or list')
        text = json.dumps(payload, sort_keys=True, indent=1, ensure_ascii=False) if payload is not None else ''
        if len(text) > MAX_WRITE_BODY:
            raise OmadaError(400, 'body_too_large', f'the body is limited to {MAX_WRITE_BODY} characters')
        req.update(method=method, body=payload)
        req['request'].update(method=method, body=payload)
        req['approval'] = not admin and level < 6
        req['text'] = '\n'.join(['omada write on controller', 'method: ' + method, 'path: ' + req['path']]
                                + (['body:', text] if text else ['body: (none)']))
    if op == 'trust_certificate':
        fp = str(body.get('fingerprint') or '').lower().replace(':', '')
        if not _FP.match(fp):
            raise OmadaError(400, 'invalid_fingerprint', 'fingerprint is the SHA-256 shown on the dashboard')
        req['fingerprint'] = fp
    req.setdefault('approval', False)
    return req


def run(db, req, who):
    op = req['op']
    if op == 'status':
        return status(db, refresh=req.get('refresh', False))
    if op == 'trust_certificate':
        if _ca_mode():
            raise OmadaError(409, 'ca_mode', 'the certificate is checked against omada-ca.pem; there is nothing to trust here')
        try:
            info(db, refresh=True)  # see the certificate the controller presents right now
        except OmadaError:
            pass
        seen = _state(db, 'cert_seen')
        if req['fingerprint'] != seen:
            raise OmadaError(409, 'fingerprint_mismatch', 'the controller now presents ' + (fingerprint_text(seen) or 'no certificate')
                             + '; reload and compare again')
        _set(db, 'cert_trusted', seen)
        _set(db, 'cert_trusted_by', who)
        with _lock:
            _token.update(value=None, expires=0.0, refresh=None)
        return {'status': 'trusted', 'fingerprint': fingerprint_text(seen)}
    if op == 'sites':
        found, more = sites(db)
        return {'sites': found, 'more': more}
    if op in ('clients', 'devices'):
        sid, _ = site_id(db, req.get('site'))
        items, more = _pages(db, f'/sites/{sid}/{op}')
        keys = CLIENT_KEYS if op == 'clients' else DEVICE_KEYS
        mac, ip, name = _norm_mac(req.get('mac')), req.get('ip'), (req.get('name') or '').lower()
        hits = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if mac and _norm_mac(item.get('mac')) != mac:
                continue
            if ip and str(item.get('ip') or '') != ip:
                continue
            if name and name not in ' '.join(str(item.get(k) or '') for k in ('name', 'hostName')).lower():
                continue
            hits.append(_pick(item, keys))
        return {'site': sid, op: hits[:200], 'count': len(hits), 'scanned': len(items), 'more': more,
                **({'note': f'only the first {len(items)} were searched'} if more else {})}
    if op == 'get':
        result = api(db, 'GET', req['path'], req['query'] or None)
        text = json.dumps(result, indent=1, ensure_ascii=False)
        part = text[req['offset']:req['offset'] + PAGE]
        return {'path': req['path'], 'content': part, 'offset': req['offset'], 'length': len(text),
                'eof': req['offset'] + PAGE >= len(text)}
    if op == 'write':
        result = api(db, req['method'], req['path'], body=req['body'])
        text = json.dumps(result, indent=1, ensure_ascii=False)
        return {'status': 'done', 'method': req['method'], 'path': req['path'], 'result': text[:PAGE], 'truncated': len(text) > PAGE}
    raise ValueError(op)


def summary(req, result=None, error=None):
    op = req['op']
    if op in ('get', 'write'):
        text = (req.get('method') or 'GET') + ' ' + req['path']
        if op == 'get' and req.get('query'):
            text += '?' + urllib.parse.urlencode(req['query'])
        if op == 'write' and req.get('body') is not None:
            text += ' ' + json.dumps(req['body'], sort_keys=True)[:1500]
    elif op in ('clients', 'devices'):
        text = op + ' ' + (req.get('site') or '(only site)') + ''.join(f' {k}={req[k]}' for k in ('mac', 'ip', 'name') if req.get(k))
    elif op == 'trust_certificate':
        text = 'trust certificate ' + fingerprint_text(req['fingerprint'])
    else:
        text = op
    if error:
        text += ' -> ' + error
    elif isinstance(result, dict) and 'count' in result:
        text += f" -> {result['count']} found"
    return text[:2000]


def digest(text):
    return hashlib.sha256((NAME + '\n' + text).encode()).hexdigest()  # the same as cli.digest


def _earned(db, agent):
    """min(agent level, service level), without unhinged mode (it does not apply to the controller)."""
    row = db.execute('SELECT level FROM agent_policy WHERE agent_id=?', (str(agent or ''),)).fetchone()
    svc = db.execute('SELECT level FROM server_policy WHERE server=?', (NAME,)).fetchone()
    return min(row[0] if row else 0, svc[0] if svc else 0)


def handle_post(db, body, admin, task, audit):
    """POST /v1/omada -> (status, payload). Agents act at min(agent, omada:controller) level; a write below level 6 answers
    403 approval_required with the exact text until the owner approved it (POST /v1/approvals {server: omada:controller,
    command: text}); the approval is used once. The admin token is the owner (dashboard)."""
    op = body.get('operation')
    if admin:
        who = body.get('actor')
        if not isinstance(who, str) or not who.strip() or len(who) > 128:
            return 400, {'error': 'actor_required'}
        who, agent, level = who.strip(), None, 6
    else:
        agent = body.get('agent_id')
        who, level = 'agent:' + str(agent), _earned(db, agent)
    shown = str(op)[:40]
    try:
        req = check(db, op, body, level, admin)
    except OmadaError as exc:
        if exc.status == 403:
            audit(NAME, 'omada_denied', digest(shown), actor=who, agent=agent, task=task, detail=shown + ' -> ' + exc.error + ': ' + str(exc.detail)[:300])
        return exc.status, exc.payload()
    approved_by = None
    if req['approval']:
        d = digest(req['text'])
        db.execute('BEGIN IMMEDIATE')
        used = db.execute('UPDATE approvals SET used=1 WHERE digest=? AND used=0 AND expires>?', (d, int(time.time()))).rowcount
        db.execute('COMMIT')
        if used != 1:
            audit(NAME, 'omada_approval_required', d, actor=who, agent=agent, task=task, detail=summary(req))
            return 403, {'error': 'approval_required', 'service': NAME, 'approval_text': req['text'], 'digest': d[:16],
                         'request': req['request'], 'detail': 'the owner approves this exact request on the dashboard; then it runs once'}
        row = db.execute("SELECT actor FROM audit WHERE action='chat_approval' AND digest=? ORDER BY id DESC LIMIT 1", (d,)).fetchone()
        approved_by = (row[0] if row else None) or 'the owner'
    d = digest(json.dumps(req['request'], sort_keys=True))
    action = 'omada_' + op
    quiet = admin and op in ('status', 'sites')  # dashboard reads are not audited
    try:
        result = run(db, req, who)
    except OmadaError as exc:
        if not quiet:
            audit(NAME, action, d, {'exit_code': 1, 'timed_out': False, 'truncated': False}, actor=who, agent=agent, task=task,
                  detail=summary(req, error=exc.error + ': ' + str(exc.detail)[:400]))
        return exc.status, exc.payload()
    except Exception as exc:  # noqa: BLE001 - audited and answered with the type only
        audit(NAME, action, d, actor=who, agent=agent, task=task, detail=summary(req, error=type(exc).__name__))
        return 502, {'error': 'omada_failed', 'exception_type': type(exc).__name__}
    if not quiet:
        audit(NAME, action, d, {'exit_code': 0, 'timed_out': False, 'truncated': bool(result.get('more') or result.get('truncated'))},
              actor=who, agent=agent, task=task, detail=summary(req, result) + (f' (approved by {approved_by})' if approved_by else ''))
    return 200, {'service': NAME, 'operation': op, **result}
