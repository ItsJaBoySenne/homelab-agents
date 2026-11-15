"""SSH backend: credentials fetched from the secret store on every request; no key exposure.

Servers and services come from the inventory (config/inventory.yaml, gateway/inventory.py); secrets from the secret store
(gateway/secret_store.py: the local encrypted file by default, or Infisical)."""
import base64, hashlib, io, os, socket, sys, time
import paramiko

from . import inventory as _inventory
from . import secret_store

try:
 INVENTORY=_inventory.load()
except _inventory.InventoryError as exc:
 # The gateway refuses to start on a broken inventory rather than guessing: the message names the entry and the problem.
 print('INVENTORY ERROR: '+str(exc),file=sys.stderr)
 raise SystemExit(2)

# name -> (pinned host, secret folder). Several servers have a role elsewhere: the sandbox (sandbox: true), the website
# server (role: website) and the Traefik host (role: traefik); see SANDBOX, WEBSITE_SERVER and TRAEFIK_SERVER.
SERVERS={s['name']:(s['host'],s['secret_path']) for s in INVENTORY['servers']}
# name -> {user, port, description, sandbox, role, default_level, host_fingerprint, vmid} from the inventory.
SERVER_INFO={s['name']:s for s in INVENTORY['servers']}
SANDBOX=_inventory.sandbox(INVENTORY)
WEBSITE_SERVER=_inventory.role(INVENTORY,'website')
TRAEFIK_SERVER=_inventory.role(INVENTORY,'traefik')
PUBLIC_IPS=list(INVENTORY['public_ips'])

def _services(inv):
 """Gateway services: name -> (pinned address/URL, secret folder). Integrations left out of the inventory do not exist."""
 svc,cfg={},inv['services']
 # HLA_HA_V1: Home Assistant with HA_TOKEN from the secret store (gateway/homeassistant.py).
 if 'homeassistant' in cfg:svc['homeassistant']=(cfg['homeassistant']['url'],cfg['homeassistant']['secret_path'])
 # HLA_SECRETS_V1: the generated namespace of the secret store, see gateway/agent_secrets.py.
 svc['secrets']=(None,None)
 # HLA_GITHUB_V1: pull requests on allowlisted repos; token from the credential store, GITHUB_TOKEN or the secret store.
 if 'github' in cfg:svc['github']=(cfg['github']['api_url'],cfg['github']['secret_path'])
 # HLA_HTTP_TOOLS_V1: HTTP API tools defined on the dashboard; each has its own fixed host, see gateway/http_tools.py.
 svc['http_tools']=(None,None)
 # HLA_PROXMOX_V1: the Proxmox VE API of each node (gateway/proxmox.py), TOKEN_ID/TOKEN_SECRET in the secret store.
 for node in (cfg.get('proxmox') or {}).get('nodes',[]):svc['proxmox:'+node['name']]=(node['host'],node['secret_path'])
 # HLA_OMADA_V1: the Omada controller's Open API (CLIENT_ID, CLIENT_SECRET, APPLICATION_NAME in the secret store).
 if 'omada' in cfg:svc['omada:controller']=(cfg['omada']['url'],cfg['omada']['secret_path'])
 return svc

SERVICES=_services(INVENTORY)

# HLA_PROXMOX_V1: servers created by the gateway on Proxmox (gateway/proxmox.py), table servers_dynamic. They are added to
# SERVERS (host, 'dynamic:<name>') whenever the database is opened (load_dynamic); their SSH key lives in the credential store
# and their host key fingerprint was recorded on first contact. Static names above can never be taken by a dynamic server.
STATIC_SERVERS=frozenset(SERVERS)
DYNAMIC={}

def load_dynamic(db):
 """Sync SERVERS/DYNAMIC with the servers_dynamic table (called by cli.db_open on every request)."""
 try:rows=db.execute('SELECT name,host,user,key_credential,host_fingerprint,node,vmid FROM servers_dynamic').fetchall()
 except Exception:return
 seen=set()
 for name,host,user,key,fingerprint,node,vmid in rows:
  if name in STATIC_SERVERS:continue
  seen.add(name)
  DYNAMIC[name]={'host':host,'user':user,'key_credential':key,'host_fingerprint':fingerprint,'node':node,'vmid':vmid}
  SERVERS[name]=(host,'dynamic:'+name)
 for name in list(DYNAMIC):
  if name not in seen:
   DYNAMIC.pop(name,None)
   if name not in STATIC_SERVERS:SERVERS.pop(name,None)

def dynamic_credentials(server):
 """A dynamic server's key from the credential store and its pinned fingerprint (same shape as credentials())."""
 from .cli import db_open
 from . import credentials as store
 record=DYNAMIC.get(server)
 if not record:raise ValueError('Unknown server')
 with db_open() as db:key=store.get(db,record['key_credential'])
 if not key or 'BEGIN OPENSSH PRIVATE KEY' not in key:raise RuntimeError('Invalid private SSH key')
 if record['user']!='svc-hla-agent':raise RuntimeError('Credential target does not match pinned server map')
 return {'SSH_HOST':record['host'],'SSH_USER':record['user'],'SSH_PRIVATE_KEY':key,'SSH_HOST_FINGERPRINT':record['host_fingerprint'] or '',
         'SSH_KEY_PASSPHRASE':''}

def env(name):
 v=os.environ.get(name,'').strip()
 if not v: raise RuntimeError('Missing environment variable '+name)
 return v

def platform_secrets(path,names,optional=()):
 """Read the named secrets from one folder of the platform namespace of the secret store (gateway/secret_store.py)."""
 return secret_store.platform_secrets(path,names,optional)

