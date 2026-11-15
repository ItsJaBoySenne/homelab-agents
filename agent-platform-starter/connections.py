"""The Connections page (HLA_CONNECTIONS_V1): credentials, GitHub and HTTP tools, all held by the SSH gateway.

The platform never stores or returns a credential value: a value typed on the dashboard is passed to the gateway's
write-only store (PUT /v1/credentials/NAME) once and only names and metadata come back. Every route here talks to
the gateway with POLICY_ADMIN_TOKEN (the token that may change policy); changes need the UI header and an allowed
approver, like policy edits. The gateway encrypts values with GATEWAY_CREDENTIAL_KEY, which lives only in its own
.env.gateway.

GET    /ui/api/connections               what is set up (admin token, credential store, approver)
GET    /ui/api/credentials               names and metadata of stored credentials
PUT    /ui/api/credentials/{name}        {value, kind, description}: store or replace (value never returned)
DELETE /ui/api/credentials/{name}
GET    /ui/api/github/status             token source (store / env / Infisical), login and scopes (never the token)
GET    /ui/api/github/repos              repositories the token reaches, with their allowlist state (max 300)
GET    /ui/api/http-tools                tool definitions (credentials by name)
PUT    /ui/api/http-tools/{name}         create or replace a tool
DELETE /ui/api/http-tools/{name}
POST   /ui/api/http-tools/{name}/test    {params}: one call with the admin token (status + truncated response)
"""
import json
import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Request

GATEWAY_URL = 'http://ssh-gateway-api:8099'
_CREDENTIAL = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
_TOOL = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
NO_ADMIN = 'Set POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway to manage connections from the dashboard'
NO_KEY = ('The credential store is off: set GATEWAY_CREDENTIAL_KEY in ../ssh-gateway/.env.gateway '
          '(openssl rand -base64 32) and restart the gateway')
TEST_RESPONSE_CHARS = 4000  # what the Test button shows of a response


def _admin(required=True):
    admin = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(admin) < 32:
        if required:
            raise HTTPException(503, NO_ADMIN)
        return None
    return admin


def _no_value(data):
    """Defence in depth: nothing named value leaves this module, whatever the gateway sends."""
    if isinstance(data, dict):
        return {k: _no_value(v) for k, v in data.items() if k != 'value'}
    if isinstance(data, list):
        return [_no_value(x) for x in data]
    return data


async def _gateway(method, path, admin, actor=None, **kw):
    headers = {'Authorization': 'Bearer ' + admin, **({'X-HLA-Actor': actor[:120]} if actor else {})}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(method, GATEWAY_URL + path, headers=headers, timeout=kw.pop('timeout', 15), **kw)
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable') from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def _raise(status, body, what):
    """Gateway refusals as dashboard errors with a sentence to show."""
    error, detail = body.get('error'), body.get('detail') or ''
    if error == 'credentials_not_configured':
        raise HTTPException(503, NO_KEY)
    if status == 401:
        raise HTTPException(502, 'The SSH gateway refused POLICY_ADMIN_TOKEN: it must match ../ssh-gateway/.env.gateway')
    if status == 404 and error == 'not_found':
        raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to use ' + what)
    if status == 400:
        raise HTTPException(422, detail or str(error or 'Invalid request'))
    if status == 404:
        raise HTTPException(404, detail or str(error or 'Not found'))
    raise HTTPException(502 if status >= 500 or status < 400 else status,
                        (detail or str(error or 'HTTP ' + str(status)))[:300])


