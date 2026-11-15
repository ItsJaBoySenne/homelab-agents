"""Evidence-aware, workspace-isolated memory with no extra LLM calls.
Only selected verified tool fields become trusted facts; task prose is unverified history.

HLA_FACTS_V1: every fact records where it came from (source, task, agent, server), when it was verified and
when it expires. Expired facts are still shown, marked stale, so agents re-check them live instead of trusting
them. When a fact changes, the previous value is kept so the dashboard can show last known vs current.
"""
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

# How long an observation stays trustworthy (hours) before it must be re-checked live.
DIAGNOSTIC_TTL = {'identity': 720, 'system': 24, 'disk': 6, 'nginx-status': 1, 'docker-status': 1, 'failed-units': 1,
                  'containers': 6, 'shares': 168, 'path-permissions': 24, 'container-detail': 24}
# Logs are transient and may contain credentials; they are never stored.
NOT_CAPTURED = {'container-logs'}
CHANGE_TTL = 24 * 365   # what was changed, and when: history, kept for a year
PROXMOX_TTL = 24
# Conservative: any NAME_PASSWORD=, apikey:, secret= … means the output is not stored at all.
_SECRET = re.compile(r'(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|(password|passwd|secret|token|api[_-]?key)\w*["\']?\s*[:=])')

