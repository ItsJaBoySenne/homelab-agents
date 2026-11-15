"""Reset the agent sandbox VM to its clean snapshot via the Proxmox API of its host (HLA_SANDBOX_V1).

The token needs only VM.Snapshot.Rollback and VM.PowerMgmt on that one VM. TLS is verified against that
host's own CA file: pve2 is a separate Proxmox install with a different CA than proxmox-ca.pem.
"""
import asyncio
import os
import re
import ssl
from urllib.parse import quote

import httpx

SANDBOX_HOST = '192.168.40.204'
_lock = asyncio.Lock()


def config():
    cfg = {
        'url': os.getenv('SANDBOX_PROXMOX_URL', 'https://192.168.20.31:8006').rstrip('/'),
        'node': os.getenv('SANDBOX_PROXMOX_NODE', 'pve2'),
        'vmid': os.getenv('SANDBOX_VMID', '204'),
        'snapshot': os.getenv('SANDBOX_SNAPSHOT', 'clean'),
        'token_id': os.getenv('SANDBOX_PROXMOX_TOKEN_ID', ''),
        'secret': os.getenv('SANDBOX_PROXMOX_TOKEN_SECRET', ''),
        'ca': os.getenv('SANDBOX_PROXMOX_CA', '/app/pve2-ca.pem'),
    }
    missing = [name for name, key in (('SANDBOX_PROXMOX_TOKEN_ID', 'token_id'), ('SANDBOX_PROXMOX_TOKEN_SECRET', 'secret')) if not cfg[key]]
    if not os.path.isfile(cfg['ca']):
        missing.append('pve2 CA file (pve2-ca.pem)')
    elif not os.access(cfg['ca'], os.R_OK):
        missing.append('a readable pve2 CA file (pve2-ca.pem is not readable by the app; rebuild the image)')
    if not (cfg['url'].startswith('https://') and cfg['vmid'].isdigit() and re.fullmatch(r'[A-Za-z0-9-]{1,63}', cfg['node'])
            and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{1,39}', cfg['snapshot'])):
        missing.append('valid SANDBOX_PROXMOX_URL/NODE/VMID/SNAPSHOT')
    return cfg, missing


def describe():
    cfg, missing = config()
    return {'configured': not missing, 'missing': missing, 'host': SANDBOX_HOST, 'node': cfg['node'],
            'vmid': cfg['vmid'], 'snapshot': cfg['snapshot'], 'resetting': _lock.locked()}


async def _wait(client, cfg, upid, headers):
    """Proxmox runs rollback/start as tasks; wait until the task has stopped and check it succeeded."""
    url = f"{cfg['url']}/api2/json/nodes/{cfg['node']}/tasks/{quote(upid, safe='')}/status"
    deadline = asyncio.get_running_loop().time() + 120  # wall clock: slow status calls must not stretch this
    while asyncio.get_running_loop().time() < deadline:
        r = await client.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        task = r.json().get('data') or {}
        if task.get('status') == 'stopped':
            if task.get('exitstatus') != 'OK':
                raise RuntimeError('Proxmox task failed: ' + str(task.get('exitstatus'))[:200])
            return
        await asyncio.sleep(1)
    raise RuntimeError('Proxmox task did not finish within 2 minutes')


async def reset():
    cfg, missing = config()
    if missing:
        raise RuntimeError('Sandbox reset is not configured: ' + ', '.join(missing))
    if _lock.locked():
        raise RuntimeError('A sandbox reset is already running')
    async with _lock:
        headers = {'Authorization': f"PVEAPIToken={cfg['token_id']}={cfg['secret']}"}
        base = f"{cfg['url']}/api2/json/nodes/{cfg['node']}/qemu/{cfg['vmid']}"
        try:
            tls = ssl.create_default_context(cafile=cfg['ca'])
        except (OSError, ssl.SSLError) as exc:
            raise RuntimeError(f"Cannot load the pve2 CA file: {type(exc).__name__}") from None
        async with httpx.AsyncClient(verify=tls, timeout=30) as client:
            step = 'rollback'
            try:
                r = await client.post(f"{base}/snapshot/{cfg['snapshot']}/rollback", headers=headers, data={'start': 1})
                started = r.status_code != 400  # older Proxmox has no start option: start it separately
                if not started:
                    r = await client.post(f"{base}/snapshot/{cfg['snapshot']}/rollback", headers=headers)
                r.raise_for_status()
                step = 'reading the rollback task status'
                await _wait(client, cfg, r.json()['data'], headers)
                if not started:
                    step = 'start'
                    r = await client.post(f'{base}/status/start', headers=headers)
                    r.raise_for_status()
                    await _wait(client, cfg, r.json()['data'], headers)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                hint = {401: 'token ID or secret not accepted',
                        403: ('token has no permission on /vms/' + cfg['vmid'] + ' (with privilege separation, the token needs its own ACL)'
                              if step != 'reading the rollback task status' else 'token may not read its task status on node ' + cfg['node'])}.get(code, '')
                raise RuntimeError(f'Proxmox refused the {step} (HTTP {code}{": " + hint if hint else ""})') from None
            except httpx.HTTPError as exc:
                raise RuntimeError('Proxmox unreachable: ' + type(exc).__name__) from None
    return {'status': 'reset', 'snapshot': cfg['snapshot']}
