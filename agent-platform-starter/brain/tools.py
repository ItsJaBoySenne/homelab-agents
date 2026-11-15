"""Shared KB read and evidence-backed upsert, with workspace isolation.

Never put model-authored assertions into 'verified' storage: the write operation
performs a fresh fixed diagnostic via the authenticated private gateway.
"""
import os
import uuid
from datetime import datetime, timezone
import psycopg
from psycopg.rows import dict_row
from memory import store as memory_store
from memory import knowledge as kb

SERVERS = ('dev','jellyfin','docker','traefik','omada','public-website','db-01','fs-01')
DIAGNOSTICS = ('identity','system','nginx-status','docker-status','disk','failed-units')

DEFINITIONS = [
 {'type':'function','function':{'name':'knowledge_search',
   'description':('Search the knowledge base: notes (by the human, by agents, and handoffs of finished project tasks) and facts '
     'recorded from earlier verified diagnostics and changes. Results are ranked by relevance. Each note says who wrote it and '
     'when; each fact has freshness fresh, stale (expired: re-check live) or unverified, plus when and by which tool it was verified. '
     'scope: all (default), project (this project only) or workspace (notes outside projects).'),
   'parameters':{'type':'object','properties':{'query':{'type':'string','minLength':2,'maxLength':160},
     'scope':{'type':'string','enum':['all','project','workspace']}},'required':['query'],'additionalProperties':False}}},
 {'type':'function','function':{'name':'knowledge_save',
   'description':('Save a durable note so later runs and other agents reuse it instead of re-discovering it: a decision and why, '
     'a convention, hosts, paths, URLs, branches, ports, how something is set up, or WHERE a credential is stored (Infisical path, '
     '{{secret:NAME}}, env variable name). Never a password, token, key or other secret value: such notes are refused. '
     'Saving again with the same title updates your earlier note. scope project (in a project task) shares it with the '
     "project's other tasks and its planner; workspace shares it with every agent. Keep it short and factual."),
   'parameters':{'type':'object','properties':{'title':{'type':'string','minLength':2,'maxLength':160},
     'text':{'type':'string','minLength':3,'maxLength':4000},'scope':{'type':'string','enum':['workspace','project']}},
     'required':['title','text'],'additionalProperties':False}}},
 {'type':'function','function':{'name':'knowledge_save_diagnostic',
   'description':'Run a fresh fixed SSH diagnostic on an authorized server and upsert its REAL output into the shared Home Knowledge Base. This is a verified snapshot, not a claim about configuration not inspected.',
   'parameters':{'type':'object','properties':{'server':{'type':'string','enum':list(SERVERS)},'diagnostic':{'type':'string','enum':list(DIAGNOSTICS)}},'required':['server','diagnostic'],'additionalProperties':False}}}
]

def _connect():
    return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)

def _redact(value):
    # Conservative filtering for common secrets in outputs; reject rather than save suspicious output.
    if memory_store._SECRET.search(value):
        raise ValueError('Diagnostic output may contain secrets; not saved')
    return value

def _note(r):
    return {'id':str(r['id']),'title':r['title'],'body':r['body'][:1500],'source':r['source'],'origin':r.get('origin') or 'human',
            'author':r.get('author'),'project':r.get('project_title'),'updated_at':r['updated_at'].isoformat()}


