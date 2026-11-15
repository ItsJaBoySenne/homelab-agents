"""Per-agent, per-server autonomy policy, enforced by the gateway on every request (HLA_POLICY_V1).

Effective level on a server = min(agent level, server level). Levels are cumulative capability sets:
  0 nothing · 1 read-only diagnostics · 2 install allowlisted packages · 3 restart allowlisted services
  and containers · 4 deploy approved Compose projects · 5 exact commands approved once in the CLI ·
  6 free shell: any command without approval (meant for the disposable sandbox).
Unhinged mode (time-limited or until switched off) lets level-5 agents run any command without approval
on every server whose own level is at least 1; a server at level 0 stays out of reach.
"""
import json
import re
import time

from .core import SERVERS, SERVICES, DYNAMIC, SERVER_INFO, SANDBOX
from . import http_tools

LEVELS = {0: 'Nothing', 1: 'Read-only diagnostics', 2: 'Install allowlisted packages',
          3: 'Restart allowlisted services and containers', 4: 'Deploy approved Compose projects',
          5: 'Commands you approved once', 6: 'Free shell (no approval)'}
# The level a server starts with: default_level from the inventory, else 6 for the sandbox and 1 (diagnostics) for others.
DEFAULT_SERVER_LEVEL = {name: (info['default_level'] if info.get('default_level') is not None else 6 if info['sandbox'] else 1)
                        for name, info in SERVER_INFO.items()}
# HLA_PROXMOX_V1: the Proxmox nodes (proxmox:<node>) start off; every other service starts read-only.
# HLA_OMADA_V1: so does the Omada controller (omada:controller).
DEFAULT_SERVICE_LEVEL = {name: 0 for name in SERVICES if name.startswith(('proxmox:', 'omada:'))}
# HLA_OMADA_V1: services that unhinged mode never lifts (the network controller: one wrong write cuts off the home network).
NO_UNHINGED = ('omada:',)
_AGENT_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_PROJECT = re.compile(r'^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$')
UNHINGED_HOURS = (1, 8, 24, None)  # None = until switched off
# HLA_DEPLOY_V1: apps deployable with gateway/deploy.py (level 4 on the app's server).
_APP_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{1,39}$')
_REPO = re.compile(r'^(git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+|(https|ssh)://[A-Za-z0-9.@:-]+/[A-Za-z0-9._/-]+)$')
# Checked on the server itself, so the same URL works on the sandbox and the target.
_GITHUB_REPO = re.compile(r'^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$')
# HLA_MCP_V1: registered MCP servers are services named mcp:<name> (gateway/mcp.py); tools have levels 1..6 or are disabled.
MCP_PREFIX = 'mcp:'
_MCP_TOOL = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
_HEALTH = re.compile(r'^http://(127\.0\.0\.1|localhost)(:[0-9]{1,5})?(/[A-Za-z0-9._~/?=&%-]*)?$')


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS server_policy (server TEXT PRIMARY KEY, level INTEGER NOT NULL, compose_projects TEXT NOT NULL DEFAULT \'[]\')')
    db.execute('CREATE TABLE IF NOT EXISTS agent_policy (agent_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT \'\', level INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS policy_settings (key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS policy_audit (id INTEGER PRIMARY KEY, timestamp INTEGER, actor TEXT, change TEXT)')
    for server in SERVERS:
        # HLA_PROXMOX_V1: servers the gateway created on Proxmox start at 0 (off) until the owner raises them.
        db.execute('INSERT OR IGNORE INTO server_policy(server,level) VALUES (?,?)',
                   (server, 0 if server in DYNAMIC else DEFAULT_SERVER_LEVEL.get(server, 1)))
    # HLA_HA_V1: services (Home Assistant) get a level like servers; they start read-only.
    for service in SERVICES:
        db.execute('INSERT OR IGNORE INTO server_policy(server,level) VALUES (?,?)', (service, DEFAULT_SERVICE_LEVEL.get(service, 1)))
    db.execute('''CREATE TABLE IF NOT EXISTS deploy_apps (name TEXT PRIMARY KEY, server TEXT NOT NULL, path TEXT NOT NULL,
        repo TEXT NOT NULL, health_url TEXT NOT NULL DEFAULT '', staging INTEGER NOT NULL DEFAULT 1)''')


