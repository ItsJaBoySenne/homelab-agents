"""GitHub pull requests through the gateway (HLA_GITHUB_V1): agents push a branch and open a PR instead of merging.

The token never leaves the gateway: GITHUB_TOKEN from the credential store (Connections page), else .env.gateway,
else GITHUB_TOKEN in the secret store (folder GitHub, i.e. GitHub/GITHUB_TOKEN; the inventory's services.github.secret_path or
INFISICAL_GITHUB_PATH change the folder), read on every request like the Home Assistant token. The github service exists
only when the inventory has services.github.
Without either, every call answers 503 github_not_configured.

Only repositories on the allowlist (Policy page, `github.repos` in the policy) can be reached; none by default.
Levels are min(agent level, "github" level), unhinged mode as on servers:
  1  pr_status, list_prs
  4  create_pr, comment
  6  (or unhinged mode) merge; a repository can allow merges from level 4 with its own `merge` flag
The dashboard (admin token) may also ask `status` (token source, login, scopes) and `list_repos` (what the token
reaches, max 300); agents cannot.
"""
import json
import os
import re
import urllib.error
import urllib.request

from .core import SERVICES, platform_secrets
from .secret_store import SecretStoreError

NAME = 'github'
API = SERVICES.get(NAME, ('https://api.github.com', None))[0]
OPERATIONS = {'pr_status': 1, 'list_prs': 1, 'create_pr': 4, 'comment': 4, 'merge': 6}
TIMEOUT = 15
_REPO = re.compile(r'^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$')
_BRANCH = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$')


class GitHubError(Exception):
    """A refusal or failure with a reason safe to show to the agent (never the token)."""
    def __init__(self, status, error, detail=''):
        super().__init__(error)
        self.status, self.error, self.detail = status, error, detail


def valid_repo(repo):
    return isinstance(repo, str) and bool(_REPO.match(repo)) and '..' not in repo


def token():
    """The GitHub token, or GitHubError github_not_configured."""
    return token_source()[0]


def token_source():
    """(token, where it came from: store | env | secret_store), or GitHubError github_not_configured."""
    from . import credentials
    from .cli import db_open
    # The dashboard's credential store first (Connections page), then .env.gateway, then the secret store.
    source = 'store'
    value = (credentials.values(db_open).get('GITHUB_TOKEN') or '').strip()
    if not value:
        source, value = 'env', os.getenv('GITHUB_TOKEN', '').strip()
    if not value:
        source = 'secret_store'
        path = os.getenv('INFISICAL_GITHUB_PATH', '').strip() or SERVICES.get(NAME, (None, '/GitHub'))[1] or '/GitHub'
        try:
            value = platform_secrets(path, ('GITHUB_TOKEN',))['GITHUB_TOKEN'].strip()
        except SecretStoreError as exc:
            if exc.code in ('not_found', 'not_configured'):
                raise GitHubError(503, 'github_not_configured', 'no GITHUB_TOKEN on the Connections page, in .env.gateway or in the '
                                  'secret store (' + path.strip('/') + '/GITHUB_TOKEN)') from None
            raise GitHubError(502, 'github_token_unavailable', str(exc)[:200]) from None
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise GitHubError(502, 'github_token_unavailable', type(exc).__name__) from None
    if not value or any(c in value for c in '\r\n '):
        raise GitHubError(503, 'github_not_configured', 'GITHUB_TOKEN is empty or invalid')
    return value, source


