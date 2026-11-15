"""Domains and public routes on the dashboard (HLA_ROUTES_V1): the "Domains & routes" panel of the Websites view.

The SSH gateway owns the routes (gateway/routes.py): it keeps them in its database, renders the one Traefik file it owns
(hla-managed.yml), uploads it, checks it through the Traefik API and rolls back when Traefik does not take it. This module
only forwards, with POLICY_ADMIN_TOKEN. Writes need the UI write header and an approver (approvals.require_approver).
Agents ask for a route with the request_route tool; their pending requests appear in "Needs you" (kind route_request).

GET    /ui/api/routes                         managed routes (DNS, Traefik state), domains, requests, hand-written routers (read-only)
GET    /ui/api/routes/domains                 the configured domains only (cheap; the project form)
PUT    /ui/api/routes/domains                 {domains: [...]}
POST   /ui/api/routes/check-dns               {host, protection}
PUT    /ui/api/routes/{name}                  {host, environment, backend_port, path_prefix, protection, basic_user, project_id, create}
DELETE /ui/api/routes/{name}
POST   /ui/api/routes/requests/{id}/approve   {protection, backend_port, name}: creates the route through the gateway
POST   /ui/api/routes/requests/{id}/reject    {note}
"""
import asyncio
import os
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

import approvals

GATEWAY_URL = 'http://ssh-gateway-api:8099'
NO_ADMIN = 'Set POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway to manage domains and routes from the dashboard'
_NAME = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')
_FIELDS = ('host', 'environment', 'backend_port', 'backend', 'path_prefix', 'protection', 'basic_user', 'cert_resolver', 'entrypoints',
           'project_id', 'enabled', 'create', 'refresh_password')
INBOX_SECONDS = 20
_inbox = {'at': 0.0, 'requests': []}


def _admin():
    token = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, NO_ADMIN)
    return token


def _name(name):
    if not _NAME.fullmatch(name or ''):
        raise HTTPException(422, 'Route names are 1-40 lowercase letters, digits and -')
    return name


async def _gateway(method, path, *, json=None, timeout=20, headers=None, params=None):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(method, GATEWAY_URL + path, json=json, timeout=timeout, params=params,
                                            headers={'Authorization': 'Bearer ' + _admin(), **(headers or {})})
        except httpx.TimeoutException:
            raise HTTPException(504, 'The SSH gateway did not answer in time') from None
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable') from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200:
        return body
    detail = body.get('detail') or body.get('error') or f'HTTP {response.status_code}'
    if response.status_code == 400:
        raise HTTPException(422, detail)
    if response.status_code == 401:
        raise HTTPException(502, 'The gateway refused POLICY_ADMIN_TOKEN: it must match .env.gateway')
    if response.status_code == 404:
        if body.get('error') == 'not_found':
            raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to manage routes')
        raise HTTPException(404, detail)
    if response.status_code in (409, 429, 503):
        raise HTTPException(response.status_code, detail)
    raise HTTPException(502, detail)


def _remember(requests):
    _inbox.update(at=time.monotonic(), requests=[r for r in requests or [] if r.get('status') == 'pending'])


def forget():
    _inbox.update(at=0.0)


def inbox_items(db):
    """HLA_INBOX_V1: pending route requests of agents (from the gateway, cached INBOX_SECONDS; nothing when unreachable)."""
    if len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
        return []
    if time.monotonic() - _inbox['at'] >= INBOX_SECONDS:
        try:
            body = asyncio.run(_gateway('GET', '/v1/routes/requests', params={'status': 'pending'}, timeout=4))
            _remember(body.get('requests'))
        except (HTTPException, RuntimeError):
            _inbox['at'] = time.monotonic()  # keep what was known; ask again later
    items = []
    tasks = [r['task_id'] for r in _inbox['requests'] if r.get('task_id')]
    where = {}
    if tasks:
        for row in db.execute('''SELECT k.id::text AS id,p.id AS project_id,p.title FROM tasks k LEFT JOIN projects p ON p.id=k.project_id
                WHERE k.id::text = ANY(%s)''', (tasks,)).fetchall():
            where[row['id']] = row
    agents = {}
    ids = [r['agent_id'] for r in _inbox['requests'] if r.get('agent_id')]
    if ids:
        for row in db.execute('SELECT id::text AS id,name FROM agents WHERE id::text = ANY(%s)', (ids,)).fetchall():
            agents[row['id']] = row['name']
    from datetime import datetime, timezone
    for r in _inbox['requests']:
        ctx = where.get(r.get('task_id')) or {}
        items.append({'kind': 'route_request', 'title': 'Route requested: ' + r['host'],
                      'context': ctx.get('title') or agents.get(r.get('agent_id')) or 'Agent request',
                      'detail': f"{r['environment']} · {r['protection']} · {r['backend']}" + (' · ' + r['reason'][:160] if r.get('reason') else ''),
                      'since': datetime.fromtimestamp(r['created_at'], timezone.utc), 'request_id': r['id'], 'host': r['host'],
                      'environment': r['environment'], 'protection': r['protection'], 'backend_port': r['backend_port'],
                      'agent': agents.get(r.get('agent_id')), 'project_id': ctx.get('project_id')})
    return items