def apps(db):
    return {name: {'name': name, 'server': server, 'path': path, 'repo': repo, 'health_url': health, 'staging': bool(staging)}
            for name, server, path, repo, health, staging in
            db.execute('SELECT name,server,path,repo,health_url,staging FROM deploy_apps ORDER BY name')}


def app(db, name):
    return apps(db).get(name) if isinstance(name, str) else None


def github_repos(db):
    """HLA_GITHUB_V1: {"owner/name" (lowercase): {"merge": bool}}; the github service reaches only these. None by default."""
    row = db.execute("SELECT value FROM policy_settings WHERE key='github_repos'").fetchone()
    try:
        value = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _mcp_reviewed(db, server, names):
    """An override is the owner's review of a tool: new tools of that server lose their 'new' mark (gateway/mcp.py)."""
    if not names:
        return
    row = db.execute('SELECT tools FROM mcp_servers WHERE name=?', (server,)).fetchone()
    try:
        tools = json.loads(row[0]) if row and row[0] else None
    except ValueError:
        tools = None
    if not isinstance(tools, list):
        return
    touched = False
    for tool in tools:
        if isinstance(tool, dict) and tool.get('name') in names and (tool.get('new') or tool.get('changed')):
            tool.pop('new', None); tool.pop('changed', None)
            touched = True
    if touched:
        db.execute('UPDATE mcp_servers SET tools=? WHERE name=?', (json.dumps(tools), server))


def mcp_tools(db):
    """HLA_MCP_V1: per-tool overrides {"server": {"tool": {"level": 1..6} | {"disabled": true}}}; none by default."""
    row = db.execute("SELECT value FROM policy_settings WHERE key='mcp_tools'").fetchone()
    try:
        value = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _service(name):
    """A service row: the fixed services, and MCP servers (their row exists while the server is registered)."""
    return name in SERVICES or name.startswith(MCP_PREFIX)


def unhinged_until(db):
    """0 = until switched off, a timestamp while active, None when off or expired."""
    row = db.execute("SELECT value FROM policy_settings WHERE key='unhinged_until'").fetchone()
    if not row or row[0] is None:
        return None
    until = int(row[0])
    return until if until == 0 or until > time.time() else None


def snapshot(db):
    until = unhinged_until(db)
    return {
        'levels': LEVELS,
        'servers': {s: {'level': lvl, 'compose_projects': json.loads(projects)}
                    for s, lvl, projects in db.execute('SELECT server,level,compose_projects FROM server_policy ORDER BY server')
                    if s in SERVERS},
        'services': {s: {'level': lvl} for s, lvl in db.execute('SELECT server,level FROM server_policy ORDER BY server') if _service(s)},
        'agents': {a: {'name': name, 'level': lvl} for a, name, lvl in db.execute('SELECT agent_id,name,level FROM agent_policy')},
        'unhinged': {'active': until is not None, 'until': until or None},
        'apps': apps(db),
        'github': {'repos': github_repos(db)},
        # HLA_HTTP_TOOLS_V1: the defined HTTP tools and the level each needs (definitions: GET /v1/http_tools).
        'http_tools': {'tools': {t['name']: {'level': t['level'], 'method': t['method'], 'approval_required': t['approval_required'],
                                             'enabled': t['enabled']} for t in http_tools.listing(db)}},
        'mcp': {'tools': mcp_tools(db)},
    }


def effective(db, agent_id):
    """Per server: the level this agent may act at, and whether commands skip approval."""
    row = db.execute('SELECT level FROM agent_policy WHERE agent_id=?', (str(agent_id or ''),)).fetchone()
    agent_level = row[0] if row else 0
    unhinged = unhinged_until(db) is not None and agent_level >= 5
    result = {}
    deployable = {}
    for name, value in apps(db).items():
        deployable.setdefault(value['server'], []).append(name)
    for server, level, projects in db.execute('SELECT server,level,compose_projects FROM server_policy'):
        if server not in SERVERS:
            continue
        lvl = min(agent_level, level)
        if unhinged and level >= 1: lvl = max(lvl, 5)  # unhinged lifts level-5 agents to 5 everywhere; 6 stays earned
        result[server] = {'level': lvl, 'unhinged': unhinged and level >= 1, 'free_shell': lvl >= 6 or (unhinged and level >= 1),
                          'compose_projects': json.loads(projects), 'apps': deployable.get(server, [])}
    return result


