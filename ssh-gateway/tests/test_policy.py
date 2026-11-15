"""Gateway API against its policy: real HTTP server and SQLite, SSH execution replaced by a stand-in.

python3 tests/test_policy.py   (needs paramiko importable; no network, nothing is executed remotely)
"""
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

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402

RAN = []
STDIN = []  # (stdin, max_output) per call
DEPLOY_OUT = {}  # server -> reply for deploy scripts
def fake_execute(server, command, timeout=30, max_output=12000, stdin=None):
    RAN.append((server, command, timeout)); STDIN.append((stdin, max_output))
    if 'HLA_BYTES' in command:
        return {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stdout': f'HLA_BYTES={len(stdin)}\n', 'stderr': ''}
    if 'HLA_SIZE' in command:
        return {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stdout': 'HLA_SIZE=20\n0123456789', 'stderr': ''}
    if 'HLA_SHA' in command:
        if DEPLOY_OUT.get('delay'): time.sleep(DEPLOY_OUT['delay'])
        return DEPLOY_OUT[server]
    return {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stdout': 'HLA_VERIFY=install ok installed|1.7.1-1\n', 'stderr': ''}
srv.execute = fake_execute

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT, OTHER = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'

def call(method, path, body=None, token='t' * 40):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def policy(change, actor='jordan'): return call('PUT', '/v1/policy', {'change': change, 'actor': actor}, token='a' * 40)
def run(agent, server, **kw): RAN.clear(); STDIN.clear(); return call('POST', '/v1/execute', {'agent_id': agent, 'server': server, **kw})
def approve(server, command):
    with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
        db.execute('INSERT OR REPLACE INTO approvals(digest,expires,used) VALUES (?,?,0)', (srv.digest(server, command), int(time.time()) + 600))
def last_audit():
    with sqlite3.connect(os.environ['GATEWAY_DB']) as db: return db.execute('SELECT server,action FROM audit ORDER BY id DESC LIMIT 1').fetchone()

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

# Seeded defaults match the pre-policy gateway; an agent without a policy can do nothing.
code, snap = call('GET', '/v1/policy')
check(code == 200 and snap['servers']['dev']['level'] == 5 and snap['servers']['sandbox']['level'] == 6
      and all(v['level'] == 1 for s, v in snap['servers'].items() if s not in ('dev', 'sandbox')), 'seeded: dev 5, sandbox 6, others 1')
check(snap['unhinged'] == {'active': False, 'until': None} and snap['agents'] == {}, 'seeded: unhinged off, no agents')
check(run(AGENT, 'dev', mode='diagnostic', diagnostic='identity')[1]['error'] == 'policy_denied' and RAN == [], 'agent without policy denied')

# Only the admin token changes policy; changes are validated and audited.
check(call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'level': 5}}}})[0] == 401, 'tool token cannot change policy')
check(policy({'agents': {AGENT: {'name': 'Infra', 'level': 9}}})[0] == 400, 'invalid level refused')
check(policy({'agents': {'not-a-uuid': {'level': 1}}})[0] == 400, 'non-UUID agent refused')
check(policy({'servers': {'dev': {'compose_projects': ['/srv/../etc']}}})[0] == 400, 'path traversal in compose project refused')
check(policy({'servers': {'secrets-vm': {'level': 5}}})[0] == 400, 'unknown server refused')
code, snap = policy({'agents': {AGENT: {'name': 'Infra', 'level': 5}, OTHER: {'name': 'Junior', 'level': 2}}})
check(code == 200 and snap['agents'][AGENT] == {'name': 'Infra', 'level': 5}, 'admin token sets agent levels')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    check(db.execute('SELECT actor FROM policy_audit ORDER BY id DESC').fetchone()[0] == 'jordan', 'policy change audited with actor')

