"""Private gateway API. Every request is checked against the calling agent's policy (gateway/policy.py).

POST /v1/execute          tool token   {agent_id, server, mode, ...}; modes write_file {path, content, append} and
                          read_file {path, offset, length} need a free shell (level 6 or unhinged), see gateway/files.py
GET  /v1/policy           tool token   full policy (for the dashboard)
GET  /v1/policy/effective tool token   ?agent_id=… per-server effective level for one agent
PUT  /v1/policy           admin token  {change, actor}; the tool token alone cannot raise its own permissions
POST /v1/approvals        admin token  {server, command, actor}: one execution of that exact command (approve-in-chat)
POST /v1/deploy           tool token {app, ref, agent_id} (level 4 on the app's server) or admin token {app, ref | sha, actor};
                          sha (roll back to a commit that ran before) skips staging and is admin-only
GET  /v1/deployments      tool token   ?app=… recent deployments
POST /v1/homeassistant    tool token   {agent_id, operation, ...}: Home Assistant REST/WebSocket with the token from
                          Infisical, levels per gateway/homeassistant.py (dashboard saves keep a backup)
POST /v1/secrets          tool token   {agent_id, operation list|create, name, kind, replace}: agent secrets in the Agent
                          Generated Infisical project; {{secret:NAME}} in commands and written files is filled in here
                          and every value is redacted from what POST endpoints return (gateway/agent_secrets.py)
GET  /v1/audit            tool or admin token  ?agent=&server=&action=&q=&since=&until=&limit=&before_id=&facets=1
                          the audit log, newest first (max 200 per page), secrets masked (gateway/audit.py)
POST requests may carry X-HLA-Task: <dashboard task id>; it is stored on their audit rows (nothing else).
POST /v1/github           tool token   {agent_id, operation pr_status|list_prs|create_pr|comment|merge, repo, ...}: pull
                          requests on allowlisted repos with the token held here (gateway/github.py); admin token
                          {actor, operation pr_status|list_prs, repo, ...} for the dashboard (read only)
GET  /v1/credentials      admin token  names and metadata of stored credentials (never values; gateway/credentials.py)
PUT  /v1/credentials/NAME admin token  {value, kind, description, actor}: store or replace (write-only, encrypted)
DELETE /v1/credentials/NAME admin token
POST /v1/github           admin token  {actor, operation status|list_repos} for the Connections page: where the token comes
                          from and who it is (GET /user), and the repositories it can reach (max 300); never the token
POST /v1/http_tool        tool token   {agent_id, tool, params}: one call of an HTTP tool at the agent's http_tools level;
                          admin token {actor, tool, params} tests a tool from the dashboard (gateway/http_tools.py)
GET  /v1/http_tools[/NAME] admin token  tool definitions (credentials by name only)
PUT  /v1/http_tools/NAME  admin token  {definition fields..., actor}: create or replace a tool
DELETE /v1/http_tools/NAME admin token
GET  /v1/mcp/tools        tool token   ?agent_id=… the MCP tools this agent may call now, with input schemas (gateway/mcp.py)
POST /v1/mcp/call         tool token   {agent_id, server, tool, arguments}: one tools/call at the tool's level on service
                          mcp:<server>; the gateway adds the auth header from the credential store; every call is audited
GET  /v1/mcp/servers[/NAME] admin token registered MCP servers, status, tools with levels (and credential names, never values)
PUT  /v1/mcp/servers/NAME admin token  {url | url_credential, auth {type none|bearer|header, credential, header}, enabled, timeout,
                          allow_private, allow_insecure, description, actor}; a new server starts at level 0 (off)
DELETE /v1/mcp/servers/NAME admin token
POST /v1/mcp/servers/NAME/refresh admin token {actor}: connect, list the tools and store the snapshot
GET  /v1/websites         tool or admin token  ?agent_id=… the websites on public-website with their Traefik routes and live
                          status (gateway/websites.py), cached 60 s; an agent needs level 1 on public-website and traefik;
                          ?refresh=1 (admin token only, at most every 15 s) reads everything again
GET  /v1/routes           admin token  managed Traefik routes (gateway/routes.py) with DNS and router state, domains, requests,
                          the other (hand-written, read-only) routers; GET /v1/routes/requests[?status=pending] only the requests,
                          GET /v1/routes/domains only the domains
PUT  /v1/routes/NAME      admin token  {host, environment, backend_port | backend, path_prefix, protection, basic_user, cert_resolver,
                          entrypoints, project_id, enabled, create, refresh_password, actor}: render the managed file, upload, verify
                          through the Traefik API, roll back on failure
DELETE /v1/routes/NAME    admin token  (X-HLA-Actor)
PUT  /v1/routes/domains   admin token  {domains: [...], actor}
POST /v1/routes/check-dns admin token  {host, protection}
POST /v1/routes/request   tool token   {agent_id, host, backend_port, environment, protection, reason, project_id}: a pending request
                          only (level 4 on traefik); POST /v1/routes/requests/ID/approve {actor, name, protection, ...} and
                          /reject {actor, note} (admin token)
GET  /v1/proxmox/inventory tool or admin token ?agent_id=&node=&refresh=1: both Proxmox nodes (VMs, storages, images, bridges,
                          next VMID, token permissions) for the nodes the agent has level 1 on (admin: all, plus the servers the
                          gateway created and their VMs) (gateway/proxmox.py)
POST /v1/proxmox          tool token {agent_id, node, operation, ...} at min(agent, proxmox:<node>) level; create_vm,
                          create_audit_token and destroy_vm answer 403 approval_required with the exact text to approve
                          (POST /v1/approvals {server: "proxmox:<node>", command: <that text>}); admin token {actor, node,
                          operation, ...} for the dashboard
POST /v1/omada            tool token {agent_id, operation status|sites|clients|devices|get|write, ...} at min(agent,
                          omada:controller) level (unhinged mode does not apply); a write below level 6 answers 403
                          approval_required with the exact text (POST /v1/approvals {server: "omada:controller", command: <text>});
                          admin token {actor, operation status|sites|...|trust_certificate {fingerprint}} for the dashboard
                          (gateway/omada.py)
Commands run without approval at level 6 (free shell) or in unhinged mode, with a 300 s timeout.
"""
import hmac,json,os,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from gateway.core import SERVERS,execute
from gateway.autonomy import resolve as resolve_maintenance, outcome as maintenance_outcome, TIMEOUT as MAINTENANCE_TIMEOUT
from gateway.cli import db_open,digest,DIAGNOSTICS
from gateway import policy, investigate, deploy, files, homeassistant, agent_secrets, github, audit as audit_log, credentials as creds
from gateway import http_tools, websites, routes, proxmox, masking, omada
from gateway import policy, investigate, deploy, files, homeassistant, agent_secrets, github, audit as audit_log, credentials as creds, mcp