def _call(method, path, payload=None, token=None):
    """One GitHub REST call: (status, parsed JSON or {}). Tests replace this."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'hla-gateway', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read(4 * 1024 * 1024) or b'{}')
    except urllib.error.HTTPError as exc:
        try: body = json.loads(exc.read(20000) or b'{}')
        except ValueError: body = {}
        return exc.code, body


def _user(token):
    """GET /user: (status, body, granted scopes or None). Tests replace this. Classic tokens list their scopes in
    X-OAuth-Scopes; fine-grained tokens send no such header (their access is set per repository)."""
    req = urllib.request.Request(API + '/user', headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'hla-gateway'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            scopes = resp.headers.get('X-OAuth-Scopes')
            return resp.status, json.loads(resp.read(1024 * 1024) or b'{}'), scopes
    except urllib.error.HTTPError as exc:
        try: body = json.loads(exc.read(20000) or b'{}')
        except ValueError: body = {}
        return exc.code, body, None


def _safe(text, secret):
    text = str(text or '')[:300]
    return text.replace(secret, '[token]') if secret else text


def _message(body, secret):
    """GitHub's own explanation (message plus the first validation errors), with the token removed."""
    if not isinstance(body, dict):
        return ''
    errors = [e.get('message') or e.get('code') for e in body.get('errors') or [] if isinstance(e, dict)]
    return _safe(' '.join(str(x) for x in [body.get('message'), *errors[:3]] if x), secret)


def check(op, body, level, free, repos):
    """Validate a /v1/github request at this level against the repo allowlist: the request or GitHubError.
    repos: {"owner/name" (lowercase): {"merge": bool}}."""
    if op not in OPERATIONS:
        raise GitHubError(400, 'invalid_operation', 'operation must be one of ' + ', '.join(OPERATIONS))
    if level < 1:
        raise GitHubError(403, 'policy_denied')
    repo = body.get('repo')
    if not valid_repo(repo):
        raise GitHubError(400, 'invalid_repo', 'repo is owner/name, e.g. octo/site')
    allowed = repos.get(repo.lower())
    if allowed is None:
        raise GitHubError(403, 'repo_not_allowed', repo + ' is not on the allowed repositories list (Policy page, GitHub)')
    need = OPERATIONS[op]
    if op == 'merge' and allowed.get('merge'):
        need = 4  # the owner allowed merges in this repository
    if not free and level < need:
        raise GitHubError(403, 'policy_denied', f'{op} needs level {need} on github (you have {level})' if need < 6 else
                          'merging needs level 6 or unhinged mode on github, or merges allowed for this repository')
    req = {'op': op, 'repo': repo.lower(), 'need': need}  # GitHub names are case-insensitive
    if op in ('pr_status', 'comment', 'merge'):
        number = body.get('number')
        if type(number) is not int or number < 1:
            raise GitHubError(400, 'invalid_number', 'number is the pull request number')
        req['number'] = number
    if op == 'list_prs':
        state = body.get('state') or 'open'
        if state not in ('open', 'closed', 'all'):
            raise GitHubError(400, 'invalid_state', 'state is open, closed or all')
        req['state'] = state
    elif op == 'create_pr':
        head, base = body.get('head'), body.get('base') or 'main'
        for name, value in (('head', head), ('base', base)):
            if not isinstance(value, str) or not _BRANCH.match(value) or '..' in value or value.endswith('/'):
                raise GitHubError(400, 'invalid_' + name, name + ' is a branch name, e.g. task/login-form')
        if head == base:
            raise GitHubError(400, 'invalid_head', 'head and base must differ')
        title, text = body.get('title'), body.get('body') or ''
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise GitHubError(400, 'invalid_title', 'title is 1-256 characters')
        if not isinstance(text, str) or len(text) > 60000:
            raise GitHubError(400, 'invalid_body', 'body is text of at most 60000 characters')
        req.update(head=head, base=base, title=title.strip(), body=text, draft=body.get('draft') is True)
    elif op == 'comment':
        text = body.get('body')
        if not isinstance(text, str) or not text.strip() or len(text) > 60000:
            raise GitHubError(400, 'invalid_body', 'body is the comment text (1-60000 characters)')
        req['body'] = text
    elif op == 'merge':
        method = body.get('method') or 'squash'
        if method not in ('squash', 'merge'):
            raise GitHubError(400, 'invalid_method', 'method is squash or merge')
        req['method'] = method
    return req


