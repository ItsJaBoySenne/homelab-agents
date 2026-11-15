"""Agent secrets (HLA_SECRETS_V1): passwords the agents create and use without ever seeing them.

Secrets live in a separate Infisical project ("Agent Generated", INFISICAL_GENERATED_PROJECT_ID) with its own machine
identity (INFISICAL_GENERATED_CLIENT_ID/_SECRET, read and write there; falls back to the gateway's main identity). The project with the servers' SSH keys is never reachable this way.

  create   the gateway generates the value (letters and digits only, so it is safe unquoted in shell, SQL, URLs
           and YAML), stores it, and returns only the reference {{secret:NAME}}.
  use      an agent writes {{secret:NAME}} in a command or in file content; the gateway puts the value in just
           before running it. Approvals and the audit log see the reference, not the value.
  redact   every value from this project is replaced by [secret:NAME] in all output the gateway returns, so a
           `cat .env` or a container inspect shows the reference again.

Levels (min(agent level, "secrets" level), unhinged mode as on servers):
  1  list names, use references (the command or file write still needs its own server permission)
  4  create new secrets
  6  (or unhinged mode) replace the value of an existing secret

Redaction prevents accidental leaks into chats, logs and model context. It cannot stop an agent with root that
deliberately transforms a value (e.g. base64) before printing it; keep this project to application passwords.
"""
import json
import os
import re
import secrets as _random
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import masking
from .core import env

NAME = 'secrets'
SECRET_NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
REF = re.compile(r'\{\{secret:([A-Za-z0-9_]{1,64})\}\}')
# The servers' credentials and the platform's own settings are never created or replaced here.
RESERVED = ('SSH_', 'INFISICAL_', 'TOOL_GATEWAY_', 'POLICY_ADMIN_', 'AGENT_PLATFORM_', 'HA_')
KINDS = {'password': (string.ascii_letters + string.digits, 32), 'token': (string.ascii_letters + string.digits, 48),
         'hex': ('0123456789abcdef', 64)}
OPERATIONS = {'list': 1, 'create': 4}
CACHE_SECONDS = 60
MIN_REDACT = masking.MIN_LENGTH  # shorter values would mangle ordinary output
TIMEOUT = 8

_lock = threading.Lock()
_cache = {'at': 0.0, 'values': {}, 'meta': {}, 'error': None}


class SecretError(Exception):
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


def configured():
    return bool(os.getenv('INFISICAL_GENERATED_PROJECT_ID', '').strip())


def _settings():
    base = env('INFISICAL_URL').rstrip('/')
    if not base.startswith('https://'): raise RuntimeError('Infisical HTTPS required')
    project = env('INFISICAL_GENERATED_PROJECT_ID')
    if project == os.getenv('INFISICAL_PROJECT_ID', '').strip():
        raise RuntimeError('INFISICAL_GENERATED_PROJECT_ID must differ from the servers project')
    environment = os.getenv('INFISICAL_GENERATED_ENV', '').strip() or os.getenv('INFISICAL_ENV', 'prod')
    path = os.getenv('INFISICAL_GENERATED_PATH', '/').strip() or '/'
    return base, project, environment, path


def _call(method, base, path, payload=None, token=None):
    """One Infisical API call; returns (status, json body or {})."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try: body = json.loads(exc.read() or b'{}')
        except ValueError: body = {}
        return exc.code, body if isinstance(body, dict) else {}


def _identity():
    """The Agent Generated identity when both its settings are set, else the gateway's main identity."""
    cid = os.getenv('INFISICAL_GENERATED_CLIENT_ID', '').strip()
    secret = os.getenv('INFISICAL_GENERATED_CLIENT_SECRET', '').strip()
    if cid or secret:
        if not (cid and secret):
            raise RuntimeError('set both INFISICAL_GENERATED_CLIENT_ID and INFISICAL_GENERATED_CLIENT_SECRET')
        return cid, secret, 'Agent Generated identity'
    return env('INFISICAL_CLIENT_ID'), env('INFISICAL_CLIENT_SECRET'), 'gateway identity'


def _message(body):
    return str((body or {}).get('message') or '')[:200]


def _login(base):
    cid, secret, who = _identity()
    status, body = _call('POST', base, '/api/v1/auth/universal-auth/login', {'clientId': cid, 'clientSecret': secret})
    if status != 200 or not body.get('accessToken'):
        raise RuntimeError('Infisical login failed for the %s (HTTP %s) %s' % (who, status, _message(body)))
    return body['accessToken']


