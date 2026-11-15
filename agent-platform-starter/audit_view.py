"""The SSH gateway's audit log in the dashboard (HLA_AUDIT_V1).

GET /ui/api/audit passes the filters to the gateway's GET /v1/audit (which masks secrets), then adds what only
the platform knows: agent names, and the conversation of the task a row belongs to. The gateway is read with
POLICY_ADMIN_TOKEN when it is set (the token that already may read everything there), otherwise with
TOOL_GATEWAY_TOKEN; the gateway accepts either for reads, like /v1/policy.
"""
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

GATEWAY_URL = 'http://ssh-gateway-api:8099'
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ACTION = re.compile(r'^[a-z_]{1,40}\*?$')
_SERVER = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')  # mcp:<name> services too


def _token():
    admin = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(admin) >= 32:
        return admin
    tool = os.getenv('TOOL_GATEWAY_TOKEN', '')
    if len(tool) < 32:
        raise HTTPException(503, 'Missing server setting: TOOL_GATEWAY_TOKEN')
    return tool


def build_router(connect, dashboard_read):
    router = APIRouter(prefix='/ui/api', tags=['audit'])

    @router.get('/audit')
    async def audit(request: Request, agent: str = '', server: str = '', action: str = '', q: str = '',
                    since: int | None = None, until: int | None = None, before_id: int | None = None, limit: int = 50):
        dashboard_read(request)
        if not 1 <= limit <= 200:
            raise HTTPException(422, 'limit must be 1..200')
        if agent and agent != 'none' and not _UUID.match(agent):
            raise HTTPException(422, 'agent must be an agent id')
        if server and not _SERVER.match(server):
            raise HTTPException(422, 'Invalid server')
        if action and not _ACTION.match(action):
            raise HTTPException(422, 'Invalid action')
        if len(q) > 200:
            raise HTTPException(422, 'Search text is at most 200 characters')
        params = {k: v for k, v in {'agent': agent, 'server': server, 'action': action, 'q': q.strip(), 'since': since,
                                    'until': until, 'before_id': before_id, 'limit': limit}.items() if v not in (None, '')}
        if before_id is None:
            params['facets'] = 1
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(GATEWAY_URL + '/v1/audit', params=params, timeout=10,
                                            headers={'Authorization': 'Bearer ' + _token()})
            except httpx.HTTPError:
                raise HTTPException(502, 'SSH gateway unreachable; the audit log cannot be shown') from None
        if response.status_code == 400:
            raise HTTPException(422, response.json().get('detail') or 'Invalid filter')
        if response.status_code == 404:
            raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to read the audit log')
        if response.status_code != 200:
            raise HTTPException(502, f'SSH gateway refused the audit read (HTTP {response.status_code})')
        body = response.json()
        rows = body.get('rows') or []
        facets = body.get('facets')
        agent_ids = {r['agent_id'] for r in rows if r.get('agent_id')} | set((facets or {}).get('agents') or [])
        task_ids = {r['task_id'] for r in rows if r.get('task_id')}
        with connect() as db:
            names = {str(a['id']): a['name'] for a in db.execute('SELECT id,name FROM agents WHERE id = ANY(%s)',
                                                                ([uuid.UUID(a) for a in agent_ids],)).fetchall()} if agent_ids else {}
            tasks, threads = set(), {}
            if task_ids:
                ids = [uuid.UUID(t) for t in task_ids]
                tasks = {str(t['id']) for t in db.execute('SELECT id FROM tasks WHERE id = ANY(%s)', (ids,)).fetchall()}
                threads = {str(m['task_id']): str(m['thread_id']) for m in db.execute(
                    'SELECT DISTINCT ON (task_id) task_id,thread_id FROM agent_thread_messages WHERE task_id = ANY(%s) ORDER BY task_id,created_at',
                    (ids,)).fetchall()}
        for row in rows:
            row['time'] = datetime.fromtimestamp(row['timestamp'], timezone.utc).isoformat() if row.get('timestamp') else None
            row['agent_name'] = names.get(row.get('agent_id') or '')
            task = row.get('task_id')
            row['task_known'] = task in tasks
            row['thread_id'] = threads.get(task) if task else None
        result = {'rows': rows, 'next_before_id': body.get('next_before_id')}
        if facets is not None:
            # Every agent the platform knows, plus ids in the log whose agent was deleted since.
            with connect() as db:
                known = db.execute('SELECT id,name FROM agents ORDER BY name').fetchall()
            listed = [{'id': str(a['id']), 'name': a['name']} for a in known]
            seen = {a['id'] for a in listed}
            listed += [{'id': a, 'name': 'Deleted agent ' + a[:8]} for a in sorted(set(facets.get('agents') or []) - seen)]
            result['facets'] = {'servers': facets.get('servers') or [], 'actions': facets.get('actions') or [], 'agents': listed}
        return result

    return router
