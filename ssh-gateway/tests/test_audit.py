"""Audit log read endpoint (HLA_AUDIT_V1): real HTTP server and SQLite, SSH execution replaced by a stand-in.

python3 tests/test_audit.py   (needs paramiko importable; no network, nothing is executed remotely)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 'T0olToken' * 5, 'POLICY_ADMIN_TOKEN': 'Adm1nToken' * 4})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import audit as audit_log  # noqa: E402

TOOL, ADMIN = os.environ['TOOL_GATEWAY_TOKEN'], os.environ['POLICY_ADMIN_TOKEN']
RAN = []
def fake_execute(server, command, timeout=30, max_output=12000, stdin=None):
    RAN.append((server, command))
    return {'exit_code': 1 if 'false' in command else 0, 'timed_out': False, 'truncated': False, 'stdout': 'ok', 'stderr': ''}
srv.execute = fake_execute

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT, OTHER = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
TASK = '33333333-3333-4333-8333-333333333333'

def call(method, path, body=None, token=TOOL, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def audit(token=TOOL, **params): return call('GET', '/v1/audit?' + urlencode(params), token=token)
def run(agent, server, task=None, **kw):
    return call('POST', '/v1/execute', {'agent_id': agent, 'server': server, **kw}, headers={'X-HLA-Task': task} if task else None)

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'name': 'Infra', 'level': 5}, OTHER: {'name': 'Junior', 'level': 1}}}, 'actor': 'jordan'}, token=ADMIN)

# A legacy row from before HLA_AUDIT_V1: digest only, agent in the actor.
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    db.execute("INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated,actor) VALUES (?,?,?,?,?,?,?,?)",
               (int(time.time()) - 86400 * 3, 'secrets', 'agent_secret_created', 'ab' * 32, 0, 0, 0, 'agent:' + AGENT))

# Activity: diagnostics, a denied command, a dashboard grant and the approved run, commands with credentials.
run(AGENT, 'dev', task=TASK, mode='diagnostic', diagnostic='disk')
run(OTHER, 'jellyfin', mode='diagnostic', diagnostic='path-permissions', target='/srv/media')
run(AGENT, 'dev', task='not-a-uuid', mode='approved_command', command='sudo systemctl restart nginx')  # denied: no approval
cmd = 'curl -H "Authorization: Bearer abcdefghijklmnop1234" -d password=hunter2xyz https://x/api && echo {{secret:APP_DB}}'
call('POST', '/v1/approvals', {'server': 'dev', 'command': cmd, 'actor': 'jordan'}, token=ADMIN, headers={'X-HLA-Task': TASK})
cmd_run = cmd.replace(' && echo {{secret:APP_DB}}', '')
call('POST', '/v1/approvals', {'server': 'dev', 'command': cmd_run, 'actor': 'jordan'}, token=ADMIN)
run(AGENT, 'dev', task=TASK, mode='approved_command', command=cmd_run)
call('POST', '/v1/approvals', {'server': 'dev', 'command': 'false && echo ' + TOOL, 'actor': 'jordan'}, token=ADMIN)
run(AGENT, 'dev', mode='approved_command', command='false && echo ' + TOOL)
call('POST', '/v1/approvals', {'server': 'dev', 'command': 'git push https://bot:ghp_0123456789abcdefghijABCDEFGHIJ@github.com/x/y aK9fL2mQ8rT4vX7zB1nC5dE3gH6jP0sW',
                               'actor': 'jordan'}, token=ADMIN)

# Reading: either token, never without one.
check(audit(token='wrong' * 10)[0] == 401, 'audit read needs a token')
code, body = audit(token=ADMIN, limit=200)
check(code == 200 and len(body['rows']) == 10 and body['next_before_id'] is None, 'admin token reads the whole log')
code, body = audit(limit=200, facets=1)
rows = body['rows']
check(code == 200 and [r['id'] for r in rows] == sorted((r['id'] for r in rows), reverse=True), 'tool token reads it, newest first')

diag = next(r for r in rows if r['action'] == 'agent_diagnostic' and r['server'] == 'dev')
check(diag['agent_id'] == AGENT and diag['detail'] == 'disk' and diag['task_id'] == TASK and diag['outcome'] == 'ok'
      and diag['exit_code'] == 0 and len(diag['digest']) == 16, 'diagnostic row: agent, detail, task id from X-HLA-Task, outcome')
target = next(r for r in rows if r['server'] == 'jellyfin')
check(target['detail'] == 'path-permissions /srv/media' and target['agent_id'] == OTHER and target['task_id'] is None, 'targeted diagnostic shows its target')
denied = next(r for r in rows if r['action'] == 'agent_denied')
check(denied['outcome'] == 'denied' and denied['detail'] == 'sudo systemctl restart nginx' and denied['task_id'] is None,
      'denied command audited with its text; an invalid task header is ignored')
ran = next(r for r in rows if r['action'] == 'agent_approved_command' and 'curl' in r['detail'])
check(ran['approved_by'] == 'jordan' and ran['task_id'] == TASK and ran['agent_id'] == AGENT, 'approved command: approver from the grant')
ref = next(r for r in rows if r['action'] == 'chat_approval' and '{{secret:' in (r['detail'] or ''))
check('abcdefghijklmnop1234' not in ran['detail'] and 'hunter2xyz' not in ran['detail'] and 'Authorization: Bearer [redacted]' in ran['detail']
      and ref['detail'].endswith('echo {{secret:APP_DB}}') and ref['task_id'] == TASK, 'bearer token and password masked, secret reference kept ' + ran['detail'])
failed = next(r for r in rows if r['action'] == 'agent_approved_command' and 'false' in r['detail'])
check(failed['outcome'] == 'failed' and failed['exit_code'] == 1 and TOOL not in failed['detail'] and '[secret:TOOL_GATEWAY_TOKEN]' in failed['detail'],
      "failed command: exit code, and the gateway's own token never shown")
grant = next(r for r in rows if r['action'] == 'chat_approval' and 'git push' in r['detail'])
check('ghp_0123456789' not in grant['detail'] and 'aK9fL2mQ8rT4vX7zB1nC5dE3gH6jP0sW' not in grant['detail'] and grant['actor'] == 'jordan'
      and grant['outcome'] == 'recorded', 'URL credentials, GitHub tokens and random keys masked ' + grant['detail'])
legacy = next(r for r in rows if r['action'] == 'agent_secret_created')
check(legacy['agent_id'] == AGENT and legacy['detail'] is None and legacy['actor'] is None, 'legacy row: agent taken from the actor')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    dump = json.dumps(db.execute('SELECT * FROM audit').fetchall())
check(TOOL not in dump and 'hunter2xyz' not in dump and 'ghp_0123456789' not in dump, 'credentials are masked before they are stored')
check(RAN and all(TOOL in c or 'false' not in c for _, c in RAN), 'the command itself still ran unchanged')

f = body['facets']
check(f['servers'] == ['dev', 'jellyfin', 'secrets'] and 'agent_diagnostic' in f['actions'] and f['agents'] == sorted([AGENT, OTHER]),
      'facets: servers, actions and agents for the filters')

# Filters.
check({r['agent_id'] for r in audit(agent=OTHER)[1]['rows']} == {OTHER}, 'filter by agent')
check(len(audit(agent=AGENT)[1]['rows']) == 5, 'agent filter includes legacy rows by actor')
check(all(r['agent_id'] is None for r in audit(agent='none')[1]['rows']) and len(audit(agent='none')[1]['rows']) == 4, 'agent=none: grants and other non-agent rows')
check({r['server'] for r in audit(server='jellyfin')[1]['rows']} == {'jellyfin'}, 'filter by server')
check({r['action'] for r in audit(action='chat_approval')[1]['rows']} == {'chat_approval'}, 'filter by action')
check({r['action'] for r in audit(action='agent_approved*')[1]['rows']} == {'agent_approved_command'}, 'action prefix filter')
check([r['action'] for r in audit(q='nginx')[1]['rows']] == ['agent_denied'], 'text search in the detail')
check(audit(q='hunter2xyz')[1]['rows'] == [], 'a masked value cannot be searched for')
check(audit(q='100%_')[0] == 200 and audit(q='100%_')[1]['rows'] == [], 'LIKE wildcards in the search are literal')
now = int(time.time())
check(len(audit(since=now - 86400)[1]['rows']) == 9 and len(audit(until=now - 86400)[1]['rows']) == 1, 'date range (since inclusive, until exclusive)')

# Pagination by id.
code, page1 = audit(limit=4)
code, page2 = audit(limit=4, before_id=page1['next_before_id'])
code, page3 = audit(limit=4, before_id=page2['next_before_id'])
ids = [r['id'] for p in (page1, page2, page3) for r in p['rows']]
check(len(ids) == 10 and len(set(ids)) == 10 and page3['next_before_id'] is None and 'facets' not in page2, 'pages chain by before_id without gaps or repeats')

# Bad parameters.
for params, why in (({'limit': 201}, 'limit over 200'), ({'limit': 0}, 'limit 0'), ({'agent': 'bob'}, 'agent not an id'),
                    ({'action': 'x;drop'}, 'odd action'), ({'since': 'yesterday'}, 'since not a number'), ({'q': 'x' * 201}, 'search too long'),
                    ({'server': 'a b'}, 'odd server')):
    code, b = audit(**params)
    check(code == 400 and b['error'] == 'invalid_query', 'refused: ' + why)

# Masking on its own.
m = audit_log.mask
check(m('git checkout 3f2a9c1e0b7d4a6f8e5c2b1a0d9e8f7c6b5a4d3e') == 'git checkout 3f2a9c1e0b7d4a6f8e5c2b1a0d9e8f7c6b5a4d3e', 'commit ids stay readable')
check(m('cat /srv/www/site/index.html') == 'cat /srv/www/site/index.html', 'paths stay readable')
check('s3cr3t' not in m('mysql -u root --password=s3cr3t db') and 'Pa55' not in m('export DB_PASSWORD="Pa55word!"'), 'password flags and variables masked')
check(m('echo hi9Kq2x', [('hi9Kq2x', 'X')]) == 'echo [secret:X]' and m('echo hi', [('hi', 'X')]) == 'echo hi', 'known values replaced by name (values shorter than the masking minimum are left alone)')
# Short password options of particular commands: the value is gone, the command stays readable.
for cmd, shown in (
        ('mysql -u root -pS3cretPw1 appdb', 'mysql -u root -p[redacted] appdb'),
        ('mysql -u root -p S3cretPw1 appdb', 'mysql -u root -p [redacted] appdb'),
        ("mariadb -u root -p'my S3cret' appdb", 'mariadb -u root -p[redacted] appdb'),
        ('mysqldump -u root -pS3cretPw1 appdb > /tmp/d.sql', 'mysqldump -u root -p[redacted] appdb > /tmp/d.sql'),
        ('curl -u admin:S3cretPw1 https://x.lan/api', 'curl -u admin:[redacted] https://x.lan/api'),
        ('curl -s --user admin:S3cretPw1 https://x.lan', 'curl -s --user admin:[redacted] https://x.lan'),
        ('curl --user=admin:S3cretPw1 https://x.lan', 'curl --user=admin:[redacted] https://x.lan'),
        ('sshpass -p S3cretPw1 ssh root@host uptime', 'sshpass -p [redacted] ssh root@host uptime'),
        ('sshpass -pS3cretPw1 ssh root@host', 'sshpass -p[redacted] ssh root@host'),
        ('redis-cli -h cache -a S3cretPw1 ping', 'redis-cli -h cache -a [redacted] ping'),
        ('redis-cli --pass S3cretPw1 ping', 'redis-cli --pass [redacted] ping'),
        ('docker login -u me -p S3cretPw1 reg.lan', 'docker login -u me -p [redacted] reg.lan'),
        ('docker login --password S3cretPw1 reg.lan', 'docker login --password [redacted] reg.lan'),
        ('htpasswd -b /etc/nginx/.htpasswd admin S3cretPw1', 'htpasswd -b /etc/nginx/.htpasswd admin [redacted]'),
        ('htpasswd -bc /etc/nginx/.htpasswd admin S3cretPw1 && nginx -s reload', 'htpasswd -bc /etc/nginx/.htpasswd admin [redacted] && nginx -s reload'),
        ('htpasswd -nb admin S3cretPw1', 'htpasswd -nb admin [redacted]'),
        ('PGPASSWORD=S3cretPw1 psql -h db -U app', 'PGPASSWORD=[redacted] psql -h db -U app'),
        ('MYSQL_PWD=S3cretPw1 mysql -u root appdb', 'MYSQL_PWD=[redacted] mysql -u root appdb')):
    got = m(cmd)
    check(got == shown and 'S3cret' not in got, f'masked: {cmd.split()[0]} -> {got}')
for cmd in ('echo "$T" | docker login --password-stdin -u me reg.lan', 'docker run -p 8080:80 nginx', 'mkdir -p /var/lib/mysql/data',
            'mysql -u root -p --database appdb', 'mysql -u root -p{{secret:DB_ROOT}} appdb', 'htpasswd -B -c /etc/htpasswd admin'):
    check(m(cmd) == cmd, f'left alone: {cmd}')
# The gateway's GitHub token and Infisical client secrets from its environment are replaced by name.
os.environ.update({'GITHUB_TOKEN': 'plainGitHubTokenValue42', 'INFISICAL_CLIENT_SECRET': 'infisicalClientSecret77',
                   'INFISICAL_GENERATED_CLIENT_SECRET': 'generatedClientSecret99'})
known = srv.known_secrets()
masked = m('git clone https://x:plainGitHubTokenValue42@h/r; echo infisicalClientSecret77 generatedClientSecret99', known)
check('plainGitHubTokenValue42' not in masked and 'infisicalClientSecret77' not in masked and 'generatedClientSecret99' not in masked
      and '[secret:INFISICAL_CLIENT_SECRET]' in masked and '[secret:INFISICAL_GENERATED_CLIENT_SECRET]' in masked,
      'GITHUB_TOKEN and Infisical client secrets are known values: ' + masked)
check(m('token is plainGitHubTokenValue42', known) == 'token is [secret:GITHUB_TOKEN]', 'GITHUB_TOKEN replaced by name')
for k in ('GITHUB_TOKEN', 'INFISICAL_CLIENT_SECRET', 'INFISICAL_GENERATED_CLIENT_SECRET'):
    os.environ.pop(k)

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
httpd.shutdown()
sys.exit(1 if failures else 0)