def _fetch():
    base, project, environment, path = _settings()
    token = _login(base)
    query = urllib.parse.urlencode({'projectId': project, 'environment': environment, 'secretPath': path})
    status, body = _call('GET', base, '/api/v4/secrets?' + query, token=token)
    if status != 200:
        raise RuntimeError('Infisical refused listing the Agent Generated project (HTTP %s): %s' % (status, _message(body) or 'the identity needs read access'))
    values, meta = {}, {}
    for item in body.get('secrets') or []:
        key = item.get('secretKey')
        if isinstance(key, str):
            values[key] = item.get('secretValue') or ''
            meta[key] = {'name': key, 'comment': (item.get('secretComment') or '')[:200],
                         'updated': item.get('updatedAt') or item.get('createdAt')}
    return values, meta


def load(force=False):
    """{name: value} of the Agent Generated project, cached for CACHE_SECONDS (failures too, so a down Infisical
    does not slow every command). Raises RuntimeError only when force is set and Infisical cannot be read."""
    if not configured(): return {}
    with _lock:
        fresh = time.monotonic() - _cache['at'] < CACHE_SECONDS
        if fresh and not force:
            return dict(_cache['values'])
        try:
            values, meta = _fetch()
            _cache.update(at=time.monotonic(), values=values, meta=meta, error=None)
        except Exception as exc:  # noqa: BLE001 - kept for the next caller, raised when needed
            _cache.update(at=time.monotonic(), error=str(exc)[:200])
            if force: raise RuntimeError(_cache['error']) from None
        return dict(_cache['values'])


def references(text):
    return sorted(set(REF.findall(text))) if isinstance(text, str) else []


def substitute(text):
    """(text with values, {name: value}) for every {{secret:NAME}}; SecretError when one does not exist."""
    names = references(text)
    if not names: return text, {}
    if not configured():
        raise SecretError(503, 'secrets_not_configured', 'INFISICAL_GENERATED_PROJECT_ID is not set on the gateway')
    values = load()
    if any(n not in values for n in names):
        try: values = load(force=True)
        except RuntimeError as exc: raise SecretError(502, 'secrets_unavailable', str(exc)) from None
    missing = [n for n in names if n not in values]
    if missing:
        raise SecretError(404, 'unknown_secret', 'No secret named ' + ', '.join(missing) + ' (list shows the names)')
    used = {n: values[n] for n in names}
    return REF.sub(lambda m: used[m.group(1)], text), used


def redact(obj, extra=None):
    """Replace every secret value in strings and keys (recursively, also in encoded forms) with [secret:NAME]."""
    values = {**(load() if configured() else {}), **(extra or {})}
    return masking.mask(obj, masking.prepare([(v, '[secret:' + n + ']') for n, v in values.items()]))


def check(op, body, level, free):
    """Validate a /v1/secrets request at this level: the parsed request or SecretError."""
    if op not in OPERATIONS: raise SecretError(400, 'invalid_operation', 'operation is list or create')
    if level < 1: raise SecretError(403, 'policy_denied')
    if not configured():
        raise SecretError(503, 'secrets_not_configured', 'INFISICAL_GENERATED_PROJECT_ID is not set on the gateway')
    if op == 'list': return {'op': op, 'need': 1}
    name, kind = body.get('name'), body.get('kind') or 'password'
    if not isinstance(name, str) or not SECRET_NAME.match(name):
        raise SecretError(400, 'invalid_name', 'names are 3-64 characters: A-Z, 0-9 and _, starting with a letter')
    if name.startswith(RESERVED): raise SecretError(400, 'reserved_name', 'names starting with ' + ', '.join(RESERVED) + ' are reserved')
    if kind not in KINDS: raise SecretError(400, 'invalid_kind', 'kind is password, token or hex')
    replace = body.get('replace', False) is True
    if level < 4 and not free: raise SecretError(403, 'policy_denied', 'creating secrets needs level 4')
    if replace and level < 6 and not free: raise SecretError(403, 'policy_denied', 'replacing a secret needs level 6 or unhinged mode')
    note = body.get('description') or ''
    if not isinstance(note, str): raise SecretError(400, 'invalid_description')
    note = re.sub(r'[\x00-\x1f\x7f]', ' ', note)[:200]
    return {'op': op, 'need': 6 if replace else 4, 'name': name, 'kind': kind, 'replace': replace, 'description': note}


