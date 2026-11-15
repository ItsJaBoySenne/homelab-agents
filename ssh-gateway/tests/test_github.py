"""GitHub pull requests through the gateway API: real HTTP server and SQLite, GitHub and Infisical replaced by stand-ins.

python3 tests/test_github.py   (needs paramiko importable; no network)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
TOKEN = 'ghp_' + 'S3cr3t' * 6
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'INFISICAL_URL': 'https://infisical.test', 'INFISICAL_CLIENT_ID': 'id', 'INFISICAL_CLIENT_SECRET': 'cs',
                   'INFISICAL_PROJECT_ID': 'servers-project', 'INFISICAL_ENV': 'prod'})
os.environ.pop('GITHUB_TOKEN', None)
os.environ.pop('INFISICAL_GITHUB_PATH', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import github as gh  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

# --- Infisical stand-in: the token lives in /GitHub of the servers project (or nowhere) ---
INFISICAL = {'/GitHub': {'GITHUB_TOKEN': TOKEN}}
READS = []
from gateway.secret_store import SecretStoreError  # noqa: E402
def fake_secrets(path, names, optional=()):
    READS.append(path)
    if path not in INFISICAL:
        raise SecretStoreError('not_found', path.strip('/') + '/GITHUB_TOKEN is not in the secret store')
    return {n: INFISICAL[path][n] for n in names}
gh.platform_secrets = fake_secrets

# --- GitHub stand-in: one repository with pull requests ---
PRS = {7: {'number': 7, 'title': 'Old work', 'state': 'open', 'html_url': 'https://github.com/octo/site/pull/7', 'draft': False,
           'head': {'ref': 'task/old', 'sha': 'abc1234def5678'}, 'base': {'ref': 'main'}, 'user': {'login': 'agent'},
           'mergeable': True, 'mergeable_state': 'clean', 'additions': 10, 'deletions': 2, 'changed_files': 3}}
CALLS = []
def fake_call(method, path, payload=None, token=None):
    CALLS.append((method, path, payload, token))
    if token != TOKEN:
        return 401, {'message': 'Bad credentials'}
    if not path.startswith('/repos/octo/site/'):
        return 404, {'message': 'Not Found'}
    rest = path[len('/repos/octo/site'):]
    if method == 'GET' and rest.startswith('/pulls?'):
        if 'head=octo:task/old' in rest: return 200, [PRS[7]]
        return 200, [p for p in PRS.values() if 'state=all' in rest or 'state=' + p['state'] in rest]
    if method == 'POST' and rest == '/pulls':
        if payload['head'] == 'task/old': return 422, {'message': 'Validation Failed', 'errors': [{'message': 'A pull request already exists for octo:task/old.'}]}
        if payload['head'] == 'task/empty': return 422, {'message': 'Validation Failed', 'errors': [{'message': 'No commits between main and task/empty ' + TOKEN}]}
        n = max(PRS) + 1
        PRS[n] = {'number': n, 'title': payload['title'], 'state': 'open', 'html_url': f'https://github.com/octo/site/pull/{n}',
                  'draft': payload['draft'], 'head': {'ref': payload['head'], 'sha': 'fff0000aaa1111'}, 'base': {'ref': payload['base']},
                  'user': {'login': 'agent'}}
        return 201, PRS[n]
    if method == 'GET' and rest.startswith('/pulls/') and rest.endswith('/reviews?per_page=100'):
        return 200, [{'user': {'login': 'kim'}, 'state': 'CHANGES_REQUESTED'}, {'user': {'login': 'kim'}, 'state': 'APPROVED'},
                     {'user': {'login': 'bob'}, 'state': 'COMMENTED'}]
    if method == 'GET' and rest.startswith('/pulls/'):
        n = int(rest.split('/')[2])
        return (200, PRS[n]) if n in PRS else (404, {'message': 'Not Found'})
    if method == 'GET' and '/check-runs' in rest:
        return 200, {'check_runs': [{'name': 'tests', 'status': 'completed', 'conclusion': 'success'},
                                    {'name': 'lint', 'status': 'completed', 'conclusion': 'failure'},
                                    {'name': 'e2e', 'status': 'in_progress', 'conclusion': None}]}
    if method == 'GET' and rest.endswith('/status'):
        return 200, {'statuses': [{'context': 'ci/legacy', 'state': 'success'}]}
    if method == 'POST' and rest.startswith('/issues/'):
        return 201, {'html_url': 'https://github.com/octo/site/pull/7#issuecomment-1'}
    if method == 'PUT' and rest.endswith('/merge'):
        n = int(rest.split('/')[2])
        if n == 99: return 405, {'message': 'Pull Request is not mergeable'}
        PRS[n]['state'] = 'closed'; PRS[n]['merged_at'] = '2026-11-08T10:00:00Z'
        return 200, {'merged': True, 'sha': 'deadbeef00', 'message': 'Pull Request successfully merged'}
    return 404, {'message': 'Not Found'}
gh._call = fake_call

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % httpd.server_address[1]
AGENT = '11111111-2222-3333-4444-555555555555'

def call(method, path, body=None, token='t' * 40):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def policy(change):
    return call('PUT', '/v1/policy', {'change': change, 'actor': 'test'}, token='a' * 40)

def github(**body):
    return call('POST', '/v1/github', {'agent_id': AGENT, **body})

# Policy: github is a service at level 1 with no repositories allowed.
s, snap = call('GET', '/v1/policy')
check(snap['services'].get('github') == {'level': 1} and snap['github'] == {'repos': {}}, 'github listed as a service at level 1, no repos allowed')
policy({'agents': {AGENT: {'name': 'Builder', 'level': 4}}})
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
check(eff['services']['github']['level'] == 1, 'effective github level for the agent')
s, r = github(operation='list_prs', repo='octo/site')
check(s == 403 and r['error'] == 'repo_not_allowed' and not CALLS, 'default: no repository is reachable')
s, r = policy({'github': {'repos': {'Octo/Site': {'merge': False}, 'octo/other': {}}}})
check(s == 200 and r['github']['repos'] == {'octo/site': {'merge': False}, 'octo/other': {'merge': False}}, 'repositories added to the allowlist')
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
check(eff['services']['github']['repos'] == ['octo/other', 'octo/site'] and eff['services']['github']['merge_repos'] == [],
      'the effective policy lists the reachable repositories for the agent tool')
for bad in ({'github': {'repos': {'not a repo': {}}}}, {'github': {'repos': {'a/b': {'merge': 'yes'}}}}, {'github': []}):
    s, r = policy(bad)
    check(s == 400, 'invalid allowlist change refused: %s' % json.dumps(bad))

# Level 1: read only.
s, r = github(operation='list_prs', repo='octo/site')
check(s == 200 and r['pull_requests'][0]['number'] == 7 and r['pull_requests'][0]['head'] == 'task/old' and READS[-1] == '/GitHub',
      'list_prs: compact list; token read from Infisical /GitHub')
s, r = github(operation='pr_status', repo='OCTO/site', number=7)
check(s == 200 and r['state'] == 'open' and r['mergeable'] is True and r['checks']['state'] == 'failure'
      and r['checks']['failing'] == ['lint'] and r['checks']['passed'] == 2 and r['checks']['pending'] == 1,
      'pr_status: state, mergeable and a checks summary')
check(r['reviews']['approved'] == 1 and r['reviews']['changes_requested'] == 0 and r['reviews']['by'] == {'kim': 'approved', 'bob': 'commented'},
      'pr_status: the latest review per reviewer')
s, r = github(operation='create_pr', repo='octo/site', head='task/login', title='Login form')
check(s == 403 and r['error'] == 'policy_denied' and 'level 4' in r['detail'], 'create_pr needs level 4')

# Level 4: create PRs and comment; merging still refused.
policy({'servers': {'github': {'level': 4}}})
s, r = github(operation='create_pr', repo='octo/site', head='task/login', base='main', title='Login form', body='Adds the form.')
check(s == 200 and r['created'] and r['number'] == 8 and r['url'] == 'https://github.com/octo/site/pull/8' and CALLS[-1][2]['base'] == 'main',
      'create_pr opens a pull request and returns its URL')
s, r = github(operation='create_pr', repo='octo/site', head='task/old', title='Again')
check(s == 200 and r['existing'] and r['number'] == 7, 'create_pr for a branch with an open PR returns that PR')
s, r = github(operation='create_pr', repo='octo/site', head='task/empty', title='Nothing')
check(s == 409 and 'No commits between' in r['detail'] and TOKEN not in json.dumps(r) and '[token]' in r['detail'],
      'GitHub errors are explained, with the token redacted')
for bad, err in (({'head': '-x'}, 'invalid_head'), ({'head': 'a..b'}, 'invalid_head'), ({'head': 'main'}, 'invalid_head'),
                 ({'head': 'task/x', 'title': ''}, 'invalid_title'), ({'head': 'task/x', 'base': 'ma in'}, 'invalid_base')):
    s, r = github(operation='create_pr', repo='octo/site', **{'title': 'T', **bad})
    check(s == 400 and r['error'] == err, 'create_pr refuses %s (%s)' % (json.dumps(bad), err))
s, r = github(operation='comment', repo='octo/site', number=7, body='Reviewed: please fix lint.')
check(s == 200 and r['commented'] and CALLS[-1][1] == '/repos/octo/site/issues/7/comments', 'comment on a PR')
s, r = github(operation='merge', repo='octo/site', number=7)
check(s == 403 and 'level 6' in r['detail'], 'merge needs level 6 (or the repository allows it)')
s, r = github(operation='list_prs', repo='octo/private')
check(s == 403 and r['error'] == 'repo_not_allowed', 'repositories off the list stay unreachable at level 4')
s, r = github(operation='pr_status', repo='../etc', number=1)
check(s == 400 and r['error'] == 'invalid_repo', 'repo must be owner/name')

# Merges: allowed per repository from level 4, or at level 6 / unhinged.
policy({'github': {'repos': {'octo/site': {'merge': True}}}})
s, r = github(operation='merge', repo='octo/site', number=7, method='rebase')
check(s == 400 and r['error'] == 'invalid_method', 'merge method is squash or merge')
s, r = github(operation='merge', repo='octo/site', number=7)
check(s == 200 and r['merged'] and CALLS[-1][2] == {'merge_method': 'squash'}, 'merge allowed for this repository at level 4 (squash by default)')
PRS[99] = dict(PRS[8], number=99)
s, r = github(operation='merge', repo='octo/site', number=99, method='merge')
check(s == 409 and 'not mergeable' in r['detail'], 'an unmergeable PR is explained')
policy({'github': {'repos': {'octo/site': {'merge': False}}}, 'servers': {'github': {'level': 6}}, 'agents': {AGENT: {'name': 'Builder', 'level': 6}}})
s, r = github(operation='merge', repo='octo/site', number=8)
check(s == 200 and r['merged'], 'level 6 merges')
s, r = github(operation='list_prs', repo='octo/site', state='closed')
check(s == 200 and {p['state'] for p in r['pull_requests']} == {'merged'}, 'merged PRs are reported as merged')

# The dashboard (admin token) reads PR states, never changes anything.
s, r = call('POST', '/v1/github', {'actor': 'dashboard', 'operation': 'pr_status', 'repo': 'octo/site', 'number': 8}, token='a' * 40)
check(s == 200 and r['state'] == 'merged', 'admin token: pr_status for the dashboard')
s, r = call('POST', '/v1/github', {'actor': 'dashboard', 'operation': 'comment', 'repo': 'octo/site', 'number': 8, 'body': 'x'}, token='a' * 40)
check(s == 403, 'admin token: read only')
s, r = call('POST', '/v1/github', {'operation': 'pr_status', 'repo': 'octo/site', 'number': 8}, token='a' * 40)
check(s == 400 and r['error'] == 'actor_required', 'admin token: names its actor')
s, r = call('POST', '/v1/github', {'operation': 'pr_status', 'repo': 'octo/site', 'number': 8}, token='x' * 40)
check(s == 401, 'other tokens refused')

# Removing a repository from the list makes it unreachable again.
policy({'github': {'repos': {'octo/other': None}}})
s, snap = call('GET', '/v1/policy')
check(snap['github']['repos'] == {'octo/site': {'merge': False}}, 'a repository can be removed from the list')

# Token: from .env.gateway first; a wrong token is explained; none at all is "not configured".
os.environ['GITHUB_TOKEN'] = 'wrong-token'
n = len(READS)
s, r = github(operation='pr_status', repo='octo/site', number=7)
check(s == 502 and r['error'] == 'github_refused' and 'Bad credentials' in r['detail'] and len(READS) == n,
      'GITHUB_TOKEN in .env.gateway is used first; a refused token is explained')
os.environ.pop('GITHUB_TOKEN')
INFISICAL.clear()
s, r = github(operation='list_prs', repo='octo/site')
check(s == 503 and r['error'] == 'github_not_configured' and 'GitHub/GITHUB_TOKEN' in r['detail'], 'no token anywhere: github_not_configured')
os.environ['INFISICAL_GITHUB_PATH'] = '/Tokens/GitHub'
INFISICAL['/Tokens/GitHub'] = {'GITHUB_TOKEN': TOKEN}
s, r = github(operation='list_prs', repo='octo/site')
check(s == 200 and READS[-1] == '/Tokens/GitHub', 'INFISICAL_GITHUB_PATH chooses the folder')

# Connections page (admin token): where the token comes from, who it is, and the repositories it reaches.
USERS = []
def fake_user(token):
    USERS.append(token)
    if token != TOKEN: return 401, {'message': 'Bad credentials'}, None
    return 200, {'login': 'hla-bot', 'name': 'Homelab Agents ' + TOKEN}, 'repo, read:org'
gh._user = fake_user
REPOS = [{'full_name': f'octo/repo{i:03d}', 'private': i % 2 == 0, 'default_branch': 'main', 'permissions': {'push': True, 'admin': i == 1}}
         for i in range(250)] + [{'full_name': 'Octo/Site', 'private': True, 'default_branch': 'main', 'permissions': {'push': True}}]
REPO_PAGES = []
plain_call = gh._call
def repo_call(method, path, payload=None, token=None):
    if path.startswith('/user/repos?'):
        REPO_PAGES.append(path)
        if token != TOKEN: return 401, {'message': 'Bad credentials'}
        page = int(path.split('&page=')[1].split('&')[0])
        return 200, REPOS[(page - 1) * 100:page * 100]
    return plain_call(method, path, payload, token)
gh._call = repo_call
def admin(**body):
    return call('POST', '/v1/github', {'actor': 'owner', **body}, token='a' * 40)
s, r = admin(operation='status')
check(s == 200 and r['configured'] and r['ok'] and r['source'] == 'secret_store' and r['login'] == 'hla-bot' and r['scopes'] == ['read:org', 'repo']
      and r['token_type'] == 'classic' and TOKEN not in json.dumps(r) and USERS[-1] == TOKEN, 'status: source secret_store, login and scopes, never the token')
os.environ['GITHUB_TOKEN'] = TOKEN
s, r = admin(operation='status')
check(s == 200 and r['source'] == 'env', 'status: GITHUB_TOKEN from .env.gateway reported as env')
os.environ['GITHUB_TOKEN'] = 'wrong-token'
s, r = admin(operation='status')
check(s == 200 and r['configured'] and not r['ok'] and r['error'] == 'github_refused' and 'Bad credentials' in r['detail'], 'status: a refused token is explained')
import base64  # noqa: E402
from gateway import credentials as creds  # noqa: E402
os.environ['GATEWAY_CREDENTIAL_KEY'] = base64.b64encode(os.urandom(32)).decode()
with srv.db_open() as db:
    creds.put(db, 'GITHUB_TOKEN', TOKEN, actor='owner')
s, r = admin(operation='status')
check(s == 200 and r['source'] == 'store' and r['ok'], 'status: the credential store comes first and is reported as store')
s, r = admin(operation='list_repos')
check(s == 200 and len(r['repos']) == 251 and not r['truncated'] and len(REPO_PAGES) == 3 and 'per_page=100' in REPO_PAGES[0],
      'list_repos: paginated, 100 per page (%d)' % len(r.get('repos', [])))
site = next(x for x in r['repos'] if x['repo'] == 'octo/site')
check(site == {'repo': 'octo/site', 'full_name': 'Octo/Site', 'private': True, 'archived': False, 'default_branch': 'main', 'push': True,
               'admin': False, 'allowed': True, 'merge': False} and not next(x for x in r['repos'] if x['repo'] == 'octo/repo000')['allowed'],
      'list_repos marks which repositories are on the allowlist')
REPOS.extend({'full_name': f'octo/more{i:03d}'} for i in range(100))
s, r = admin(operation='list_repos')
check(s == 200 and len(r['repos']) == 300 and r['truncated'], 'list_repos stops at 300 and says so')
s, r = github(operation='status')
check(s == 400 and r['error'] == 'invalid_operation', 'agents cannot call the dashboard operations')
s, r = github(operation='list_repos', repo='octo/site')
check(s == 400 and r['error'] == 'invalid_operation', 'agents cannot list repositories')
with srv.db_open() as db:
    creds.delete(db, 'GITHUB_TOKEN')
os.environ.pop('GITHUB_TOKEN')
saved_infisical = dict(INFISICAL); INFISICAL.clear()
s, r = admin(operation='status')
check(s == 200 and not r['configured'] and r['source'] is None and r['error'] == 'github_not_configured', 'status: no token anywhere')
s, r = admin(operation='list_repos')
check(s == 503 and r['error'] == 'github_not_configured', 'list_repos without a token: not configured')
INFISICAL.update(saved_infisical)

# Audit: changes recorded (reads are not), and the token appears nowhere.
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    actions = {a for (a,) in db.execute("SELECT action FROM audit WHERE server='github'")}
    dump = json.dumps(db.execute('SELECT * FROM audit').fetchall()) + json.dumps(db.execute('SELECT * FROM policy_audit').fetchall())
check(actions == {'agent_github_change', 'agent_github_merge'}, f'audit has PR changes and merges ({sorted(actions)})')
check(TOKEN not in dump, 'no token in the audit log')

httpd.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