def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS agent_memories (
      id UUID PRIMARY KEY, workspace TEXT NOT NULL CHECK(workspace IN ('home','work')),
      key TEXT NOT NULL, fact TEXT NOT NULL, source TEXT NOT NULL,
      confidence TEXT NOT NULL CHECK(confidence IN ('verified','unverified')),
      verified_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(workspace,key))""")
    db.execute('CREATE INDEX IF NOT EXISTS agent_memories_workspace_idx ON agent_memories(workspace,updated_at DESC)')
    db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS partial_text TEXT')
    db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workspace TEXT')
    for column in ('kind TEXT', 'server TEXT', 'task_id UUID', 'agent_id UUID', 'expires_at TIMESTAMPTZ',
                   'previous_fact TEXT', 'previous_verified_at TIMESTAMPTZ'):
        db.execute('ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS ' + column)
    db.execute("UPDATE agent_memories SET kind='task' WHERE kind IS NULL AND source='task-history'")
    db.execute("UPDATE agent_memories SET kind='proxmox' WHERE kind IS NULL AND source='proxmox-api'")
    db.execute('CREATE INDEX IF NOT EXISTS agent_memories_server_idx ON agent_memories(workspace,server)')

def save(db,workspace,key,fact,source,verified,*,kind=None,server=None,task_id=None,agent_id=None,ttl_hours=None):
    now=datetime.now(timezone.utc)
    expires=now+timedelta(hours=ttl_hours) if ttl_hours else None
    # A changed value keeps the old one (and when it was verified) as "last known".
    db.execute("""INSERT INTO agent_memories(id,workspace,key,fact,source,confidence,verified_at,kind,server,task_id,agent_id,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(workspace,key) DO UPDATE SET
          previous_fact=CASE WHEN agent_memories.fact<>EXCLUDED.fact THEN agent_memories.fact ELSE agent_memories.previous_fact END,
          previous_verified_at=CASE WHEN agent_memories.fact<>EXCLUDED.fact THEN agent_memories.verified_at ELSE agent_memories.previous_verified_at END,
          fact=EXCLUDED.fact,source=EXCLUDED.source,confidence=EXCLUDED.confidence,verified_at=EXCLUDED.verified_at,
          kind=EXCLUDED.kind,server=EXCLUDED.server,task_id=EXCLUDED.task_id,agent_id=EXCLUDED.agent_id,
          expires_at=EXCLUDED.expires_at,updated_at=NOW()""",
       (uuid.uuid4(),workspace,key,fact[:1800],source,'verified' if verified else 'unverified',now if verified else None,
        kind,server,task_id,agent_id,expires))


def _clean(text):
    """Output safe to keep, or None when it may contain a secret (then nothing is stored)."""
    text=(text or '').strip()
    return None if not text or _SECRET.search(text) else text


def capture_tool(db, workspace, call, result, *, task_id=None, agent_id=None):
    """Record what one successful gateway call verified. Returns the fact key, or None when nothing was stored.

    call is the request sent to the gateway, result its JSON reply. Only exit codes and outcomes the gateway
    reported count; the model's own words never become facts here.
    """
    if workspace!='home' or not isinstance(call,dict) or not isinstance(result,dict):
        return None
    server,mode=call.get('server') or result.get('server'),call.get('mode')
    if not isinstance(server,str) or server=='sandbox':  # the sandbox is reset to a snapshot; its state is not knowledge
        return None
    meta=dict(server=server,task_id=task_id,agent_id=agent_id)
    ok=result.get('exit_code')==0 and not result.get('timed_out')
    if mode=='diagnostic':
        name,target=call.get('diagnostic'),call.get('target')
        output=_clean(result.get('stdout'))
        if not ok or name in NOT_CAPTURED or name not in DIAGNOSTIC_TTL or output is None:
            return None
        key='diag:'+server+':'+name+(':'+target if target else '')
        fact=(f'{name}{" of "+target if target else ""} on {server}:\n'+output[:1500]
              +('\n[output truncated]' if result.get('truncated') or len(output)>1500 else ''))
        save(db,workspace,key,fact,'ssh-gateway:diagnostic',True,kind='diagnostic',ttl_hours=DIAGNOSTIC_TTL[name],**meta)
        return key
    if mode=='maintenance':
        op,target,outcome=call.get('operation'),call.get('target'),result.get('outcome') or {}
        if not isinstance(op,str) or not isinstance(target,str):
            return None
        key=f'op:{server}:{op}:{target}'
        if not outcome.get('verified'):
            # A failed change is history worth knowing, never a fact about the server's state.
            save(db,workspace,key,f'{op} {target} on {server} was attempted and NOT verified ({outcome.get("error_class") or "unknown"}).',
                 'ssh-gateway:maintenance',False,kind='change',ttl_hours=CHANGE_TTL,**meta)
            return key
        detail={'install_package':f'Package {target} is installed on {server}'+(f' (version {outcome["version"]})' if outcome.get('version') else '')
                    +', verified by dpkg-query after the install.',
                'restart_service':f'Service {target} on {server} was restarted and is {outcome.get("service_state") or "active"}.',
                'restart_container':f'Container {target} on {server} was restarted and is running.',
                'compose_up':f'Compose project {target} on {server} was deployed (docker compose up) and verified.'}.get(op)
        if not detail:
            return None
        # Package versions stay true for a while; a restart says little about the state an hour later.
        save(db,workspace,key,detail,'ssh-gateway:maintenance',True,kind='change',
             ttl_hours=720 if op=='install_package' else 168 if op=='compose_up' else 1,**meta)
        return key
    if mode=='deploy':
        app=call.get('app')
        if not isinstance(app,str):
            return None
        # A failure (usually rolled back) must not overwrite which commit is running: it gets its own key.
        key=f'deploy:{server}:{app}' if result.get('verified') else f'deploy-failed:{server}:{app}'
        sha=(result.get('sha') or '')[:12]
        if result.get('verified'):
            fact=(f'App {app} on {server} runs commit {sha} ({call.get("ref")}), deployed and verified'
                  +(' after passing staging on the sandbox' if result.get('staging') else '')+f'; previous commit {(result.get("from") or "?")[:12]}.')
        else:
            fact=f'Deploying {app} ({call.get("ref")}) on {server} failed: {result.get("status")} ({result.get("error_class")}).'
        save(db,workspace,key,fact,'ssh-gateway:deploy',bool(result.get('verified')),kind='change',ttl_hours=CHANGE_TTL,**meta)
        return key
    if mode in ('approved_command','command'):
        command=call.get('command')
        if not isinstance(command,str):
            return None
        shown=_clean(command) or '[command not stored: it may contain a secret]'
        state='timed out' if result.get('timed_out') else 'exit '+str(result.get('exit_code'))
        key='cmd:'+server+':'+hashlib.sha256(command.encode()).hexdigest()[:16]
        save(db,workspace,key,f'Command ran on {server} ({state}): {shown[:600]}','ssh-gateway:command',ok,
             kind='change',ttl_hours=CHANGE_TTL,**meta)
        return key
    return None


def prune(db):
    """Retention: drop observations long past their expiry, old unverified task history and year-old changes."""
    db.execute("""DELETE FROM agent_memories WHERE (kind IS DISTINCT FROM 'change' AND expires_at < NOW() - interval '60 days')
        OR (source='task-history' AND updated_at < NOW() - interval '90 days')
        OR (kind='change' AND updated_at < NOW() - interval '365 days')""")

def capture_proxmox(db, inventory, workspace):
    if workspace != 'home' or not isinstance(inventory,dict):
        return
    for node in inventory.get('nodes',[])[:100]:
        name=node.get('node')
        if isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,90}',name):
            save(db,workspace,'pve:node:'+name,f'Proxmox node {name} exists in inventory.','proxmox-api',True,kind='proxmox',ttl_hours=PROXMOX_TTL)
    for guest in inventory.get('guests',[])[:300]:
        vmid=guest.get('vmid');name=guest.get('name');type_=guest.get('type');node=guest.get('node')
        if isinstance(vmid,int) and type_ in ('qemu','lxc') and isinstance(name,str) and isinstance(node,str):
            save(db,workspace,f'pve:guest:{vmid}',f'Guest {vmid}: name={name[:100]}, type={type_}, node={node[:90]}.','proxmox-api',True,
                 kind='proxmox',ttl_hours=PROXMOX_TTL)

def capture_task(db,task_id,workspace,text,answer):
    # Unverified result is retained for history but never promoted to a trusted infrastructure fact.
    # No extra model call, and no copied credentials into new memory records.
    if not answer or not workspace in ('home','work'):
        return
    db.execute("""INSERT INTO agent_memories(id,workspace,key,fact,source,confidence,kind,task_id)
        VALUES(%s,%s,%s,%s,%s,'unverified','task',%s) ON CONFLICT(workspace,key) DO NOTHING""",
        (uuid.uuid4(),workspace,'task:'+str(task_id),
         ('Prior task: '+text[:240]+' | Answer summary: '+answer[:540])[:790], 'task-history', task_id))

def freshness(row, now=None):
    """'fresh', 'stale' or 'unverified' for a fact row."""
    if row['confidence']!='verified':
        return 'unverified'
    now=now or datetime.now(timezone.utc)
    return 'stale' if row.get('expires_at') and row['expires_at']<=now else 'fresh'


def _stamp(value):
    return value.strftime('%Y-%m-%d %H:%M UTC') if value else 'never'


def label(row):
    state=freshness(row)
    if state=='fresh':
        return f'VERIFIED {_stamp(row["verified_at"])}, fresh until {_stamp(row.get("expires_at"))}' if row.get('expires_at') \
            else f'VERIFIED {_stamp(row["verified_at"])}'
    if state=='stale':
        return f'STALE: verified {_stamp(row["verified_at"])}, expired {_stamp(row["expires_at"])}; re-check live before relying on it'
    return 'UNVERIFIED' + (' TASK HISTORY' if row.get('kind')=='task' else '')


def retrieve(db,workspace,task_text,limit=7):
    words=set(re.findall(r'[a-z0-9_-]{3,}',task_text.casefold()))
    rows=db.execute("""SELECT key,fact,source,confidence,verified_at,expires_at,kind FROM agent_memories
        WHERE workspace=%s ORDER BY updated_at DESC LIMIT 400""",(workspace,)).fetchall()
    now=datetime.now(timezone.utc)
    scored=[]
    for r in rows:
        overlap=len(words & set(re.findall(r'[a-z0-9_-]{3,}',(r['key']+' '+r['fact']).casefold())))
        if overlap:
            scored.append((overlap+{'fresh':2,'stale':1,'unverified':0}[freshness(r,now)],r))
    scored.sort(key=lambda x:x[0],reverse=True)
    lines=[]
    for _,r in scored[:limit]:
        lines.append(f'[{label(r)}; {r["source"]}] {r["fact"][:800]}')
    return '\n'.join(lines)
