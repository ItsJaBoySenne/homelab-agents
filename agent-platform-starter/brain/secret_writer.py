"""Generate random application secrets in a dedicated Infisical project.

No secret value is ever exposed to the LLM, logs, or the knowledge database.
Requires a separate, create-only identity. Fail closed when unset.
"""
import os
import re
import secrets
import httpx

SECRET_NAME = re.compile(r'^[A-Z][A-Z0-9_]{2,63}$')
ALLOWED_KINDS = {'password': 32, 'token': 48, 'hex_key': 32}

async def generate_secret(client, name, kind):
    if os.environ.get('AGENT_SECRET_WRITER_ENABLED','false').lower() != 'true':
        return {'status':'denied','reason':'secret_writer_disabled'}
    if not isinstance(name,str) or not SECRET_NAME.fullmatch(name):
        return {'status':'denied','reason':'invalid_name'}
    if name.startswith(('SSH_', 'INFISICAL_', 'AGENT_PLATFORM_', 'TOOL_GATEWAY_', 'DATABASE_', 'POSTGRES_')):
        return {'status':'denied','reason':'reserved_name'}
    if kind not in ALLOWED_KINDS:
        return {'status':'denied','reason':'invalid_kind'}
    config = {k:os.environ.get(k,'') for k in (
        'AGENT_SECRET_INFISICAL_URL','AGENT_SECRET_PROJECT_ID',
        'AGENT_SECRET_CLIENT_ID','AGENT_SECRET_CLIENT_SECRET')}
    if not all(config.values()):
        return {'status':'denied','reason':'writer_identity_not_configured'}
    base=config['AGENT_SECRET_INFISICAL_URL'].rstrip('/')
    # Self-hosted private Infisical only. No arbitrary URLs or paths from the model.
    if base != 'https://192.168.10.203':
        return {'status':'denied','reason':'untrusted_infisical_endpoint'}
    environment='prod'
    path='/development'
    # Generate inside trusted Python; never receive a credential from model input.
    value=(secrets.token_urlsafe(ALLOWED_KINDS[kind]) if kind!='hex_key'
           else secrets.token_hex(ALLOWED_KINDS[kind]))
    ca=os.environ.get('AGENT_SECRET_INFISICAL_CA','/app/infisical-ca.pem')
    # Infisical CA is separate from Proxmox CA; do not substitute an unrelated CA.
    if not os.path.isfile(ca):
        return {'status':'denied','reason':'infisical_ca_unavailable'}
    try:
        async with httpx.AsyncClient(verify=ca,trust_env=False,timeout=15) as c:
            login=await c.post(base+'/api/v1/auth/universal-auth/login',json={
                'clientId':config['AGENT_SECRET_CLIENT_ID'],
                'clientSecret':config['AGENT_SECRET_CLIENT_SECRET']})
            if login.status_code != 200:
                return {'status':'failed','stage':'authentication','http_status':login.status_code}
            token=login.json().get('accessToken')
            if not token:return {'status':'failed','stage':'authentication_response'}
            write=await c.post(base+'/api/v4/secrets/'+name,
               headers={'Authorization':'Bearer '+token},json={
                   'projectId':config['AGENT_SECRET_PROJECT_ID'],
                   'environment':environment,'secretPath':path,
                   'secretValue':value,'type':'shared',
                   'secretComment':'Created by Homelab Agents; value deliberately not returned to agent.'})
            if write.status_code not in (200,201):
                return {'status':'failed','stage':'creation','http_status':write.status_code,
                        'note':'Existing secrets must not be overwritten. Check the dedicated project/identity permissions.'}
    except (httpx.HTTPError,ValueError,KeyError):
        return {'status':'failed','stage':'transport_or_response'}
    finally:
        value=None
    return {'status':'created','name':name,'kind':kind,'environment':environment,
            'path':path,'project':'dedicated Agent Generated project',
            'value_returned':False}
