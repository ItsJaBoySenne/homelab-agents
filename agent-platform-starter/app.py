from tool_context.task_messages import enrich_messages
from threads.routes import (migrate as migrate_threads, build_router as build_threads_router,
                            claim_thread_for_task, post_task_outcome, command_thread, run_approved_command)
from memory.store import (migrate as migrate_memory, capture_proxmox, capture_task, capture_tool,
                          prune as prune_memory, freshness as memory_freshness)
from memory import knowledge as kb
from streaming.provider import generate as stream_generate
import providers
from agent_tool.runtime import completion_with_tools, fetch_permissions
import approvals
import attachments
import runs
import sandbox
import schedules
import projects
import push
import inbox
import audit_view
import fact_recheck
import costs
import voice
import models
import connections
import model_registry
import mcp_admin
import subscriptions
import proxmox_admin
import omada_admin
import websites
import public_routes
import chief
import threads.routes as thread_runs
from threads.routes import _launch as launch_thread_run, _start as start_thread_run
from streaming.events import event_response, TERMINAL as TERMINAL_STATUSES
from streaming import bus as live_bus
import asyncio
import os
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
import threading
from psycopg.types.json import Jsonb
from fastapi import FastAPI, Header, HTTPException, Response, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse


# HLA_PERF_V1: one pool for the API process instead of a new Postgres connection per query. Used as
# `with database_connection() as db:` like psycopg.connect: commit on success, rollback on error.
_pool = None
_pool_lock = threading.Lock()


def database_connection():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(os.environ['DATABASE_URL'], min_size=2, max_size=int(os.getenv('HLA_DB_POOL_SIZE', '20')),
                                       kwargs={'row_factory': dict_row}, check=ConnectionPool.check_connection, open=True)
    return _pool.connection()


