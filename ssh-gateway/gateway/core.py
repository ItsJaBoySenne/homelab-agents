"""SSH backend: credentials fetched from Infisical on every request; no key exposure."""
import base64, hashlib, io, json, os, socket, time, urllib.error, urllib.parse, urllib.request
import paramiko

# Example inventory: replace the names and addresses with your own servers (name -> (pinned IP, Infisical folder)).
# Several names have a role elsewhere: 'traefik' (routes), 'public-website' (websites), 'omada', 'sandbox'.
SERVERS = {
 'dev': ('192.168.10.104','/servers/dev'),
 'jellyfin': ('192.168.10.101','/servers/jellyfin'),
 'docker': ('192.168.10.102','/servers/docker'),
 'traefik': ('192.168.30.103','/servers/traefik'),
 'omada': ('192.168.20.101','/servers/omada'),
 'public-website': ('192.168.30.11','/servers/public-website'),
 'db-01': ('192.168.10.107','/servers/db-01'),
 'fs-01': ('192.168.10.108','/servers/fs-01'),
 'sandbox': ('192.168.40.204','/servers/sandbox'),  # sandbox VM on an isolated VLAN, resettable
}

# HLA_HA_V1: HTTP services reached by the gateway with a token from Infisical (gateway/homeassistant.py).
# name -> (pinned base URL, Infisical folder holding HA_URL and HA_TOKEN in the same project as the servers)
SERVICES = {
 'homeassistant': ('http://192.168.10.100:8123','/HomeAssistant'),
 # HLA_SECRETS_V1: the Agent Generated Infisical project (INFISICAL_GENERATED_PROJECT_ID), see gateway/agent_secrets.py.
 'secrets': (None,None),
 # HLA_GITHUB_V1: pull requests on allowlisted repos; token from GITHUB_TOKEN or Infisical, see gateway/github.py.
 'github': ('https://api.github.com',None),
 # HLA_HTTP_TOOLS_V1: HTTP API tools defined on the dashboard; each has its own fixed host, see gateway/http_tools.py.
 'http_tools': (None,None),
 # HLA_PROXMOX_V1: the Proxmox VE API of each node; NODE_IP must equal the pinned address, token from Infisical,
 # TLS checked against the node's CA (gateway/proxmox.py). Seeded at level 0 (off).
 'proxmox:proxmox': ('192.168.20.30','/Proxmox/Node_1'),
 'proxmox:pve2': ('192.168.20.31','/Proxmox/Node_2'),
 # HLA_OMADA_V1: the Omada controller's Open API (client-credentials app in /Omada: APPLICATION_NAME, CLIENT_ID,
 # CLIENT_SECRET); certificate pinned or checked against certs/omada-ca.pem (gateway/omada.py). Seeded at level 0 (off).
 'omada:controller': ('https://192.168.20.101:8043','/Omada'),
}

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

def api(base,path,payload=None,token=None):
 data=json.dumps(payload).encode() if payload is not None else None
 headers={'Content-Type':'application/json'}
 if token: headers['Authorization']='Bearer '+token
 req=urllib.request.Request(base+path,data=data,headers=headers,method='POST' if data is not None else 'GET')
 with urllib.request.urlopen(req,timeout=12) as resp: return json.load(resp)

def infisical_secrets(path,names):
 """Read the named secrets from one folder of the gateway's Infisical project (fresh login per request)."""
 base=env('INFISICAL_URL').rstrip('/')
 if not base.startswith('https://'): raise RuntimeError('Infisical HTTPS required')
 token=api(base,'/api/v1/auth/universal-auth/login',{'clientId':env('INFISICAL_CLIENT_ID'),'clientSecret':env('INFISICAL_CLIENT_SECRET')})['accessToken']
 query=urllib.parse.urlencode({'projectId':env('INFISICAL_PROJECT_ID'),'environment':os.getenv('INFISICAL_ENV','prod'),'secretPath':path})
 return {name:api(base,'/api/v4/secrets/'+name+'?'+query,token=token)['secret']['secretValue'] for name in names}

def credentials(server):
 if server not in SERVERS: raise ValueError('Unknown server')
 if server in DYNAMIC: return dynamic_credentials(server)
 expected_ip,path=SERVERS[server]
 base=env('INFISICAL_URL').rstrip('/')
 if not base.startswith('https://'): raise RuntimeError('Infisical HTTPS required')
 auth=api(base,'/api/v1/auth/universal-auth/login',{'clientId':env('INFISICAL_CLIENT_ID'),'clientSecret':env('INFISICAL_CLIENT_SECRET')})
 token=auth['accessToken']
 query=urllib.parse.urlencode({'projectId':env('INFISICAL_PROJECT_ID'),'environment':os.getenv('INFISICAL_ENV','prod'),'secretPath':path})
 values={}
 for name in ('SSH_HOST','SSH_USER','SSH_PRIVATE_KEY','SSH_HOST_FINGERPRINT'):
  result=api(base,'/api/v4/secrets/'+name+'?'+query,token=token)
  values[name]=result['secret']['secretValue']
 try:
  result=api(base,'/api/v4/secrets/SSH_KEY_PASSPHRASE?'+query,token=token)
  values['SSH_KEY_PASSPHRASE']=result['secret']['secretValue']
 except urllib.error.HTTPError as exc:
  if exc.code != 404: raise
  values['SSH_KEY_PASSPHRASE']=''
 if values['SSH_HOST'].strip()!=expected_ip or values['SSH_USER'].strip()!='svc-hla-agent':
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
 return run_ssh(SERVERS[server][0],key,creds['SSH_HOST_FINGERPRINT'].strip().split(),command,timeout,max_output,stdin)

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

def run_ssh(host,key,pinned,command,timeout=30,max_output=12000,stdin=None):
 """One command as svc-hla-agent on host, only if its ed25519 host key matches one of the pinned fingerprints."""
 if not command or len(command)>2048 or '\x00' in command: raise ValueError('Invalid command')
 sock=socket.create_connection((host,22),timeout=8)
 transport=paramiko.Transport(sock)
 try:
  transport.get_security_options().key_types=['ssh-ed25519']  # the only host key type that is ever pinned
  transport.start_client(timeout=8)
  remote_key=transport.get_remote_server_key()
  fingerprint=fingerprint_of(remote_key)
  if remote_key.get_name()!='ssh-ed25519' or not pinned or fingerprint not in pinned:
   raise RuntimeError('SSH host fingerprint mismatch')
  transport.auth_publickey('svc-hla-agent',key)
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