# Effective level = min(agent, server).
code, eff = call('GET', f'/v1/policy/effective?agent_id={OTHER}')
check(eff['servers']['dev']['level'] == 2 and eff['servers']['jellyfin']['level'] == 1, 'effective = min(agent, server)')
check(run(AGENT, 'jellyfin', mode='diagnostic', diagnostic='disk')[0] == 200 and RAN[0][0] == 'jellyfin', 'level 1 server: diagnostics run')
check(run(AGENT, 'jellyfin', mode='maintenance', operation='install_package', target='jq')[1]['error'] == 'policy_denied', 'level 1 server: maintenance denied')
code, body = run(AGENT, 'jellyfin', mode='diagnostic', diagnostic='path-permissions', target='/srv/files/shared')
check(code == 200 and "p=/srv/files/shared" in RAN[0][1] and 'namei -l' in RAN[0][1], 'level 1: path investigation runs with the target quoted')
code, body = run(AGENT, 'jellyfin', mode='diagnostic', diagnostic='path-permissions', target='../etc')
check(code == 400 and 'absolute path' in body['detail'] and RAN == [], 'bad investigation target refused with reason')
check(run(OTHER, 'omada', mode='diagnostic', diagnostic='containers')[0] == 200, 'containers diagnostic at level 1')
code, body = run(OTHER, 'dev', mode='maintenance', operation='install_package', target='jq')
check(code == 200 and body['outcome']['verified'] and RAN[0][2] == 120, 'level 2 agent installs jq on dev with maintenance timeout')
check(run(OTHER, 'dev', mode='maintenance', operation='install_package', target='htop')[1]['error'] == 'policy_denied', 'level 2 agent cannot install htop (level 3)')
check(run(OTHER, 'dev', mode='maintenance', operation='restart_service', target='nginx')[1]['error'] == 'policy_denied', 'level 2 agent cannot restart nginx')
check(run(AGENT, 'dev', mode='maintenance', operation='compose_up', target='/srv/app')[1]['error'] == 'policy_denied', 'compose project not in list denied')
policy({'servers': {'dev': {'compose_projects': ['/srv/app']}}})
check(run(AGENT, 'dev', mode='maintenance', operation='compose_up', target='/srv/app')[0] == 200, 'listed compose project deploys at level 4+')

# Level 5: exact approved commands, single use; not on servers below 5.
check(run(AGENT, 'dev', mode='approved_command', command='sudo -n id')[1]['error'] == 'approval_required' and RAN == [], 'command without approval refused')
approve('dev', 'sudo -n id')
check(run(AGENT, 'dev', mode='approved_command', command='sudo -n id')[0] == 200 and RAN[0][1] == 'sudo -n id', 'approved command runs')
check(run(AGENT, 'dev', mode='approved_command', command='sudo -n id')[1]['error'] == 'approval_required', 'approval is single use')
approve('jellyfin', 'uptime')
check(run(AGENT, 'jellyfin', mode='approved_command', command='uptime')[1]['error'] == 'policy_denied', 'approved command needs server level 5')

# Unhinged: level-5 agents run anything without approval on servers at level >= 1.
check(policy({'unhinged': {'enabled': True, 'hours': 3}})[0] == 400, 'unhinged duration must be 1, 8, 24 or until off')
code, snap = policy({'unhinged': {'enabled': True, 'hours': 1}, 'servers': {'omada': {'level': 0}}})
check(snap['unhinged']['active'] and snap['unhinged']['until'] > time.time() + 3500, 'unhinged on for 1 hour')
code, body = run(AGENT, 'jellyfin', mode='command', command='sudo rm -rf /tmp/cache')
check(code == 200 and RAN[0][1] == 'sudo rm -rf /tmp/cache' and last_audit() == ('jellyfin', 'agent_unhinged'), 'unhinged: any command, no approval, audited')
check(run(AGENT, 'omada', mode='command', command='id')[1]['error'] == 'policy_denied', 'unhinged: server at level 0 stays out')
check(run(OTHER, 'jellyfin', mode='command', command='id')[1]['error'] == 'policy_denied', 'unhinged: only level-5 agents')
policy({'unhinged': {'enabled': False}})
check(run(AGENT, 'dev', mode='command', command='id')[1]['error'] == 'approval_required', 'unhinged off: approvals required again')
policy({'unhinged': {'enabled': True, 'hours': None}})
check(call('GET', '/v1/policy')[1]['unhinged'] == {'active': True, 'until': None}, 'unhinged until switched off')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    db.execute("UPDATE policy_settings SET value=? WHERE key='unhinged_until'", (int(time.time()) - 1,))
check(not call('GET', '/v1/policy')[1]['unhinged']['active'] and run(AGENT, 'dev', mode='command', command='id')[1]['error'] == 'approval_required', 'expired unhinged mode is off')

# Level 6: free shell where both agent and server are at 6 (the sandbox); level 5 elsewhere still asks.
check(run(AGENT, 'sandbox', mode='approved_command', command='uname -a')[1]['error'] == 'approval_required', 'level-5 agent on sandbox still needs approval')
policy({'agents': {AGENT: {'name': 'Infra', 'level': 6}}})
code, eff = call('GET', f'/v1/policy/effective?agent_id={AGENT}')
check(eff['servers']['sandbox'] == {'level': 6, 'unhinged': False, 'free_shell': True, 'compose_projects': [], 'apps': []}
      and eff['servers']['dev']['level'] == 5 and not eff['servers']['dev']['free_shell'], 'level 6 only where the server is 6')