def build_router(dashboard_read, ui_write):
    router = APIRouter(prefix='/ui/api/routes', tags=['routes'])

    def writer(request):
        ui_write(request)
        actor = approvals.require_approver(request)
        _admin()
        return actor

    @router.get('')
    async def listing(request: Request):
        dashboard_read(request)
        status = approvals.approver_status(request)
        if len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
            return {'available': False, 'note': NO_ADMIN, 'routes': [], 'domains': [], 'editable': False, 'approver': status}
        body = await _gateway('GET', '/v1/routes', timeout=60)
        _remember(body.get('requests'))
        return {'available': True, **body, 'editable': bool(status['allowed']), 'approver': status}

    @router.get('/domains')
    async def list_domains(request: Request):
        dashboard_read(request)
        if len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
            return {'available': False, 'domains': [], 'note': NO_ADMIN}
        try:
            body = await _gateway('GET', '/v1/routes/domains', timeout=8)
        except HTTPException as exc:
            return {'available': False, 'domains': [], 'note': exc.detail}
        return {'available': True, 'domains': body.get('domains') or [], 'default_protection': body.get('default_protection') or {}}

    @router.put('/domains')
    async def save_domains(request: Request):
        actor = writer(request)
        body = await request.json()
        value = body.get('domains') if isinstance(body, dict) else None
        if not isinstance(value, list):
            raise HTTPException(422, 'Send {"domains": ["example.com", ...]}')
        return await _gateway('PUT', '/v1/routes/domains', json={'domains': value, 'actor': actor})

    @router.post('/check-dns')
    async def check_dns(request: Request):
        ui_write(request)
        body = await request.json()
        host = body.get('host') if isinstance(body, dict) else None
        if not isinstance(host, str) or not host.strip() or len(host) > 253:
            raise HTTPException(422, 'Name the host to check')
        return await _gateway('POST', '/v1/routes/check-dns', json={'host': host.strip(), 'protection': str(body.get('protection') or 'public')[:30]})

    @router.post('/requests/{request_id}/approve')
    async def approve(request_id: int, request: Request):
        actor = writer(request)
        body = await request.json() if (await request.body()) else {}
        change = {k: body[k] for k in ('name', 'protection', 'backend_port', 'environment', 'path_prefix', 'basic_user') if isinstance(body, dict) and k in body}
        try:
            return await _gateway('POST', f'/v1/routes/requests/{request_id}/approve', json={**change, 'actor': actor}, timeout=90)
        finally:
            forget()

    @router.post('/requests/{request_id}/reject')
    async def reject(request_id: int, request: Request):
        actor = writer(request)
        body = await request.json() if (await request.body()) else {}
        note = str(body.get('note') or '')[:300] if isinstance(body, dict) else ''
        try:
            return await _gateway('POST', f'/v1/routes/requests/{request_id}/reject', json={'note': note, 'actor': actor})
        finally:
            forget()

    @router.put('/{name}')
    async def save(name: str, request: Request):
        actor = writer(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'Send the route as an object')
        change = {k: body[k] for k in _FIELDS if k in body}
        return await _gateway('PUT', '/v1/routes/' + _name(name), json={**change, 'actor': actor}, timeout=90)

    @router.delete('/{name}')
    async def remove(name: str, request: Request):
        actor = writer(request)
        return await _gateway('DELETE', '/v1/routes/' + _name(name), headers={'X-HLA-Actor': actor}, timeout=90)

    return router