# HLA_CONNECTIONS_V1: dashboard-only reads (admin token) for the Connections page; agents cannot call these.
ADMIN_OPERATIONS = ('status', 'list_repos')
MAX_REPOS = 300


def status():
    """Where the token comes from and who it is, checked with GET /user. Never the token itself."""
    try:
        secret, source = token_source()
    except GitHubError as exc:
        return {'configured': False, 'source': None, 'ok': False, 'error': exc.error, 'detail': exc.detail}
    try:
        code, body, scopes = _user(secret)
    except (OSError, ValueError) as exc:
        return {'configured': True, 'source': source, 'ok': False, 'error': 'github_unavailable', 'detail': type(exc).__name__}
    if code != 200 or not isinstance(body, dict):
        return {'configured': True, 'source': source, 'ok': False, 'error': 'github_refused',
                'detail': f'GitHub answered HTTP {code}' + (': ' + _message(body, secret) if _message(body, secret) else '')}
    kind = 'classic' if scopes is not None else 'fine-grained' if secret.startswith('github_pat_') else 'unknown'
    return {'configured': True, 'source': source, 'ok': True, 'login': _safe(body.get('login'), secret)[:100],
            'name': _safe(body.get('name'), secret)[:100], 'token_type': kind,
            'scopes': sorted(s.strip() for s in scopes.split(',') if s.strip()) if scopes is not None else None}


def list_repos(allowlist):
    """Repositories the token can reach (at most MAX_REPOS, paginated), marked with their allowlist state."""
    secret = token_source()[0]
    repos, truncated = [], False
    pages = MAX_REPOS // 100
    for page in range(1, pages + 1):
        try:
            code, body = _call('GET', f'/user/repos?per_page=100&page={page}&sort=full_name'
                                      '&affiliation=owner,collaborator,organization_member', None, secret)
        except (OSError, ValueError) as exc:
            raise GitHubError(502, 'github_unavailable', type(exc).__name__) from None
        if code != 200:
            raise GitHubError(502, 'github_refused', f'GitHub answered HTTP {code}' + (': ' + _message(body, secret) if _message(body, secret) else ''))
        items = [i for i in body if isinstance(i, dict)] if isinstance(body, list) else []
        for item in items:
            name = str(item.get('full_name') or '')
            if not valid_repo(name):
                continue
            perms = item.get('permissions') if isinstance(item.get('permissions'), dict) else {}
            entry = allowlist.get(name.lower())
            repos.append({'repo': name.lower(), 'full_name': name, 'private': bool(item.get('private')),
                          'archived': bool(item.get('archived')), 'default_branch': str(item.get('default_branch') or '')[:100],
                          'push': bool(perms.get('push')), 'admin': bool(perms.get('admin')),
                          'allowed': entry is not None, 'merge': bool(entry and entry.get('merge'))})
        if len(items) < 100:
            break
        if page == pages:
            truncated = True
    return {'repos': repos[:MAX_REPOS], 'truncated': truncated}


def _pr(item):
    return {'number': item.get('number'), 'title': item.get('title'), 'url': item.get('html_url'),
            'state': 'merged' if item.get('merged_at') or item.get('merged') else item.get('state'),
            'draft': bool(item.get('draft')), 'head': (item.get('head') or {}).get('ref'), 'base': (item.get('base') or {}).get('ref'),
            'author': (item.get('user') or {}).get('login'), 'updated': item.get('updated_at')}


def _checks(runs, combined):
    """One summary of check runs (Actions) and commit statuses."""
    passed = failed = pending = 0
    failing = []
    for run in (runs or {}).get('check_runs') or []:
        if run.get('status') != 'completed':
            pending += 1
        elif run.get('conclusion') in ('success', 'neutral', 'skipped'):
            passed += 1
        else:
            failed += 1
            failing.append(str(run.get('name'))[:80])
    for status in (combined or {}).get('statuses') or []:
        state = status.get('state')
        if state == 'success': passed += 1
        elif state == 'pending': pending += 1
        else:
            failed += 1
            failing.append(str(status.get('context'))[:80])
    state = 'failure' if failed else 'pending' if pending else 'success' if passed else 'none'
    return {'state': state, 'passed': passed, 'failed': failed, 'pending': pending, 'failing': failing[:10]}