def search(query, workspace='home', project_id=None, scope='all'):
    if workspace!='home': return {'error':'knowledge_tools_only_enabled_for_home'}
    if not isinstance(query,str) or not (2<=len(query)<=160):return {'error':'invalid_query'}
    if scope not in ('all','project','workspace'): scope='all'
    if scope=='project' and not project_id: return {'error':'no_project','detail':'This run is not part of a project.'}
    with _connect() as db:
        # HLA_KB_V2: ranked full-text (and trigram) search, this project's notes first; substring match as fallback.
        rows=kb.search_notes(db,workspace,query,scope=scope,project_id=project_id,limit=8)
        if not rows:
            rows=kb.search_like(db,workspace,query,scope=scope,project_id=project_id,limit=8)
        rows=sorted(rows,key=lambda r:r['project_id'] is None)  # stable: project notes first, each in rank order
        # HLA_FACTS_V1: recorded facts; the whole query first, then any of its words; freshest and verified first.
        words=[query]+kb.terms(query,6)
        like=' OR '.join(['key ILIKE %s OR fact ILIKE %s']*len(words))
        facts=db.execute('''SELECT key,fact,source,confidence,kind,verified_at,expires_at FROM agent_memories
          WHERE workspace=%s AND kind IS DISTINCT FROM 'task' AND ('''+like+''')
          ORDER BY (key ILIKE %s OR fact ILIKE %s) DESC, (confidence='verified' AND (expires_at IS NULL OR expires_at>NOW())) DESC,
          updated_at DESC LIMIT 8''',
          (workspace,*[p for w in words for p in ('%'+w+'%','%'+w+'%')],'%'+query+'%','%'+query+'%')).fetchall() if scope!='project' else []
    return {'entries':[_note(r) for r in rows],
        'facts':[{'key':f['key'],'fact':f['fact'][:1200],'source':f['source'],'freshness':memory_store.freshness(f),
            'verified_at':f['verified_at'] and f['verified_at'].isoformat(),'expires_at':f['expires_at'] and f['expires_at'].isoformat()}
            for f in facts]}


def save_note(title, text, workspace='home', scope=None, project_id=None, agent_id=None, task_id=None):
    """knowledge_save: an agent-written note (origin agent, author = the agent), in the workspace or this project."""
    if workspace!='home': return {'error':'knowledge_tools_only_enabled_for_home'}
    with _connect() as db:
        result=kb.save_note(db,workspace,title,text,scope=scope or ('project' if project_id else 'workspace'),
                            project_id=project_id,agent_id=agent_id,task_id=task_id)
        db.commit()
    return result

async def save_diagnostic(client, endpoint, token, server, diagnostic, workspace='home', agent_id=None):
    if workspace!='home':return {'error':'knowledge_tools_only_enabled_for_home'}
    if server not in SERVERS or diagnostic not in DIAGNOSTICS:return {'error':'not_allowed'}
    response=await client.post(endpoint,headers={'Authorization':'Bearer '+token},json={
        'agent_id':str(agent_id or ''),'server':server,'mode':'diagnostic','diagnostic':diagnostic},timeout=65)
    if response.status_code!=200:return {'error':'gateway_rejected','http_status':response.status_code}
    value=response.json()
    if value.get('exit_code')!=0 or value.get('timed_out') or value.get('truncated'):
        return {'error':'diagnostic_not_verified','server':server,'diagnostic':diagnostic,
                'exit_code':value.get('exit_code')}
    output=value.get('stdout')
    if not isinstance(output,str) or not output.strip() or len(output)>12000:
        return {'error':'empty_or_oversized_diagnostic'}
    try: output=_redact(output)
    except ValueError:return {'error':'possible_sensitive_output_not_saved'}
    now=datetime.now(timezone.utc).isoformat()
    title=f'Server {server} / {diagnostic}'
    source=f'ssh-gateway:verified:{server}:{diagnostic}'
    body=f'Verified at: {now}\nHost profile: {server}\nDiagnostic: {diagnostic}\nExit code: 0\nActual output:\n{output}'
    # Use an advisory transaction lock keyed by the stable source to prevent duplicate inserts.
    with _connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(source,))
        existing=db.execute('SELECT id FROM knowledge WHERE workspace=%s AND source=%s ORDER BY updated_at DESC LIMIT 1 FOR UPDATE',(workspace,source)).fetchone()
        if existing:
            ident=existing['id']
            db.execute("UPDATE knowledge SET title=%s,body=%s,origin='agent',agent_id=%s,updated_at=NOW() WHERE id=%s",
                       (title,body,agent_id,ident))
            status='updated'
        else:
            ident=uuid.uuid4()
            db.execute('''INSERT INTO knowledge(id,workspace,title,body,source,origin,author,agent_id)
                VALUES (%s,%s,%s,%s,%s,'agent',(SELECT name FROM agents WHERE id=%s),%s)''',
                       (ident,workspace,title,body,source,agent_id,agent_id))
            status='created'
        db.commit()
    return {'status':status,'id':str(ident),'server':server,'diagnostic':diagnostic,
            'verified_at':now,'source':source,'preview':output[:900]}
