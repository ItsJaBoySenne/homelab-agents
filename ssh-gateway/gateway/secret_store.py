"""The gateway's secret store (HLA_SECRET_STORE_V1): one interface, two backends.

HLA_SECRETS_BACKEND=local (default) or infisical. Every module that needs a secret goes through get(); none talks to
Infisical or to the encrypted file directly.

Two namespaces, as before:
  platform   what the owner puts in: servers' SSH keys (servers/<name>/SSH_PRIVATE_KEY, SSH_HOST_FINGERPRINT,
             SSH_KEY_PASSPHRASE) and service credentials (Proxmox/<node>/TOKEN_ID, TOKEN_SECRET; Omada/CLIENT_ID,
             CLIENT_SECRET, APPLICATION_NAME; HomeAssistant/HA_TOKEN; GitHub/GITHUB_TOKEN). Names are FOLDER/KEY; the
             folders are the ones in core.SERVERS / core.SERVICES. Agents can never read or write this namespace: the only
             agent path (gateway/agent_secrets.py) is bound to the generated namespace.
  generated  passwords the agents create and use as {{secret:NAME}} without seeing them (gateway/agent_secrets.py).
             Flat names, A-Z 0-9 _; the reserved prefixes there still apply.

local
  ${HLA_SECRETS_DIR:-/data/secrets}/secrets.enc, one Fernet token (AES-128-CBC + HMAC-SHA256) over a JSON document with
  both namespaces, keyed by HLA_SECRETS_KEY (32 random bytes, urlsafe base64:
  python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())").
  Writes take an exclusive flock on secrets.lock, re-read the file, write a temporary file (0600, fsync) and rename it
  over secrets.enc (then fsync the folder), so a reader sees the old or the new file, never half of one, and two writers
  never lose each other's change. A file that does not decrypt is never overwritten. Losing the key loses the secrets.
infisical
  The servers project (INFISICAL_URL, INFISICAL_CLIENT_ID/_SECRET, INFISICAL_PROJECT_ID, INFISICAL_ENV) holds the platform
  folders; the separate Agent Generated project (INFISICAL_GENERATED_PROJECT_ID, optional own identity
  INFISICAL_GENERATED_CLIENT_ID/_SECRET, INFISICAL_GENERATED_ENV, INFISICAL_GENERATED_PATH) the generated secrets.

Nothing here ever logs, prints or returns a value except to the gateway code that asked for it.
"""
import base64
import binascii
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BACKENDS = ('local', 'infisical')
PLATFORM, GENERATED = 'platform', 'generated'
NAMESPACES = (PLATFORM, GENERATED)
FILE_NAME = 'secrets.enc'
LOCK_NAME = 'secrets.lock'
MAGIC = b'hla-secrets:1:'
KEY_HELP = ('python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"')
_KEY_PART = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
_FOLDER_PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
GENERATED_NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
MAX_VALUE = 65536
TIMEOUT = 8