code, body = run(AGENT, 'sandbox', mode='approved_command', command='apt-get install -y build-essential')
check(code == 200 and RAN[0] == ('sandbox', 'apt-get install -y build-essential', 300) and last_audit() == ('sandbox', 'agent_free_shell'), 'free shell: no approval, 300 s timeout, audited')
check(run(AGENT, 'dev', mode='approved_command', command='id')[1]['error'] == 'approval_required', 'level-6 agent still needs approval on dev (5)')
check(run(AGENT, 'dev', mode='diagnostic', diagnostic='identity')[0] == 200 and RAN[0][2] == 40, 'diagnostics keep the 40 s timeout')
check(policy({'servers': {'sandbox': {'level': 7}}})[0] == 400, 'level 7 refused')
policy({'servers': {'sandbox': {'level': 0}}})
check(run(AGENT, 'sandbox', mode='approved_command', command='id')[1]['error'] == 'policy_denied', 'sandbox at level 0 is off limits')

check(call('GET', '/v1/policy', token='wrong' * 10)[0] == 401, 'policy read needs a token')

# Approve-in-chat: the admin token grants one execution of one exact command; the agent's level still applies.
def grant(server, command, actor='jordan', token='a' * 40):
    return call('POST', '/v1/approvals', {'server': server, 'command': command, 'actor': actor}, token=token)
FIX = 'chmod g+w /srv/files/shared'
check(grant('dev', FIX, token='t' * 40)[0] == 401, 'tool token cannot grant approvals')
check(grant('nope', FIX)[1]['error'] == 'server_not_enabled' and grant('dev', '')[1]['error'] == 'invalid_command'
      and grant('dev', FIX, actor='')[1]['error'] == 'actor_required', 'grant validates server, command and actor')
code, body = grant('dev', FIX)
check(code == 200 and body['expires_in'] == 300, 'grant accepted')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    check(db.execute("SELECT action,actor FROM audit WHERE action='chat_approval' ORDER BY id DESC").fetchone() == ('chat_approval', 'jordan'), 'grant audited with actor')
check(run(AGENT, 'dev', mode='approved_command', command=FIX + ' ')[1]['error'] == 'approval_required', 'grant covers only the exact command')
code, body = run(AGENT, 'dev', mode='approved_command', command=FIX)
check(code == 200 and RAN[0] == ('dev', FIX, 40) and last_audit() == ('dev', 'agent_approved_command'), 'granted command runs once')
check(run(AGENT, 'dev', mode='approved_command', command=FIX)[1]['error'] == 'approval_required', 'grant is single-use')
grant('dev', FIX)
check(run(OTHER, 'dev', mode='approved_command', command=FIX)[1]['error'] == 'policy_denied', 'grant does not lift the policy level')

# Deployments: registered apps only, level 4 on the app's server, staging first, rollback admin-only to known commits.
A_SHA, B_SHA = 'a' * 40, 'b' * 40
SITE = {'server': 'public-website', 'path': '/opt/site', 'repo': 'git@github.com:me/site.git', 'health_url': 'http://127.0.0.1:8080/', 'staging': True}
for bad, why in (({**SITE, 'server': 'sandbox'}, 'sandbox target'), ({**SITE, 'path': '/opt/../etc'}, 'path with ..'),
                 ({**SITE, 'repo': 'git@github.com:me/site.git; rm -rf /'}, 'repo with shell'), ({**SITE, 'health_url': 'http://192.168.1.5/'}, 'non-local health URL')):
    check(policy({'apps': {'site': bad}})[0] == 400, f'app refused: {why}')
