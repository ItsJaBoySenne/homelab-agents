"""Proxmox on the dashboard (HLA_PROXMOX_V1): the Infrastructure view reads both nodes from the SSH gateway and asks it
to power VMs, take snapshots, create, onboard and destroy managed VMs and create the read-only audit token.

The gateway holds the node tokens, checks everything again and audits it; every route here uses POLICY_ADMIN_TOKEN, reads
need the dashboard session, and anything that changes something needs the UI write header and an approver
(approvals.require_approver), like policy changes. A create runs in the background (image download, boot and onboarding take
minutes); the view polls its job.

GET  /ui/api/proxmox                    both nodes (VMs, storages, bridges, images, next VMID, token permissions), the servers
                                        the gateway created, managed VMs, levels; ?refresh=1 reads the nodes again
POST /ui/api/proxmox/preview            {spec}: the validated spec and the text an agent's approval card would show
POST /ui/api/proxmox/snapshots          {node, vmid}
POST /ui/api/proxmox/action             {node, operation, vmid, snapname, description, rotate}: start, shutdown, stop, reboot,
                                        snapshot_create, snapshot_rollback, destroy_vm, onboard, create_audit_token
POST /ui/api/proxmox/create             {spec}: starts create_vm in the background -> {job}
GET  /ui/api/proxmox/jobs               the recent create jobs (this process)
PUT  /ui/api/proxmox/level              {target: "proxmox:<node>" | a server name, level} (the gateway refuses unknown ones)
"""
import asyncio
import os
import re
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request

import approvals

GATEWAY_URL = 'http://ssh-gateway-api:8099'
NO_ADMIN = 'Set POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway to manage Proxmox from the dashboard'
NODES = ('proxmox', 'pve2')
ACTIONS = ('start', 'shutdown', 'stop', 'reboot', 'snapshot_create', 'snapshot_rollback', 'destroy_vm', 'onboard', 'create_audit_token')
SPEC_FIELDS = ('node', 'vmid', 'name', 'cores', 'memory', 'disk', 'storage', 'bridge', 'vlan', 'ip', 'gateway', 'dns', 'macaddr', 'image', 'onboot')
ACTION_FIELDS = ('vmid', 'snapname', 'description', 'rotate')
_SERVER = re.compile(r'^[a-z][a-z0-9-]{0,30}[a-z0-9]$')
JOBS = {}
_running = set()
MAX_JOBS = 20


def _admin():
    token = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, NO_ADMIN)
    return token