TOKEN=os.getenv('TOOL_GATEWAY_TOKEN','')
if len(TOKEN)<32:raise SystemExit('TOOL_GATEWAY_TOKEN must be >=32 characters')
# Optional: without it, policy can only be changed from the gateway host.
ADMIN_TOKEN=os.getenv('POLICY_ADMIN_TOKEN','')
if ADMIN_TOKEN and (len(ADMIN_TOKEN)<32 or ADMIN_TOKEN==TOKEN):raise SystemExit('POLICY_ADMIN_TOKEN must be >=32 characters and differ from TOOL_GATEWAY_TOKEN')

def audit(db,server,action,d,result=None,actor=None,agent=None,detail=None,task=None):
    # HLA_AUDIT_V1: agent, detail (as sent: {{secret:NAME}} references, never values) and dashboard task.
    db.execute('INSERT INTO audit(timestamp,server,action,digest,exit_code,timed_out,truncated,actor,agent_id,detail,task_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (int(time.time()),server,action,d,result and result['exit_code'],int(bool(result and result['timed_out'])),int(bool(result and result['truncated'])),actor,
                audit_log.agent_id(agent),audit_log.mask(detail[:4000],known_secrets()) if isinstance(detail,str) else None,task))

KNOWN_ENV_SECRETS=('GITHUB_TOKEN','INFISICAL_CLIENT_SECRET','INFISICAL_GENERATED_CLIENT_SECRET')

def known_secrets():
    # The gateway's own tokens and credentials from its environment, replaced by name wherever they appear.
    env_values=tuple((name,os.getenv(name,'').strip()) for name in KNOWN_ENV_SECRETS)
    stored=tuple(creds.values(db_open).items())  # credentials entered on the dashboard
    nodes=tuple(proxmox.known().items())  # HLA_PROXMOX_V1: the Proxmox node token secrets read so far
    controller=tuple(omada.known().items())  # HLA_OMADA_V1: the Omada client secret and access tokens
    return audit_log.known_secrets(agent_secrets,(('TOOL_GATEWAY_TOKEN',TOKEN),('POLICY_ADMIN_TOKEN',ADMIN_TOKEN))+env_values+stored+nodes+controller)

def valid_command(cmd):
    return isinstance(cmd,str) and bool(cmd.strip()) and len(cmd)<=2048 and '\x00' not in cmd

