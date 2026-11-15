"""MCP servers on the dashboard (HLA_MCP_V1): the MCP page reads and edits the registry in the SSH gateway.

The gateway holds the servers, their tool snapshots, the auth (credential names from its credential store; values never
leave it) and enforces the levels. Every route here goes to the gateway with POLICY_ADMIN_TOKEN; writes need the UI
write header and an approver (approvals.require_approver), like policy changes.

GET    /ui/api/mcp                          servers with status and tools (levels, overrides), credential names
PUT    /ui/api/mcp/servers/{name}           {url | url_credential, auth {type, credential, header}, enabled, timeout, allow_private,
                                            allow_insecure, description}
DELETE /ui/api/mcp/servers/{name}
POST   /ui/api/mcp/servers/{name}/refresh   connect and list the tools again
PUT    /ui/api/mcp/servers/{name}/level     {level}: the service level of mcp:<name>
PUT    /ui/api/mcp/servers/{name}/tools     {tool, override: {level} | {disabled: true} | null}
"""
import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Request

import approvals

GATEWAY_URL = 'http://ssh-gateway-api:8099'
_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
_TOOL = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
_FIELDS = ('url', 'url_credential', 'auth', 'enabled', 'timeout', 'allow_private', 'allow_insecure', 'description')
NO_ADMIN = 'Set POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway to manage MCP servers from the dashboard'


def _admin():
    token = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, NO_ADMIN)
    return token


def _name(name):
    if not _NAME.match(name or ''):
        raise HTTPException(422, 'Server names are 1-32 lowercase letters, digits, - and _')
    return name


async def _gateway(method, path, *, json=None, timeout=15, headers=None):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(method, GATEWAY_URL + path, json=json, timeout=timeout,
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
            raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to manage MCP servers')
        raise HTTPException(404, 'No such MCP server' if body.get('error') == 'unknown_server' else detail)
    # 403/502/503/504 from connecting to the MCP server: say what the gateway found.
    raise HTTPException(502, 'MCP server: ' + str(body.get('error') or '') + (' — ' + str(body.get('detail')) if body.get('detail') else ''))


def build_router(dashboard_read, ui_write):
    router = APIRouter(prefix='/ui/api/mcp', tags=['mcp'])

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
            return {'available': False, 'note': NO_ADMIN, 'servers': [], 'credentials': [], 'editable': False, 'approver': status}
        body = await _gateway('GET', '/v1/mcp/servers', timeout=10)
        return {'available': True, 'servers': body.get('servers') or [], 'credentials': body.get('credentials') or [],
                'credentials_configured': body.get('credentials_configured', False), 'levels': body.get('levels') or {},
                'editable': bool(status['allowed']), 'approver': status}

    @router.put('/servers/{name}')
    async def save(name: str, request: Request):
        actor = writer(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'Send the server settings as an object')
        change = {k: body[k] for k in _FIELDS if k in body}
        return await _gateway('PUT', '/v1/mcp/servers/' + _name(name), json={**change, 'actor': actor})

    @router.delete('/servers/{name}')
    async def remove(name: str, request: Request):
        actor = writer(request)
        return await _gateway('DELETE', '/v1/mcp/servers/' + _name(name), headers={'X-HLA-Actor': actor})

    @router.post('/servers/{name}/refresh')
    async def refresh(name: str, request: Request):
        actor = writer(request)
        return await _gateway('POST', '/v1/mcp/servers/' + _name(name) + '/refresh', json={'actor': actor}, timeout=150)

    @router.put('/servers/{name}/level')
    async def level(name: str, request: Request):
        actor = writer(request)
        body = await request.json()
        value = body.get('level') if isinstance(body, dict) else None
        if isinstance(value, bool) or value not in range(0, 7):
            raise HTTPException(422, 'level must be 0..6')
        return await _gateway('PUT', '/v1/policy', json={'change': {'servers': {'mcp:' + _name(name): {'level': value}}}, 'actor': actor})

    @router.put('/servers/{name}/tools')
    async def tool_override(name: str, request: Request):
        actor = writer(request)
        body = await request.json()
        tool = body.get('tool') if isinstance(body, dict) else None
        if not isinstance(tool, str) or not _TOOL.match(tool):
            raise HTTPException(422, 'Name the tool')
        override = body.get('override')
        return await _gateway('PUT', '/v1/policy', json={'change': {'mcp': {'tools': {_name(name): {tool: override}}}}, 'actor': actor})

    return router