@asynccontextmanager
async def lifespan(app):
    with database_connection() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                task_text TEXT NOT NULL,
                status TEXT NOT NULL,
                provider TEXT,
                worker_model TEXT,
                result_text TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                openrouter_cost_usd NUMERIC(18, 10),
                typesafe_input_tokens INTEGER,
                typesafe_output_tokens INTEGER,
                error_message TEXT
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
              id UUID PRIMARY KEY, workspace TEXT NOT NULL CHECK (workspace IN ('home','work')),
              title TEXT NOT NULL, body TEXT NOT NULL, source TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        db.execute('''CREATE TABLE IF NOT EXISTS agents (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            workspace TEXT NOT NULL CHECK(workspace IN ('home','work')),
            model TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            daily_budget_usd NUMERIC(12, 5) NOT NULL DEFAULT 0.25,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )''')
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id) ON DELETE SET NULL')
        db.execute('CREATE INDEX IF NOT EXISTS tasks_agent_created_idx ON tasks (agent_id, created_at DESC)')
        # HLA_PERF_V1: the search vector is stored and indexed instead of recomputed for every note on every run.
        db.execute("ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED")
        db.execute('CREATE INDEX IF NOT EXISTS knowledge_tsv_idx ON knowledge USING GIN (tsv)')
        db.execute('DROP INDEX IF EXISTS knowledge_search_idx')
        db.execute('CREATE INDEX IF NOT EXISTS knowledge_ws_updated_idx ON knowledge (workspace, updated_at DESC)')
        db.execute("""CREATE TABLE IF NOT EXISTS queued_jobs (
          id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
          task_text TEXT NOT NULL, workspace TEXT NOT NULL CHECK(workspace IN ('home','work')),
          agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
          max_output_tokens INTEGER NOT NULL DEFAULT 1500,
          reasoning_effort TEXT NOT NULL DEFAULT 'low',
          status TEXT NOT NULL DEFAULT 'queued',
          api_task_id UUID, result_text TEXT, error_message TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS queued_jobs_status_idx ON queued_jobs(status, created_at)")
        migrate_memory(db)
        prune_memory(db)
        migrate_threads(db)
        schedules.migrate(db)
        projects.migrate(db)
        attachments.migrate(db)  # HLA_ATTACHMENTS_V1 (after threads, projects and tasks)
        kb.migrate(db)  # after projects: project knowledge refers to them
        push.migrate(db)
        inbox.migrate(db)
        fact_recheck.migrate(db)  # after migrate_memory: adds re-check columns to agent_memories
        costs.migrate(db)   # HLA_COSTS_V1 (after projects)
        models.migrate(db)  # HLA_MODEL_ROUTING_V2
        model_registry.migrate(db)  # HLA_MODEL_REGISTRY_V1: seeded from AGENT_ALLOWED_MODELS when empty
        websites.migrate(db)  # HLA_WEBSITES_V1: owner overrides and the last status per website
        chief.migrate(db)  # HLA_CHIEF_V1: agents.platform_tools and the Chief's agent/conversation (after threads)
        # HLA_TASK_APPROVAL_V1: append-only UI approval audit.
        db.execute("""CREATE TABLE IF NOT EXISTS task_approval_events (
            id UUID PRIMARY KEY, task_id UUID NOT NULL REFERENCES tasks(id),
            action TEXT NOT NULL CHECK (action IN ('approved','rejected')),
            actor TEXT NOT NULL DEFAULT 'dashboard_session',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        # HLA_THREAD_APPROVAL_V1: run settings and risk scores survive an approval hold.
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS run_params JSONB')
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS risk_probability DOUBLE PRECISION')
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS expert_probability DOUBLE PRECISION')
        db.execute('ALTER TABLE task_approval_events ADD COLUMN IF NOT EXISTS reason TEXT')
        # HLA_APPROVAL_AUDIT_V1: the event log covers the whole approval lifecycle, not only the decision.
        db.execute('ALTER TABLE task_approval_events ADD COLUMN IF NOT EXISTS scope TEXT')
        db.execute('ALTER TABLE task_approval_events DROP CONSTRAINT IF EXISTS task_approval_events_action_check')
        db.execute("""ALTER TABLE task_approval_events ADD CONSTRAINT task_approval_events_action_check CHECK (action IN
            ('requested','approved','rejected','expired','completed','incomplete','failed','cancelled'))""")
        # HLA_RUNS_V1: tool steps of a run, shown live and kept for the conversation view.
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress JSONB')
        # HLA_TIMINGS_V1: where a run's time went (stages, model rounds with first token, tools).
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS timings JSONB')
        db.execute('CREATE INDEX IF NOT EXISTS task_approval_events_task_idx ON task_approval_events(task_id,created_at)')
        # HLA_PERF_V1 (tests/perf/REPORT.md): task list order and filters, approval expiry, agent cost totals.
        db.execute('CREATE INDEX IF NOT EXISTS tasks_created_idx ON tasks (created_at DESC)')
        db.execute('CREATE INDEX IF NOT EXISTS tasks_workspace_created_idx ON tasks (workspace, created_at DESC)')
        db.execute('CREATE INDEX IF NOT EXISTS tasks_status_created_idx ON tasks (status, created_at DESC)')
        db.execute("CREATE INDEX IF NOT EXISTS tasks_awaiting_approval_idx ON tasks (created_at) WHERE status = 'human_approval_required'")
        db.execute("CREATE INDEX IF NOT EXISTS tasks_agent_cost_idx ON tasks (agent_id) INCLUDE (openrouter_cost_usd)")
        # HLA_CHAT_APPROVAL_V1: the shell command a run asked to execute, and what became of it.
        db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pending_command JSONB')
        db.execute("CREATE INDEX IF NOT EXISTS tasks_pending_command_idx ON tasks (created_at) WHERE pending_command->>'status' = 'pending'")
        db.execute("""UPDATE tasks SET pending_command=pending_command||'{"status":"interrupted"}'
            WHERE pending_command->>'status'='running'""")
        db.execute('''CREATE TABLE IF NOT EXISTS sandbox_resets (id UUID PRIMARY KEY, actor TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ, outcome TEXT)''')
        # A reset that was running when the API stopped can never finish; say so instead of "in progress".
        db.execute('''UPDATE sandbox_resets SET finished_at=NOW(),
            outcome='interrupted by an API restart; check the VM and reset again if needed' WHERE finished_at IS NULL''')
        # Single API process: a task still running at startup was interrupted and will never finish.
        db.execute("""UPDATE tasks SET status='failed',finished_at=NOW(),
            error_message='Interrupted by service restart' WHERE status='running'""")
        db.commit()
    try:
        with database_connection() as db:
            db.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            db.execute('CREATE INDEX IF NOT EXISTS tasks_text_trgm_idx ON tasks USING gin (task_text gin_trgm_ops)')
            db.commit()
    except Exception:
        pass  # without pg_trgm the task search still works, just without an index
    try:
        async with httpx.AsyncClient() as client:
            await seed_policy(client)
    except Exception:
        pass  # gateway may start later; the Policy page retries the seeding
    # HLA_SCHEDULES_V1: scheduled conversation runs.
    scheduler = asyncio.create_task(schedules.loop(database_connection, launch_thread_run, start_thread_run))
    # HLA_PROJECTS_V1: multi-agent projects (orchestrator loop; runs report back when they finish).
    orchestrator = asyncio.create_task(projects.loop())
    # HLA_PUSH_V1: notifications to the installed app (outbox filled by the orchestrator).
    notifier = asyncio.create_task(push.loop())
    # HLA_FACT_RECHECK_V1: weekly re-check of stale diagnostic facts (off unless HLA_FACT_RECHECK=weekly).
    rechecker = asyncio.create_task(fact_recheck.loop())
    # HLA_WEBSITES_V1: status of the websites on public-website every HLA_WEBSITE_CHECK_MINUTES (0 = off).
    site_monitor = asyncio.create_task(websites.loop())
    yield
    site_monitor.cancel()
    rechecker.cancel()
    scheduler.cancel()
    orchestrator.cancel()
    notifier.cancel()
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None  # a later startup (tests, reload) opens a fresh pool


app = FastAPI(title='Lean Agent Router Phase 2', docs_url='/docs', lifespan=lifespan)


# HLA_MODEL_ROUTING_V1: runtime limits come from environment; configured models remain allowlisted.
def hla_token_cap():
    """Upper bound for a requested output limit, or None. Not asking for a limit (the default) means unlimited:
    no max_tokens is sent and the model writes until it is done (up to its own maximum)."""
    try: value = int(os.getenv('HLA_MAX_OUTPUT_TOKENS', '0') or 0)
    except (ValueError, TypeError): return None
    return max(100, value) if value > 0 else None


def clamp_tokens(value):
    """A requested output limit within HLA_MAX_OUTPUT_TOKENS; None stays None (unlimited)."""
    cap = hla_token_cap()
    return min(value, cap) if value and cap else (value or None)

class Task(BaseModel):
    # Conversation runs carry history and, for projects, the brief (threads.routes.MAX_PROMPT).
    task: str = Field(min_length=3, max_length=60000)
    workspace: str = Field(default='home', pattern='^(home|work)$')
    max_output_tokens: int | None = Field(default=None, ge=100, le=200000)  # None: unlimited
    model_override: str | None = Field(default=None, min_length=3, max_length=160)
    agent_id: uuid.UUID | None = None
    reasoning_effort: str | None = Field(default=None, pattern='^(none|minimal|low|medium|high)$')
    # HLA_ATTACHMENTS_V1: staged uploads sent with a dashboard task (start-stream links them to the run).
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


def task_run_params(task):
    """Settings needed to re-run a task exactly as submitted after an approval hold."""
    return Jsonb({'max_output_tokens': task.max_output_tokens, 'model_override': task.model_override,
                  'reasoning_effort': task.reasoning_effort})


def record_approval_event(task_id, action, actor, *, reason=None, scope=None):
    with database_connection() as db:
        approvals.record(db, task_id, action, actor, reason=reason, scope=scope)
        db.commit()



def risk_review_enabled():
    return os.getenv('HLA_RISK_REVIEW', 'false').strip().lower() in ('1', 'true', 'yes', 'on')


def required_env(key):
    value = os.getenv(key)
    if not value:
        raise HTTPException(503, f'Missing server setting: {key}')
    return value


def authorize(authorization):
    token = required_env('AGENT_PLATFORM_TOKEN')
    if not authorization or not secrets.compare_digest(authorization, 'Bearer ' + token):
        raise HTTPException(401, 'Unauthorized')


def update_task(task_id, **fields):
    if not fields:
        return
    columns = ', '.join(f'{key} = %s' for key in fields)
    with database_connection() as db:
        db.execute(f'UPDATE tasks SET {columns} WHERE id = %s', [*fields.values(), task_id])
        db.commit()
    # HLA_LIVE_V1: followers re-read the task now instead of at their next poll.
    if fields.get('status') in TERMINAL_STATUSES:
        live_bus.finish(task_id)
    else:
        live_bus.changed(task_id)


@app.get('/health')
def health():
    with database_connection() as db:
        db.execute('SELECT 1')
    return {'status': 'ok', 'database': 'connected'}


@app.get('/tasks')
def list_tasks(authorization: str | None = Header(default=None), limit: int = 20):
    authorize(authorization)
    if not 1 <= limit <= 100:
        raise HTTPException(422, 'limit must be 1..100')
    with database_connection() as db:
        rows = db.execute('''
            SELECT id, created_at, finished_at, task_text, status, provider,
                   worker_model, result_text, input_tokens, output_tokens,
                   openrouter_cost_usd, agent_id, typesafe_input_tokens,
                   typesafe_output_tokens, error_message
            FROM tasks ORDER BY created_at DESC LIMIT %s
        ''', (limit,)).fetchall()
    return {'tasks': rows}


@app.get('/tasks/{task_id}')
def get_task(task_id: uuid.UUID, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with database_connection() as db:
        row = db.execute('SELECT * FROM tasks WHERE id = %s', (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Task not found')
    return row


class KnowledgeEntry(BaseModel):
    workspace: str = Field(default='home', pattern='^(home|work)$')
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=3, max_length=20000)
    source: str | None = Field(default=None, max_length=500)
    # HLA_KB_V2: a project's knowledge (None: the whole workspace). Left out on an edit, it stays as it was.
    project_id: uuid.UUID | None = None


def retrieve_knowledge(task_text, workspace, limit=4):
    # PostgreSQL FTS ranking (plus pg_trgm title similarity when installed); strictly scoped to the task workspace.
    # Do not perform broad retrieval across home/work. Conversation runs carry the transcript; search for what the
    # user asked (kb.focus), not the fixed preamble.
    with database_connection() as db:
        rows = kb.search_notes(db, workspace, kb.focus(task_text), limit=limit)
    # Fixed-size context budget; no infinite document injection.
    budget = 2600
    excerpts = []
    for row in rows:
        text = row['body'][:750]
        line = f"[{row['id']}] {row['title']} (source: {row['source'] or 'manual'}): {text}"
        if len(line) > budget: line = line[:budget]
        excerpts.append(line)
        budget -= len(line)
        if budget <= 0: break
    return excerpts


def _knowledge_project(db, project_id, workspace):
    """The project a note is filed under, checked: it must exist in the note's workspace."""
    if project_id is None: return None
    project = db.execute('SELECT id,workspace FROM projects WHERE id=%s', (project_id,)).fetchone()
    if not project: raise HTTPException(422, 'Project not found')
    if project['workspace'] != workspace: raise HTTPException(422, 'The project is in the other workspace')
    return project['id']


@app.get('/knowledge')
def list_knowledge(authorization: str | None = Header(default=None), workspace: str = 'home', q: str = '', project: str = ''):
    """Notes, newest first. project: '' (all, each with its project id/title), 'none' (workspace notes only) or a project id.
    origin says who wrote a note: human, agent (knowledge_save; author is the agent) or auto (a project task handoff)."""
    authorize(authorization)
    if workspace not in ('home', 'work'): raise HTTPException(422, 'Invalid workspace')
    if project not in ('', 'none'):
        try: project = str(uuid.UUID(project))
        except ValueError: raise HTTPException(422, 'project must be a project id or none')
    with database_connection() as db:
        rows = kb.list_entries(db, workspace, project=project, q=q, limit=200 if project else 100)
    return {'entries': rows}


@app.post('/knowledge')
def create_knowledge(entry: KnowledgeEntry, authorization: str | None = Header(default=None), author: str | None = None):
    authorize(authorization)
    uid=uuid.uuid4()
    with database_connection() as db:
        project_id = _knowledge_project(db, entry.project_id, entry.workspace)
        db.execute("INSERT INTO knowledge(id,workspace,title,body,source,project_id,origin,author) VALUES (%s,%s,%s,%s,%s,%s,'human',%s)",
                   (uid,entry.workspace,entry.title,entry.body,entry.source,project_id,author))
        db.commit()
    return {'id':uid,'status':'created'}


@app.put('/knowledge/{entry_id}')
def edit_knowledge(entry_id: uuid.UUID, entry: KnowledgeEntry, authorization: str | None = Header(default=None), author: str | None = None):
    authorize(authorization)
    with database_connection() as db:
        current = db.execute('SELECT project_id FROM knowledge WHERE id=%s FOR UPDATE', (entry_id,)).fetchone()
        if not current: raise HTTPException(404, 'Not found')
        # project_id left out of the request: the note stays where it is (and must stay in its project's workspace).
        project_id = _knowledge_project(db, entry.project_id if 'project_id' in entry.model_fields_set else current['project_id'],
                                        entry.workspace)
        # A note the human edits is theirs from now on: automatic capture no longer overwrites it.
        db.execute('''UPDATE knowledge SET workspace=%s,title=%s,body=%s,source=%s,project_id=%s,origin='human',
            author=COALESCE(%s,author),updated_at=NOW() WHERE id=%s''',
            (entry.workspace,entry.title,entry.body,entry.source,project_id,author,entry_id))
        db.commit()
    return {'id':entry_id,'status':'updated'}


@app.delete('/knowledge/{entry_id}')
def delete_knowledge(entry_id: uuid.UUID, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with database_connection() as db:
        row=db.execute('DELETE FROM knowledge WHERE id=%s RETURNING id', (entry_id,)).fetchone()
        db.commit()
    if not row: raise HTTPException(404, 'Not found')
    return {'status':'deleted'}


class AgentPayload(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role: str = Field(default='', max_length=160)
    instructions: str = Field(default='', max_length=6000)
    workspace: str = Field(default='home', pattern='^(home|work)$')
    # Explicit allowlist of configured model IDs prevents arbitrary provider/model routing.
    model: str = Field(min_length=3, max_length=160)
    enabled: bool = True
    daily_budget_usd: float = Field(default=0.25, ge=0, le=100)


def agent_model_allowed(model: str):
    # HLA_MODEL_REGISTRY_V1: enabled models of the registry (Models page); AGENT_ALLOWED_MODELS while it is empty.
    model_registry.check(model)


@app.get('/agents')
def list_agents(authorization: str | None = Header(default=None), workspace: str | None = None):
    authorize(authorization)
    if workspace is not None and workspace not in ('home', 'work'):
        raise HTTPException(422, 'Invalid workspace')
    with database_connection() as db:
        return {'agents': db.execute('''SELECT a.*,
            (SELECT COUNT(*) FROM tasks t WHERE t.agent_id=a.id)::int AS task_count,
            COALESCE((SELECT SUM(t.openrouter_cost_usd) FROM tasks t WHERE t.agent_id=a.id),0) AS total_cost_usd
            FROM agents a WHERE (%s::text IS NULL OR workspace=%s) ORDER BY name''', (workspace,workspace)).fetchall()}


@app.post('/agents')
def create_agent(agent: AgentPayload, authorization: str | None = Header(default=None)):
    authorize(authorization)
    agent_model_allowed(agent.model)
    uid=uuid.uuid4()
    with database_connection() as db:
        db.execute('''INSERT INTO agents(id,name,role,instructions,workspace,model,enabled,daily_budget_usd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (uid,agent.name,agent.role,agent.instructions,agent.workspace,agent.model,agent.enabled,agent.daily_budget_usd))
        db.commit()
    return {'id':uid,'status':'created'}


@app.put('/agents/{agent_id}')
def edit_agent(agent_id: uuid.UUID, agent: AgentPayload, authorization: str | None = Header(default=None)):
    authorize(authorization)
    agent_model_allowed(agent.model)
    with database_connection() as db:
        result=db.execute('''UPDATE agents SET name=%s,role=%s,instructions=%s,workspace=%s,
            model=%s,enabled=%s,daily_budget_usd=%s,updated_at=NOW()
            WHERE id=%s RETURNING id''',
            (agent.name,agent.role,agent.instructions,agent.workspace,agent.model,agent.enabled,agent.daily_budget_usd,agent_id)).fetchone()
        db.commit()
    if not result: raise HTTPException(404,'Agent not found')
    return {'id':agent_id,'status':'updated'}


@app.delete('/agents/{agent_id}')
def delete_agent(agent_id: uuid.UUID, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with database_connection() as db:
        result=db.execute('DELETE FROM agents WHERE id=%s RETURNING id',(agent_id,)).fetchone()
        db.commit()
    if not result: raise HTTPException(404,'Agent not found')
    return {'status':'deleted'}


# HLA_MAINTENANCE_RISK_V1 - narrow text recognition; gateway is final authorization boundary.
def hla_maintenance_intent(text):
    import re
    if not isinstance(text,str): return None
    last=text.rsplit('LATEST USER MESSAGE:',1)[-1].strip()
    if len(last)>420: return None
    # This exception is restricted to a single explicitly named tool and a single allowlisted operation.
    if len(re.findall(r'\bserver_maintenance\b',last,re.I))!=1: return None
    if re.search(r'\b(and then|after that|also|additionally|delete|remove|wipe|ssh|shell|sudo|firewall|credential|secret|password|rotate|upload|download|overwrite|script|command|execute|reboot)\b',last,re.I):return None
    if not re.search(r'\bdev\b',last,re.I):return None
    pkg=re.findall(r'\b(?:install|installation of)\s+(curl|jq|git|htop|nginx)\b',last,re.I)
    svc=re.findall(r'\brestart\s+(nginx)\b',last,re.I)
    if len(pkg)+len(svc)!=1: return None
    if pkg:
        return {'server':'dev','operation':'install_package','target':pkg[0].lower()}
    return {'server':'dev','operation':'restart_service','target':svc[0].lower()}

async def execute_task(task: Task, authorization: str | None, *, stream_mode=False, task_id_override=None, approved_risk=False, read_only=False, risk_text=None,
                       tool_budget=None, timings=None, voice=False):
    authorize(authorization)
    # HLA_TIMINGS_V1: where the time of this run goes (setup, knowledge, permissions, risk review, prompt, every model
    # round with its first token, every tool, the first text the user sees), stored on the task as `timings`.
    run_started = time.monotonic()
    timings = timings if timings is not None else []
    def mark(stage, started, **extra):
        timings.append({'stage': stage, 'ms': int((time.monotonic() - started) * 1000), **extra})
    # HLA_RISK_REVIEW_V1: the TypeSafe risk review is opt-in (HLA_RISK_REVIEW=true); off, nothing is held for it and
    # no risk score is recorded. Agent levels, command approvals and unhinged mode work the same either way.
    risk_review_on = risk_review_enabled()
    typesafe_key = required_env('TYPESAFE_API_KEY') if risk_review_on else None
    task.max_output_tokens = clamp_tokens(task.max_output_tokens)
    # HLA_MODEL_REGISTRY_V1: the dashboard's default model, else OPENROUTER_WORKER_MODEL.
    # Read in a worker thread (it may query the database), so the event loop keeps serving other requests.
    model, model_source = await asyncio.to_thread(model_registry.default_model)
    if model_source == 'unavailable':
        raise HTTPException(503, 'The model registry cannot be read right now (database unavailable); try again shortly')
    model = model or required_env('OPENROUTER_WORKER_MODEL')
    agent = None
    if task.agent_id:
        # HLA_PERF_V1: database work in this async run goes to a worker thread so other requests keep flowing.
        def load_agent():
            with database_connection() as db:
                agent=db.execute('SELECT * FROM agents WHERE id=%s',(task.agent_id,)).fetchone()
                spent=db.execute('''SELECT COALESCE(SUM(openrouter_cost_usd),0) AS cost FROM tasks
                    WHERE agent_id=%s AND created_at >= date_trunc('day',now() at time zone 'utc') AT TIME ZONE 'UTC' ''',(task.agent_id,)).fetchone()['cost'] if agent else 0
            return agent, spent
        agent, spent = await asyncio.to_thread(load_agent)
        if not agent: raise HTTPException(404,'Agent not found')
        if not agent['enabled']: raise HTTPException(409,'Agent disabled')
        if agent['workspace'] != task.workspace: raise HTTPException(403,'Agent workspace mismatch')
        # Completed usage is tracked by UTC day. Safety margin avoids overspending tiny budgets.
        if float(spent) >= float(agent['daily_budget_usd']): raise HTTPException(429,'Agent daily budget reached')
        await asyncio.to_thread(agent_model_allowed, agent['model'])
        model = agent['model']
    if task.model_override:
        await asyncio.to_thread(agent_model_allowed, task.model_override)
        model = task.model_override
    # HLA_XAI_V1: the model's provider (OpenRouter or xAI) needs its key; say so before anything runs.
    try:
        provider_key = providers.resolve(model)['key']
    except providers.ProviderError as exc:
        if task_id_override is not None:  # the run's row exists already: say why it did not run
            await asyncio.to_thread(update_task, task_id_override, status='failed', finished_at=datetime.now(timezone.utc),
                                    provider=providers.name_of(model), worker_model=model, error_message=exc.message)
        raise HTTPException(exc.status, exc.message) from None
    provider_name = providers.name_of(model)
    task_id = task_id_override or uuid.uuid4()
    if task_id_override is None:
        def insert_task():
            with database_connection() as db:
                db.execute('INSERT INTO tasks (id, task_text, status, agent_id, workspace, run_params) VALUES (%s, %s, %s, %s, %s, %s)',
                           (task_id, task.task, 'running', task.agent_id, task.workspace, task_run_params(task)))
                db.commit()
        await asyncio.to_thread(insert_task)

    # HLA_DEV_SECRET_WORKFLOW_V2: restricted backend operation, no LLM call.
    if (not read_only and os.getenv('HLA_DEV_SECRET_WORKFLOW','false').lower() == 'true'
            and os.getenv('AGENT_SECRET_WRITER_ENABLED','false').lower() == 'true'
            and task.workspace == 'home'
            and task.agent_id is not None
            and str(task.agent_id) == os.getenv('SSH_TOOL_AGENT_ID','')):
        from brain.dev_secret_request import parse_request
        operation = parse_request(task.task)
        if operation is not None:
            from brain.secret_writer import generate_secret
            # The gateway generates the value and stores it in its secret store; only the reference comes back.
            async with httpx.AsyncClient(timeout=25) as writer_client:
                outcome = await generate_secret(writer_client, operation['name'], operation['kind'])
            successful = outcome.get('status') == 'created'
            status = 'completed' if successful else 'failed'
            # Never persist secret values or a credential-bearing API response.
            message = (
                'Secret created in the secret store (generated secrets): '
                + operation['name'] + ' (' + operation['kind'] + '), use it as {{secret:' + operation['name'] + '}}. Secret value not shown.'
                if successful else
                'Secret creation failed for ' + operation['name']
                + ' (stage: ' + str(outcome.get('stage',outcome.get('reason','unknown')))
                + ', HTTP status: ' + str(outcome.get('http_status','n/a')) + ').'
            )
            await asyncio.to_thread(update_task, task_id, status=status, finished_at=datetime.now(timezone.utc),
                        provider='internal_secret_writer', result_text=message,
                        error_message=None if successful else message)
            return {'id':str(task_id),'status':status,'provider':'internal_secret_writer',
                    'result':message,'error':None if successful else message}

    mark('setup', run_started)

    def recall():
        # HLA_KB_V2: notes (this project's first) and facts most relevant to the request, labelled and size-bounded.
        with database_connection() as db:
            project = db.execute('''SELECT p.id,p.title FROM tasks k JOIN projects p ON p.id=k.project_id
                WHERE k.id=%s''', (task_id,)).fetchone() if task_id_override else None
            block, counts = kb.context(db, task.workspace, kb.focus(task.task), project=project)
            return block, counts, project
    def fetch_inventory_for_task():
        inventory = proxmox_inventory()
        try:
            with database_connection() as db:
                capture_proxmox(db, inventory, task.workspace)
                db.commit()
        except Exception:
            pass  # Do not prevent read-only tool usage if memory capture fails.
        return inventory
    questions = {
        'expert': {'type': 'noul', 'instructions': 'Does this task require expert-level reasoning, difficult architecture, or complicated debugging rather than a routine response?'},
        'risky': {'type': 'noul', 'instructions': 'Could attempting this task involve destructive infrastructure changes, security-sensitive actions, real-money trading, or other irreversible operations?'},
    }
    timeout = httpx.Timeout(45.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # HLA_CHIEF_LATENCY_V1: the risk review runs while the prompt is prepared (permissions, knowledge, the Chief's
        # snapshot, the inventory context); nothing reaches the model or a tool before its decision below.
        async def risk_review():
            started = time.monotonic()
            try:
                decision_result = await client.post(
                    'https://api.typesafe.ai/v1/systemone',
                    headers={'Authorization': f'Bearer {typesafe_key}'},
                    # Conversations pass a focused risk_text (new message + previous reply) instead of the whole transcript.
                    json={'model': os.getenv('TYPESAFE_MODEL', 'jev-latest'), 'state': risk_text or task.task, 'questions': questions},
                )
                decision_result.raise_for_status()
                decision = decision_result.json()
                answers = decision['answers']
                result = decision, float(answers['expert']['noul']), float(answers['risky']['noul'])
                mark('risk_review', started)
                return result
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                mark('risk_review', started, failed=True)
                await asyncio.to_thread(update_task, task_id, status='failed', finished_at=datetime.now(timezone.utc),
                                        error_message=f'TypeSafe failed: {type(exc).__name__}', timings=Jsonb(timings))
                raise HTTPException(502, f'TypeSafe decision failed: {type(exc).__name__}') from exc
        async def no_risk_review():
            return {}, None, None
        risk_job = asyncio.create_task(risk_review() if risk_review_on else no_risk_review())

        async def load_permissions():
            # HLA_POLICY_V1: what this agent may do on each server comes from the gateway's policy.
            if not (task.workspace == 'home' and task.agent_id is not None and os.getenv('SSH_TOOL_ENABLED','false').lower() == 'true'):
                return {}
            started = time.monotonic()
            try:
                return await fetch_permissions(client, task.agent_id)
            finally:
                mark('permissions', started)

        async def load_knowledge():
            started = time.monotonic()
            try:
                return await asyncio.to_thread(recall)
            except Exception:  # context is a help: a failed lookup must not fail the run
                return '', {'project': 0, 'workspace': 0, 'facts': 0}, None
            finally:
                mark('knowledge', started)

        async def load_snapshot():
            # HLA_CHIEF_LATENCY_V1: a spoken Chief turn gets the platform's state in its prompt (one model round).
            if not (voice and agent and agent.get('platform_tools')):
                return None
            started = time.monotonic()
            def build():
                import chief
                with database_connection() as db:
                    thread_id = chief._chief_thread(db, task_id)
                    return chief.snapshot(db, thread_id) if chief.wants_snapshot(db, thread_id) else None
            try:
                return await asyncio.to_thread(build)
            except Exception:
                return None  # the tools still answer; a failed snapshot must not fail the run
            finally:
                mark('snapshot', started)

        try:
            permissions, (knowledge_block, knowledge_counts, project), snapshot = await asyncio.gather(
                load_permissions(), load_knowledge(), load_snapshot())
            started = time.monotonic()
            base_messages = [
                {'role':'system','content':'You are an efficient assistant. Respond accurately and concisely. Do not claim to execute external actions.'},
                {'role':'system','content': ('Agent: '+agent['name']+'\nRole: '+agent['role']+'\nInstructions: '+agent['instructions'][:6000])} if agent else {'role':'system','content':'General worker; no assigned agent.'},
                {'role':'system','content':knowledge_block} if knowledge_block else {'role':'system','content':'No knowledge notes or recorded facts match this request.'},
                *([{'role':'system','content':chief.SNAPSHOT_NOTE + snapshot}] if snapshot else []),
                {'role':'user','content':task.task},
            ]
            prepared_messages = await asyncio.to_thread(enrich_messages, base_messages, task.task, task.workspace, fetch_inventory_for_task)
            mark('prompt', started)
            decision, expert_probability, risk_probability = await risk_job
        except BaseException:
            risk_job.cancel()
            try:
                await risk_job
            except BaseException:
                pass
            raise
        unhinged = any(p.get('unhinged') for p in permissions.values())

        typesafe_usage = decision.get('usage') or {}
        ts_fields = {
            'typesafe_input_tokens': typesafe_usage.get('input_tokens'),
            'typesafe_output_tokens': typesafe_usage.get('output_tokens'),
            'risk_probability': risk_probability,
            'expert_probability': expert_probability,
        }
        # Only single-operation dev maintenance may proceed past a risky classification.
        maintenance_intent = None
        if (os.getenv('HLA_DEV_MAINTENANCE_RISK_EXCEPTION','false').lower()=='true'
                and permissions and not read_only):
            maintenance_intent = hla_maintenance_intent(task.task)
        risky = risk_probability is not None and risk_probability >= 0.40
        maintenance_restricted = risky and maintenance_intent is not None
        if risky and unhinged and not maintenance_restricted and not approved_risk:
            # Unhinged mode: no approval hold, but the risk and the bypass are on record.
            await asyncio.to_thread(record_approval_event, task_id, 'approved', 'unhinged-mode', reason=f'risk {risk_probability:.2f}', scope='full')
            approved_risk = True
        if risky and not maintenance_restricted and not approved_risk:
            await asyncio.to_thread(update_task, task_id, status='human_approval_required', finished_at=datetime.now(timezone.utc),
                                    timings=Jsonb(timings), **ts_fields)
            await asyncio.to_thread(record_approval_event, task_id, 'requested', 'typesafe', reason=f'risk {risk_probability:.2f}')
            return {'id': str(task_id), 'status': 'human_approval_required', 'reason': 'Potentially high-impact task', 'type_safe': decision, 'result': None}

        try:
            payload = {
                'model': model,
                'messages': prepared_messages,
                **({'max_tokens': task.max_output_tokens} if task.max_output_tokens else {}),
                'reasoning': {'effort':task.reasoning_effort or os.getenv('DEFAULT_REASONING_EFFORT','low')},
            }
            # HLA_ATTACHMENTS_V1: files of this message, conversation, project or task: text in the prompt, images as
            # image parts for models that accept them. A failed lookup must not fail the run.
            attachment_ctx = None
            try:
                attachment_ctx = await asyncio.to_thread(attachments.run_context, database_connection, task_id, model, bool(permissions))
                payload['messages'] = attachments.apply(payload['messages'], attachment_ctx)
            except Exception:
                attachment_ctx = None
            if read_only:
                payload['messages'].insert(-1, {'role':'system','content':(
                    'This run was approved for READ-ONLY investigation. Only read-only diagnostics are available. '
                    'Do not attempt changes; describe any change you recommend (exact target, command and how to undo it) '
                    'so a human can approve it separately.')})
            async def record_partial(text):
                # HLA_LIVE_V1: followers get every update from memory; the database copy (for reloads and
                # restarts) is refreshed every 2 s.
                if text and not record_partial.seen:  # HLA_TIMINGS_V1: the first words the user sees (and hears)
                    record_partial.seen = True
                    mark('first_text', run_started)
                live_bus.set_text(task_id, text)
                now = time.monotonic()
                if now - record_partial.last >= 2.0:
                    record_partial.last = now
                    await asyncio.to_thread(update_task, task_id, partial_text=text)
            record_partial.last = 0.0
            record_partial.seen = False
            # HLA_CHIEF_V1: the Chief's platform tools run in the same tool loop, also without any server access.
            platform_tools = bool(agent and agent.get('platform_tools'))
            ssh_tool_active = bool(permissions) or platform_tools
            async def record_progress(step):
                # A step is reported when it starts (status running) and again when it ends; the second replaces the first.
                def write():
                    with database_connection() as db:
                        db.execute('''UPDATE tasks SET progress=CASE WHEN jsonb_array_length(COALESCE(progress,'[]'::jsonb))>%s
                            THEN jsonb_set(progress,ARRAY[%s::text],%s) ELSE COALESCE(progress,'[]'::jsonb)||%s END WHERE id=%s''',
                            (step['n'],step['n'],Jsonb(step),Jsonb([step]),task_id))
                        db.commit()
                await asyncio.to_thread(write)
                live_bus.changed(task_id)
            async def record_evidence(call, reply):
                def write():
                    with database_connection() as db:
                        capture_tool(db, task.workspace, call, reply, task_id=task_id, agent_id=task.agent_id)
                        db.commit()
                try:
                    await asyncio.to_thread(write)
                except Exception:
                    pass  # Fact capture must never break the run that produced the evidence.
            async def record_command_request(command):
                await asyncio.to_thread(update_task, task_id, pending_command=Jsonb({**command, 'status': 'pending'}))
            if ssh_tool_active:
                result = await completion_with_tools(client, payload, provider_key,
                    agent_id=task.agent_id, permissions=permissions,
                    maintenance_intent=maintenance_intent if maintenance_restricted else None, read_only=read_only,
                    on_progress=record_progress, on_command_request=record_command_request, on_evidence=record_evidence,
                    on_text=record_partial if stream_mode else None, tool_budget=tool_budget,
                    project_id=project and project['id'], task_id=task_id,
                    attachment_scope=(attachment_ctx or {}).get('scope') or {'task_id': task_id}, platform=platform_tools,
                    on_timing=timings.append, voice=voice)
                await asyncio.to_thread(update_task, task_id, partial_text=(result['choices'][0].get('message',{}).get('content') or ''))
            elif stream_mode:
                started = time.monotonic()
                result = await stream_generate(client,payload,record_partial)
                mark('model', started, round=1)
                await asyncio.to_thread(update_task, task_id, partial_text=result['choices'][0].get('message',{}).get('content',''))
            else:
                result = await providers.complete(client, payload)
            choice = result['choices'][0]
            output = (choice.get('message', {}).get('content') or '').strip()
            finish_reason = choice.get('finish_reason')
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            # Say what the provider reported (its message or HTTP status), not only the exception type.
            label = providers.LABELS[provider_name]
            reason = (str(exc)[:300] if isinstance(exc, (providers.ProviderHTTPError, providers.ProviderError)) else
                      type(exc).__name__ + (f' (HTTP {exc.response.status_code})' if isinstance(exc, httpx.HTTPStatusError)
                                           else f': {str(exc)[:200]}' if isinstance(exc, ValueError) and str(exc) else ''))
            mark('total', run_started)
            await asyncio.to_thread(update_task, task_id, status='failed', finished_at=datetime.now(timezone.utc), provider=provider_name,
                                    worker_model=model, error_message=f'{label} failed: {reason}', timings=Jsonb(timings), **ts_fields)
            raise HTTPException(502, f'{label} worker failed: {reason}') from exc

        usage = result.get('usage') or {}
        # OpenRouter may charge for reasoning tokens even when no final text is generated.
        # An answer cut off by the output limit is not a completed task.
        task_status = 'completed' if output and finish_reason not in ('length', 'content_filter', 'tool_budget') else 'incomplete'
        issue = None
        if not output:
            issue = 'Model returned no final answer (possibly exhausted output token budget)'
        elif finish_reason == 'length':
            issue = 'Model response truncated at output token limit'
        elif finish_reason == 'content_filter':
            issue = 'Model response stopped by content filter'
        elif finish_reason == 'tool_budget':
            issue = 'Tool-call budget used up before the agent gave a final answer'
        mark('total', run_started)
        update_task(
            task_id, status=task_status, finished_at=datetime.now(timezone.utc), timings=Jsonb(timings),
            provider=provider_name, worker_model=model, result_text=output or None,
            input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'),
            openrouter_cost_usd=usage.get('cost'), error_message=issue, **ts_fields,
        )
        if task_status == 'completed':
            def remember():
                with database_connection() as db:
                    capture_task(db, task_id, task.workspace, task.task, output)
                    db.commit()
            try:
                await asyncio.to_thread(remember)
            except Exception:
                pass  # Persistence of final task must not depend on optional memory capture.
        return {
            'id': str(task_id), 'status': task_status, 'provider': provider_name,
            'worker_model': model, 'type_safe': decision, 'finish_reason': finish_reason,
            # HLA_CONTINUE_V1: the tool budget ran out before the work was done; the reply ends with a checkpoint.
            'budget_exhausted': bool((result.get('hla') or {}).get('budget_exhausted')),
            'worker_usage': usage, 'knowledge_matches': knowledge_counts['project'] + knowledge_counts['workspace'],
            'knowledge_facts': knowledge_counts['facts'],
            'result': output or None, 'error': issue,
        }

@app.post('/tasks')
async def run_task(task: Task, authorization: str | None = Header(default=None)):
    return await execute_task(task, authorization)

# Local-only dashboard with signed, HttpOnly session cookies.
UI_DIR = Path(__file__).resolve().parent / 'ui'
SESSION_MAX_AGE = 43200

class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=256)

def _session_key():
    return hmac.new(required_env('AGENT_PLATFORM_TOKEN').encode(), ('dashboard-v1:' + required_env('DASHBOARD_PASSWORD')).encode(), hashlib.sha256).digest()

def _sign(payload):
    return hmac.new(_session_key(), payload.encode(), hashlib.sha256).hexdigest()

def _valid_session(cookie):
    if not cookie or '.' not in cookie:
        return False
    payload, sig = cookie.rsplit('.', 1)
    if not hmac.compare_digest(_sign(payload), sig):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return data.get('v') == 1 and int(data.get('exp', 0)) > time.time()
    except (ValueError, TypeError):
        return False

def require_dashboard(request):
    if not _valid_session(request.cookies.get('agent_session')):
        raise HTTPException(401, 'Sign in first')

def require_ui_write(request):
    require_dashboard(request)
    if request.headers.get('x-agent-ui') != '1':
        raise HTTPException(403, 'Missing UI request header')
    origin = request.headers.get('origin')
    if origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get('host', ''):
            raise HTTPException(403, 'Cross-origin request blocked')

app.include_router(projects.build_router(database_connection, require_dashboard, require_ui_write, launch_thread_run, start_thread_run))
thread_runs._deps.update(may_continue=projects.may_continue, after_run=projects.after_run,
                        checkpoint_continues=projects.checkpoint_continues)
app.include_router(push.build_router(database_connection, require_dashboard, require_ui_write))
app.include_router(inbox.build_router(database_connection, require_dashboard, require_ui_write))
app.include_router(audit_view.build_router(database_connection, require_dashboard))  # HLA_AUDIT_V1
app.include_router(fact_recheck.build_router(database_connection, require_dashboard, require_ui_write))
app.include_router(costs.build_router(database_connection, require_dashboard))
app.include_router(chief.build_router(database_connection, require_dashboard, require_ui_write))  # HLA_CHIEF_V1
app.include_router(voice.build_router(require_dashboard, require_ui_write))  # HLA_VOICE_V1
app.include_router(models.build_router(database_connection, require_dashboard, require_ui_write,
                                       lambda request: approvals.approver_status(request)['actor'] or 'dashboard_session'))
app.include_router(connections.build_router(require_dashboard, require_ui_write, approvals.require_approver,  # HLA_CONNECTIONS_V1
                                            approvals.approver_status))
# HLA_MODEL_REGISTRY_V1: /ui/api/models (registry, catalog, default, test); changes to what agents run need an approver.
app.include_router(model_registry.build_router(database_connection, require_dashboard, require_ui_write,
                                               approvals.require_approver, approvals.approver_status))
app.include_router(mcp_admin.build_router(require_dashboard, require_ui_write))  # HLA_MCP_V1
# HLA_ENGINES_V1: /ui/api/subscriptions (sign the Claude Code / Codex CLIs in with the owner's plan).
app.include_router(subscriptions.build_router(require_dashboard, require_ui_write, approvals.require_approver))
app.include_router(websites.build_router(database_connection, require_dashboard, require_ui_write))  # HLA_WEBSITES_V1
app.include_router(public_routes.build_router(require_dashboard, require_ui_write))  # HLA_ROUTES_V1
app.include_router(proxmox_admin.build_router(require_dashboard, require_ui_write))  # HLA_PROXMOX_V1
app.include_router(attachments.build_router(database_connection, require_dashboard, require_ui_write))  # HLA_ATTACHMENTS_V1
app.include_router(omada_admin.build_router(require_dashboard, require_ui_write))  # HLA_OMADA_V1
app.include_router(schedules.build_router(database_connection, require_dashboard, require_ui_write, launch_thread_run, start_thread_run))
app.include_router(build_threads_router(database_connection, require_dashboard, require_ui_write, execute_task, Task, task_run_params,
                                       token_cap=clamp_tokens))

@app.get('/ui')
@app.get('/ui/')
def dashboard():
    return FileResponse(UI_DIR / 'index.html')

@app.get('/ui/app.js')
def ui_script():
    return FileResponse(UI_DIR / 'app.js', media_type='application/javascript')

@app.get('/ui/markdown.js')
def ui_markdown_script():
    return FileResponse(UI_DIR / 'markdown.js', media_type='application/javascript')

@app.get('/ui/style.css')
def ui_style():
    return FileResponse(UI_DIR / 'style.css', media_type='text/css')

# Installable app (PWA): manifest, service worker and icons. Public like the other UI files; they hold no data.
UI_ICONS = {'icon-192.png': 'image/png', 'icon-512.png': 'image/png', 'maskable-512.png': 'image/png',
            'apple-touch-icon.png': 'image/png', 'icon.svg': 'image/svg+xml'}

@app.get('/ui/manifest.webmanifest')
def ui_manifest():
    return FileResponse(UI_DIR / 'manifest.webmanifest', media_type='application/manifest+json')

@app.get('/ui/sw.js')
def ui_service_worker():
    return FileResponse(UI_DIR / 'sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})

@app.get('/ui/icons/{name}')
def ui_icon(name: str):
    if name not in UI_ICONS:
        raise HTTPException(404, 'Not found')
    return FileResponse(UI_DIR / 'icons' / name, media_type=UI_ICONS[name], headers={'Cache-Control': 'public, max-age=86400'})

# HLA_UI_V3: the UI font (Inter, SIL Open Font License, bundled: the app works offline and loads nothing from a CDN).
UI_FONTS = {'InterVariable.woff2': 'font/woff2', 'Inter-LICENSE.txt': 'text/plain; charset=utf-8'}

@app.get('/ui/fonts/{name}')
def ui_font(name: str):
    if name not in UI_FONTS:
        raise HTTPException(404, 'Not found')
    return FileResponse(UI_DIR / 'fonts' / name, media_type=UI_FONTS[name], headers={'Cache-Control': 'public, max-age=2592000'})

@app.post('/ui/api/login')
def dashboard_login(body: LoginPayload, response: Response):
    if not secrets.compare_digest(body.password, required_env('DASHBOARD_PASSWORD')):
        raise HTTPException(401, 'Invalid password')
    payload = base64.urlsafe_b64encode(json.dumps({'v': 1, 'exp': int(time.time() + SESSION_MAX_AGE)}, separators=(',', ':')).encode()).decode().rstrip('=')
    response.set_cookie('agent_session', payload + '.' + _sign(payload), httponly=True, samesite='strict', secure=False, path='/', max_age=SESSION_MAX_AGE)
    return {'ok': True}

@app.post('/ui/api/logout')
def dashboard_logout(request: Request, response: Response):
    require_ui_write(request)
    response.delete_cookie('agent_session', path='/')
    return {'ok': True}

@app.get('/ui/api/session')
def dashboard_session(request: Request):
    if not _valid_session(request.cookies.get('agent_session')):
        return {'authenticated': False}
    status = approvals.approver_status(request)
    # Shown in the UI so a missing Traefik/Authelia header setup is visible rather than silent.
    return {'authenticated': True, 'approver': status, 'max_output_tokens': hla_token_cap(),
            'client_ip': request.client.host if request.client else None}

@app.get('/ui/api/summary')
def dashboard_summary(request: Request):
    require_dashboard(request)
    with database_connection() as db:
        return db.execute('''
            SELECT count(*)::int AS tasks,
                   count(*) FILTER (WHERE status = 'completed')::int AS completed,
                   count(*) FILTER (WHERE status = 'failed')::int AS failed,
                   count(*) FILTER (WHERE status = 'running')::int AS running,
                   coalesce(sum(openrouter_cost_usd), 0) AS openrouter_cost_usd,
                   coalesce(sum(input_tokens), 0)::bigint AS input_tokens,
                   coalesce(sum(output_tokens), 0)::bigint AS output_tokens,
                   coalesce(sum(typesafe_input_tokens), 0)::bigint AS typesafe_input_tokens,
                   coalesce(sum(typesafe_output_tokens), 0)::bigint AS typesafe_output_tokens
            FROM tasks
        ''').fetchone()

# HLA_UI_V3: filtered, paginated history. Conversation runs are summarised by their latest user message.
TASK_STATUSES = {'running','completed','incomplete','failed','human_approval_required','rejected','expired','cancelled'}

# HLA_DISPLAY_TITLE_V1: a short, human title for a run, derived from its stored prompt (which stays unchanged) and
# what it belongs to: the latest user message of a conversation, the project task's title, "Continued: ..." for an
# automatic continuation, "Plan: <project>" for a planning run, else the first meaningful line of the request.
_LATEST_USER = re.compile(r'LATEST USER MESSAGE[^\n:]*:\s*\n?(.*)$', re.S)
_PROMPT_BOILERPLATE = ('this is a follow-up in an existing agent conversation', 'task instructions (still in force)',
                       'previous conversation:', 'summary of the earlier conversation')


def _title_cut(text, n=80):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


# HLA_UI_V5: polite fillers ("can you", "please") go; the first letter is a capital; a question keeps its mark.
_FILLER = re.compile(r"^(?:(?:hey|hi|ok|okay|chief)\b[,!]?\s+)*(?:(?:can|could|would|will) you(?: please)?|please)\s+", re.I)


def _tidy(text):
    t = str(text or '').strip()
    stripped = _FILLER.sub('', t)
    if stripped != t:
        t = stripped.rstrip(' ?')
    t = t.rstrip(' .!') or t
    return t[:1].upper() + t[1:]


def display_context(thread_title=None, project_title=None, title=None):
    """Where a run belongs, shown next to its title: the project, else the conversation (when it adds something)."""
    if project_title:
        return _title_cut(project_title, 60)
    if thread_title and thread_title.strip().lower() not in (title or '').lower():
        return _title_cut(thread_title, 60)
    return None


def display_title(task_text, thread_title=None, project_task_title=None):
    text = task_text or ''
    message = None
    m = _LATEST_USER.search(text)
    if m:
        message = m.group(1).split('\n\nVOICE REPLY', 1)[0].strip()
    continued = bool(message and message.startswith('Continue from your CHECKPOINT'))
    if project_task_title and (continued or not message or 'YOUR TASK (' in text[:4000]):
        return ('Continued: ' if continued else '') + _title_cut(project_task_title)
    if continued:
        return 'Continued: ' + _title_cut(thread_title or 'conversation', 70)
    if message:
        first = next((line for line in message.splitlines() if line.strip()), message)
        return _title_cut(_tidy(first))
    if text.startswith('Plan project: '):
        return 'Plan: ' + _title_cut(text[len('Plan project: '):].splitlines()[0], 74)
    for line in text.splitlines():
        line = line.strip().lstrip('#').strip()
        if line and not line.lower().startswith(_PROMPT_BOILERPLATE):
            return _title_cut(_tidy(line))
    return _title_cut(thread_title) if thread_title else 'Run'


_TITLE_JOIN = """LEFT JOIN LATERAL (SELECT th.title AS thread_title, pt.title AS project_task_title, pj.title AS project_title
                FROM agent_threads th
                LEFT JOIN project_tasks pt ON pt.thread_id = th.id
                LEFT JOIN projects pj ON pj.id = coalesce(pt.project_id, th.project_id)
                WHERE th.id = coalesce((SELECT m.thread_id FROM agent_thread_messages m WHERE m.task_id = t.id LIMIT 1),
                                       (SELECT x.id FROM agent_threads x WHERE x.current_task_id = t.id LIMIT 1))
                LIMIT 1) ttl ON TRUE"""


def _full_task_text(task_id):
    with database_connection() as db:
        row = db.execute('SELECT task_text FROM tasks WHERE id=%s', (task_id,)).fetchone()
    return row and row['task_text']


@app.get('/ui/api/tasks')
def dashboard_tasks(request: Request, limit: int = 50, offset: int = 0, status: str = '', workspace: str = '', q: str = ''):
    require_dashboard(request)
    if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
        raise HTTPException(422, 'limit must be 1..100 and offset 0..100000')
    where, args = [], []
    statuses = [x for x in status.split(',') if x]
    if statuses:
        if not set(statuses) <= TASK_STATUSES: raise HTTPException(422, 'Unknown status filter')
        where.append('t.status = ANY(%s)'); args.append(statuses)
    if workspace:
        if workspace not in ('home','work'): raise HTTPException(422, 'workspace must be home or work')
        where.append('t.workspace = %s'); args.append(workspace)
    if q.strip():
        term = q.strip()[:200].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where.append('t.task_text ILIKE %s'); args.append('%' + term + '%')
    with database_connection() as db:
        approvals.expire_stale(db)
        # HLA_PERF_V1: pick the page first (index on created_at), then build summaries for those rows only.
        rows = db.execute(f"""SELECT t.id, t.created_at, t.finished_at, t.status, t.worker_model, t.openrouter_cost_usd,
                t.agent_id, a.name AS agent_name, t.workspace, t.risk_probability, t.error_message,
                left(btrim(coalesce(substring(t.task_text from 'LATEST USER MESSAGE:\\s*(.*)$'), t.task_text)), 300) AS summary,
                left(t.task_text, 8000) AS title_source, ttl.thread_title, ttl.project_task_title, ttl.project_title
            FROM (SELECT * FROM tasks t {'WHERE ' + ' AND '.join(where) if where else ''}
                  ORDER BY t.created_at DESC LIMIT %s OFFSET %s) t
            LEFT JOIN agents a ON a.id = t.agent_id
            {_TITLE_JOIN}
            ORDER BY t.created_at DESC""", [*args, limit + 1, offset]).fetchall()
    for row in rows:
        source = row.pop('title_source')
        if source and len(source) >= 8000 and 'LATEST USER MESSAGE' not in source:
            source = _full_task_text(row['id'])  # the latest message sits past the first 8000 characters
        thread_title = row.pop('thread_title')
        row['display_title'] = display_title(source, thread_title, row.pop('project_task_title'))
        row['display_context'] = display_context(thread_title, row.pop('project_title'), row['display_title'])
    return {'tasks': rows[:limit], 'has_more': len(rows) > limit}

# HLA_TASK_APPROVAL_V1: a deliberate, one-time dashboard decision.
@app.post('/ui/api/tasks/{task_id}/approval')
async def hla_task_approval(request: Request, task_id: uuid.UUID):
    require_ui_write(request)
    body=await request.json()
    action=body.get('action') if isinstance(body,dict) else None
    # HLA_READ_ONLY_APPROVAL_V1: approve_read_only runs the request with diagnostics-only tools.
    if action not in ('approve','approve_read_only','reject'):
        raise HTTPException(422, 'action must be approve, approve_read_only or reject')
    actor=approvals.require_approver(request)
    reason=body.get('reason')
    if reason is not None and not isinstance(reason,str):
        raise HTTPException(422, 'reason must be text')
    reason=(reason or '').strip()[:500] or None
    scope=None if action=='reject' else ('read_only' if action=='approve_read_only' else 'full')
    new_status='rejected' if action=='reject' else 'running'
    with database_connection() as db:
        approvals.expire_stale(db)
        # Atomic compare-and-set prevents two approvals from triggering two executions.
        row=db.execute("""UPDATE tasks SET status=%s, finished_at=NULL, error_message=NULL
            WHERE id=%s AND status='human_approval_required'
            RETURNING id,task_text,workspace,agent_id,run_params""",(new_status,task_id)).fetchone()
        if not row:
            current=db.execute('SELECT status FROM tasks WHERE id=%s',(task_id,)).fetchone()
            if current and current['status']=='expired':
                raise HTTPException(409,'This approval request has expired; send the request again')
            raise HTTPException(409,'Task is no longer awaiting approval (or does not exist)')
        approvals.record(db,task_id,'rejected' if action=='reject' else 'approved',actor,reason=reason,scope=scope)
        if action=='reject':
            db.execute('UPDATE tasks SET finished_at=NOW(), error_message=%s WHERE id=%s',
                       ('Rejected by '+actor+(': '+reason if reason else ''),task_id))
        db.commit()
    if action=='reject':
        post_task_outcome(database_connection,task_id,reason=reason)
        return {'id':str(task_id),'status':'rejected','actor':actor}
    # HLA_THREAD_APPROVAL_V1: re-run with the submitted settings; tasks from before run_params keep the old defaults.
    params=row['run_params'] or {}
    # The risk review hold is bypassed ONLY for this explicitly approved task ID.
    async def resume():
        claimed=False
        try:
            claimed=claim_thread_for_task(database_connection,task_id)
            resume_task=Task(task=row['task_text'],workspace=row['workspace'] or 'home',agent_id=row['agent_id'],
                max_output_tokens=clamp_tokens(params.get('max_output_tokens')),
                model_override=params.get('model_override'),reasoning_effort=params.get('reasoning_effort'))
            await execute_task(resume_task,'Bearer '+required_env('AGENT_PLATFORM_TOKEN'),
                stream_mode=True,task_id_override=task_id,approved_risk=True,read_only=scope=='read_only')
        except asyncio.CancelledError:
            runs.mark_cancelled(database_connection,task_id)
            raise
        except Exception as exc:
            with database_connection() as db:
                db.execute("""UPDATE tasks SET status='failed',finished_at=NOW(),
                    error_message=%s WHERE id=%s AND status='running'""",
                    ('Approved task execution failed: '+type(exc).__name__,task_id))
                db.commit()
        finally:
            try:
                with database_connection() as db:
                    final=db.execute('SELECT status FROM tasks WHERE id=%s',(task_id,)).fetchone()['status']
                    if final in ('completed','incomplete','failed','cancelled'):
                        approvals.record(db,task_id,final,'system',scope=scope)
                    db.commit()
                post_task_outcome(database_connection,task_id,release=claimed)
            except Exception:
                pass  # The task row already holds the outcome; the audit/conversation copies are best effort.
    runs.spawn(resume(),key=task_id)
    return {'id':str(task_id),'status':'running','actor':actor,'scope':scope}

@app.get('/ui/api/tasks/{task_id}')
def dashboard_task(request: Request, task_id: uuid.UUID):
    require_dashboard(request)
    with database_connection() as db:
        approvals.expire_stale(db)
    task = get_task(task_id, 'Bearer ' + required_env('AGENT_PLATFORM_TOKEN'))
    with database_connection() as db:
        task['approval_events'] = approvals.events(db, task_id)
        ttl = db.execute(f"""SELECT ttl.thread_title, ttl.project_task_title, ttl.project_title FROM tasks t {_TITLE_JOIN} WHERE t.id=%s""",
                         (task_id,)).fetchone() or {}
        source = (db.execute('SELECT task_text FROM tasks WHERE id=%s', (task_id,)).fetchone() or {}).get('task_text')
    task['display_title'] = display_title(source, ttl.get('thread_title'), ttl.get('project_task_title'))
    task['display_context'] = display_context(ttl.get('thread_title'), ttl.get('project_title'), task['display_title'])
    return task

@app.post('/ui/api/tasks')
async def dashboard_create_task(request: Request, task: Task):
    require_ui_write(request)
    return await run_task(task, 'Bearer ' + required_env('AGENT_PLATFORM_TOKEN'))

@app.post('/ui/api/tasks/start-stream')
async def dashboard_start_stream(request: Request, task: Task):
    require_ui_write(request)
    task_id=uuid.uuid4()
    with database_connection() as db:
        files=attachments.check_staged(db,task.attachment_ids)
        db.execute('INSERT INTO tasks (id,task_text,status,agent_id,workspace,run_params) VALUES (%s,%s,%s,%s,%s,%s)',
                   (task_id,task.task,'running',task.agent_id,task.workspace,task_run_params(task)))
        attachments.link(db,[f['id'] for f in files],task_id=task_id)
        db.commit()
    async def execute():
        try:
            await execute_task(task,'Bearer '+required_env('AGENT_PLATFORM_TOKEN'),stream_mode=True,task_id_override=task_id)
        except asyncio.CancelledError:
            runs.mark_cancelled(database_connection,task_id)
            raise
        except Exception as exc:
            # Never include upstream exception text (may contain credentials).
            with database_connection() as db:
                db.execute("UPDATE tasks SET status='failed',error_message=%s,finished_at=NOW() WHERE id=%s AND status='running'",
                           ('Streaming execution failed: '+type(exc).__name__,task_id))
                db.commit()
    runs.spawn(execute(),key=task_id)
    return {'id':str(task_id),'status':'running'}

# HLA_CHAT_APPROVAL_V1: approve (run once) or reject the shell command an agent asked to run in a conversation.
@app.post('/ui/api/tasks/{task_id}/command')
async def dashboard_command_decision(request: Request, task_id: uuid.UUID):
    require_ui_write(request)
    body = await request.json()
    action = body.get('action') if isinstance(body, dict) else None
    if action not in ('approve', 'reject'):
        raise HTTPException(422, 'action must be approve or reject')
    actor = approvals.require_approver(request)
    if action == 'approve' and len(os.getenv('POLICY_ADMIN_TOKEN', '')) < 32:
        raise HTTPException(503, 'Set POLICY_ADMIN_TOKEN in .env and .env.gateway to approve commands from the dashboard')
    with database_connection() as db:
        approvals.expire_stale(db)
        thread_id = command_thread(db, task_id)
        # The thread is locked first: the agent's follow-up run needs it, and Stop must reach the command.
        if action == 'approve' and not (thread_id and db.execute('''UPDATE agent_threads SET status='running',current_task_id=%s,
                updated_at=NOW() WHERE id=%s AND status='idle' RETURNING id''', (task_id, thread_id)).fetchone()):
            raise HTTPException(409, 'Wait until the agent has finished (or stop it), then approve')
        decision = {'status': 'running' if action == 'approve' else 'rejected', 'decided_by': actor,
                    'decided_at': datetime.now(timezone.utc).isoformat()}
        # Atomic compare-and-set: a double click or a second approver cannot run the command twice.
        row = db.execute("""UPDATE tasks SET pending_command=pending_command||%s
            WHERE id=%s AND pending_command->>'status'='pending' RETURNING agent_id,pending_command""",
            (Jsonb(decision), task_id)).fetchone()
        if not row:
            db.rollback()
            current = db.execute('SELECT pending_command FROM tasks WHERE id=%s', (task_id,)).fetchone()
            if current and (current['pending_command'] or {}).get('status') == 'expired':
                raise HTTPException(409, 'This command waited too long; ask the agent again if it is still needed')
            raise HTTPException(409, 'No command is waiting for approval here')
        approvals.record(db, task_id, 'approved' if action == 'approve' else 'rejected', actor, scope='command')
        db.commit()
    if action == 'approve':
        runs.spawn(run_approved_command(task_id, thread_id, row['agent_id'], row['pending_command'], actor), key=task_id)
    return {'id': str(task_id), 'status': decision['status'], 'actor': actor}

# HLA_RUNS_V1: stop a background run started by this API process.
@app.post('/ui/api/tasks/{task_id}/cancel')
async def dashboard_cancel_task(request: Request, task_id: uuid.UUID):
    require_ui_write(request)
    if not runs.cancel(task_id):
        raise HTTPException(409, 'This task is not running')
    return {'id': str(task_id), 'status': 'cancelling'}

@app.get('/ui/api/tasks/{task_id}/events')
def dashboard_task_events(request: Request,task_id:uuid.UUID):
    require_dashboard(request)
    return event_response(database_connection,task_id)

# Dashboard wrappers keep the bearer secret server-side.
# HLA_FACTS_V1: recorded facts with provenance, freshness and the previous value, for review and deletion.
FACT_STATES = {'fresh': "confidence='verified' AND (expires_at IS NULL OR expires_at>NOW())",
               'stale': "confidence='verified' AND expires_at<=NOW()", 'unverified': "confidence='unverified'"}

@app.get('/ui/api/facts')
def dashboard_facts(request: Request, workspace: str = 'home', q: str = '', state: str = '', limit: int = 50, offset: int = 0):
    require_dashboard(request)
    if workspace not in ('home', 'work'): raise HTTPException(422, 'Invalid workspace')
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    where, args = ["m.workspace=%s", "m.kind IS DISTINCT FROM 'task'"], [workspace]
    if state in FACT_STATES: where.append(FACT_STATES[state])
    if q.strip():
        where.append('(m.key ILIKE %s OR m.fact ILIKE %s)'); args += ['%' + q.strip()[:160] + '%'] * 2
    with database_connection() as db:
        rows = db.execute('''SELECT m.id,m.key,m.fact,m.source,m.confidence,m.kind,m.server,m.task_id,m.verified_at,m.expires_at,
                m.updated_at,m.previous_fact,m.previous_verified_at,m.rechecked_at,m.recheck_outcome,a.name AS agent_name
            FROM agent_memories m LEFT JOIN agents a ON a.id=m.agent_id WHERE ''' + ' AND '.join(where) +
            ' ORDER BY m.updated_at DESC LIMIT %s OFFSET %s', [*args, limit + 1, offset]).fetchall()
        counts = db.execute('SELECT ' + ', '.join(f'count(*) FILTER (WHERE {cond}) AS {name}' for name, cond in FACT_STATES.items())
                            + " FROM agent_memories WHERE workspace=%s AND kind IS DISTINCT FROM 'task'", (workspace,)).fetchone()
    for row in rows:
        row['freshness'] = memory_freshness(row)
    return {'facts': rows[:limit], 'has_more': len(rows) > limit, 'counts': counts}

@app.delete('/ui/api/facts/{fact_id}')
def dashboard_delete_fact(request: Request, fact_id: uuid.UUID):
    require_ui_write(request)
    with database_connection() as db:
        deleted = db.execute('DELETE FROM agent_memories WHERE id=%s RETURNING id', (fact_id,)).fetchone()
        db.commit()
    if not deleted: raise HTTPException(404, 'Fact not found')
    return {'deleted': str(fact_id)}

@app.get('/ui/api/knowledge')
def dashboard_knowledge(request: Request, workspace: str = 'home', q: str = '', project: str = ''):
    require_dashboard(request)
    return list_knowledge('Bearer '+required_env('AGENT_PLATFORM_TOKEN'), workspace, q, project)

@app.post('/ui/api/knowledge')
def dashboard_add_knowledge(request: Request, entry: KnowledgeEntry):
    require_ui_write(request)
    return create_knowledge(entry, 'Bearer '+required_env('AGENT_PLATFORM_TOKEN'), approvals.approver_status(request)['actor'])

@app.put('/ui/api/knowledge/{entry_id}')
def dashboard_edit_knowledge(request: Request, entry_id: uuid.UUID, entry: KnowledgeEntry):
    require_ui_write(request)
    return edit_knowledge(entry_id, entry, 'Bearer '+required_env('AGENT_PLATFORM_TOKEN'), approvals.approver_status(request)['actor'])

@app.delete('/ui/api/knowledge/{entry_id}')
def dashboard_delete_knowledge(request: Request, entry_id: uuid.UUID):
    require_ui_write(request)
    return delete_knowledge(entry_id, 'Bearer '+required_env('AGENT_PLATFORM_TOKEN'))


# Authenticated dashboard API wrappers. The bearer token never leaves the server.
@app.get('/ui/api/agents')
def dashboard_agents(request: Request, workspace: str | None = None):
    require_dashboard(request)
    return list_agents('Bearer '+required_env('AGENT_PLATFORM_TOKEN'),workspace)

@app.post('/ui/api/agents')
def dashboard_create_agent(request: Request, agent: AgentPayload):
    require_ui_write(request)
    return create_agent(agent,'Bearer '+required_env('AGENT_PLATFORM_TOKEN'))

@app.put('/ui/api/agents/{agent_id}')
def dashboard_update_agent(request: Request, agent_id: uuid.UUID, agent: AgentPayload):
    require_ui_write(request)
    return edit_agent(agent_id,agent,'Bearer '+required_env('AGENT_PLATFORM_TOKEN'))

@app.delete('/ui/api/agents/{agent_id}')
def dashboard_remove_agent(request: Request, agent_id: uuid.UUID):
    require_ui_write(request)
    return delete_agent(agent_id,'Bearer '+required_env('AGENT_PLATFORM_TOKEN'))


# Phase 6A: durable queue. Jobs run through the existing guarded text-only /tasks API.
# Only the worker may claim jobs; UI/API clients can enqueue, inspect, or cancel pending jobs.
class QueuePayload(BaseModel):
    task: str = Field(min_length=3, max_length=12000)
    workspace: str = Field(default='home', pattern='^(home|work)$')
    agent_id: uuid.UUID | None = None
    max_output_tokens: int = Field(default=1500, ge=100, le=32768)
    reasoning_effort: str = Field(default='low', pattern='^(none|minimal|low|medium|high)$')


def queue_add(body: QueuePayload):
    if body.agent_id:
        with database_connection() as db:
            a=db.execute('SELECT workspace,enabled FROM agents WHERE id=%s',(body.agent_id,)).fetchone()
        if not a: raise HTTPException(404,'Agent not found')
        if not a['enabled'] or a['workspace'] != body.workspace:
            raise HTTPException(403,'Agent disabled or workspace mismatch')
    uid=uuid.uuid4()
    with database_connection() as db:
        db.execute("""INSERT INTO queued_jobs(id,task_text,workspace,agent_id,max_output_tokens,reasoning_effort)
                      VALUES (%s,%s,%s,%s,%s,%s)""",
                   (uid,body.task,body.workspace,body.agent_id,body.max_output_tokens,body.reasoning_effort))
        db.commit()
    return {'id':uid,'status':'queued'}


def queue_list(limit: int=50):
    if not 1<=limit<=100: raise HTTPException(422,'limit must be 1..100')
    with database_connection() as db:
        return {'jobs':db.execute("""SELECT id,created_at,started_at,finished_at,task_text,workspace,
             agent_id,max_output_tokens,reasoning_effort,status,api_task_id,result_text,error_message
             FROM queued_jobs ORDER BY created_at DESC LIMIT %s""",(limit,)).fetchall()}


def queue_cancel(job_id: uuid.UUID):
    with database_connection() as db:
        r=db.execute("""UPDATE queued_jobs SET status='cancelled',finished_at=NOW()
             WHERE id=%s AND status='queued' RETURNING id""",(job_id,)).fetchone()
        db.commit()
    if not r: raise HTTPException(409,'Only queued jobs can be cancelled')
    return {'id':job_id,'status':'cancelled'}

@app.post('/queue')
def api_queue_add(body: QueuePayload, authorization: str | None=Header(default=None)):
    authorize(authorization)
    return queue_add(body)

@app.get('/queue')
def api_queue_list(limit:int=50, authorization:str|None=Header(default=None)):
    authorize(authorization)
    return queue_list(limit)

@app.post('/queue/{job_id}/cancel')
def api_queue_cancel(job_id:uuid.UUID, authorization:str|None=Header(default=None)):
    authorize(authorization)
    return queue_cancel(job_id)

@app.post('/ui/api/queue')
def ui_queue_add(body:QueuePayload,request:Request):
    require_ui_write(request)
    return queue_add(body)

@app.get('/ui/api/queue')
def ui_queue_list(request:Request,limit:int=50):
    require_dashboard(request)
    return queue_list(limit)

@app.post('/ui/api/queue/{job_id}/cancel')
def ui_queue_cancel(job_id:uuid.UUID,request:Request):
    require_ui_write(request)
    return queue_cancel(job_id)


# Phase 6B: a narrow, read-only Proxmox inventory integration.
# No arbitrary URL, method, path, or shell commands can be supplied by a user or agent.
def proxmox_inventory():
    base = os.getenv('PROXMOX_API_URL', '').strip().rstrip('/')
    token_id = os.getenv('PROXMOX_TOKEN_ID', '').strip()
    token_secret = os.getenv('PROXMOX_TOKEN_SECRET', '').strip()
    if not base or not token_id or not token_secret:
        raise HTTPException(503, 'Proxmox integration not configured')
    url = urlsplit(base)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise HTTPException(503, 'Invalid Proxmox API URL: use https://HOST:8006 only')
    # TLS certificate validation is intentionally always enabled.
    headers = {'Authorization': 'PVEAPIToken=' + token_id + '=' + token_secret}
    try:
        with httpx.Client(timeout=12, follow_redirects=False, verify="/app/proxmox-ca.pem", trust_env=False) as client:
            responses = {}
            for name, path in (('nodes', '/api2/json/nodes'), ('resources', '/api2/json/cluster/resources?type=vm')):
                response = client.get(base + path, headers=headers)
                response.raise_for_status()
                data = response.json().get('data', [])
                if not isinstance(data, list):
                    raise ValueError('Unexpected Proxmox response')
                responses[name] = data[:500]
    except (httpx.HTTPError, ValueError):
        # Never leak token, URL, or upstream response bodies in UI or logs.
        raise HTTPException(502, 'Proxmox request failed. Check URL, TLS trust, token permissions, and reachability.') from None
    nodes = [{k: r.get(k) for k in ('node', 'status', 'cpu', 'maxcpu', 'mem', 'maxmem')}
             for r in responses['nodes']]
    guests = [{k: r.get(k) for k in ('vmid', 'name', 'node', 'type', 'status', 'cpu', 'mem', 'maxmem', 'maxdisk')}
              for r in responses['resources'] if r.get('type') in ('qemu','lxc')]
    return {'nodes': nodes, 'guests': guests, 'source': 'Proxmox API (read-only)',
            'collected_at': datetime.now(timezone.utc).isoformat()}


@app.get('/ui/api/infrastructure/proxmox')
def dashboard_proxmox_inventory(request: Request):
    require_dashboard(request)
    return proxmox_inventory()


@app.get('/infrastructure/proxmox')
def api_proxmox_inventory(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return proxmox_inventory()


# HLA_POLICY_V1: the policy lives in the SSH gateway, which enforces it; the dashboard reads and edits it here.
POLICY_URL = 'http://ssh-gateway-api:8099/v1/policy'

async def seed_policy(client):
    """Give the pre-policy SSH agent its existing powers (level 5) once, so a deploy does not remove its tools."""
    agent, admin = os.getenv('SSH_TOOL_AGENT_ID', ''), os.getenv('POLICY_ADMIN_TOKEN', '')
    if not agent or not admin or os.getenv('SSH_TOOL_ENABLED', 'false').lower() != 'true':
        return
    current = await client.get(POLICY_URL, headers={'Authorization': 'Bearer ' + required_env('TOOL_GATEWAY_TOKEN')}, timeout=5)
    current.raise_for_status()
    if current.json().get('agents'):
        return
    with database_connection() as db:
        row = db.execute('SELECT name FROM agents WHERE id=%s', (agent,)).fetchone()
    await client.put(POLICY_URL, headers={'Authorization': 'Bearer ' + admin}, timeout=5,
                     json={'actor': 'migration', 'change': {'agents': {agent: {'name': row['name'] if row else '', 'level': 5}}}})

@app.get('/ui/api/policy')
async def dashboard_policy(request: Request):
    require_dashboard(request)
    async with httpx.AsyncClient() as client:
        try:
            await seed_policy(client)
        except (httpx.HTTPError, HTTPException, ValueError):
            pass  # seeding is best effort; the read below reports gateway problems
        try:
            response = await client.get(POLICY_URL, headers={'Authorization': 'Bearer ' + required_env('TOOL_GATEWAY_TOKEN')}, timeout=8)
            response.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable; policy cannot be shown') from None
    with database_connection() as db:
        agents = db.execute('SELECT id,name,workspace,enabled FROM agents ORDER BY name').fetchall()
    return {'policy': response.json(), 'agents': agents, 'editable': bool(os.getenv('POLICY_ADMIN_TOKEN')),
            'approver': approvals.approver_status(request), 'ssh_tools_enabled': os.getenv('SSH_TOOL_ENABLED', 'false').lower() == 'true'}

@app.put('/ui/api/policy')
async def dashboard_update_policy(request: Request):
    require_ui_write(request)
    actor = approvals.require_approver(request)
    admin = os.getenv('POLICY_ADMIN_TOKEN', '')
    if not admin:
        raise HTTPException(503, 'Set POLICY_ADMIN_TOKEN in .env and .env.gateway to edit policy from the dashboard')
    body = await request.json()
    change = body.get('change') if isinstance(body, dict) else None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(POLICY_URL, headers={'Authorization': 'Bearer ' + admin}, timeout=8,
                                        json={'change': change, 'actor': actor})
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable') from None
    if response.status_code == 400:
        raise HTTPException(422, response.json().get('detail', 'Invalid policy change'))
    if response.status_code != 200:
        raise HTTPException(502, f'Gateway refused the change (HTTP {response.status_code})')
    return response.json()


# HLA_DEPLOY_V1: deployment history and human-started deploys/rollbacks (staging and rollback run in the gateway).
GATEWAY_URL = 'http://ssh-gateway-api:8099'

@app.get('/ui/api/deployments')
async def dashboard_deployments(request: Request):
    require_dashboard(request)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(GATEWAY_URL + '/v1/deployments', timeout=8,
                                        headers={'Authorization': 'Bearer ' + required_env('TOOL_GATEWAY_TOKEN')})
            response.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(502, 'SSH gateway unreachable; deployments cannot be shown') from None
    return response.json()

@app.post('/ui/api/deployments')
async def dashboard_deploy(request: Request):
    """Deploy {app, ref} (staged first) or roll back {app, sha} to a commit that ran before. Runs in the background;
    errors the gateway reports at once (bad ref, deploy already running) are returned directly."""
    require_ui_write(request)
    actor = approvals.require_approver(request)
    admin = os.getenv('POLICY_ADMIN_TOKEN', '')
    if len(admin) < 32:
        raise HTTPException(503, 'Set POLICY_ADMIN_TOKEN in .env and .env.gateway to deploy from the dashboard')
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get('app'), str):
        raise HTTPException(422, 'app is required')
    request_body = {'app': body['app'], 'actor': actor, **({'sha': body['sha']} if body.get('sha') else {'ref': body.get('ref')})}

    async def deploy():
        async with httpx.AsyncClient() as client:
            response = await client.post(GATEWAY_URL + '/v1/deploy', headers={'Authorization': 'Bearer ' + admin},
                                         json=request_body, timeout=660)
        reply = response.json()
        if response.status_code == 200:
            try:
                with database_connection() as db:
                    capture_tool(db, 'home', {'mode': 'deploy', 'app': body['app'], 'ref': reply.get('ref')}, reply)
                    db.commit()
            except Exception:
                pass  # the gateway's history already holds the outcome
        return response.status_code, reply

    run = runs.spawn(deploy())
    try:
        status, reply = await asyncio.wait_for(asyncio.shield(run), 2)
    except asyncio.TimeoutError:
        return {'status': 'started', 'app': body['app'], 'actor': actor}
    except httpx.HTTPError:
        raise HTTPException(502, 'SSH gateway unreachable') from None
    if status != 200:
        raise HTTPException(409 if status == 409 else 422, {'deploy_in_progress': 'A deployment of this app is already running',
            'unknown_commit': 'Rollback is only possible to a commit this app ran before', 'invalid_ref': 'Invalid branch, tag or commit',
            'staging_unavailable': 'Staging needs the sandbox (its server level is 0)', 'unknown_app': 'Unknown app'}.get(
            reply.get('error'), 'Gateway refused: ' + str(reply.get('error'))))
    return reply

# HLA_SANDBOX_V1: reset the disposable agent sandbox to its clean snapshot.
@app.get('/ui/api/sandbox')
def dashboard_sandbox(request: Request):
    require_dashboard(request)
    with database_connection() as db:
        last = db.execute('SELECT actor,started_at,finished_at,outcome FROM sandbox_resets ORDER BY started_at DESC LIMIT 1').fetchone()
    return {**sandbox.describe(), 'last_reset': last}

@app.post('/ui/api/sandbox/reset')
async def dashboard_reset_sandbox(request: Request):
    require_ui_write(request)
    actor = approvals.require_approver(request)
    reset_id = uuid.uuid4()
    with database_connection() as db:
        db.execute('INSERT INTO sandbox_resets(id,actor) VALUES (%s,%s)', (reset_id, actor))
        db.commit()
    try:
        result = await sandbox.reset()
        outcome = 'ok'
    except RuntimeError as exc:
        outcome = str(exc)[:300]
    except Exception as exc:  # never leave the reset row 'in progress'
        outcome = 'Reset failed: ' + type(exc).__name__
    with database_connection() as db:
        db.execute('UPDATE sandbox_resets SET finished_at=NOW(),outcome=%s WHERE id=%s', (outcome, reset_id))
        db.commit()
    if outcome != 'ok':
        raise HTTPException(409 if 'already running' in outcome else 502, outcome)
    return result