class Handler(BaseHTTPRequestHandler):
    def authorized(self,token):
        return bool(token) and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)

    def body(self,limit=16384):
        length=int(self.headers.get('Content-Length','0'))
        if not 2<=length<=limit:raise ValueError('invalid_length')
        body=json.loads(self.rfile.read(length))
        if not isinstance(body,dict):raise ValueError('invalid_payload')
        return body

    def do_GET(self):
        url=urlsplit(self.path)
        if not self.authorized(TOKEN) and not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'unauthorized'})
        with db_open() as db:
            if url.path.startswith('/v1/mcp/'):return self.mcp_get(db,url)
            if url.path=='/v1/credentials':
                if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
                return self.reply(200,creds.listing(db))
            if url.path=='/v1/http_tools' or url.path.startswith('/v1/http_tools/'):
                # HLA_HTTP_TOOLS_V1: definitions for the dashboard; credentials appear by name, whether stored or not.
                if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
                stored={c['name'] for c in creds.listing(db)['credentials']}
                mark=lambda t:{**t,'credential_stored':bool(t.get('auth')) and t['auth']['credential'] in stored}
                if url.path=='/v1/http_tools':
                    return self.reply(200,{'tools':[mark(t) for t in http_tools.listing(db)],'credentials_configured':creds.configured()})
                tool=http_tools.get(db,url.path[len('/v1/http_tools/'):])
                return self.reply(200,mark(tool)) if tool else self.reply(404,{'error':'unknown_tool'})
            if url.path=='/v1/websites':return self.websites(db,url)
            if url.path=='/v1/proxmox/inventory':return self.proxmox_inventory(db,url)
            if url.path in ('/v1/routes','/v1/routes/requests','/v1/routes/domains'):
                # HLA_ROUTES_V1: managed routes for the dashboard (admin token).
                if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
                if url.path=='/v1/routes/domains':  # cheap: no Traefik or DNS call (the project form's dropdowns)
                    return self.reply(200,{'domains':routes.domains(db),'default_protection':routes.DEFAULT_PROTECTION})
                if url.path=='/v1/routes/requests':
                    status=(parse_qs(url.query).get('status') or [None])[0]
                    if status not in (None,'pending','approved','rejected'):return self.reply(400,{'error':'invalid_status'})
                    return self.reply(200,{'requests':routes.requests(db,status)})
                try:return self.reply(200,routes.listing(db,execute))
                except routes.RouteError as exc:return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            if url.path=='/v1/policy':return self.reply(200,policy.snapshot(db))
            if url.path=='/v1/audit':
                params={k:v[0] for k,v in parse_qs(url.query).items()}
                try:
                    return self.reply(200,audit_log.query(db,params,known_secrets()))
                except (ValueError,TypeError) as exc:
                    return self.reply(400,{'error':'invalid_query','detail':str(exc)[:200]})
            if url.path=='/v1/deployments':
                return self.reply(200,{'deployments':deploy.history(db,(parse_qs(url.query).get('app') or [None])[0])})
            if url.path=='/v1/policy/effective':
                agent=(parse_qs(url.query).get('agent_id') or [''])[0]
                return self.reply(200,{'agent_id':agent,'servers':policy.effective(db,agent),
                                       'services':policy.effective_services(db,agent)})
        return self.reply(404,{'error':'not_found'})

    def websites(self,db,url):
        """HLA_WEBSITES_V1: the website inventory. Read-only fixed scripts; Traefik's config never leaves the gateway."""
        params={k:v[0] for k,v in parse_qs(url.query).items()}
        admin=self.authorized(ADMIN_TOKEN)
        refresh=params.get('refresh') in ('1','true')
        agent=params.get('agent_id') or None
        task=audit_log.task_id(self.headers.get('X-HLA-Task'))
        if refresh and not admin:return self.reply(403,{'error':'refresh_needs_admin_token'})
        if agent and not admin:
            allowed=policy.effective(db,agent)
            level=min(allowed.get(s,{}).get('level',0) for s in websites.NEEDS)
            if level<1:
                audit(db,websites.NAME,'agent_websites_denied',digest(websites.NAME,'read'),agent=agent,task=task,
                      detail='needs level 1 on '+' and '.join(websites.NEEDS))
                return self.reply(403,{'error':'policy_denied','detail':'reading the websites needs level 1 on '+' and '.join(websites.NEEDS),'level':level})
        try:data,ran=websites.get(execute,refresh=refresh)
        except websites.RateLimited as exc:
            return self.reply(429,{'error':'refresh_rate_limited','retry_after':exc.retry,'detail':f'refreshed less than {websites.REFRESH_SECONDS} s ago'})
        except Exception as exc:
            return self.reply(502,{'error':'websites_failed','exception_type':type(exc).__name__})
        who=dict(actor=('agent:'+agent) if agent else 'dashboard',agent=agent,task=task)
        ok={'exit_code':0 if not (data.get('containers_error') or data.get('routes_source')=='none') else 1,'timed_out':False,'truncated':False}
        if ran:audit(db,websites.NAME,'websites_refresh',digest(websites.NAME,'refresh:'+str(data['checked_at'])),ok,
                     detail=('forced: ' if refresh else '')+websites.summary(data),**who)
        elif agent:audit(db,websites.NAME,'agent_websites_read',digest(websites.NAME,'read'),ok,detail='cached: '+websites.summary(data),**who)
        if agent and not admin:
            return self.reply(200,{'checked_at':data['checked_at'],'routes_source':data['routes_source'],'routes_error':data['routes_error'],
                                   'containers_error':data['containers_error'],'checks_error':data['checks_error'],
                                   'sites':websites.for_agent(data,params.get('environment'),params.get('search'))})
        return self.reply(200,data)

    def route_name(self):
        """NAME of /v1/routes/NAME (not the fixed sub-paths), or None."""
        path=urlsplit(self.path).path
        if not path.startswith('/v1/routes/'):return None
        name=path[len('/v1/routes/'):]
        return name if name not in ('domains','check-dns','request','requests') and '/' not in name else None

    def route_audit(self,db,actor,agent=None):
        def write(action,detail,ok):
            audit(db,routes.NAME,action,digest(routes.NAME,action+':'+detail),{'exit_code':0 if ok else 1,'timed_out':False,'truncated':False},
                  actor=actor,agent=agent,detail=detail,task=getattr(self,'task',None))
        return write

    def route_write(self,method):
        """HLA_ROUTES_V1: PUT /v1/routes/NAME, PUT /v1/routes/domains, DELETE /v1/routes/NAME (admin token)."""
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        path=urlsplit(self.path).path
        if method=='DELETE':body,actor={},self.headers.get('X-HLA-Actor','dashboard')[:120]
        else:
            try:body=self.body(65536)
            except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
            actor=str(body.get('actor') or 'dashboard')[:120]
        with db_open() as db:
            try:
                if path=='/v1/routes/domains' and method=='PUT':
                    out=routes.set_domains(db,body.get('domains'))
                    audit(db,routes.NAME,'route_domains_saved',digest(routes.NAME,'domains:'+','.join(out)),actor=actor,detail=', '.join(out) or '(none)')
                    return self.reply(200,{'domains':out})
                name=self.route_name()
                if name is None:return self.reply(404,{'error':'not_found'})
                if method=='DELETE':return self.reply(200,routes.delete(db,execute,name,actor,self.route_audit(db,actor)))
                return self.reply(200,routes.save(db,execute,name,body,actor,self.route_audit(db,actor),create=body.get('create') is True))
            except routes.RouteError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                return self.reply(502,{'error':'routes_failed','exception_type':type(exc).__name__})

    def route_post(self):
        """HLA_ROUTES_V1: DNS checks and request decisions (admin token); agent requests (tool token, pending only)."""
        path=urlsplit(self.path).path
        if path=='/v1/routes/request':
            if not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
            try:body=self.body()
            except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
            agent=body.get('agent_id')
            with db_open() as db:
                level=policy.effective(db,agent).get(routes.TRAEFIK_SERVER,{}).get('level',0)
                shown=str(body.get('host'))[:120]+' -> port '+str(body.get('backend_port'))[:8]
                if level<routes.AGENT_LEVEL:
                    audit(db,routes.NAME,'agent_route_denied',digest(routes.NAME,'request:'+shown),agent=agent,task=self.task,
                          actor='agent:'+str(agent),detail=shown+f' (level {level} on traefik, needs {routes.AGENT_LEVEL})')
                    return self.reply(403,{'error':'policy_denied','detail':f'requesting a route needs level {routes.AGENT_LEVEL} on traefik','level':level})
                try:out=routes.file_request(db,body,agent)
                except routes.RouteError as exc:return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
                if self.task:db.execute('UPDATE route_requests SET task_id=? WHERE id=?',(self.task,out['id']))
                audit(db,routes.NAME,'agent_route_requested',digest(routes.NAME,'request:'+str(out['id'])),{'exit_code':0,'timed_out':False,'truncated':False},
                      agent=agent,task=self.task,actor='agent:'+str(agent),
                      detail=f"#{out['id']} {out['host']} -> {out['backend']} ({out['environment']}, {out['protection']})")
            return self.reply(200,out)
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):body={}
        actor=str(body.get('actor') or 'dashboard')[:120]
        with db_open() as db:
            try:
                if path=='/v1/routes/check-dns':
                    host=str(body.get('host') or '').strip().lower().rstrip('.')
                    routes._dns_cache.pop(host,None)  # an explicit check asks again
                    return self.reply(200,routes.dns_check(host,routes.domains(db),str(body.get('protection') or 'public')))
                parts=path.split('/')
                if len(parts)==6 and parts[3]=='requests' and parts[5] in ('approve','reject'):
                    if parts[5]=='reject':
                        out=routes.reject(db,parts[4],body.get('note'),actor)
                        audit(db,routes.NAME,'route_request_rejected',digest(routes.NAME,'request:'+str(out['request_id'])),actor=actor,
                              detail=f"#{out['request_id']} {out['host']}"+(': '+str(body.get('note'))[:200] if body.get('note') else ''))
                        return self.reply(200,out)
                    return self.reply(200,routes.approve(db,execute,parts[4],body,actor,self.route_audit(db,actor)))
            except routes.RouteError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                return self.reply(502,{'error':'routes_failed','exception_type':type(exc).__name__})
        return self.reply(404,{'error':'not_found'})

    def credential_name(self):
        path=urlsplit(self.path).path
        return path[len('/v1/credentials/'):] if path.startswith('/v1/credentials/') else None

    def http_tool_name(self):
        path=urlsplit(self.path).path
        return path[len('/v1/http_tools/'):] if path.startswith('/v1/http_tools/') else None

    def mcp_name(self,suffix=''):
        """NAME of /v1/mcp/servers/NAME[suffix], or None."""
        path=urlsplit(self.path).path
        if not path.startswith('/v1/mcp/servers/') or not path.endswith(suffix):return None
        name=path[len('/v1/mcp/servers/'):len(path)-len(suffix)]
        return name if mcp.SERVER_NAME.match(name) else None

    def mcp_get(self,db,url):
        """HLA_MCP_V1: tools for an agent (tool token); servers and their tools for the dashboard (admin token)."""
        if url.path=='/v1/mcp/tools':
            agent=(parse_qs(url.query).get('agent_id') or [''])[0]
            return self.reply(200,{'agent_id':agent,'tools':mcp.agent_tools(db,agent)})
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        stored=creds.listing(db)
        known=[c['name'] for c in stored['credentials']]
        if url.path=='/v1/mcp/servers':
            return self.reply(200,{'servers':[mcp.view(db,cfg,known=known) for cfg in mcp.servers(db).values()],
                                   'credentials':known,'credentials_configured':stored['configured'],'levels':policy.LEVELS})
        name=self.mcp_name()
        cfg=mcp.server(db,name) if name else None
        if not cfg:return self.reply(404,{'error':'unknown_server'})
        return self.reply(200,mcp.view(db,cfg,schemas=True,known=known))

    def mcp_save(self,name):
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        actor=str(body.get('actor') or 'dashboard')[:120]
        with db_open() as db:
            try:cfg=mcp.save(db,name,body,actor)
            except mcp.MCPError as exc:return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            shown_url=('[credential:'+cfg['url_credential']+']') if cfg.get('url_credential') else mcp.display_url(cfg['url'])
            audit(db,mcp.PREFIX+name,'mcp_server_saved',digest(mcp.PREFIX+name,cfg['url'] or cfg.get('url_credential') or ''),actor=actor,
                  detail=shown_url+' (auth '+cfg['auth_type']+(' '+cfg['credential'] if cfg['credential'] else '')+(', off' if not cfg['enabled'] else '')+')')
            return self.reply(200,mcp.view(db,cfg,known=[c['name'] for c in creds.listing(db)['credentials']]))

    def mcp_refresh(self,name):
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):body={}
        actor=str(body.get('actor') or 'dashboard')[:120]
        with db_open() as db:
            if not mcp.server(db,name):return self.reply(404,{'error':'unknown_server'})
            d=digest(mcp.PREFIX+name,'refresh')
            try:cfg=mcp.refresh(db,name)
            except mcp.MCPError as exc:
                audit(db,mcp.PREFIX+name,'mcp_refresh',d,actor=actor,detail=name+' · '+exc.error)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                audit(db,mcp.PREFIX+name,'mcp_refresh',d,actor=actor,detail=name+' · '+type(exc).__name__)
                return self.reply(502,{'error':'mcp_failed','exception_type':type(exc).__name__})
            audit(db,mcp.PREFIX+name,'mcp_refresh',d,{'exit_code':0,'timed_out':False,'truncated':False},actor=actor,
                  detail=mcp.refresh_summary(cfg))
            return self.reply(200,mcp.view(db,cfg,known=[c['name'] for c in creds.listing(db)['credentials']]))

    def mcp_call(self):
        """HLA_MCP_V1: one tool call for an agent; auth from the credential store, result text capped, always audited."""
        if not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body(mcp.MAX_ARGUMENTS+20000)
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        agent=body.get('agent_id')
        with db_open() as db:
            try:cfg,tool,arguments,need=mcp.check_call(db,body)
            except mcp.MCPError as exc:
                if exc.status==403:  # a refused call is on record like a denied command
                    service=mcp.PREFIX+str(body.get('server'))[:40]
                    audit(db,service,'agent_mcp_denied',digest(service,str(body.get('tool'))[:128]),agent=agent,task=self.task,
                          detail=str(body.get('tool'))[:128]+' · '+exc.error)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            service=mcp.PREFIX+cfg['name']
            d=digest(service,tool['name']+':'+json.dumps(arguments,sort_keys=True,default=str)[:100000])
            action='agent_mcp_read' if need<=1 else 'agent_mcp_call'
            prepared=creds.prepared(creds.values(db_open))  # masked before results and previews are cut
            try:result=mcp.call(db,cfg,tool,arguments,prepared)
            except mcp.MCPError as exc:
                audit(db,service,action,d,agent=agent,task=self.task,detail=mcp.audit_detail(tool['name'],arguments,error=exc.error,prepared=prepared))
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                audit(db,service,action,d,agent=agent,task=self.task,detail=mcp.audit_detail(tool['name'],arguments,error=type(exc).__name__,prepared=prepared))
                return self.reply(502,{'error':'mcp_failed','exception_type':type(exc).__name__})
            audit(db,service,action,d,{'exit_code':1 if result['is_error'] else 0,'timed_out':False,'truncated':result['truncated']},
                  agent=agent,task=self.task,detail=mcp.audit_detail(tool['name'],arguments,result,prepared=prepared))
        return self.reply(200,{'service':service,'server':cfg['name'],'tool':tool['name'],**result})

    def proxmox_inventory(self,db,url):
        """HLA_PROXMOX_V1: inventory of the nodes this agent may read (level 1 on proxmox:<node>); admin: all nodes."""
        params={k:v[0] for k,v in parse_qs(url.query).items()}
        status,payload=proxmox.handle_inventory(db,params,self.authorized(ADMIN_TOKEN),audit_log.task_id(self.headers.get('X-HLA-Task')),
                                               self.proxmox_audit(db))
        return self.reply(status,payload)

    def proxmox_audit(self,db):
        def write(server,action,d,result=None,actor=None,agent=None,detail=None,task=None):
            audit(db,server,action,d,result,actor=actor,agent=agent,detail=detail,task=task)
        return write

    def proxmox_post(self):
        """HLA_PROXMOX_V1: one Proxmox operation (gateway/proxmox.py handle_post): agents at their level, with an approval of
        the exact request text where needed; the dashboard (admin token) acts as the owner."""
        admin=self.authorized(ADMIN_TOKEN)
        if not admin and not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        with db_open() as db:
            status,payload=proxmox.handle_post(db,body,admin,self.task,self.proxmox_audit(db))
        return self.reply(status,payload)

    def omada_post(self):
        """HLA_OMADA_V1: one Omada controller operation (gateway/omada.py handle_post): agents at their level, writes with an
        approval of the exact request text below level 6; the dashboard (admin token) acts as the owner."""
        admin=self.authorized(ADMIN_TOKEN)
        if not admin and not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body(65536)
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        with db_open() as db:
            status,payload=omada.handle_post(db,body,admin,self.task,self.proxmox_audit(db))
        return self.reply(status,payload)

    def do_DELETE(self):
        if urlsplit(self.path).path.startswith('/v1/routes/'):return self.route_write('DELETE')
        tool=self.http_tool_name()
        if tool is not None:
            if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
            with db_open() as db:
                gone=http_tools.delete(db,tool)
                if gone:audit(db,'http_tools','http_tool_deleted',digest('http_tools','tool:'+tool),
                              actor=self.headers.get('X-HLA-Actor','dashboard')[:120],detail=tool)
            return self.reply(200 if gone else 404,{'deleted':gone,'name':tool})
        mcp_server=self.mcp_name()
        if mcp_server is not None:
            if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
            actor=self.headers.get('X-HLA-Actor','dashboard')[:120]
            with db_open() as db:
                gone=mcp.delete(db,mcp_server)
                if gone:audit(db,mcp.PREFIX+mcp_server,'mcp_server_deleted',digest(mcp.PREFIX+mcp_server,'delete'),actor=actor,detail=mcp_server)
            return self.reply(200 if gone else 404,{'deleted':gone,'name':mcp_server})
        name=self.credential_name()
        if name is None:return self.reply(404,{'error':'not_found'})
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        with db_open() as db:
            gone=creds.delete(db,name)
            audit(db,'gateway','credential_deleted',digest('gateway','credential:'+name),actor=self.headers.get('X-HLA-Actor','dashboard')[:120],detail=name)
            db.commit()
        return self.reply(200 if gone else 404,{'deleted':gone,'name':name})

    def do_PUT(self):
        if urlsplit(self.path).path.startswith('/v1/routes/'):return self.route_write('PUT')
        tool=self.http_tool_name()
        if tool is not None:
            # HLA_HTTP_TOOLS_V1: create or replace one tool definition.
            if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
            try:
                body=self.body(65536)
                actor=str(body.get('actor') or 'dashboard')[:120]
                with db_open() as db:
                    item=t=http_tools.put(db,tool,body,actor)
                    audit(db,'http_tools','http_tool_saved',digest('http_tools','tool:'+tool),actor=actor,
                          detail=f"{tool}: {t['method']} {t['base_url']}{t['path']} (level {t['level']}"
                                 +(', credential '+t['auth']['credential'] if t.get('auth') else '')
                                 +(', private network' if t['allow_private'] else '')+')')
                return self.reply(200,item)
            except http_tools.HttpToolError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except (ValueError,TypeError,json.JSONDecodeError):
                return self.reply(400,{'error':'invalid_payload'})
        mcp_server=self.mcp_name()
        if mcp_server is not None:return self.mcp_save(mcp_server)
        name=self.credential_name()
        if name is not None:
            # HLA_CREDENTIALS_V1: write-only; the reply carries metadata, never the value.
            if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
            try:
                body=self.body()
                actor=str(body.get('actor') or 'dashboard')[:120]
                with db_open() as db:
                    item=creds.put(db,name,body.get('value'),kind=body.get('kind') or 'token',description=body.get('description') or '',actor=actor)
                    audit(db,'gateway','credential_stored',digest('gateway','credential:'+name),actor=actor,detail=name+' ('+item['kind']+')')
                    db.commit()
                return self.reply(200,item)
            except creds.CredentialError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except (ValueError,TypeError,json.JSONDecodeError):
                return self.reply(400,{'error':'invalid_payload'})
        if urlsplit(self.path).path!='/v1/policy':return self.reply(404,{'error':'not_found'})
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        try:
            body=self.body()
            with db_open() as db:
                return self.reply(200,policy.update(db,body.get('change'),body.get('actor') or 'unknown'))
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            return self.reply(400,{'error':'invalid_policy','detail':str(exc)[:200]})

    def do_POST(self):
        # HLA_SECRETS_V1: nothing a POST returns may carry a secret value (command output, files, deploy logs).
        self.redacting,self.used=True,{}
        self.task=audit_log.task_id(self.headers.get('X-HLA-Task'))
        if self.path=='/v1/secrets':return self.secrets()
        if self.path=='/v1/approvals':return self.grant()
        if self.path=='/v1/deploy':return self.deploy()
        if self.path=='/v1/homeassistant':return self.homeassistant()
        if self.path=='/v1/github':return self.github()
        if self.path=='/v1/http_tool':return self.http_tool()
        if self.path=='/v1/mcp/call':return self.mcp_call()
        if self.path=='/v1/proxmox':return self.proxmox_post()
        if self.path=='/v1/omada':return self.omada_post()
        if self.mcp_name('/refresh') is not None:return self.mcp_refresh(self.mcp_name('/refresh'))
        if urlsplit(self.path).path.startswith('/v1/routes/'):return self.route_post()
        if self.path!='/v1/execute':return self.reply(404,{'error':'not_found'})
        if not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:
            body=self.body(600000)  # write_file carries up to 256 KiB of content, JSON-escaped
            server=body.get('server');mode=body.get('mode')
            if server not in SERVERS:return self.reply(403,{'error':'server_not_enabled'})
            with db_open() as db:
                allowed=policy.effective(db,body.get('agent_id')).get(server,{'level':0,'unhinged':False,'compose_projects':[]})
                level=allowed['level']
                if mode in ('write_file','read_file'):return self.file(db,body,server,mode,allowed)
                if mode=='diagnostic':
                    name=body.get('diagnostic')
                    if level<1:return self.reply(403,{'error':'policy_denied','server':server,'level':level})
                    if name in DIAGNOSTICS:cmd=DIAGNOSTICS[name]
                    else:
                        # HLA_INVESTIGATE_V1: read-only checks of a path or container (target is shell-quoted).
                        try:cmd=investigate.resolve(name,body.get('target'))
                        except ValueError as exc:return self.reply(400,{'error':'invalid_diagnostic','detail':str(exc)})
                elif mode=='maintenance':
                    try:
                        cmd=resolve_maintenance(body.get('operation'),body.get('target'),level,allowed['compose_projects'])
                    except ValueError:
                        return self.reply(403,{'error':'policy_denied','server':server,'level':level})
                elif mode in ('approved_command','command'):
                    cmd=body.get('command')
                    if not valid_command(cmd):return self.reply(400,{'error':'invalid_command'})
                    if level<5:return self.reply(403,{'error':'policy_denied','server':server,'level':level})
                else:return self.reply(400,{'error':'invalid_mode'})
                d=digest(server,cmd)  # approvals and the audit log see {{secret:NAME}}, never the value
                action='agent_'+mode
                agent=body.get('agent_id')
                shown={'diagnostic':lambda:' '.join(str(x) for x in (body.get('diagnostic'),body.get('target')) if x),
                       'maintenance':lambda:f"{body.get('operation')} {body.get('target')}"}.get(mode,lambda:cmd)()
                free=mode in ('approved_command','command') and allowed.get('free_shell')
                if mode in ('approved_command','command'):
                    if free:
                        # Level 6 (free shell) or unhinged mode: no approval, still audited.
                        action='agent_free_shell' if level>=6 else 'agent_unhinged'
                    else:
                        db.execute('BEGIN IMMEDIATE')
                        updated=db.execute('UPDATE approvals SET used=1 WHERE digest=? AND used=0 AND expires>?',(d,int(time.time())))
                        db.execute('COMMIT')
                        if updated.rowcount!=1:
                            audit(db,server,'agent_denied',d,agent=agent,detail=shown,task=self.task)
                            return self.reply(403,{'error':'approval_required','server':server})
                timeout=MAINTENANCE_TIMEOUT if mode=='maintenance' else (300 if free else 40)
                if mode in ('approved_command','command') and agent_secrets.references(cmd):
                    if self.fill(db,body,server,cmd):return
                    cmd=self.filled
                try:result=execute(server,cmd,timeout=timeout,max_output=12000 if free else 8000)
                except Exception as exc:
                    audit(db,server,action,d,agent=agent,detail=shown,task=self.task)
                    return self.reply(502,{'error':'execution_failed','exception_type':type(exc).__name__,'server':server})
                audit(db,server,action,d,result,agent=agent,detail=shown,task=self.task)
            if mode=='maintenance':
                # HLA_SELF_HEALING_V1: report whether the change took effect, not only the exit code.
                result['outcome']=maintenance_outcome(body['operation'],body['target'],result)
            return self.reply(200,{'server':server,**result})
        except (ValueError,TypeError,json.JSONDecodeError):
            return self.reply(400,{'error':'invalid_payload'})

    def file(self,db,body,server,mode,allowed):
        """HLA_FILES_V1: write or read one file as svc-hla-agent; only with a free shell on that server."""
        try:path,extra=files.request(mode,body)
        except ValueError as exc:return self.reply(400,{'error':'invalid_file_request','detail':str(exc)})
        level=allowed['level']
        if level<1:return self.reply(403,{'error':'policy_denied','server':server,'level':level})
        if not allowed.get('free_shell'):return self.reply(403,{'error':'file_tools_need_free_shell','server':server,'level':level})
        if mode=='write_file':
            data,append=extra
            sha=files.sha256(data)  # of the content with {{secret:NAME}} references, as the agent sent it
            if agent_secrets.references(body.get('content')):
                if self.fill(db,body,server,body['content']):return
                data=self.filled.encode('utf-8')
            cmd,stdin,max_output=files.write_command(path,append),data,8000
        else:
            offset,length=extra
            cmd,stdin,max_output=files.read_command(path,offset,length),None,files.MAX_READ+2000
        if mode!='write_file':sha=files.sha256(b'')
        d=digest(server,mode+':'+path+':'+sha);action='agent_'+mode
        shown=path+(f' ({"append" if extra[1] else "write"}, sha256 {sha[:12]})' if mode=='write_file' else f' (offset {extra[0]}, length {extra[1]})')
        who=dict(agent=body.get('agent_id'),detail=shown,task=self.task)
        try:result=execute(server,cmd,timeout=files.TIMEOUT,max_output=max_output,stdin=stdin)
        except Exception as exc:
            audit(db,server,action,d,**who)
            return self.reply(502,{'error':'execution_failed','exception_type':type(exc).__name__,'server':server})
        audit(db,server,action,d,result,**who)
        if mode=='write_file':
            return self.reply(200,{'server':server,'path':path,'bytes':files.parse_write(result['stdout']),'sha256':sha,**result})
        size,content=files.parse_read(result['stdout'])
        returned=0 if size is None else max(0,min(length,size-offset))
        return self.reply(200,{'server':server,'path':path,'size':size,'offset':offset,'content':content,
                               'eof':size is not None and offset+returned>=size,**result,'stdout':''})

    def grant(self):
        """A dashboard approver approved this exact command in a conversation: allow one execution within 5 minutes.
        The agent's policy level is still checked when the command is executed."""
        if not self.authorized(ADMIN_TOKEN):return self.reply(401,{'error':'policy_admin_token_required'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        server,cmd,actor=body.get('server'),body.get('command'),body.get('actor')
        if proxmox.node_of(server):
            # HLA_PROXMOX_V1: the exact Proxmox request text (create/destroy/audit token) the owner approved on its card.
            if not isinstance(cmd,str) or not cmd.startswith('proxmox ') or len(cmd)>4000 or '\x00' in cmd:return self.reply(400,{'error':'invalid_command'})
        elif server==omada.NAME:
            # HLA_OMADA_V1: the exact Omada write the owner approved on its card.
            if not isinstance(cmd,str) or not cmd.startswith('omada ') or len(cmd)>4000 or '\x00' in cmd:return self.reply(400,{'error':'invalid_command'})
        elif server not in SERVERS:return self.reply(403,{'error':'server_not_enabled'})
        elif not valid_command(cmd):return self.reply(400,{'error':'invalid_command'})
        if not isinstance(actor,str) or not actor.strip() or len(actor)>128:return self.reply(400,{'error':'actor_required'})
        d=digest(server,cmd)
        with db_open() as db:
            db.execute('INSERT OR REPLACE INTO approvals(digest,expires,used) VALUES (?,?,0)',(d,int(time.time())+300))
            audit(db,server,'chat_approval',d,actor=actor.strip(),detail=cmd,task=self.task)
        return self.reply(200,{'server':server,'digest':d[:16],'expires_in':300})

    def deploy(self):
        """HLA_DEPLOY_V1: stage on the sandbox, deploy the verified commit, roll back automatically on failure."""
        admin=self.authorized(ADMIN_TOKEN)
        if not admin and not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        ref,sha=body.get('ref'),body.get('sha')
        with db_open() as db:
            app=policy.app(db,body.get('app'))
            if not app:return self.reply(404,{'error':'unknown_app'})
            if admin:
                who=body.get('actor')
                if not isinstance(who,str) or not who.strip() or len(who)>128:return self.reply(400,{'error':'actor_required'})
            else:
                who='agent:'+str(body.get('agent_id'))
                level=policy.effective(db,body.get('agent_id')).get(app['server'],{}).get('level',0)
                if level<4:return self.reply(403,{'error':'policy_denied','server':app['server'],'level':level})
                if sha is not None:return self.reply(403,{'error':'rollback_is_admin_only'})
            if sha is not None:
                # Rolling back skips staging, so only to a commit that already ran on this app.
                if not isinstance(sha,str) or not deploy.SHA.match(sha) or not deploy.known_good(db,app['name'],sha):
                    return self.reply(400,{'error':'unknown_commit'})
                ref=sha
            elif not deploy.valid_ref(ref):return self.reply(400,{'error':'invalid_ref'})
            staging=sha is None and app['staging']
            if staging and policy.snapshot(db)['servers'].get('sandbox',{}).get('level',0)<1:
                return self.reply(409,{'error':'staging_unavailable'})
            if not deploy.claim(app['name']):return self.reply(409,{'error':'deploy_in_progress'})
            try:ident=deploy.start(db,app,ref,who.strip())
            except Exception:
                deploy.release(app['name']);raise
        try:
            try:result=deploy.run(execute,app,ref,sha=sha,staging=staging)
            except Exception as exc:
                result={'app':app['name'],'server':app['server'],'ref':ref,'status':'error','verified':False,
                        'error_class':'execution_failed','exception_type':type(exc).__name__}
            with db_open() as db:
                deploy.finish(db,ident,result)
                audit(db,app['server'],'deploy_'+result['status'],digest(app['server'],app['name']+'@'+str(result.get('sha'))),actor=who.strip(),
                      agent=None if admin else body.get('agent_id'),task=self.task,
                      detail=f"{app['name']} {ref}"+(f" -> {str(result.get('sha'))[:12]}" if result.get('sha') else '')
                             +(f" ({result.get('error_class')})" if result.get('error_class') else ''))
        finally:
            deploy.release(app['name'])
        return self.reply(200,{'id':ident,**result})

    def homeassistant(self):
        """HLA_HA_V1: one Home Assistant request at the agent's level; the token never leaves the gateway."""
        if not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body(600000)  # a dashboard config can be large
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        name=homeassistant.NAME
        with db_open() as db:
            allowed=policy.effective_services(db,body.get('agent_id')).get(name,{'level':0})
            try:
                req=homeassistant.check(body.get('operation'),body,allowed['level'],bool(allowed.get('free_shell')))
            except homeassistant.HAError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail,'level':allowed['level']})
            what=req['op']+':'+str(req.get('path') or (req.get('message') or {}).get('type') or req.get('dashboard') or '')
            d=digest(name,what+':'+json.dumps(body,sort_keys=True,default=str)[:100000])
            action='agent_ha_'+('change' if req['need']>=4 else 'read')
            who=dict(agent=body.get('agent_id'),detail=(req['method']+' ' if req.get('method') else '')+what,task=self.task)
            try:
                result=homeassistant.run(db,req,'agent:'+str(body.get('agent_id')))
            except homeassistant.HAError as exc:
                audit(db,name,action,d,**who)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                audit(db,name,action,d,**who)
                return self.reply(502,{'error':'homeassistant_failed','exception_type':type(exc).__name__,
                                       'detail':str(exc)[:200] if isinstance(exc,RuntimeError) else ''})
            audit(db,name,action,d,{'exit_code':0,'timed_out':False,'truncated':False},**who)
        return self.reply(200,{'service':name,**result})

    def github(self):
        """HLA_GITHUB_V1: pull requests on allowlisted repositories; the token never leaves the gateway."""
        admin=self.authorized(ADMIN_TOKEN)
        if not admin and not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body(200000)  # a PR description can be long
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        name=github.NAME
        with db_open() as db:
            if admin:  # the dashboard refreshes PR states: read operations only, still only allowlisted repositories
                who=body.get('actor')
                if not isinstance(who,str) or not who.strip() or len(who)>128:return self.reply(400,{'error':'actor_required'})
                if body.get('operation') in github.ADMIN_OPERATIONS:
                    # HLA_CONNECTIONS_V1: token source and identity, and the repositories it reaches (read only).
                    try:
                        result=github.status() if body['operation']=='status' else github.list_repos(policy.github_repos(db))
                    except github.GitHubError as exc:
                        return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
                    except Exception as exc:
                        return self.reply(502,{'error':'github_failed','exception_type':type(exc).__name__})
                    return self.reply(200,{'service':name,**result})
                if github.OPERATIONS.get(body.get('operation'),9)>1:return self.reply(403,{'error':'policy_denied','detail':'the dashboard only reads'})
                allowed,who={'level':1},who.strip()
            else:
                allowed=policy.effective_services(db,body.get('agent_id')).get(name,{'level':0})
                who='agent:'+str(body.get('agent_id'))
            try:
                req=github.check(body.get('operation'),body,allowed['level'],bool(allowed.get('free_shell')),policy.github_repos(db))
            except github.GitHubError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail,'level':allowed['level']})
            d=digest(name,req['op']+':'+req['repo']+':'+str(req.get('number') or req.get('head') or ''))
            # HLA_AUDIT_V1: the agent, what it did to which PR, and its dashboard task.
            meta=dict(agent=None if admin else body.get('agent_id'),task=self.task,
                      detail=req['op']+' '+req['repo']+(' #'+str(req['number']) if req.get('number') else '')+(' '+str(req['head']) if req.get('head') else ''))
            action='agent_github_'+('read' if req['need']<=1 else 'merge' if req['op']=='merge' else 'change')
            try:result=github.run(req)
            except github.GitHubError as exc:
                if req['need']>1:audit(db,name,action,d,actor=who,**meta)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                if req['need']>1:audit(db,name,action,d,actor=who,**meta)
                return self.reply(502,{'error':'github_failed','exception_type':type(exc).__name__})
            if req['need']>1:  # reads are not audited (the dashboard refreshes them often)
                audit(db,name,action,d,{'exit_code':0,'timed_out':False,'truncated':False},actor=who,**meta)
        return self.reply(200,{'service':name,**result})

    def http_tool(self):
        """HLA_HTTP_TOOLS_V1: one call of an owner-defined HTTP tool; credentials are filled in here and masked
        everywhere. Tool token: at the agent's http_tools level. Admin token: the dashboard's Test button (no level)."""
        admin=self.authorized(ADMIN_TOKEN)
        if not admin and not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body(200000)
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        name=http_tools.NAME
        with db_open() as db:
            if admin:
                who=body.get('actor')
                if not isinstance(who,str) or not who.strip() or len(who)>128:return self.reply(400,{'error':'actor_required'})
                level,free,who,agent=None,False,who.strip(),None
            else:
                allowed=policy.effective_services(db,body.get('agent_id')).get(name,{'level':0})
                level,free,agent=allowed['level'],bool(allowed.get('free_shell')),body.get('agent_id')
                who='agent:'+str(agent)
            tool=http_tools.get(db,body.get('tool')) if isinstance(body.get('tool'),str) else None
            try:
                req=http_tools.check(tool,body.get('params'),level,free)
            except http_tools.HttpToolError as exc:
                if exc.error=='policy_denied':
                    audit(db,name,'agent_http_denied',digest(name,str(body.get('tool'))),actor=who,agent=agent,
                          detail=str(body.get('tool'))[:60]+' (level '+str(level)+')',task=self.task)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail,**({'level':level} if level is not None else {})})
            shown=http_tools.shown(req)
            action=('admin_http_test' if admin else 'agent_http_'+('read' if tool['method']=='GET' else 'change'))
            d=digest(name,shown+':'+json.dumps(body.get('params'),sort_keys=True,default=str)[:100000])
            meta=dict(actor=who,agent=agent,task=self.task)
            try:
                result=http_tools.run(db,req,creds.get)
            except http_tools.HttpToolError as exc:
                audit(db,name,action,d,{'exit_code':None,'timed_out':exc.error=='timeout','truncated':False},detail=shown+' -> '+exc.error,**meta)
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail})
            except Exception as exc:
                audit(db,name,action,d,detail=shown+' -> failed',**meta)
                return self.reply(502,{'error':'http_tool_failed','exception_type':type(exc).__name__})
            code=result['http_status']
            audit(db,name,action,d,{'exit_code':0 if result['ok'] else code,'timed_out':False,'truncated':result['truncated']},
                  detail=shown+' -> '+str(code),**meta)
        return self.reply(200,{'service':name,**result})

    def fill(self,db,body,server,text):
        """Put secret values in for {{secret:NAME}}: sets self.filled, or sends the error reply and returns True."""
        allowed=policy.effective_services(db,body.get('agent_id')).get(agent_secrets.NAME,{'level':0})
        if allowed['level']<1:
            self.reply(403,{'error':'secrets_not_allowed','detail':'this agent may not use secrets'});return True
        try:self.filled,used=agent_secrets.substitute(text)
        except agent_secrets.SecretError as exc:
            self.reply(exc.status,{'error':exc.error,'detail':exc.detail});return True
        self.used.update(used)
        audit(db,server,'agent_secret_use',digest(server,','.join(sorted(used))),actor='agent:'+str(body.get('agent_id')),
              agent=body.get('agent_id'),detail='uses '+', '.join('{{secret:'+n+'}}' for n in sorted(used)),task=self.task)
        return None

    def secrets(self):
        """HLA_SECRETS_V1: list names or create a secret; values never leave the gateway."""
        if not self.authorized(TOKEN):return self.reply(401,{'error':'unauthorized'})
        try:body=self.body()
        except (ValueError,TypeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid_payload'})
        name=agent_secrets.NAME
        with db_open() as db:
            allowed=policy.effective_services(db,body.get('agent_id')).get(name,{'level':0})
            try:
                req=agent_secrets.check(body.get('operation'),body,allowed['level'],bool(allowed.get('free_shell')))
                result=agent_secrets.run(req,'agent:'+str(body.get('agent_id')))
            except agent_secrets.SecretError as exc:
                return self.reply(exc.status,{'error':exc.error,'detail':exc.detail,'level':allowed['level']})
            if req['op']!='list':
                audit(db,name,'agent_secret_'+result['status'],digest(name,req['name']),{'exit_code':0,'timed_out':False,'truncated':False},
                      actor='agent:'+str(body.get('agent_id')),agent=body.get('agent_id'),task=self.task,
                      detail=req['op']+' {{secret:'+req['name']+'}}'+(f" ({req['kind']})" if req.get('kind') else ''))
        return self.reply(200,{'service':name,**result})

    def reply(self,status,payload):
        if getattr(self,'redacting',False):payload=agent_secrets.redact(payload,self.used)
        payload=creds.redact(payload,creds.values(db_open))  # stored credentials never leave the gateway
        node_tokens={**proxmox.known(),**omada.known()}  # HLA_PROXMOX_V1 node token secrets; HLA_OMADA_V1 client secret and tokens
        if node_tokens:payload=masking.mask(payload,masking.prepare([(v,'[secret:'+n+']') for n,v in node_tokens.items()]))
        data=json.dumps(payload).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def log_message(self,fmt,*args):pass

if __name__=='__main__':
    with db_open() as db:deploy.mark_interrupted(db)
    ThreadingHTTPServer(('0.0.0.0',8099),Handler).serve_forever()
