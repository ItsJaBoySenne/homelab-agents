"""Credential store (HLA_CREDENTIALS_V1): tokens and keys for connections (GitHub, MCP servers, HTTP tools),
entered on the dashboard and kept encrypted in the gateway's own database.

  - Write-only from the outside: the API lists names and metadata, never values. A value can be replaced or
    deleted, not read back. Only gateway code (a connection making its call) decrypts a value.
  - AES-256-GCM with GATEWAY_CREDENTIAL_KEY (32 random bytes, base64; e.g. `openssl rand -base64 32`), which lives
    only in .env.gateway. Without the key the store is off (`credentials_not_configured`); losing the key makes
    stored values unreadable (enter them again), it never exposes them.
  - Every value is masked in all gateway replies and audit rows ([credential:NAME]), like the Agent Generated secrets.
  - Agents cannot reference these values: {{secret:NAME}} only reaches the Agent Generated project.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets as _random
import threading
import time

from . import masking

NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
KINDS = ('token', 'password', 'api_key', 'header', 'other')
MAX_VALUE = 16384
MIN_REDACT = masking.MIN_LENGTH
_cache = {'at': 0.0, 'values': {}}
_lock = threading.Lock()
CACHE_SECONDS = 30


class CredentialError(Exception):
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS credentials (
        name TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'token', description TEXT NOT NULL DEFAULT '',
        nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, fingerprint TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '')''')


def _key():
    raw = os.getenv('GATEWAY_CREDENTIAL_KEY', '').strip()
    if not raw:
        raise CredentialError(503, 'credentials_not_configured',
                              'set GATEWAY_CREDENTIAL_KEY in .env.gateway (openssl rand -base64 32) and restart the gateway')
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        raise CredentialError(503, 'credentials_not_configured', 'GATEWAY_CREDENTIAL_KEY is not valid base64') from None
    if len(key) != 32:
        raise CredentialError(503, 'credentials_not_configured', 'GATEWAY_CREDENTIAL_KEY must be 32 bytes (base64)')
    return key


def configured():
    try:
        _key()
        return True
    except CredentialError:
        return False


def _aes():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # shipped with paramiko
    return AESGCM(_key())


def _fingerprint(value):
    # Lets the dashboard show that a value changed without revealing anything usable about it: an HMAC with a subkey
    # of GATEWAY_CREDENTIAL_KEY, so a guessed value cannot be confirmed from the fingerprint without the key.
    subkey = hmac.new(_key(), b'fingerprint', hashlib.sha256).digest()
    return hmac.new(subkey, value.encode(), hashlib.sha256).hexdigest()[:10]


def _refresh_fingerprints(db, rows):
    """Rows written before keyed fingerprints get theirs recomputed when listed (unreadable rows stay as they are)."""
    out = []
    for row in rows:
        row = list(row)
        try:
            value = get(db, row[0]) if configured() else None
            fresh = _fingerprint(value) if value else row[3]
        except CredentialError:
            fresh = row[3]
        if fresh != row[3]:
            db.execute('UPDATE credentials SET fingerprint=? WHERE name=?', (fresh, row[0]))
            db.commit()
            row[3] = fresh
        out.append(row)
    return out


def put(db, name, value, *, kind='token', description='', actor=''):
    migrate(db)
    if not isinstance(name, str) or not NAME.match(name):
        raise CredentialError(400, 'invalid_name', 'use UPPER_CASE letters, digits and _ (3-64), e.g. GITHUB_TOKEN')
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE or '\x00' in value:
        raise CredentialError(400, 'invalid_value', 'the value is 1-16384 characters of text')
    if kind not in KINDS:
        raise CredentialError(400, 'invalid_kind', 'kind is one of ' + ', '.join(KINDS))
    description = str(description or '')[:300]
    value = value.strip()
    nonce = _random.token_bytes(12)
    cipher = _aes().encrypt(nonce, value.encode(), name.encode())  # the name is bound as associated data
    now = time.time()
    db.execute('''INSERT INTO credentials(name,kind,description,nonce,ciphertext,fingerprint,created_at,updated_at,updated_by)
        VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,description=excluded.description,
        nonce=excluded.nonce,ciphertext=excluded.ciphertext,fingerprint=excluded.fingerprint,updated_at=excluded.updated_at,
        updated_by=excluded.updated_by''', (name, kind, description, nonce, cipher, _fingerprint(value), now, now, str(actor)[:120]))
    db.commit()
    _cache['at'] = 0.0
    return meta(db, name)


def delete(db, name):
    migrate(db)
    gone = db.execute('DELETE FROM credentials WHERE name=?', (name,)).rowcount
    db.commit()
    _cache['at'] = 0.0
    return bool(gone)


def _row_meta(row):
    return {'name': row[0], 'kind': row[1], 'description': row[2], 'fingerprint': row[3],
            'created_at': row[4], 'updated_at': row[5], 'updated_by': row[6]}


def meta(db, name):
    migrate(db)
    row = db.execute('SELECT name,kind,description,fingerprint,created_at,updated_at,updated_by FROM credentials WHERE name=?',
                     (name,)).fetchone()
    return _row_meta(_refresh_fingerprints(db, [row])[0]) if row else None


def listing(db):
    """Names and metadata only; never values."""
    migrate(db)
    rows = db.execute('SELECT name,kind,description,fingerprint,created_at,updated_at,updated_by FROM credentials ORDER BY name').fetchall()
    return {'configured': configured(), 'credentials': [_row_meta(r) for r in _refresh_fingerprints(db, rows)]}


def get(db, name):
    """The decrypted value (gateway-internal use only), or None when it is not stored."""
    migrate(db)
    row = db.execute('SELECT nonce,ciphertext FROM credentials WHERE name=?', (name,)).fetchone()
    if not row:
        return None
    try:
        return _aes().decrypt(row[0], row[1], name.encode()).decode()
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(500, 'credential_unreadable',
                              name + ' cannot be decrypted with the current GATEWAY_CREDENTIAL_KEY; enter it again') from None


def values(db_open):
    """{name: value} of every readable credential, cached briefly; for masking. Never raises."""
    with _lock:
        if time.time() - _cache['at'] < CACHE_SECONDS:
            return dict(_cache['values'])
        out = {}
        try:
            if configured():
                with db_open() as db:
                    migrate(db)
                    for (name,) in db.execute('SELECT name FROM credentials').fetchall():
                        try:
                            value = get(db, name)
                        except CredentialError:
                            continue
                        if value:
                            out[name] = value
        except Exception:
            pass
        _cache.update(at=time.time(), values=out)
        return dict(out)


def redact(payload, known):
    """Replace every credential value in a JSON-like payload (strings and keys, also in encoded forms) with
    [credential:NAME] (gateway/masking.py)."""
    return masking.mask(payload, prepared(known))


def prepared(known):
    """known {name: value} -> the compiled masking list for masking.mask / masking.mask_text."""
    return masking.prepare([(v, '[credential:' + n + ']') for n, v in (known or {}).items()])
