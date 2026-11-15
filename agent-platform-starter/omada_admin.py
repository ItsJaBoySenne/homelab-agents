"""The Omada controller on the dashboard (HLA_OMADA_V1): the Infrastructure view shows the connection the SSH gateway
has to the controller's Open API, lets the owner trust its certificate fingerprint and set the agents' level.

The gateway holds the app credentials (Infisical /Omada), checks everything again and audits it; every route here uses
POLICY_ADMIN_TOKEN, reads need the dashboard session, and changes need the UI write header and an approver
(approvals.require_approver), like the Proxmox routes.

GET  /ui/api/omada          connection status (controller version, omadacId, certificate, sites, last error) and the level
                            of omada:controller; ?refresh=1 reads the controller info again
POST /ui/api/omada/trust    {fingerprint}: trust the certificate the controller presents now (SHA-256 shown in the view)
PUT  /ui/api/omada/level    {level}: the level of omada:controller (0 = off; 1 read; 5 writes with approval; 6 without)
"""
import os
import re

from fastapi import APIRouter, HTTPException, Request

import approvals
from proxmox_admin import _admin, _gateway, _error, NO_ADMIN

SERVICE = 'omada:controller'
_FP = re.compile(r'^[0-9A-Fa-f]{2}(:?[0-9A-Fa-f]{2}){31}$')


def build_router(dashboard_read, ui_write):
    router = APIRouter(prefix='/ui/api/omada', tags=['omada'])

    def writer(request):
        ui_write(request)
        actor = approvals.require_approver(request)
        _admin()
        return actor

    @router.get('')
    async def status(request: Request, refresh: bool = False):
        dashboard_read(request)
        approver = approvals.approver_status(request)
        if len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
            return {'available': False, 'note': NO_ADMIN.replace('Proxmox', 'the Omada controller'), 'editable': False, 'approver': approver}
        code, out = await _gateway('POST', '/v1/omada', json={'operation': 'status', 'actor': 'dashboard', **({'refresh': True} if refresh else {})},
                                   timeout=60, soft=True)
        if code != 200:
            if code == 404 and out.get('error') == 'not_found':
                raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to use the Omada controller')
            raise _error(code, out)
        policy = await _gateway('GET', '/v1/policy', timeout=15)
        level = ((policy.get('services') or {}).get(SERVICE) or {}).get('level', 0)
        return {'available': True, **{k: v for k, v in out.items() if k not in ('operation',)}, 'level': level,
                'editable': bool(approver['allowed']), 'approver': approver}

    @router.post('/trust')
    async def trust(request: Request):
        actor = writer(request)
        body = await request.json()
        fp = body.get('fingerprint') if isinstance(body, dict) else None
        if not isinstance(fp, str) or not _FP.match(fp):
            raise HTTPException(422, 'fingerprint is the SHA-256 shown in the view')
        return await _gateway('POST', '/v1/omada', json={'operation': 'trust_certificate', 'fingerprint': fp, 'actor': actor}, timeout=60)

    @router.put('/level')
    async def level(request: Request):
        actor = writer(request)
        body = await request.json()
        value = body.get('level') if isinstance(body, dict) else None
        if isinstance(value, bool) or value not in range(0, 7):
            raise HTTPException(422, 'level must be 0..6')
        return await _gateway('PUT', '/v1/policy', json={'change': {'servers': {SERVICE: {'level': value}}}, 'actor': actor})

    return router
