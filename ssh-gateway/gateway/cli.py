"""Private multi-server administrative CLI. Mutating commands need exact approvals.

Secret store (the owner's commands; never print a value):
  python -m gateway.cli secrets status                       backend, whether it is usable, how many names
  python -m gateway.cli secrets list [--generated]           names only (platform namespace by default)
  python -m gateway.cli secrets set NAME [--generated]       the value comes from stdin (a pipe, or typed without echo)
  python -m gateway.cli secrets delete NAME [--generated]
  python -m gateway.cli secrets import-ssh-key SERVER PATH   SERVER's private key (PATH, or - for stdin) as servers/SERVER/SSH_PRIVATE_KEY
  python -m gateway.cli secrets pin-host-key SERVER          record SERVER's ed25519 host key as servers/SERVER/SSH_HOST_FINGERPRINT
  python -m gateway.cli secrets export NAME --out FILE       write one value to a new file (mode 0600), never to a terminal
Platform names are FOLDER/KEY (servers/web/SSH_PRIVATE_KEY, GitHub/GITHUB_TOKEN, HomeAssistant/HA_TOKEN,
Proxmox/<node>/TOKEN_ID, Proxmox/<node>/TOKEN_SECRET, Omada/CLIENT_ID, Omada/CLIENT_SECRET); generated names are flat.
"""
import argparse, getpass, hashlib, io, json, os, sqlite3, stat, sys, time
from pathlib import Path
from .core import SERVERS, SERVER_INFO, execute, load_dynamic
from . import policy, investigate, deploy, homeassistant, audit, mcp, routes, proxmox, omada

DB=os.getenv('GATEWAY_DB','/data/approvals-v2.sqlite3')
DIAGNOSTICS={'identity':'whoami && hostname && id', 'system':'hostnamectl && uptime && free -h && df -h', 'nginx-status':'systemctl is-active nginx || true', 'docker-status':'systemctl is-active docker || true',
 'disk':'df -h -x tmpfs -x devtmpfs && df -i -x tmpfs -x devtmpfs', 'failed-units':'systemctl --failed --no-pager --no-legend || true'}

def db_open():
 Path(DB).parent.mkdir(exist_ok=True)
 db=sqlite3.connect(DB,timeout=15,isolation_level=None)
 db.execute('CREATE TABLE IF NOT EXISTS approvals (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
 db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, timestamp INTEGER, server TEXT, action TEXT, digest TEXT, exit_code INTEGER, timed_out INTEGER, truncated INTEGER)')
 # Who granted an approval from the dashboard (chat approvals); NULL for CLI and agent rows.
 if 'actor' not in {r[1] for r in db.execute('PRAGMA table_info(audit)')}:db.execute('ALTER TABLE audit ADD COLUMN actor TEXT')
 proxmox.migrate(db)  # HLA_PROXMOX_V1: servers_dynamic and managed_vms, before the policy seeds server levels
 load_dynamic(db)
 policy.migrate(db)
 deploy.migrate(db)
 homeassistant.migrate(db)
 audit.migrate(db)
 mcp.migrate(db)  # HLA_MCP_V1
 routes.migrate(db)  # HLA_ROUTES_V1
 omada.migrate(db)  # HLA_OMADA_V1
 return db

def digest(server,command): return hashlib.sha256((server+'\n'+command).encode()).hexdigest()

def invoke(db,server,command,action,need_approval):
 d=digest(server,command)
 if need_approval:
  db.execute('BEGIN IMMEDIATE')
  changed=db.execute('UPDATE approvals SET used=1 WHERE digest=? AND used=0 AND expires>?',(d,int(time.time())))
  db.execute('COMMIT')
  if changed.rowcount!=1:
   print('DENIED: exact command not approved, expired, or already used',file=sys.stderr); return 2
 try:
  result=execute(server,command)
 except Exception as exc:
  db.execute('INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated) VALUES (?,?,?,?,?,?,?)',(int(time.time()),server,action,d,None,0,0))
  print('EXECUTION FAILED:',type(exc).__name__,file=sys.stderr);return 3
 db.execute('INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated) VALUES (?,?,?,?,?,?,?)',(int(time.time()),server,action,d,result['exit_code'],int(result['timed_out']),int(result['truncated'])))
 print(json.dumps({'server':server,**result},indent=2))
 return 0 if result['exit_code']==0 else 4

def _say(*parts):
 print(*parts,flush=True)

def _read_value(prompt):
 """A value from stdin: piped (one trailing newline dropped) or typed at a terminal without echo. Never from argv."""
 if sys.stdin.isatty():
  value=getpass.getpass(prompt)
 else:
  value=sys.stdin.read()
  if value.endswith('\n'):value=value[:-1]
  if value.endswith('\r'):value=value[:-1]
 return value