def effective_services(db, agent_id):
    """Per service (Home Assistant): the level this agent may act at; unhinged mode works as on servers."""
    row = db.execute('SELECT level FROM agent_policy WHERE agent_id=?', (str(agent_id or ''),)).fetchone()
    agent_level = row[0] if row else 0
    unhinged = unhinged_until(db) is not None and agent_level >= 5
    result = {}
    for service, level in db.execute('SELECT server,level FROM server_policy'):
        if not _service(service):
            continue
        lvl = min(agent_level, level)
        lifted = unhinged and level >= 1 and not service.startswith(NO_UNHINGED)
        if lifted: lvl = max(lvl, 5)
        result[service] = {'level': lvl, 'unhinged': lifted, 'free_shell': lvl >= 6 or lifted}
    if 'github' in result:  # HLA_GITHUB_V1: the agent's tool lists the repositories it can reach
        repos = github_repos(db)
        result['github'].update(repos=sorted(repos), merge_repos=sorted(r for r, v in repos.items() if v.get('merge')))
    if 'http_tools' in result:  # HLA_HTTP_TOOLS_V1: each tool this agent may call becomes its own function
        h = result['http_tools']
        h['tools'] = http_tools.for_agent(db, h['level'], h['free_shell'])
    return result


def update(db, change, actor):
    """Apply a validated partial change: {servers: {name: {level, compose_projects}}, agents: {id: {name, level} | null},
    unhinged: {enabled, hours}, github: {repos}, mcp: {tools: {server: {tool: override | null}}}}.
    Raises ValueError with a message safe to show."""
    if not isinstance(change, dict):
        raise ValueError('change must be an object')
    servers, agents, unhinged = change.get('servers') or {}, change.get('agents') or {}, change.get('unhinged')
    new_apps = change.get('apps') or {}
    if not isinstance(new_apps, dict):
        raise ValueError('apps must be an object')
    for name, value in new_apps.items():
        if not _APP_NAME.match(str(name)):
            raise ValueError('app names are 2-40 lowercase letters, digits and dashes')
        if value is None:
            continue
        if not isinstance(value, dict) or value.get('server') not in SERVERS or value.get('server') == SANDBOX:
            raise ValueError('app server must be a managed server other than the sandbox')
        path = value.get('path')
        # Length limits keep the deploy scripts under the gateway's 2048-character cap.
        if not isinstance(path, str) or len(path) > 120 or not _PROJECT.match(path) or '..' in path.split('/') or path.count('/') < 2:
            raise ValueError('app path must be an absolute folder below / (e.g. /opt/site)')
        if not isinstance(value.get('repo'), str) or len(value['repo']) > 200 or not _REPO.match(value['repo']):
            raise ValueError('repo must be a git@host:owner/repo.git, ssh:// or https:// URL')
        health = value.get('health_url') or ''
        if not isinstance(health, str) or len(health) > 120 or (health and not _HEALTH.match(health)):
            raise ValueError('health_url must be http://127.0.0.1[:port]/path or http://localhost[:port]/path (checked on the server)')
    mcp_services = {s for (s,) in db.execute("SELECT server FROM server_policy WHERE server LIKE 'mcp:%'")}
    for server, value in servers.items():
        if server not in SERVERS and server not in SERVICES and server not in mcp_services or not isinstance(value, dict):
            raise ValueError(f'unknown server {server!r}')
        if 'level' in value and value['level'] not in LEVELS:
            raise ValueError('level must be 0..6')
        projects = value.get('compose_projects')
        if projects is not None and (server in SERVICES or server in mcp_services):
            raise ValueError('services have no compose projects')
        if projects is not None and (not isinstance(projects, list) or len(projects) > 20 or not all(
                isinstance(p, str) and _PROJECT.match(p) and '..' not in p.split('/') for p in projects)):
            raise ValueError('compose_projects must be absolute directory paths')
    for agent_id, value in agents.items():
        if not _AGENT_ID.match(str(agent_id)):
            raise ValueError('agent ids are UUIDs')
        if value is not None and (not isinstance(value, dict) or value.get('level') not in LEVELS):
            raise ValueError('agent level must be 0..6')
    github = {} if change.get('github') is None else change['github']
    repos = github.get('repos') if isinstance(github, dict) else None
    if not isinstance(github, dict) or (repos is not None and not isinstance(repos, dict)) or len(repos or {}) > 50:
        raise ValueError('github must be {"repos": {"owner/name": {"merge": false} | null}}')
    for repo, value in (repos or {}).items():
        if not _GITHUB_REPO.match(str(repo)) or '..' in str(repo):
            raise ValueError('GitHub repositories are owner/name, e.g. octo/site')
        if value is not None and (not isinstance(value, dict) or not isinstance(value.get('merge', False), bool)):
            raise ValueError('a GitHub repository entry is {"merge": true|false}, or null to remove it')
    mcp = {} if change.get('mcp') is None else change['mcp']
    mcp_changes = mcp.get('tools') if isinstance(mcp, dict) else None
    if not isinstance(mcp, dict) or (mcp_changes is not None and not isinstance(mcp_changes, dict)):
        raise ValueError('mcp must be {"tools": {"server": {"tool": {"level": 1..6} | {"disabled": true} | null}}}')
    for name, tools in (mcp_changes or {}).items():
        if MCP_PREFIX + str(name) not in mcp_services or not isinstance(tools, dict) or len(tools) > 200:
            raise ValueError(f'unknown MCP server {name!r}')
        for tool, value in tools.items():
            if not _MCP_TOOL.match(str(tool)):
                raise ValueError('invalid MCP tool name')
            if value is not None and not (isinstance(value, dict) and (value == {'disabled': True} or (
                    set(value) == {'level'} and value['level'] in range(1, 7) and not isinstance(value['level'], bool)))):
                raise ValueError('an MCP tool override is {"level": 1..6}, {"disabled": true}, or null for the default')
    if unhinged is not None and (not isinstance(unhinged, dict) or (unhinged.get('enabled') and unhinged.get('hours') not in UNHINGED_HOURS)):
        raise ValueError('unhinged needs enabled and hours of 1, 8, 24 or null')
    db.execute('BEGIN IMMEDIATE')
    for server, value in servers.items():
        if 'level' in value:
            db.execute('UPDATE server_policy SET level=? WHERE server=?', (value['level'], server))
        if value.get('compose_projects') is not None:
            db.execute('UPDATE server_policy SET compose_projects=? WHERE server=?', (json.dumps(value['compose_projects']), server))
    for agent_id, value in agents.items():
        if value is None:
            db.execute('DELETE FROM agent_policy WHERE agent_id=?', (agent_id,))
        else:
            db.execute('INSERT INTO agent_policy(agent_id,name,level) VALUES (?,?,?) ON CONFLICT(agent_id) DO UPDATE SET name=excluded.name, level=excluded.level',
                       (agent_id, str(value.get('name') or '')[:80], value['level']))
    for name, value in new_apps.items():
        if value is None:
            db.execute('DELETE FROM deploy_apps WHERE name=?', (name,))
        else:
            db.execute('''INSERT INTO deploy_apps(name,server,path,repo,health_url,staging) VALUES (?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET server=excluded.server,path=excluded.path,repo=excluded.repo,
                health_url=excluded.health_url,staging=excluded.staging''',
                (name, value['server'], value['path'], value['repo'], value.get('health_url') or '', int(value.get('staging', True) is not False)))
    if repos:
        current = github_repos(db)
        for repo, value in repos.items():
            if value is None: current.pop(repo.lower(), None)
            else: current[repo.lower()] = {'merge': value.get('merge') is True}
        db.execute("INSERT INTO policy_settings(key,value) VALUES ('github_repos',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(current, sort_keys=True),))
    if mcp_changes:
        current = mcp_tools(db)
        for name, tools in mcp_changes.items():
            entry = current.setdefault(name, {})
            for tool, value in tools.items():
                if value is None: entry.pop(tool, None)
                else: entry[tool] = value
            if not entry: current.pop(name)
            _mcp_reviewed(db, name, [t for t, v in tools.items() if v is not None])
        db.execute("INSERT INTO policy_settings(key,value) VALUES ('mcp_tools',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(current, sort_keys=True),))
    if unhinged is not None:
        until = None
        if unhinged.get('enabled'):
            until = 0 if unhinged.get('hours') is None else int(time.time()) + 3600 * unhinged['hours']
        db.execute("INSERT INTO policy_settings(key,value) VALUES ('unhinged_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (until,))
    db.execute('INSERT INTO policy_audit(timestamp,actor,change) VALUES (?,?,?)', (int(time.time()), str(actor)[:128], json.dumps(change)[:4000]))
    db.execute('COMMIT')
    return snapshot(db)