class SecretStoreError(RuntimeError):
    """code: not_configured | wrong_key | not_found | unavailable | invalid | refused. The message never holds a value."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def backend_name():
    name = (os.getenv('HLA_SECRETS_BACKEND', '').strip() or 'local').lower()
    if name not in BACKENDS:
        raise SecretStoreError('not_configured', f'HLA_SECRETS_BACKEND must be local or infisical, not {name!r}')
    return name


def split_platform(name):
    """'servers/dev/SSH_PRIVATE_KEY' -> ('/servers/dev', 'SSH_PRIVATE_KEY'); SecretStoreError when malformed."""
    if not isinstance(name, str):
        raise SecretStoreError('invalid', 'a platform secret name is FOLDER/KEY, e.g. servers/web/SSH_PRIVATE_KEY')
    parts = name.strip().strip('/').split('/')
    key, folders = parts[-1], parts[:-1]
    if not _KEY_PART.match(key) or len(folders) > 4 or not all(_FOLDER_PART.match(f) for f in folders):
        raise SecretStoreError('invalid', 'a platform secret name is FOLDER/KEY (folders: letters, digits, . _ -; KEY: '
                                          'A-Z, 0-9 and _), e.g. servers/web/SSH_PRIVATE_KEY or GitHub/GITHUB_TOKEN')
    return '/' + '/'.join(folders) if folders else '/', key


def join_platform(folder, key):
    folder = (folder or '/').strip('/')
    return (folder + '/' if folder else '') + key


def check_name(namespace, name):
    if namespace == PLATFORM:
        folder, key = split_platform(name)
        return join_platform(folder, key)
    if namespace == GENERATED:
        if not isinstance(name, str) or not GENERATED_NAME.match(name):
            raise SecretStoreError('invalid', 'generated secret names are 3-64 characters: A-Z, 0-9 and _, starting with a letter')
        return name
    raise SecretStoreError('invalid', 'namespace is platform or generated')


def _check_value(value):
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE or '\x00' in value:
        raise SecretStoreError('invalid', f'a value is 1-{MAX_VALUE} characters of text')


# ── local: one encrypted file ────────────────────────────────────────────────────────────────────────────────────────

def parse_key(raw):
    """The Fernet key (bytes) for HLA_SECRETS_KEY, accepting urlsafe or standard base64 with or without padding."""
    raw = (raw or '').strip()
    if not raw:
        raise SecretStoreError('not_configured', 'HLA_SECRETS_KEY is not set: generate one with ' + KEY_HELP)
    text = raw.replace('+', '-').replace('/', '_')
    try:
        key = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise SecretStoreError('not_configured', 'HLA_SECRETS_KEY is not valid base64: generate one with ' + KEY_HELP) from None
    if len(key) != 32:
        raise SecretStoreError('not_configured', f'HLA_SECRETS_KEY must be 32 random bytes in base64 (it is {len(key)}): '
                                                 'generate one with ' + KEY_HELP)
    return base64.urlsafe_b64encode(key)


class LocalStore:
    backend = 'local'

    def __init__(self, directory=None, key=None):
        self.directory = directory or os.getenv('HLA_SECRETS_DIR', '').strip() or '/data/secrets'
        self._raw_key = key if key is not None else os.getenv('HLA_SECRETS_KEY', '')
        self._mutex = threading.Lock()
        self._cache = (None, None)  # (file signature, document)

    @property
    def file(self):
        return os.path.join(self.directory, FILE_NAME)

    def _fernet(self):
        from cryptography.fernet import Fernet
        return Fernet(parse_key(self._raw_key))

    def configured(self, namespace=None):
        try:
            parse_key(self._raw_key)
            return True
        except SecretStoreError:
            return False

    def describe(self):
        return {'backend': self.backend, 'configured': self.configured(), 'location': self.file}

    # file handling
    def _signature(self):
        try:
            st = os.stat(self.file)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _read(self):
        """The decrypted document ({platform: {...}, generated: {...}}); cached while the file is unchanged."""
        fernet = self._fernet()
        with self._mutex:
            sig = self._signature()
            if sig is not None and self._cache[0] == sig:
                return self._cache[1]
            if sig is None:
                doc = {'version': 1, PLATFORM: {}, GENERATED: {}}
            else:
                try:
                    with open(self.file, 'rb') as fh:
                        blob = fh.read()
                except PermissionError:
                    raise SecretStoreError('unavailable', f'{self.file} is not readable by the gateway user') from None
                if not blob.startswith(MAGIC):
                    raise SecretStoreError('wrong_key', f'{self.file} is not a Homelab Agents secret file (or it is damaged)')
                from cryptography.fernet import InvalidToken
                try:
                    plain = fernet.decrypt(blob[len(MAGIC):].strip())
                except InvalidToken:
                    raise SecretStoreError('wrong_key', f'{self.file} cannot be decrypted with HLA_SECRETS_KEY: the key is '
                                                        'not the one the file was written with (or the file is damaged). '
                                                        'Nothing was changed.') from None
                try:
                    doc = json.loads(plain)
                except ValueError:
                    raise SecretStoreError('wrong_key', f'{self.file} decrypted to something that is not a secret document') from None
                finally:
                    plain = None
                for ns in NAMESPACES:
                    doc.setdefault(ns, {})
            self._cache = (sig, doc)
            return doc

    def _write(self, doc):
        """Atomic replace: temp file in the same folder (0600), fsync, rename, fsync the folder."""
        blob = MAGIC + self._fernet().encrypt(json.dumps(doc, separators=(',', ':'), sort_keys=True).encode()) + b'\n'
        fd, tmp = tempfile.mkstemp(prefix='.secrets.', suffix='.tmp', dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.file)
            tmp = None
            dfd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        with self._mutex:
            self._cache = (None, None)

    def _modify(self, change):
        """Run change(doc) -> result under the exclusive file lock, on a freshly read document, and save it."""
        import fcntl
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise SecretStoreError('unavailable', f'cannot create {self.directory} ({exc.strerror})') from None
        try:
            lock = os.open(os.path.join(self.directory, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise SecretStoreError('unavailable', f'{self.directory} is not writable by the gateway ({exc.strerror})') from None
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self._mutex:
                self._cache = (None, None)  # always re-read under the lock
            doc = json.loads(json.dumps(self._read()))  # a private copy
            result = change(doc)
            self._write(doc)
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    # the interface
    def read_platform(self, folder, names, optional=()):
        space = self._read()[PLATFORM]
        out = {}
        for name in tuple(names) + tuple(optional):
            entry = space.get(join_platform(folder, name))
            if entry is None:
                if name in optional:
                    continue
                raise SecretStoreError('not_found', f'{join_platform(folder, name)} is not in the secret store')
            out[name] = entry['value']
        return out

    def generated(self):
        """({name: value}, {name: {name, comment, updated}}) of the generated namespace."""
        space = self._read()[GENERATED]
        return ({n: e['value'] for n, e in space.items()},
                {n: {'name': n, 'comment': e.get('comment', '')[:200], 'updated': e.get('updated')} for n, e in space.items()})

    def put(self, namespace, name, value, comment='', exists=None):
        """Store or replace; returns True when it replaced an existing value. (exists: a hint the Infisical backend uses.)"""
        name = check_name(namespace, name)
        _check_value(value)
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        def change(doc):
            existed = name in doc[namespace]
            doc[namespace][name] = {'value': value, 'comment': str(comment or '')[:300], 'updated': now}
            return existed
        return self._modify(change)

    def delete(self, namespace, name):
        name = check_name(namespace, name)
        return self._modify(lambda doc: doc[namespace].pop(name, None) is not None)

    def names(self, namespace):
        if namespace not in NAMESPACES:
            raise SecretStoreError('invalid', 'namespace is platform or generated')
        return sorted(self._read()[namespace])


# ── infisical ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _env(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise SecretStoreError('not_configured', 'Missing environment variable ' + name)
    return value


def _call(method, base, path, payload=None, token=None):
    """One Infisical API call; returns (status, json body or {})."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'{}')
        except ValueError:
            body = {}
        return exc.code, body if isinstance(body, dict) else {}
    except OSError as exc:
        raise SecretStoreError('unavailable', f'Infisical did not answer ({type(exc).__name__})') from None


