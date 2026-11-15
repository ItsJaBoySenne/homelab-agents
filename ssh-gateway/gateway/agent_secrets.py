"""Agent secrets (HLA_SECRETS_V1): passwords the agents create and use without ever seeing them.

Secrets live in the "generated" namespace of the gateway's secret store (gateway/secret_store.py): the local encrypted
file (HLA_SECRETS_BACKEND=local, the default) or the separate "Agent Generated" Infisical project
(HLA_SECRETS_BACKEND=infisical, INFISICAL_GENERATED_PROJECT_ID). The platform namespace with the servers' SSH keys and
service credentials is never reachable this way: every read and write here names the generated namespace.

  create   the gateway generates the value (letters and digits only, so it is safe unquoted in shell, SQL, URLs
           and YAML), stores it, and returns only the reference {{secret:NAME}}.
  use      an agent writes {{secret:NAME}} in a command or in file content; the gateway puts the value in just
           before running it. Approvals and the audit log see the reference, not the value.
  redact   every value from this namespace is replaced by [secret:NAME] in all output the gateway returns, so a
           `cat .env` or a container inspect shows the reference again.

Levels (min(agent level, "secrets" level), unhinged mode as on servers):
  1  list names, use references (the command or file write still needs its own server permission)
  4  create new secrets
  6  (or unhinged mode) replace the value of an existing secret

Redaction prevents accidental leaks into chats, logs and model context. It cannot stop an agent with root that
deliberately transforms a value (e.g. base64) before printing it; keep this namespace to application passwords.
"""
import os
import re
import secrets as _random
import string
import threading
import time

from . import masking
from . import secret_store
from .secret_store import GENERATED, SecretStoreError

NAME = 'secrets'
SECRET_NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
REF = re.compile(r'\{\{secret:([A-Za-z0-9_]{1,64})\}\}')
# The servers' credentials and the platform's own settings are never created or replaced here.
RESERVED = ('SSH_', 'INFISICAL_', 'TOOL_GATEWAY_', 'POLICY_ADMIN_', 'AGENT_PLATFORM_', 'HA_', 'HLA_SECRETS_', 'GATEWAY_CREDENTIAL_')
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
    try:
        return bool(secret_store.get().configured(GENERATED))
    except SecretStoreError:
        return False


def not_configured_detail():
    """What to set so agent secrets work, for the 503 secrets_not_configured replies."""
    try:
        backend = secret_store.backend_name()
    except SecretStoreError as exc:
        return str(exc)
    if backend == 'infisical':
        return 'INFISICAL_GENERATED_PROJECT_ID is not set on the gateway'
    return 'HLA_SECRETS_KEY is not set (or not valid) on the gateway'


def _fetch():
    try:
        return secret_store.get().generated()
    except SecretStoreError as exc:
        raise RuntimeError(str(exc)) from None


def load(force=False):
    """{name: value} of the generated namespace, cached for CACHE_SECONDS (failures too, so a down store does not
    slow every command). Raises RuntimeError only when force is set and the store cannot be read."""
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
        raise SecretError(503, 'secrets_not_configured', not_configured_detail())
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
        raise SecretError(503, 'secrets_not_configured', not_configured_detail())
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
    comment = ('Created by ' if not exists else 'Replaced by ') + str(actor)[:80] + ' via the Homelab Agents gateway' \
        + (': ' + req['description'] if req['description'] else '')
    _write(name, value, comment, exists)
    value = None
    return {'name': name, 'kind': req['kind'], 'status': 'replaced' if exists else 'created',
            'reference': '{{secret:' + name + '}}', 'value_returned': False}


def _write(name, value, comment, exists=None):
    """Put one value into the generated namespace (never anywhere else) and into the cache."""
    try:
        secret_store.get().put(GENERATED, name, value, comment, exists=exists)
    except SecretStoreError as exc:
        if exc.code == 'refused':
            raise SecretError(502, 'secret_write_refused', str(exc)[:200]) from None
        raise SecretError(502, 'secrets_unavailable', str(exc)[:200]) from None
    except OSError as exc:
        raise SecretError(502, 'secrets_unavailable', type(exc).__name__) from None
    with _lock:
        _cache['values'][name] = value
        _cache['meta'][name] = {'name': name, 'comment': comment[:200], 'updated': None}


def store(name, value, actor, description='', replace=False):
    """HLA_PROXMOX_V1: store a value the gateway obtained itself (e.g. an API token Proxmox generated) under NAME in the
    generated namespace, so agents can use {{secret:NAME}} without seeing it. Same reply shape as run(); the value is
    never returned. An existing name is only overwritten with replace=True."""
    if not configured():
        raise SecretError(503, 'secrets_not_configured', not_configured_detail())
    if not isinstance(name, str) or not SECRET_NAME.match(name) or name.startswith(RESERVED):
        raise SecretError(400, 'invalid_name', 'not a valid secret name')
    if not isinstance(value, str) or not value:
        raise SecretError(400, 'invalid_value')
    try: exists = name in load(force=True)
    except RuntimeError as exc: raise SecretError(502, 'secrets_unavailable', str(exc)) from None
    if exists and not replace:
        raise SecretError(409, 'secret_exists', name + ' already exists in the generated secrets')
    note = re.sub(r'[\x00-\x1f\x7f]', ' ', str(description or ''))[:200]
    comment = ('Stored by ' if not exists else 'Replaced by ') + str(actor)[:80] + ' via the Homelab Agents gateway' + (': ' + note if note else '')
    _write(name, value, comment, exists)
    return {'name': name, 'status': 'replaced' if exists else 'created', 'reference': '{{secret:' + name + '}}', 'value_returned': False}