def _reviews(items):
    latest = {}
    for review in items or []:
        user, state = (review.get('user') or {}).get('login'), review.get('state')
        if user and state in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED') or (user and state == 'COMMENTED' and user not in latest):
            latest[user] = state.lower()
    counts = {s: sum(1 for v in latest.values() if v == s) for s in ('approved', 'changes_requested', 'commented')}
    return {**counts, 'by': latest}


def run(req):
    """Carry out a checked request. Returns the reply payload (a dict) or raises GitHubError."""
    secret = token()
    repo, op = req['repo'], req['op']

    def call(method, path, payload=None, ok=(200,)):
        try:
            status, body = _call(method, '/repos/' + repo + path, payload, secret)
        except (OSError, ValueError) as exc:
            raise GitHubError(502, 'github_unavailable', type(exc).__name__) from None
        if status in ok:
            return body
        detail = f'GitHub answered HTTP {status}' + (': ' + _message(body, secret) if _message(body, secret) else '')
        if status in (401, 403):
            raise GitHubError(502, 'github_refused', detail + ' (check the token and its access to ' + repo + ')')
        if status == 404:
            raise GitHubError(404, 'github_not_found', detail)
        raise GitHubError(409 if status in (405, 409, 422) else 502, 'github_error', detail)

    if op == 'list_prs':
        items = call('GET', f'/pulls?state={req["state"]}&per_page=30&sort=updated&direction=desc')
        return {'repo': repo, 'pull_requests': [_pr(i) for i in items if isinstance(i, dict)]}
    if op == 'pr_status':
        pr = call('GET', f'/pulls/{req["number"]}')
        sha = (pr.get('head') or {}).get('sha') or ''
        runs = combined = reviews = None
        if re.match(r'^[0-9a-f]{7,40}$', sha):
            try:
                runs = call('GET', f'/commits/{sha}/check-runs?per_page=100')
                combined = call('GET', f'/commits/{sha}/status')
            except GitHubError:
                pass  # checks are extra: a token without that permission still gets the PR state
        try: reviews = call('GET', f'/pulls/{req["number"]}/reviews?per_page=100')
        except GitHubError: pass
        return {'repo': repo, **_pr(pr), 'head_sha': sha[:40], 'mergeable': pr.get('mergeable'),
                'mergeable_state': pr.get('mergeable_state'), 'additions': pr.get('additions'), 'deletions': pr.get('deletions'),
                'changed_files': pr.get('changed_files'), 'checks': _checks(runs, combined), 'reviews': _reviews(reviews)}
    if op == 'create_pr':
        payload = {'title': req['title'], 'head': req['head'], 'base': req['base'], 'body': req['body'], 'draft': req['draft']}
        try:
            pr = call('POST', '/pulls', payload, ok=(201,))
        except GitHubError as exc:
            if 'already exists' not in exc.detail:
                raise
            # The branch already has an open PR (a later turn of the same task): return that one.
            owner = repo.split('/')[0]
            found = call('GET', f'/pulls?state=open&head={owner}:{req["head"]}')
            if not found:
                raise
            return {'repo': repo, **_pr(found[0]), 'created': False, 'existing': True}
        return {'repo': repo, **_pr(pr), 'created': True}
    if op == 'comment':
        item = call('POST', f'/issues/{req["number"]}/comments', {'body': req['body']}, ok=(201,))
        return {'repo': repo, 'number': req['number'], 'commented': True, 'url': item.get('html_url')}
    result = call('PUT', f'/pulls/{req["number"]}/merge', {'merge_method': req['method']})
    return {'repo': repo, 'number': req['number'], 'merged': bool(result.get('merged')), 'sha': result.get('sha'),
            'method': req['method'], 'message': _safe(result.get('message'), secret)}