def credentials(server):
 """SSH_PRIVATE_KEY (required), SSH_HOST_FINGERPRINT (from the inventory or the store), SSH_KEY_PASSPHRASE (optional).
 SSH_HOST and SSH_USER, when an older secret folder still has them, must match the inventory."""
 if server not in SERVERS: raise ValueError('Unknown server')
 if server in DYNAMIC: return dynamic_credentials(server)
 expected_ip,path=SERVERS[server]
 info=SERVER_INFO[server]
 pinned=info['host_fingerprint']
 values=platform_secrets(path,('SSH_PRIVATE_KEY',),('SSH_KEY_PASSPHRASE','SSH_HOST','SSH_USER')+(() if pinned else ('SSH_HOST_FINGERPRINT',)))
 if pinned:values['SSH_HOST_FINGERPRINT']=' '.join(pinned)
 elif not values.get('SSH_HOST_FINGERPRINT','').strip():
  raise RuntimeError('No pinned host key for '+server+': set host_fingerprint in the inventory or run '
                     '"python -m gateway.cli secrets pin-host-key '+server+'"')
 values.setdefault('SSH_KEY_PASSPHRASE','')
 if values.setdefault('SSH_HOST',expected_ip).strip()!=expected_ip or values.setdefault('SSH_USER',info['user']).strip()!=info['user']:
  raise RuntimeError('Credential target does not match pinned server map')
 if 'BEGIN OPENSSH PRIVATE KEY' not in values['SSH_PRIVATE_KEY']:
  raise RuntimeError('Invalid private SSH key')
 return values

def execute(server,command,timeout=30,max_output=12000,stdin=None):
 """stdin (bytes): sent after the command starts, then end-of-file (file writes keep content out of the command)."""
 if server not in SERVERS: raise ValueError('Unknown server')
 if not command or len(command)>2048 or '\x00' in command: raise ValueError('Invalid command')
 creds=credentials(server)
 key=paramiko.Ed25519Key.from_private_key(io.StringIO(creds['SSH_PRIVATE_KEY']),password=creds['SSH_KEY_PASSPHRASE'] or None)
 info=SERVER_INFO.get(server) or {}
 extra={k:info[k] for k,d in (('user','svc-hla-agent'),('port',22)) if info.get(k,d)!=d}  # only when not the defaults
 return run_ssh(SERVERS[server][0],key,creds['SSH_HOST_FINGERPRINT'].strip().split(),command,timeout,max_output,stdin,**extra)

def fingerprint_of(remote_key):
 return 'SHA256:'+base64.b64encode(hashlib.sha256(remote_key.asbytes()).digest()).decode().rstrip('=')

def host_fingerprint(host,port=22,timeout=8):
 """HLA_PROXMOX_V1: the ed25519 host key fingerprint a host presents (first contact of a VM the gateway created)."""
 sock=socket.create_connection((host,port),timeout=timeout)
 transport=paramiko.Transport(sock)
 try:
  transport.get_security_options().key_types=['ssh-ed25519']
  transport.start_client(timeout=timeout)
  remote_key=transport.get_remote_server_key()
  if remote_key.get_name()!='ssh-ed25519':raise RuntimeError('host has no ed25519 host key')
  return fingerprint_of(remote_key)
 finally:
  transport.close()

def run_ssh(host,key,pinned,command,timeout=30,max_output=12000,stdin=None,user='svc-hla-agent',port=22):
 """One command as user (svc-hla-agent) on host, only if its ed25519 host key matches one of the pinned fingerprints."""
 if not command or len(command)>2048 or '\x00' in command: raise ValueError('Invalid command')
 sock=socket.create_connection((host,port),timeout=8)
 transport=paramiko.Transport(sock)
 try:
  transport.get_security_options().key_types=['ssh-ed25519']  # the only host key type that is ever pinned
  transport.start_client(timeout=8)
  remote_key=transport.get_remote_server_key()
  fingerprint=fingerprint_of(remote_key)
  if remote_key.get_name()!='ssh-ed25519' or not pinned or fingerprint not in pinned:
   raise RuntimeError('SSH host fingerprint mismatch')
  transport.auth_publickey(user,key)
  if not transport.is_authenticated(): raise RuntimeError('SSH authentication failed')
  channel=transport.open_session(timeout=8)
  deadline=time.monotonic()+min(max(int(timeout),1),300)
  channel.exec_command(command)
  if stdin is not None:
   channel.settimeout(max(deadline-time.monotonic(),1))
   try:channel.sendall(stdin)
   except socket.timeout:
    channel.close()
    return {'exit_code':None,'timed_out':True,'stdout':'','stderr':'','truncated':False}
   except OSError:pass  # the command stopped reading early; its exit status and stderr say why
   channel.settimeout(None);channel.shutdown_write()
  out=bytearray(); err=bytearray(); truncated=False
  while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
   if time.monotonic()>deadline:
    channel.close()
    return {'exit_code':None,'timed_out':True,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace'),'truncated':truncated}
   for ready,read,buf in ((channel.recv_ready,channel.recv,out),(channel.recv_stderr_ready,channel.recv_stderr,err)):
    if ready():
     chunk=read(4096); room=max_output-len(out)-len(err)
     if room<=0: truncated=True
     else:
      buf.extend(chunk[:room]); truncated |= len(chunk)>room
   time.sleep(.02)
  return {'exit_code':channel.recv_exit_status(),'timed_out':False,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace'),'truncated':truncated}
 finally:
  transport.close()