check(policy({'apps': {'Site!': SITE}})[0] == 400, 'app refused: bad name')
code, snap = policy({'apps': {'site': SITE}})
check(code == 200 and snap['apps']['site']['path'] == '/opt/site' and snap['apps']['site']['staging'] is True, 'app registered')
def dep(token='t' * 40, **kw): return call('POST', '/v1/deploy', kw, token=token)
STAGED = {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stderr': '', 'stdout': f'HLA_SHA={B_SHA}\nHLA_VERIFY=running\n'}
DEPLOYED = {**STAGED, 'stdout': f'HLA_FROM={A_SHA}\nHLA_SHA={B_SHA}\nHLA_VERIFY=running\n'}
DEPLOY_OUT.update({'sandbox': STAGED, 'public-website': DEPLOYED})
policy({'servers': {'sandbox': {'level': 6}}})  # set to 0 by an earlier check
check(dep(agent_id=AGENT, app='site', ref='main')[1]['error'] == 'policy_denied', 'agent below level 4 on the server cannot deploy')
policy({'servers': {'public-website': {'level': 4}}})
check(dep(agent_id=AGENT, app='nope', ref='main')[0] == 404 and dep(agent_id=AGENT, app='site', ref='main;id')[1]['error'] == 'invalid_ref', 'unknown app and bad ref refused')
RAN.clear()
code, body = dep(agent_id=AGENT, app='site', ref='main')
check(code == 200 and body['status'] == 'deployed' and body['sha'] == B_SHA and body['from'] == A_SHA and [r[0] for r in RAN] == ['sandbox', 'public-website']
      and f'c={B_SHA};' in RAN[1][1], 'agent deploy: staged on the sandbox, then exactly that commit on the target')
check(dep(agent_id=AGENT, app='site', sha=A_SHA)[1]['error'] == 'rollback_is_admin_only', 'agents cannot skip staging')
check(dep(token='a' * 40, app='site', sha='c' * 40, actor='jordan')[1]['error'] == 'unknown_commit', 'rollback only to a commit that ran before')
RAN.clear()
DEPLOY_OUT['public-website'] = {**DEPLOYED, 'stdout': f'HLA_FROM={B_SHA}\nHLA_SHA={A_SHA}\nHLA_VERIFY=running\n'}
code, body = dep(token='a' * 40, app='site', sha=A_SHA, actor='jordan')
check(code == 200 and body['status'] == 'deployed' and [r[0] for r in RAN] == ['public-website'], 'admin rollback to the previous commit skips staging')
DEPLOY_OUT['public-website'] = {**DEPLOYED, 'exit_code': 1, 'stdout': f'HLA_FROM={A_SHA}\nHLA_SHA={B_SHA}\nHLA_VERIFY=unhealthy\nHLA_ROLLBACK=ok\n'}
code, body = dep(agent_id=AGENT, app='site', ref='main')
check(body['status'] == 'rolled_back' and body['error_class'] == 'unhealthy' and not body['verified'], 'failed deploy reported as rolled back')
DEPLOY_OUT['sandbox'] = {**STAGED, 'exit_code': 1, 'stdout': 'HLA_VERIFY=fetch_failed\n'}
code, body = dep(agent_id=AGENT, app='site', ref='main')
check(body['status'] == 'staging_failed' and body['error_class'] == 'staging_fetch_failed', 'staging failure stops the deploy')
DEPLOY_OUT.update({'sandbox': STAGED, 'public-website': DEPLOYED})
import threading as _t
DEPLOY_OUT['delay'] = 0.5
first = _t.Thread(target=lambda: dep(agent_id=AGENT, app='site', ref='main')); first.start(); time.sleep(0.15)
check(dep(agent_id=AGENT, app='site', ref='main')[1]['error'] == 'deploy_in_progress', 'one deployment per app at a time')
first.join(); DEPLOY_OUT['delay'] = 0
history = call('GET', '/v1/deployments?app=site')[1]['deployments']
check([h['status'] for h in history[:4]] == ['deployed', 'staging_failed', 'rolled_back', 'deployed'] and history[3]['requested_by'] == 'jordan'
      and history[0]['requested_by'] == 'agent:' + AGENT and history[0]['result']['staging']['verified'], 'deployment history with who and what')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    check(db.execute("SELECT actor FROM audit WHERE action='deploy_deployed' ORDER BY id DESC").fetchone()[0] == 'agent:' + AGENT, 'deployments audited')
code, eff = call('GET', f'/v1/policy/effective?agent_id={AGENT}')
check(eff['servers']['public-website']['apps'] == ['site'], 'effective policy lists deployable apps')
policy({'servers': {'sandbox': {'level': 0}}})
check(dep(agent_id=AGENT, app='site', ref='main')[1]['error'] == 'staging_unavailable', 'no deploy with staging while the sandbox is off limits')
policy({'servers': {'sandbox': {'level': 6}}})

# File tools (HLA_FILES_V1): only with a free shell; content goes on stdin, never into the command.
import hashlib
PAGE = '<!doctype html>\n<p>"$(reboot)" \'x\' €</p>\n' + 'x' * 200 * 1024  # ~200 KiB, above the old 16 KiB body limit
code, body = run(AGENT, 'sandbox', mode='write_file', path='/srv/www/site/index.html', content=PAGE)
check(code == 200 and body['exit_code'] == 0 and body['bytes'] == len(PAGE.encode()) and body['sha256'] == hashlib.sha256(PAGE.encode()).hexdigest()
      and body['path'] == '/srv/www/site/index.html' and body['server'] == 'sandbox' and 'stderr' in body, 'free shell: 200 KiB write_file accepted')
check(STDIN[0][0] == PAGE.encode() and 'reboot' not in RAN[0][1] and 'x' * 50 not in RAN[0][1] and RAN[0][2] == 60
      and "p=/srv/www/site/index.html" in RAN[0][1] and 'cat > "$p"' in RAN[0][1], 'content passed via stdin, not in the command; 60 s timeout')
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    row = db.execute('SELECT server,action,digest,exit_code FROM audit ORDER BY id DESC LIMIT 1').fetchone()
check(row == ('sandbox', 'agent_write_file', srv.digest('sandbox', 'write_file:/srv/www/site/index.html:' + hashlib.sha256(PAGE.encode()).hexdigest()), 0),
      'write audited with path and content hash')
run(AGENT, 'sandbox', mode='write_file', path='/tmp/log', content='more', append=True)
check('cat >> "$p"' in RAN[0][1], 'append uses >>')
code, body = run(AGENT, 'sandbox', mode='read_file', path='/etc/hostname', offset=10, length=10)
check(code == 200 and body['size'] == 20 and body['offset'] == 10 and body['content'] == '0123456789' and body['eof'] is True
      and body['exit_code'] == 0 and STDIN[0] == (None, 14000) and 'tail -c +11' in RAN[0][1] and 'head -c 10' in RAN[0][1], 'read_file: size, offset, content, eof')
check(run(AGENT, 'sandbox', mode='read_file', path='/etc/hostname', length=5)[1]['eof'] is False, 'read_file: eof false when more remains')
check(last_audit() == ('sandbox', 'agent_read_file'), 'read audited')
code, body = run(AGENT, 'dev', mode='write_file', path='/tmp/x', content='x')
check(code == 403 and body == {'error': 'file_tools_need_free_shell', 'server': 'dev', 'level': 5} and RAN == [], 'level 5 without free shell: file_tools_need_free_shell')
check(run(AGENT, 'dev', mode='read_file', path='/tmp/x')[1]['error'] == 'file_tools_need_free_shell', 'read_file needs free shell too')
check(run(OTHER, 'jellyfin', mode='read_file', path='/tmp/x')[1]['error'] == 'file_tools_need_free_shell', 'low level agent: no file tools')
check(run('33333333-3333-4333-8333-333333333333', 'sandbox', mode='read_file', path='/tmp/x')[1]['error'] == 'policy_denied', 'unknown agent: policy_denied')
policy({'unhinged': {'enabled': True, 'hours': 1}})
code, body = run(AGENT, 'dev', mode='write_file', path='/tmp/x', content='x')
check(code == 200 and RAN[0][0] == 'dev' and last_audit() == ('dev', 'agent_write_file'), 'unhinged mode allows file tools')
policy({'unhinged': {'enabled': False}})
for kw, why in (({'path': 'relative/x', 'content': 'x'}, 'relative path'), ({'path': '/a\nb', 'content': 'x'}, 'control character'),
                ({'path': '/x', 'content': 'x' * (256 * 1024 + 1)}, 'content over 256 KiB'), ({'path': '/x', 'content': None}, 'content missing')):
    code, body = run(AGENT, 'sandbox', mode='write_file', **kw)
    check(code == 400 and body['error'] == 'invalid_file_request' and body['detail'] and RAN == [], f'write_file refused: {why}')
code, body = run(AGENT, 'sandbox', mode='read_file', path='/x', length=12001)
check(code == 400 and body['error'] == 'invalid_file_request' and 'length' in body['detail'], 'read_file refused: length over 12000')
check(run(AGENT, 'sandbox', mode='command', command='x' * 20000)[1]['error'] == 'invalid_command', 'commands keep the 2048-character limit')
check(call('POST', '/v1/approvals', {'server': 'dev', 'command': 'x' * 20000, 'actor': 'jordan'}, token='a' * 40)[1]['error'] == 'invalid_payload', 'other endpoints keep the 16 KiB body limit')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
httpd.shutdown()
sys.exit(1 if failures else 0)