def run(req, actor):
    if req['op'] == 'list':
        try: load(force=True)
        except RuntimeError as exc: raise SecretError(502, 'secrets_unavailable', str(exc)) from None
        with _lock: meta = sorted(_cache['meta'].values(), key=lambda m: m['name'])
        return {'secrets': [{**m, 'reference': '{{secret:' + m['name'] + '}}'} for m in meta]}
    name = req['name']
    try: exists = name in load(force=True)
    except RuntimeError as exc: raise SecretError(502, 'secrets_unavailable', str(exc)) from None
    if exists and not req['replace']:
        raise SecretError(409, 'secret_exists', name + ' already exists: use {{secret:' + name + '}}, or replace it (level 6)')
    if req['replace'] and not exists: raise SecretError(404, 'unknown_secret', 'No secret named ' + name + ' to replace')
    alphabet, length = KINDS[req['kind']]
    value = ''.join(_random.choice(alphabet) for _ in range(length))
    base, project, environment, path = _settings()
    comment = ('Created by ' if not exists else 'Replaced by ') + str(actor)[:80] + ' via the Homelab Agents gateway' \
        + (': ' + req['description'] if req['description'] else '')
    payload = {'projectId': project, 'environment': environment, 'secretPath': path, 'secretValue': value,
               'secretComment': comment}
    try:
        token = _login(base)
        if exists: status, _ = _call('PATCH', base, '/api/v4/secrets/' + name, payload, token)
        else: status, _ = _call('POST', base, '/api/v4/secrets/' + name, {**payload, 'type': 'shared'}, token)
    except (RuntimeError, OSError) as exc:
        raise SecretError(502, 'secrets_unavailable', str(exc)[:200]) from None
    if status not in (200, 201):
        raise SecretError(502, 'secret_write_refused', 'Infisical answered HTTP %s: the gateway identity needs write access '
                          'on the Agent Generated project' % status)
    with _lock:
        _cache['values'][name] = value
        _cache['meta'][name] = {'name': name, 'comment': comment[:200], 'updated': None}
    value = None
    return {'name': name, 'kind': req['kind'], 'status': 'replaced' if exists else 'created',
            'reference': '{{secret:' + name + '}}', 'value_returned': False}


def store(name, value, actor, description='', replace=False):
    """HLA_PROXMOX_V1: store a value the gateway obtained itself (e.g. an API token Proxmox generated) under NAME in the
    Agent Generated project, so agents can use {{secret:NAME}} without seeing it. Same reply shape as run(); the value is
    never returned. An existing name is only overwritten with replace=True."""
    if not configured():
        raise SecretError(503, 'secrets_not_configured', 'INFISICAL_GENERATED_PROJECT_ID is not set on the gateway')
    if not isinstance(name, str) or not SECRET_NAME.match(name) or name.startswith(RESERVED):
        raise SecretError(400, 'invalid_name', 'not a valid secret name')
    if not isinstance(value, str) or not value:
        raise SecretError(400, 'invalid_value')
    try: exists = name in load(force=True)
    except RuntimeError as exc: raise SecretError(502, 'secrets_unavailable', str(exc)) from None
    if exists and not replace:
        raise SecretError(409, 'secret_exists', name + ' already exists in the Agent Generated project')
    base, project, environment, path = _settings()
    note = re.sub(r'[\x00-\x1f\x7f]', ' ', str(description or ''))[:200]
    comment = ('Stored by ' if not exists else 'Replaced by ') + str(actor)[:80] + ' via the Homelab Agents gateway' + (': ' + note if note else '')
    payload = {'projectId': project, 'environment': environment, 'secretPath': path, 'secretValue': value, 'secretComment': comment}
    try:
        token = _login(base)
        if exists: status, _ = _call('PATCH', base, '/api/v4/secrets/' + name, payload, token)
        else: status, _ = _call('POST', base, '/api/v4/secrets/' + name, {**payload, 'type': 'shared'}, token)
    except (RuntimeError, OSError) as exc:
        raise SecretError(502, 'secrets_unavailable', str(exc)[:200]) from None
    if status not in (200, 201):
        raise SecretError(502, 'secret_write_refused', 'Infisical answered HTTP %s: the identity needs write access on the '
                          'Agent Generated project' % status)
    with _lock:
        _cache['values'][name] = value
        _cache['meta'][name] = {'name': name, 'comment': comment[:200], 'updated': None}
    return {'name': name, 'status': 'replaced' if exists else 'created', 'reference': '{{secret:' + name + '}}', 'value_returned': False}