async def _gateway(method, path, *, json=None, params=None, timeout=30, soft=False):
    """The gateway's reply; HTTP errors become HTTPException with its detail (soft: (status, body) instead)."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(method, GATEWAY_URL + path, json=json, params=params, timeout=timeout,
                                            headers={'Authorization': 'Bearer ' + _admin()})
        except httpx.TimeoutException:
            raise HTTPException(504, 'The SSH gateway did not answer in time') from None
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable') from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if soft:
        return response.status_code, body
    if response.status_code == 200:
        return body
    raise _error(response.status_code, body)


def _error(status, body):
    if status == 401:
        return HTTPException(502, 'The gateway refused POLICY_ADMIN_TOKEN: it must match .env.gateway')
    if status == 404 and body.get('error') == 'not_found':
        return HTTPException(502, 'The SSH gateway is older than the dashboard: update it to manage Proxmox')
    detail = str(body.get('detail') or body.get('error') or f'HTTP {status}')
    return HTTPException(422 if status == 400 else status if status in (403, 404, 409, 503, 504) else 502, detail)


def _node(value):
    if value not in NODES:
        raise HTTPException(422, 'node is proxmox or pve2')
    return value


def build_router(dashboard_read, ui_write):
    router = APIRouter(prefix='/ui/api/proxmox', tags=['proxmox'])

    def writer(request):
        ui_write(request)
        actor = approvals.require_approver(request)
        _admin()
        return actor

    @router.get('')
    async def listing(request: Request, refresh: bool = False):
        dashboard_read(request)
        status = approvals.approver_status(request)
        if len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
            return {'available': False, 'note': NO_ADMIN, 'nodes': [], 'servers': [], 'managed': [], 'editable': False, 'approver': status}
        body = await _gateway('GET', '/v1/proxmox/inventory', params={'refresh': '1'} if refresh else None, timeout=120)
        return {'available': True, **body, 'editable': bool(status['allowed']), 'approver': status, 'jobs': _jobs()}

    @router.post('/preview')
    async def preview(request: Request):
        ui_write(request)
        body = await request.json()
        spec = {k: body[k] for k in SPEC_FIELDS if isinstance(body, dict) and k in body}
        code, out = await _gateway('POST', '/v1/proxmox', json={**spec, 'node': _node(spec.get('node')), 'operation': 'preview_create',
                                                                 'actor': 'dashboard-preview'}, timeout=90, soft=True)
        if code == 200:
            return out
        if code in (400, 409, 502, 503) and out.get('error') not in (None, 'not_found'):
            return {'valid': False, 'error': out.get('error'), 'detail': out.get('detail') or out.get('error')}
        raise _error(code, out)

    @router.post('/snapshots')
    async def snapshots(request: Request):
        ui_write(request)
        body = await request.json()
        return await _gateway('POST', '/v1/proxmox', json={'node': _node(body.get('node')), 'vmid': body.get('vmid'), 'operation': 'snapshots',
                                                             'actor': 'dashboard'}, timeout=30)

    @router.post('/action')
    async def action(request: Request):
        actor = writer(request)
        body = await request.json()
        op = body.get('operation') if isinstance(body, dict) else None
        if op not in ACTIONS:
            raise HTTPException(422, 'operation is one of ' + ', '.join(ACTIONS))
        data = {k: body[k] for k in ACTION_FIELDS if k in body}
        code, out = await _gateway('POST', '/v1/proxmox', json={**data, 'node': _node(body.get('node')), 'operation': op, 'actor': actor},
                                   timeout=1900 if op in ('destroy_vm', 'onboard') else 400, soft=True)
        if code == 200:
            return out
        if out.get('commands'):  # e.g. the node token may not create users: what the owner can run instead
            return {'status': 'needs_owner', 'error': out.get('error'), 'detail': out.get('detail'), 'commands': out['commands']}
        raise _error(code, out)

    @router.post('/create')
    async def create(request: Request):
        actor = writer(request)
        body = await request.json()
        spec = {k: body[k] for k in SPEC_FIELDS if isinstance(body, dict) and k in body}
        _node(spec.get('node'))
        if any(j['status'] == 'running' and j['node'] == spec['node'] for j in JOBS.values()):
            raise HTTPException(409, f'A VM is already being created on {spec["node"]}; wait until it is done')
        job = {'id': str(uuid.uuid4()), 'node': spec['node'], 'vmid': spec.get('vmid'), 'name': str(spec.get('name') or '')[:40],
               'ip': str(spec.get('ip') or '')[:20], 'status': 'running', 'actor': actor, 'started': time.time(), 'finished': None,
               'result': None, 'error': None}
        JOBS[job['id']] = job
        for old in sorted(JOBS.values(), key=lambda j: j['started'])[:-MAX_JOBS]:
            JOBS.pop(old['id'], None)

        async def run():
            try:
                code, out = await _gateway('POST', '/v1/proxmox', json={**spec, 'operation': 'create_vm', 'actor': actor}, timeout=1900, soft=True)
                if code == 200:
                    job.update(status='done', result={k: out.get(k) for k in ('vmid', 'name', 'ip', 'status', 'image_source', 'server', 'steps')})
                else:
                    job.update(status='failed', error=str(out.get('detail') or out.get('error') or f'HTTP {code}')[:600],
                               result={'steps': out.get('steps') or []})
            except HTTPException as exc:
                job.update(status='failed', error=str(exc.detail))
            except Exception as exc:  # noqa: BLE001 - the job says what happened
                job.update(status='failed', error=type(exc).__name__)
            finally:
                job['finished'] = time.time()

        task = asyncio.create_task(run())
        _running.add(task)
        task.add_done_callback(_running.discard)
        return {'job': job}

    @router.get('/jobs')
    def jobs(request: Request):
        dashboard_read(request)
        return {'jobs': _jobs()}

    @router.put('/level')
    async def level(request: Request):
        actor = writer(request)
        body = await request.json()
        target, value = (body.get('target'), body.get('level')) if isinstance(body, dict) else (None, None)
        if isinstance(value, bool) or value not in range(0, 7):
            raise HTTPException(422, 'level must be 0..6')
        if not isinstance(target, str) or not (target in ('proxmox:' + n for n in NODES) or _SERVER.match(target)):
            raise HTTPException(422, 'target is proxmox:<node> or a server the gateway created')
        return await _gateway('PUT', '/v1/policy', json={'change': {'servers': {target: {'level': value}}}, 'actor': actor})

    return router


def _jobs():
    return sorted(JOBS.values(), key=lambda j: j['started'], reverse=True)