def secrets_command(args):
 """The secrets subcommands; returns the exit code. Values are read from stdin or files and never printed."""
 from . import secret_store as ss, agent_secrets
 ns=ss.GENERATED if getattr(args,'generated',False) else ss.PLATFORM
 try:
  store=ss.get()
  if args.secrets_action=='status':
   info=store.describe()
   out={'backend':info['backend'],'configured':info['configured']}
   if info['backend']=='local':out['file']=info['location']
   if info['configured']:
    out['platform_names']=len(store.names(ss.PLATFORM))
    out['generated_names']=len(store.names(ss.GENERATED)) if store.configured(ss.GENERATED) else None
   _say(json.dumps(out,indent=2));return 0
  if not store.configured(ns):
   detail=agent_secrets.not_configured_detail() if ns==ss.GENERATED else (
    'HLA_SECRETS_KEY is not set (or not valid)' if store.backend=='local' else 'the INFISICAL_* settings are incomplete')
   print('SECRET STORE NOT CONFIGURED: '+detail,file=sys.stderr);return 2
  if args.secrets_action=='list':
   for name in store.names(ns):_say(name)
   return 0
  if args.secrets_action=='set':
   name=ss.check_name(ns,args.name)
   if ns==ss.GENERATED and name.startswith(agent_secrets.RESERVED):
    print('REFUSED: names starting with '+', '.join(agent_secrets.RESERVED)+' are reserved',file=sys.stderr);return 2
   value=_read_value('Value for '+name+' (not shown): ')
   if not value.strip():print('REFUSED: empty value',file=sys.stderr);return 2
   replaced=store.put(ns,name,value,'set by the owner (gateway CLI)')
   value=None
   _say(('replaced ' if replaced else 'stored ')+ns+' secret '+name);return 0
  if args.secrets_action=='delete':
   name=ss.check_name(ns,args.name)
   if store.delete(ns,name):_say('deleted '+ns+' secret '+name);return 0
   print('NOT FOUND: '+name,file=sys.stderr);return 1
  if args.secrets_action=='export':
   name=ss.check_name(ns,args.name)
   if ns==ss.PLATFORM:
    folder,key=ss.split_platform(name)
    value=store.read_platform(folder,(),(key,)).get(key)
   else:value=store.generated()[0].get(name)
   if value is None:print('NOT FOUND: '+name,file=sys.stderr);return 1
   if args.out=='/dev/tty':print('REFUSED: not to a terminal',file=sys.stderr);return 2
   if args.out in ('-','/dev/stdout'):
    if sys.stdout.isatty():print('REFUSED: stdout is a terminal; redirect it to a file',file=sys.stderr);return 2
    sys.stdout.write(value);sys.stdout.flush();return 0
   try:fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   except FileExistsError:print('REFUSED: '+args.out+' exists',file=sys.stderr);return 2
   with os.fdopen(fd,'w') as fh:fh.write(value)
   value=None
   print('wrote '+name+' to '+args.out+' (mode 0600)',file=sys.stderr);return 0
  if args.secrets_action in ('import-ssh-key','pin-host-key'):
   if args.server not in SERVER_INFO:
    print('UNKNOWN SERVER: '+args.server+' is not in the inventory'+(' ('+', '.join(sorted(SERVER_INFO))+')' if SERVER_INFO else ' (it has no servers)'),file=sys.stderr);return 2
   folder=SERVERS[args.server][1]
   if args.secrets_action=='pin-host-key':
    from .core import host_fingerprint
    info=SERVER_INFO[args.server]
    try:fp=host_fingerprint(info['host'],info['port'])
    except Exception as exc:print('CANNOT REACH '+info['host']+':'+str(info['port'])+' ('+type(exc).__name__+')',file=sys.stderr);return 3
    store.put(ss.PLATFORM,ss.join_platform(folder,'SSH_HOST_FINGERPRINT'),fp,'pinned on first contact (gateway CLI)')
    _say('pinned '+args.server+' host key '+fp+' (check it against: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server)')
    return 0
   import paramiko
   try:text=sys.stdin.read() if args.path=='-' else Path(args.path).read_text()
   except (OSError,UnicodeDecodeError) as exc:print('CANNOT READ KEY FILE: '+args.path+' ('+type(exc).__name__+')',file=sys.stderr);return 2
   if 'BEGIN OPENSSH PRIVATE KEY' not in text:
    print('REFUSED: not an OpenSSH private key (ed25519, as made by ssh-keygen -t ed25519)',file=sys.stderr);return 2
   encrypted=False
   try:key=paramiko.Ed25519Key.from_private_key(io.StringIO(text))
   except paramiko.PasswordRequiredException:key,encrypted=None,True
   except (paramiko.SSHException,ValueError) as exc:
    print('REFUSED: not a readable ed25519 private key ('+type(exc).__name__+')',file=sys.stderr);return 2
   name=ss.join_platform(folder,'SSH_PRIVATE_KEY')
   store.put(ss.PLATFORM,name,text if text.endswith('\n') else text+'\n','imported by the owner (gateway CLI)')
   text=None
   _say('stored '+name+(' (public key '+key.get_name()+' '+key.fingerprint+')' if key is not None and hasattr(key,'fingerprint') else ''))
   if encrypted:_say('the key is passphrase-protected: also store '+ss.join_platform(folder,'SSH_KEY_PASSPHRASE')+' with "secrets set"')
   if args.fingerprint:
    store.put(ss.PLATFORM,ss.join_platform(folder,'SSH_HOST_FINGERPRINT'),args.fingerprint,'set by the owner (gateway CLI)')
    _say('pinned host key '+args.fingerprint)
   elif not SERVER_INFO[args.server]['host_fingerprint']:
    _say('next: pin its host key with "secrets pin-host-key '+args.server+'" (or host_fingerprint in the inventory)')
   return 0
 except ss.SecretStoreError as exc:
  print('SECRET STORE ERROR ('+exc.code+'): '+str(exc),file=sys.stderr);return 2
 return 2

