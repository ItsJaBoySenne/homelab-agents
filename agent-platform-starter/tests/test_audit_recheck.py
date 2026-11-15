"""Audit log proxy (HLA_AUDIT_V1) and the weekly re-check of stale facts (HLA_FACT_RECHECK_V1).

Real app on a throwaway Postgres; the gateway's audit read runs the real ssh-gateway/gateway/audit.py on an
in-memory SQLite, its /v1/execute is mocked. Run like test_thread_approval.py:
TEST_DATABASE_URL=... I_UNDERSTAND_THIS_WIPES_TEST_DB=1 python tests/test_audit_recheck.py
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import timedelta

TEST_DB = os.environ.get('TEST_DATABASE_URL')
if not TEST_DB or os.environ.get('I_UNDERSTAND_THIS_WIPES_TEST_DB') != '1':
    sys.exit('Set TEST_DATABASE_URL to a throwaway Postgres and I_UNDERSTAND_THIS_WIPES_TEST_DB=1')
os.environ.update({
    'DATABASE_URL': TEST_DB, 'AGENT_PLATFORM_TOKEN': 't' * 64, 'DASHBOARD_PASSWORD': 'pw',
    'TYPESAFE_API_KEY': 'x', 'HLA_RISK_REVIEW': 'true', 'OPENROUTER_API_KEY': 'x',
    'OPENROUTER_WORKER_MODEL': 'base/model', 'AGENT_ALLOWED_MODELS': 'base/model,other/model',
    'TOOL_GATEWAY_TOKEN': 'g' * 40,
})
for k in ('HLA_FACT_RECHECK', 'POLICY_ADMIN_TOKEN', 'SSH_TOOL_AGENT_ID'):
    os.environ.pop(k, None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'ssh-gateway'))

import httpx
import psycopg
from psycopg.rows import dict_row
from gateway import audit as gw_audit

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as db:
    db.execute('DROP SCHEMA public CASCADE'); db.execute('CREATE SCHEMA public')

# The gateway's audit table, as gateway/cli.py creates it, plus the HLA_AUDIT_V1 columns.
GW = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
GW.execute('CREATE TABLE audit (id INTEGER PRIMARY KEY, timestamp INTEGER, server TEXT, action TEXT, digest TEXT, exit_code INTEGER, timed_out INTEGER, truncated INTEGER, actor TEXT)')
gw_audit.migrate(GW)

STATE = {'audit_calls': [], 'execute': [], 'replies': {}, 'audit_status': None, 'down': False}

async def handler(request):
    if request.url.host == 'ssh-gateway-api' and STATE['down']:
        raise httpx.ConnectError('down')
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/audit':
        params = dict(request.url.params)
        STATE['audit_calls'].append((request.headers.get('authorization'), params))
        if STATE['audit_status']:
            return httpx.Response(STATE['audit_status'], json={'error': 'invalid_query', 'detail': 'limit must be 1..200'})
        try:
            return httpx.Response(200, json=gw_audit.query(GW, params))
        except ValueError as exc:
            return httpx.Response(400, json={'error': 'invalid_query', 'detail': str(exc)})
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/execute':
        sent = json.loads(request.content)
        STATE['execute'].append((request.headers.get('authorization'), sent))
        status, reply = STATE['replies'].get((sent['server'], sent.get('diagnostic')), (200, {'exit_code': 0, 'stdout': 'same', 'timed_out': False, 'truncated': False}))
        return httpx.Response(status, json={'server': sent['server'], **reply})
    return httpx.Response(503, json={'error': 'offline in test'})

_Real = httpx.AsyncClient
class MockAsync(_Real):
    def __init__(self, *a, **kw):
        kw['transport'] = httpx.MockTransport(handler); super().__init__(*a, **kw)
httpx.AsyncClient = MockAsync

import app as appmod
import fact_recheck
from memory import store as mstore
from fastapi.testclient import TestClient

H = {'x-agent-ui': '1', 'Content-Type': 'application/json'}
failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)
def q(sql, *args):
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        return db.execute(sql, args).fetchall()
def run_sql(sql, *args):
    with psycopg.connect(os.environ['DATABASE_URL']) as db:
        db.execute(sql, args); db.commit()

with TestClient(appmod.app) as c:
    check(c.get('/ui/api/audit').status_code == 401, 'audit: needs a dashboard session')
    check(c.post('/ui/api/login', json={'password': 'pw'}).status_code == 200, 'login')
    agent, gone = uuid.uuid4(), str(uuid.uuid4())
    run_sql("INSERT INTO agents(id,name,workspace,model) VALUES (%s,'Infra Agent','home','base/model')", agent)
    tid = c.post('/ui/api/threads', headers=H, json={'title': 'Disk check', 'workspace': 'home'}).json()['id']
    task, orphan = uuid.uuid4(), uuid.uuid4()
    run_sql("INSERT INTO tasks(id,task_text,status) VALUES (%s,'check disks','completed')", task)
    run_sql("INSERT INTO agent_thread_messages(id,thread_id,role,content,task_id) VALUES (%s,%s,'user','check disks',%s)", uuid.uuid4(), tid, task)
    run_sql("INSERT INTO tasks(id,task_text,status) VALUES (%s,'old run','completed')", orphan)
    now = int(time.time())
    rows = [(now - 7200, 'dev', 'agent_diagnostic', 0, None, str(agent), 'disk', str(task)),
            (now - 3600, 'jellyfin', 'agent_denied', None, None, gone, 'sudo reboot', str(orphan)),
            (now - 60, 'dev', 'chat_approval', None, 'jordan', None, 'systemctl restart nginx', None)]
    for i in range(60):  # older activity first: ids grow with time, like in the gateway
        GW.execute("INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated,agent_id,detail) VALUES (?,?,?,?,0,0,0,?,?)",
                   (now - 86400 * 3 + i, 'docker', 'agent_diagnostic', 'ef' * 32, str(agent), 'containers'))
    for ts, server, action, code, actor, ag, detail, tk in rows:
        GW.execute('INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated,actor,agent_id,detail,task_id) VALUES (?,?,?,?,?,0,0,?,?,?,?)',
                   (ts, server, action, 'cd' * 32, code, actor, ag, detail, tk))

    # 1. Rows with names and links, facets for the filters, the tool token without an admin token.
    r = c.get('/ui/api/audit?limit=3')
    body = r.json()
    check(r.status_code == 200 and [x['action'] for x in body['rows']] == ['chat_approval', 'agent_denied', 'agent_diagnostic'], 'audit: newest first')
    auth, params = STATE['audit_calls'][-1]
    check(auth == 'Bearer ' + 'g' * 40 and params.get('facets') == '1' and params.get('limit') == '3', 'audit: tool token when no admin token; facets on the first page')
    diag = body['rows'][2]
    check(diag['agent_name'] == 'Infra Agent' and diag['thread_id'] == tid and diag['task_known'] and diag['time'].endswith('+00:00'),
          'audit: agent name, conversation of the task, ISO time')
    denied = body['rows'][1]
    check(denied['agent_name'] is None and denied['thread_id'] is None and denied['task_known'], 'audit: a task without a conversation still links to the task')
    f = body['facets']
    check(f['servers'] == ['dev', 'docker', 'jellyfin'] and {'chat_approval', 'agent_denied', 'agent_diagnostic'} <= set(f['actions'])
          and {'id': str(agent), 'name': 'Infra Agent'} in f['agents'] and any(a['id'] == gone and a['name'].startswith('Deleted agent') for a in f['agents']),
          'audit: facets list servers, actions, agents (deleted ones too)')
    check(body['next_before_id'] == body['rows'][-1]['id'], 'audit: next cursor')

    # 2. Paging and filters are passed on; later pages skip the facets.
    page2 = c.get(f"/ui/api/audit?limit=50&before_id={body['next_before_id']}").json()
    check(len(page2['rows']) == 50 and 'facets' not in page2 and 'facets' not in STATE['audit_calls'][-1][1], 'audit: next page without facets')
    r = c.get(f'/ui/api/audit?agent={agent}&server=dev&action=agent_diagnostic&q=disk&since={now - 86400}&until={now}')
    check(r.status_code == 200 and len(r.json()['rows']) == 1 and r.json()['rows'][0]['detail'] == 'disk', 'audit: filters agent, server, action, text and dates')
    check(STATE['audit_calls'][-1][1] == {'agent': str(agent), 'server': 'dev', 'action': 'agent_diagnostic', 'q': 'disk', 'since': str(now - 86400),
                                           'until': str(now), 'limit': '50', 'facets': '1'}, 'audit: only the given filters reach the gateway')
    check(c.get('/ui/api/audit?agent=none').json()['rows'][0]['action'] == 'chat_approval', 'audit: rows without an agent')
    for url, why in (('limit=201', 'limit over 200'), ('agent=bob', 'agent not an id'), ('action=a;b', 'odd action'), ('server=a%20b', 'odd server'),
                     ('since=yesterday', 'since not a number')):
        check(c.get('/ui/api/audit?' + url).status_code == 422, 'audit: refused ' + why)
    STATE['audit_status'] = 400
    check(c.get('/ui/api/audit').status_code == 422, 'audit: a gateway refusal reads as a validation error')
    STATE['audit_status'] = 404
    r = c.get('/ui/api/audit')
    check(r.status_code == 502 and 'older' in r.json()['detail'], 'audit: an old gateway says so')
    STATE['audit_status'] = None
    STATE['down'] = True
    check(c.get('/ui/api/audit').status_code == 502, 'audit: gateway down is a 502')
    STATE['down'] = False
    os.environ['POLICY_ADMIN_TOKEN'] = 'p' * 40
    c.get('/ui/api/audit')
    check(STATE['audit_calls'][-1][0] == 'Bearer ' + 'p' * 40, 'audit: the admin token is used when it is set')
    os.environ.pop('POLICY_ADMIN_TOKEN')

    # 3. Fact re-check: only stale diagnostic facts that say how to run them again.
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        save = lambda key, fact, server, ag=agent, ttl=1, source='ssh-gateway:diagnostic', kind='diagnostic', ws='home', task_id=None: mstore.save(
            db, ws, key, fact, source, True, kind=kind, server=server, agent_id=ag, ttl_hours=ttl, task_id=task_id)
        save('diag:dev:disk', 'disk on dev:\nsame', 'dev', task_id=task)
        save('diag:fs-01:path-permissions:/srv/files/shared', 'path-permissions of /srv/files/shared on fs-01:\nold', 'fs-01')
        save('diag:jellyfin:system', 'system on jellyfin:\nup', 'jellyfin')
        save('diag:omada:failed-units', 'failed-units on omada:\nnone', 'omada', ag=None)
        save('diag:traefik:disk', 'disk on traefik:\nx', 'traefik')
        save('diag:docker:identity', 'identity on docker:\nfresh', 'docker', ttl=720)
        save('diag:dev:container-logs:web', 'logs', 'dev')
        save('diag:dev:path-permissions', 'no target recorded', 'dev')
        save('op:dev:install_package:jq', 'Package jq is installed on dev.', 'dev', source='ssh-gateway:maintenance', kind='change')
        save('diag:dev:system', 'system on dev (work)', 'dev', ws='work')
        db.execute("UPDATE agent_memories SET expires_at=NOW()-interval '2 hours' WHERE key<>'diag:docker:identity'")
        db.commit()
    STATE['replies'] = {('dev', 'disk'): (200, {'exit_code': 0, 'stdout': 'same', 'timed_out': False, 'truncated': False}),
                        ('fs-01', 'path-permissions'): (200, {'exit_code': 0, 'stdout': 'new', 'timed_out': False, 'truncated': False}),
                        ('jellyfin', 'system'): (403, {'error': 'policy_denied'}),
                        ('traefik', 'disk'): (200, {'exit_code': 0, 'stdout': 'DB_PASSWORD=hunter2', 'timed_out': False, 'truncated': False})}
    st = c.get('/ui/api/facts/recheck').json()
    check(st['schedule'] == 'off' and st['eligible'] == 5 and st['last_run'] is None and st['max_per_run'] == 20, 'recheck: off by default; 5 facts can be re-run')
    check(c.post('/ui/api/facts/recheck').status_code == 403, 'recheck: needs the dashboard write header')
    STATE['execute'].clear()
    r = c.post('/ui/api/facts/recheck', headers=H)
    res = r.json()
    check(r.status_code == 200 and res['status'] == 'finished' and (res['checked'], res['confirmed'], res['changed'], res['failed']) == (5, 1, 1, 3),
          'recheck: checked 5, confirmed 1, changed 1, failed 3 ' + json.dumps(res))
    sent = [s for _, s in STATE['execute']]
    check(all(s['mode'] == 'diagnostic' and set(s) <= {'agent_id', 'server', 'mode', 'diagnostic', 'target'} for s in sent) and len(sent) == 4
          and {'agent_id': str(agent), 'server': 'fs-01', 'mode': 'diagnostic', 'diagnostic': 'path-permissions', 'target': '/srv/files/shared'} in sent
          and all(a == 'Bearer ' + 'g' * 40 for a, _ in STATE['execute']), 'recheck: only read-only diagnostics, as the recorded agent, with the target')
    facts = {f['key']: f for f in c.get('/ui/api/facts?limit=50').json()['facts']}
    d = facts['diag:dev:disk']
    check(d['freshness'] == 'fresh' and d['recheck_outcome'] == 'confirmed' and d['rechecked_at'] and d['task_id'] == str(task), 'recheck: unchanged fact fresh again, task link kept')
    p = facts['diag:fs-01:path-permissions:/srv/files/shared']
    check(p['freshness'] == 'fresh' and p['recheck_outcome'] == 'changed' and p['fact'].endswith('new') and p['previous_fact'].endswith('old'), 'recheck: changed fact keeps the previous value')
    j = facts['diag:jellyfin:system']
    check(j['freshness'] == 'stale' and j['recheck_outcome'] == 'failed: policy_denied', 'recheck: a refusal leaves the fact stale and says why')
    check(facts['diag:omada:failed-units']['recheck_outcome'] == 'failed: no agent recorded to run it as', 'recheck: no agent, not run')
    t = facts['diag:traefik:disk']
    check(t['freshness'] == 'stale' and 'secret' in t['recheck_outcome'] and 'hunter2' not in json.dumps(facts), 'recheck: output that may hold a secret is not stored')
    check(facts['diag:dev:container-logs:web']['recheck_outcome'] is None and facts['diag:dev:path-permissions']['recheck_outcome'] is None
          and facts['op:dev:install_package:jq']['recheck_outcome'] is None and facts['diag:docker:identity']['recheck_outcome'] is None,
          'recheck: logs, target-less, change and fresh facts are left alone')
    check(q("SELECT recheck_outcome FROM agent_memories WHERE workspace='work'")[0]['recheck_outcome'] is None, 'recheck: home workspace only')
    st = c.get('/ui/api/facts/recheck').json()
    check(st['last_run']['trigger'] == 'manual' and st['last_run']['checked'] == 5 and st['last_run']['finished_at'] and not st['running'], 'recheck: the run is recorded')
    STATE['execute'].clear()
    res = c.post('/ui/api/facts/recheck', headers=H).json()
    check(res['checked'] == 0 and STATE['execute'] == [], 'recheck: facts just re-checked are not run again at once')

    # 4. Throttle: at most 20 per pass, oldest expiry first.
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        for i in range(25):
            mstore.save(db, 'home', f'diag:dev:path-permissions:/srv/t{i}', f'p{i}', 'ssh-gateway:diagnostic', True, kind='diagnostic', server='dev',
                        agent_id=agent, ttl_hours=1)
            db.execute("UPDATE agent_memories SET expires_at=NOW()-make_interval(hours => %s) WHERE key=%s", (30 - i, f'diag:dev:path-permissions:/srv/t{i}'))
        db.commit()
    STATE['execute'].clear()
    res = c.post('/ui/api/facts/recheck', headers=H).json()
    targets = [s['target'] for _, s in STATE['execute']]
    check(res['checked'] == 20 and targets[0] == '/srv/t0' and '/srv/t24' not in targets, 'recheck: at most 20 per pass, oldest first')

    # 5. Weekly schedule: due when no weekly pass ran in 7 days; HLA_FACT_RECHECK=weekly switches it on.
    with appmod.database_connection() as db:
        check(fact_recheck.due(db), 'weekly: due without an earlier weekly pass')
    os.environ['HLA_FACT_RECHECK'] = 'weekly'
    check(fact_recheck.enabled() and c.get('/ui/api/facts/recheck').json()['schedule'] == 'weekly', 'weekly: switched on by HLA_FACT_RECHECK=weekly')
    STATE['execute'].clear()
    res = asyncio.run(fact_recheck.recheck(appmod.database_connection, 'weekly', min_age=timedelta(days=1)))
    check(res['trigger'] == 'weekly' and res['checked'] == 5 and {s['target'] for _, s in STATE['execute']} == {f'/srv/t{i}' for i in range(20, 25)},
          'weekly: the pass checks what is left, skipping facts re-checked within a day')
    with appmod.database_connection() as db:
        check(not fact_recheck.due(db), 'weekly: not due again within the week')
        db.execute("UPDATE fact_recheck_runs SET started_at=NOW()-interval '8 days' WHERE trigger='weekly'")
        check(fact_recheck.due(db), 'weekly: due again after 7 days')
        db.rollback()
    os.environ['HLA_FACT_RECHECK'] = 'monthly'
    check(not fact_recheck.enabled(), 'weekly: any other value leaves it off')
    os.environ.pop('HLA_FACT_RECHECK')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED: {failures}'))
sys.exit(1 if failures else 0)
