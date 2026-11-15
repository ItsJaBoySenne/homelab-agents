"""Generate random application secrets through the SSH gateway's secret store (generated namespace).

The gateway generates and stores the value (POST /v1/secrets, operation create) as the configured agent
(SSH_TOOL_AGENT_ID), so its policy level applies and the value never reaches this process, the LLM, logs or the
knowledge database. Whether the store is the local encrypted file or Infisical is the gateway's business
(HLA_SECRETS_BACKEND in .env.gateway). Fail closed when unset.
"""
import os
import re

SECRET_NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
ALLOWED_KINDS = {'password': 'password', 'token': 'token', 'hex_key': 'hex'}
GATEWAY_URL = 'http://ssh-gateway-api:8099'


async def generate_secret(client, name, kind):
    if os.environ.get('AGENT_SECRET_WRITER_ENABLED','false').lower() != 'true':
        return {'status':'denied','reason':'secret_writer_disabled'}
    if not isinstance(name,str) or not SECRET_NAME.fullmatch(name):
        return {'status':'denied','reason':'invalid_name'}
    if name.startswith(('SSH_', 'INFISICAL_', 'AGENT_PLATFORM_', 'TOOL_GATEWAY_', 'DATABASE_', 'POSTGRES_', 'HLA_SECRETS_')):
        return {'status':'denied','reason':'reserved_name'}
    if kind not in ALLOWED_KINDS:
        return {'status':'denied','reason':'invalid_kind'}
    token = os.environ.get('TOOL_GATEWAY_TOKEN', '')
    agent = os.environ.get('SSH_TOOL_AGENT_ID', '')
    if len(token) < 32 or not agent:
        return {'status':'denied','reason':'gateway_not_configured'}
    base = os.environ.get('SSH_TOOL_GATEWAY_URL', GATEWAY_URL + '/v1/execute').rsplit('/v1/', 1)[0]
    try:
        response = await client.post(base + '/v1/secrets', headers={'Authorization': 'Bearer ' + token}, timeout=20,
                                     json={'agent_id': agent, 'operation': 'create', 'name': name, 'kind': ALLOWED_KINDS[kind],
                                           'description': 'Created by Homelab Agents (development secret workflow)'})
        body = response.json() if response.content else {}
    except Exception:  # noqa: BLE001 - transport or a non-JSON answer; nothing secret is in either
        return {'status':'failed','stage':'transport_or_response'}
    if not isinstance(body, dict):
        body = {}
    if response.status_code != 200 or body.get('value_returned') is not False:
        return {'status':'failed','stage':str(body.get('error') or 'creation'),'http_status':response.status_code,
                'note':'Existing secrets are not overwritten; the agent needs level 4 on the secrets service.'}
    return {'status':'created','name':name,'kind':kind,'reference':body.get('reference'),
            'project':'generated secrets (gateway secret store)','value_returned':False}
