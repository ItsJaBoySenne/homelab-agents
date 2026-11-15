"""Private multi-server administrative CLI. Mutating commands need exact approvals."""
import argparse, hashlib, json, os, sqlite3, sys, time
from pathlib import Path
from .core import SERVERS, execute, load_dynamic
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

def main():
 parser=argparse.ArgumentParser(description='Homelab Agents SSH Gateway v2 (private CLI)')
 sub=parser.add_subparsers(dest='action',required=True)
 sub.add_parser('servers')
 for action in ('diagnostic','approve','run'):
  p=sub.add_parser(action);p.add_argument('--server',required=True,choices=SERVERS)
  if action=='diagnostic':
   p.add_argument('name',choices=[*DIAGNOSTICS,*investigate.UNTARGETED,*investigate.TARGETED])
   p.add_argument('--target',help='path or container for path-permissions, container-detail, container-logs')
  else:p.add_argument('--command',required=True)
  if action=='approve':p.add_argument('--minutes',type=int,default=10)
 args=parser.parse_args()
 if args.action=='servers':
  print(json.dumps({name:{'ip':ip,'infisical_path':path,'execution':'diagnostics or one-time approved commands'} for name,(ip,path) in SERVERS.items()},indent=2));return 0
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