def main():
 parser=argparse.ArgumentParser(description='Homelab Agents SSH Gateway v2 (private CLI)')
 sub=parser.add_subparsers(dest='action',required=True)
 sub.add_parser('servers')
 sp=sub.add_parser('secrets',help='the secret store (values are never printed)')
 ssub=sp.add_subparsers(dest='secrets_action',required=True)
 ssub.add_parser('status')
 for name in ('list','set','delete','export'):
  p=ssub.add_parser(name)
  if name!='list':p.add_argument('name')
  p.add_argument('--generated',action='store_true',help='the generated namespace (agent secrets) instead of the platform one')
  if name=='export':p.add_argument('--out',required=True,help='a new file (created 0600), or - for a redirected stdout')
 p=ssub.add_parser('import-ssh-key');p.add_argument('server');p.add_argument('path',help='the private key file, or - for stdin')
 p.add_argument('--fingerprint',help="the server's ed25519 host key fingerprint (SHA256:...) to pin as well")
 p=ssub.add_parser('pin-host-key');p.add_argument('server')
 for action in ('diagnostic','approve','run'):
  p=sub.add_parser(action);p.add_argument('--server',required=True,choices=SERVERS)
  if action=='diagnostic':
   p.add_argument('name',choices=[*DIAGNOSTICS,*investigate.UNTARGETED,*investigate.TARGETED])
   p.add_argument('--target',help='path or container for path-permissions, container-detail, container-logs')
  else:p.add_argument('--command',required=True)
  if action=='approve':p.add_argument('--minutes',type=int,default=10)
 args=parser.parse_args()
 if args.action=='secrets':return secrets_command(args)
 if args.action=='servers':
  print(json.dumps({name:{'ip':ip,'user':(SERVER_INFO.get(name) or {}).get('user','svc-hla-agent'),'port':(SERVER_INFO.get(name) or {}).get('port',22),
                          'secret_path':path,'description':(SERVER_INFO.get(name) or {}).get('description',''),
                          'execution':'diagnostics or one-time approved commands'} for name,(ip,path) in SERVERS.items()},indent=2));return 0
 with db_open() as db:
  if args.action=='approve':
   if not 1<=args.minutes<=60: parser.error('minutes must be 1..60')
   if not args.command.strip() or len(args.command)>2048:parser.error('Invalid command')
   print('TARGET:',args.server,SERVERS[args.server][0]);print('COMMAND:',repr(args.command));print('EXPIRES:',args.minutes,'minutes')
   if input('Type APPROVE to grant one execution: ')!='APPROVE':return 1
   d=digest(args.server,args.command)
   db.execute('INSERT OR REPLACE INTO approvals(digest,expires,used) VALUES (?,?,0)',(d,int(time.time())+args.minutes*60))
   print('Approved:',d[:16]);return 0
  if args.action=='diagnostic':
   try:cmd=DIAGNOSTICS.get(args.name) or investigate.resolve(args.name,args.target)
   except ValueError as exc:parser.error(str(exc))
   return invoke(db,args.server,cmd,args.action,False)
  return invoke(db,args.server,args.command,args.action,True)

if __name__=='__main__':sys.exit(main())