def build_router(dashboard_read, ui_write, require_approver, approver_status):
    router = APIRouter(prefix='/ui/api', tags=['connections'])

    @router.get('/connections')
    async def overview(request: Request):
        dashboard_read(request)
        admin = _admin(False)
        status = {'editable': bool(admin), 'approver': approver_status(request), 'setup': None if admin else NO_ADMIN,
                  'credentials_configured': None}
        if admin:
            code, body = await _gateway('GET', '/v1/credentials', admin, timeout=8)
            if code == 200:
                status['credentials_configured'] = bool(body.get('configured'))
                if not body.get('configured'):
                    status['setup'] = NO_KEY
        return status

    # ---- credentials ----
    @router.get('/credentials')
    async def credentials(request: Request):
        dashboard_read(request)
        admin = _admin(False)
        if not admin:
            return {'configured': False, 'credentials': [], 'editable': False, 'setup': NO_ADMIN}
        code, body = await _gateway('GET', '/v1/credentials', admin, timeout=8)
        if code != 200:
            _raise(code, body, 'the credential store')
        configured = bool(body.get('configured'))
        return _no_value({'configured': configured, 'credentials': body.get('credentials') or [], 'editable': True,
                          'setup': None if configured else NO_KEY})

    def credential_name(name):
        if not _CREDENTIAL.match(name):
            raise HTTPException(422, 'Names are UPPER_CASE letters, digits and _ (3-64), e.g. GITHUB_TOKEN')
        return name

    @router.put('/credentials/{name}')
    async def store_credential(name: str, request: Request):
        ui_write(request)
        actor = require_approver(request)
        admin = _admin()
        credential_name(name)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('value'), str) or not body['value'].strip():
            raise HTTPException(422, 'Enter the value')
        sent = {'value': body['value'], 'kind': body.get('kind') or 'token', 'description': str(body.get('description') or '')[:300],
                'actor': actor}
        code, reply = await _gateway('PUT', '/v1/credentials/' + name, admin, json=sent)
        if code != 200:
            _raise(code, reply, 'the credential store')
        # Only metadata comes back (the gateway masks values anyway); the value is never echoed.
        return _no_value({k: reply.get(k) for k in ('name', 'kind', 'description', 'fingerprint', 'created_at', 'updated_at', 'updated_by')})

    @router.delete('/credentials/{name}')
    async def delete_credential(name: str, request: Request):
        ui_write(request)
        actor = require_approver(request)
        admin = _admin()
        credential_name(name)
        code, reply = await _gateway('DELETE', '/v1/credentials/' + name, admin, actor=actor, timeout=10)
        if code == 404 and reply.get('deleted') is False:
            raise HTTPException(404, name + ' is not stored')
        if code != 200:
            _raise(code, reply, 'the credential store')
        return {'deleted': True, 'name': name}

    # ---- GitHub ----
    async def github(operation, request):
        admin = _admin()
        actor = approver_status(request)['actor'] or 'dashboard_session'
        code, body = await _gateway('POST', '/v1/github', admin, json={'actor': actor, 'operation': operation}, timeout=45)
        if code == 400 and body.get('error') == 'invalid_operation':
            raise HTTPException(502, 'The SSH gateway is older than the dashboard: update it to use the GitHub connection')
        if code != 200:
            _raise(code, body, 'the GitHub connection')
        return body

    @router.get('/github/status')
    async def github_status(request: Request):
        dashboard_read(request)
        if not _admin(False):
            return {'configured': None, 'ok': False, 'setup': NO_ADMIN}
        return await github('status', request)

    @router.get('/github/repos')
    async def github_repos(request: Request):
        dashboard_read(request)
        return await github('list_repos', request)

    # ---- HTTP tools ----
    def tool_name(name):
        if not _TOOL.match(name):
            raise HTTPException(422, 'Tool names are snake_case: 2-40 lowercase letters, digits and _, starting with a letter')
        return name

    @router.get('/http-tools')
    async def http_tools(request: Request):
        dashboard_read(request)
        admin = _admin(False)
        if not admin:
            return {'tools': [], 'editable': False, 'setup': NO_ADMIN}
        code, body = await _gateway('GET', '/v1/http_tools', admin, timeout=8)
        if code != 200:
            _raise(code, body, 'HTTP tools')
        return _no_value({'tools': body.get('tools') or [], 'editable': True, 'credentials_configured': body.get('credentials_configured')})

    @router.put('/http-tools/{name}')
    async def save_http_tool(name: str, request: Request):
        ui_write(request)
        actor = require_approver(request)
        admin = _admin()
        tool_name(name)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'The tool definition is an object')
        code, reply = await _gateway('PUT', '/v1/http_tools/' + name, admin, json={**body, 'actor': actor})
        if code != 200:
            _raise(code, reply, 'HTTP tools')
        return _no_value(reply)

    @router.delete('/http-tools/{name}')
    async def delete_http_tool(name: str, request: Request):
        ui_write(request)
        actor = require_approver(request)
        admin = _admin()
        tool_name(name)
        code, reply = await _gateway('DELETE', '/v1/http_tools/' + name, admin, actor=actor, timeout=10)
        if code == 404 and reply.get('deleted') is False:
            raise HTTPException(404, name + ' does not exist')
        if code != 200:
            _raise(code, reply, 'HTTP tools')
        return {'deleted': True, 'name': name}

    @router.post('/http-tools/{name}/test')
    async def test_http_tool(name: str, request: Request):
        ui_write(request)
        actor = require_approver(request)
        admin = _admin()
        tool_name(name)
        body = await request.json()
        params = body.get('params') if isinstance(body, dict) else None
        if params is not None and not isinstance(params, dict):
            raise HTTPException(422, 'params is an object')
        code, reply = await _gateway('POST', '/v1/http_tool', admin, json={'actor': actor, 'tool': name, 'params': params or {}}, timeout=45)
        if code != 200:
            if code in (400, 403, 404, 502, 503, 504) and reply.get('error') not in (None, 'not_found', 'credentials_not_configured'):
                # A refusal or a failed call is a test result, not a page error.
                return {'ok': False, 'error': reply.get('error'), 'detail': reply.get('detail') or ''}
            _raise(code, reply, 'HTTP tools')
        text = reply.get('text')
        shown = {k: reply.get(k) for k in ('http_status', 'ok', 'content_type', 'seconds', 'truncated', 'note')}
        preview = json.dumps(reply['json'], indent=2) if 'json' in reply else str(text or '')
        shown['preview'] = preview[:TEST_RESPONSE_CHARS]
        shown['preview_truncated'] = len(preview) > TEST_RESPONSE_CHARS
        return shown

    return router