def _message(body):
    return str((body or {}).get('message') or '')[:200]


class InfisicalStore:
    backend = 'infisical'

    def configured(self, namespace=None):
        if namespace == GENERATED:
            return bool(os.getenv('INFISICAL_GENERATED_PROJECT_ID', '').strip())
        return all(os.getenv(k, '').strip() for k in ('INFISICAL_URL', 'INFISICAL_CLIENT_ID', 'INFISICAL_CLIENT_SECRET',
                                                       'INFISICAL_PROJECT_ID'))

    def describe(self):
        return {'backend': self.backend, 'configured': self.configured(), 'location': os.getenv('INFISICAL_URL', '').strip()}

    def _base(self):
        base = _env('INFISICAL_URL').rstrip('/')
        if not base.startswith('https://'):
            raise SecretStoreError('not_configured', 'Infisical HTTPS required (INFISICAL_URL)')
        return base

    def _target(self, namespace):
        """(project, environment, path, client id, client secret, who) for a namespace."""
        environment = os.getenv('INFISICAL_ENV', 'prod').strip() or 'prod'
        if namespace == PLATFORM:
            return (_env('INFISICAL_PROJECT_ID'), environment, None, _env('INFISICAL_CLIENT_ID'), _env('INFISICAL_CLIENT_SECRET'),
                    'gateway identity')
        project = _env('INFISICAL_GENERATED_PROJECT_ID')
        if project == os.getenv('INFISICAL_PROJECT_ID', '').strip():
            raise SecretStoreError('not_configured', 'INFISICAL_GENERATED_PROJECT_ID must differ from the servers project')
        env_name = os.getenv('INFISICAL_GENERATED_ENV', '').strip() or environment
        path = os.getenv('INFISICAL_GENERATED_PATH', '/').strip() or '/'
        cid = os.getenv('INFISICAL_GENERATED_CLIENT_ID', '').strip()
        secret = os.getenv('INFISICAL_GENERATED_CLIENT_SECRET', '').strip()
        if cid or secret:
            if not (cid and secret):
                raise SecretStoreError('not_configured', 'set both INFISICAL_GENERATED_CLIENT_ID and INFISICAL_GENERATED_CLIENT_SECRET')
            who = 'Agent Generated identity'
        else:
            cid, secret, who = _env('INFISICAL_CLIENT_ID'), _env('INFISICAL_CLIENT_SECRET'), 'gateway identity'
        return project, env_name, path, cid, secret, who

    def _login(self, base, cid, secret, who):
        status, body = _call('POST', base, '/api/v1/auth/universal-auth/login', {'clientId': cid, 'clientSecret': secret})
        if status != 200 or not body.get('accessToken'):
            raise SecretStoreError('unavailable', 'Infisical login failed for the %s (HTTP %s) %s' % (who, status, _message(body)))
        return body['accessToken']

    def read_platform(self, folder, names, optional=()):
        base = self._base()
        project, environment, _, cid, secret, who = self._target(PLATFORM)
        token = self._login(base, cid, secret, who)
        query = urllib.parse.urlencode({'projectId': project, 'environment': environment, 'secretPath': folder or '/'})
        out = {}
        for name in tuple(names) + tuple(optional):
            status, body = _call('GET', base, '/api/v4/secrets/' + name + '?' + query, token=token)
            if status == 404:
                if name in optional:
                    continue
                raise SecretStoreError('not_found', f'{join_platform(folder, name)} is not in Infisical')
            if status != 200:
                raise SecretStoreError('unavailable', f'Infisical answered HTTP {status} for {join_platform(folder, name)}')
            out[name] = ((body.get('secret') or {}).get('secretValue')) or ''
        return out

    def _list(self, namespace, folder=None):
        base = self._base()
        project, environment, path, cid, secret, who = self._target(namespace)
        token = self._login(base, cid, secret, who)
        query = {'projectId': project, 'environment': environment, 'secretPath': folder or path or '/'}
        if namespace == PLATFORM:
            query['recursive'] = 'true'
        status, body = _call('GET', base, '/api/v4/secrets?' + urllib.parse.urlencode(query), token=token)
        if status != 200:
            raise SecretStoreError('unavailable', 'Infisical refused listing the %s namespace (HTTP %s): %s'
                                   % (namespace, status, _message(body) or 'the identity needs read access'))
        return body.get('secrets') or []

    def generated(self):
        values, meta = {}, {}
        for item in self._list(GENERATED):
            key = item.get('secretKey')
            if isinstance(key, str):
                values[key] = item.get('secretValue') or ''
                meta[key] = {'name': key, 'comment': (item.get('secretComment') or '')[:200],
                             'updated': item.get('updatedAt') or item.get('createdAt')}
        return values, meta

    def _locate(self, namespace, name):
        name = check_name(namespace, name)
        if namespace == PLATFORM:
            folder, key = split_platform(name)
            return folder, key
        return None, name

    def put(self, namespace, name, value, comment='', exists=None):
        """exists True: update (PATCH), False: create (POST), None: try an update, then a create."""
        _check_value(value)
        folder, key = self._locate(namespace, name)
        base = self._base()
        project, environment, path, cid, secret, who = self._target(namespace)
        token = self._login(base, cid, secret, who)
        payload = {'projectId': project, 'environment': environment, 'secretPath': folder or path or '/', 'secretValue': value,
                   'secretComment': str(comment or '')[:300]}
        existed, status = False, None
        if exists is not False:
            status, _ = _call('PATCH', base, '/api/v4/secrets/' + key, payload, token)
            existed = status in (200, 201)
        if not existed and exists is not True:
            status, _ = _call('POST', base, '/api/v4/secrets/' + key, {**payload, 'type': 'shared'}, token)
        if status not in (200, 201):
            raise SecretStoreError('refused', 'Infisical answered HTTP %s: the %s needs write access on the %s project'
                                   % (status, who, 'servers' if namespace == PLATFORM else 'Agent Generated'))
        return existed

    def delete(self, namespace, name):
        folder, key = self._locate(namespace, name)
        base = self._base()
        project, environment, path, cid, secret, who = self._target(namespace)
        token = self._login(base, cid, secret, who)
        status, _ = _call('DELETE', base, '/api/v4/secrets/' + key, {'projectId': project, 'environment': environment,
                                                                     'secretPath': folder or path or '/'}, token)
        if status == 404:
            return False
        if status not in (200, 201):
            raise SecretStoreError('refused', 'Infisical answered HTTP %s: the %s needs write access' % (status, who))
        return True

    def names(self, namespace):
        if namespace not in NAMESPACES:
            raise SecretStoreError('invalid', 'namespace is platform or generated')
        out = []
        for item in self._list(namespace):
            key = item.get('secretKey')
            if isinstance(key, str):
                out.append(join_platform(item.get('secretPath') or '/', key) if namespace == PLATFORM else key)
        return sorted(out)


# ── the one entry point ──────────────────────────────────────────────────────────────────────────────────────────────

_stores = {}
_stores_lock = threading.Lock()


def get():
    """The configured store (one instance per backend and settings)."""
    name = backend_name()
    key = (name, os.getenv('HLA_SECRETS_DIR', ''), os.getenv('HLA_SECRETS_KEY', '')) if name == 'local' else (name,)
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = _stores[key] = LocalStore() if name == 'local' else InfisicalStore()
        return store


def describe():
    """{backend, configured} for status pages (no paths that matter, never a value). Never raises."""
    try:
        store = get()
        return {'backend': store.backend, 'configured': bool(store.configured())}
    except SecretStoreError as exc:
        return {'backend': os.getenv('HLA_SECRETS_BACKEND', '').strip() or 'local', 'configured': False, 'error': str(exc)}


def platform_secrets(folder, names, optional=()):
    """{name: value} of the named platform secrets in one folder (servers/<name>, a service's folder)."""
    return get().read_platform(folder, names, optional)
