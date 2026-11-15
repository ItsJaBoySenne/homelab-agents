"""Model routing per kind of work (HLA_MODEL_ROUTING_V2).

One function, `choose(db, kind, project, agent)`, picks the model for:
  plan      the project planner                 (HLA_MODEL_PLAN, per-project override)
  build     a project task's first run          (per-project override only)
  review    a project review task               (HLA_MODEL_REVIEW, per-project override)
  continue  a project task's checkpoint and automatic continuations (HLA_MODEL_CONTINUE)
  summary   a conversation's rolling summary    (HLA_SUMMARY_MODEL)
The environment gives the defaults; the dashboard (Costs page, "Models") stores changes in `model_settings` and
per-project overrides in `project_models`. Empty means the current behaviour: the agent's own model.
Every routed model must be enabled in the model registry (model_registry.py, Models page; AGENT_ALLOWED_MODELS
while the registry is empty): a stored or env value that is not is skipped. The one exception is HLA_SUMMARY_MODEL
from the environment, which was trusted before routing existed. The routing settings live on the Models page.
"""
import os
import uuid

import model_registry

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

KINDS = ('plan', 'review', 'continue', 'summary')        # global settings
PROJECT_KINDS = ('plan', 'build', 'review')              # per-project overrides
ENV = {'plan': 'HLA_MODEL_PLAN', 'review': 'HLA_MODEL_REVIEW', 'continue': 'HLA_MODEL_CONTINUE',
       'summary': 'HLA_SUMMARY_MODEL'}
LABELS = {'plan': 'Planning', 'build': 'Project tasks', 'review': 'Review tasks', 'continue': 'Continuations',
          'summary': 'Conversation summaries'}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS model_settings (
        kind TEXT PRIMARY KEY, model TEXT NOT NULL DEFAULT '', updated_by TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS project_models (
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE, kind TEXT NOT NULL, model TEXT NOT NULL,
        PRIMARY KEY (project_id, kind))''')


def allowed():
    """The models agents and conversations may use: enabled registry models (the same list as app.agent_model_allowed)."""
    return model_registry.enabled_ids()


def setting(db, kind):
    """(model, source) of a global setting: the dashboard's value ('' too: "use the agent's model"), else the env default."""
    row = db.execute('SELECT model FROM model_settings WHERE kind=%s', (kind,)).fetchone()
    if row:
        return row['model'], 'dashboard'
    value = os.getenv(ENV[kind], '').strip()
    return value, ('env' if value else '')


def _id(value):
    return (value or {}).get('id') if isinstance(value, dict) else value


def _project_override(db, project, kind):
    pid = _id(project)
    if not pid or kind not in PROJECT_KINDS:
        return ''
    row = db.execute('SELECT model FROM project_models WHERE project_id=%s AND kind=%s', (pid, kind)).fetchone()
    return (row or {}).get('model') or ''


def choose(db, kind, project=None, agent=None, *, fallback=True, base='build'):
    """The model for this kind of work. Order: per-project override, global setting (env default), then, with
    `fallback`, the agent's model (while it is enabled) and the registry's default model. For `continue`, `base` ('build' or 'review') is the kind of
    task being continued: without a continuation model it keeps that task's project override. Returns '' when
    nothing is routed and fallback is off."""
    allow = set(allowed())
    candidates = []  # (model, must be allowlisted)
    if kind == 'continue':
        model, _ = setting(db, 'continue')
        candidates += [(model, True), (_project_override(db, project, base), True)]
        if base == 'review':
            model, _ = setting(db, 'review')
            candidates.append((model, True))
    else:
        candidates.append((_project_override(db, project, kind), True))
        if kind in KINDS:
            model, source = setting(db, kind)
            candidates.append((model, not (kind == 'summary' and source == 'env')))
    for model, strict in candidates:
        if model and (not strict or model in allow):
            return model
    if not fallback:
        return ''
    if isinstance(agent, dict) and agent.get('model') in allow:
        return agent['model']
    if agent and not isinstance(agent, dict):
        row = db.execute('SELECT model FROM agents WHERE id=%s', (agent,)).fetchone()
        if row and row['model'] in allow:
            return row['model']
    return model_registry.default_model()[0]


def for_project_task(db, project, task):
    """Model override for a project task run started by the orchestrator, or None (the agent's own model)."""
    base = 'review' if task.get('review') else 'build'
    kind = 'continue' if task.get('retry_note') else base
    return choose(db, kind, project, fallback=False, base=base) or None


def for_continuation(db, thread):
    """Model override for an automatic continuation inside a project task's conversation, or None."""
    if not thread.get('project_id'):
        return None
    task = db.execute('SELECT review FROM project_tasks WHERE thread_id=%s LIMIT 1', (thread['id'],)).fetchone()
    return choose(db, 'continue', thread['project_id'], fallback=False,
                  base='review' if task and task['review'] else 'build') or None


# ---------------------------------------------------------------- API (Models page, "Models per kind of work")

class SettingIn(BaseModel):
    kind: str = Field(pattern='^(plan|build|review|continue|summary)$')
    model: str | None = Field(default=None, max_length=160)   # '' = the agent's model; null = back to the default


def build_router(connect, dashboard_read, dashboard_write, actor_of=lambda request: 'dashboard_session'):
    router = APIRouter(prefix='/ui/api/model-routing', tags=['models'])

    def check_model(model):
        if model and model not in allowed():
            raise HTTPException(422, 'Model is not enabled in the model registry (Models page)')

    @router.get('')
    def get_routing(request: Request):
        dashboard_read(request)
        with connect() as db:
            kinds = []
            for kind in KINDS:
                model, source = setting(db, kind)
                env = os.getenv(ENV[kind], '').strip()
                usable = not model or model in allowed() or (kind == 'summary' and source == 'env')
                kinds.append({'kind': kind, 'label': LABELS[kind], 'env': ENV[kind], 'env_value': env, 'model': model,
                              'source': source, 'usable': usable})
            rows = db.execute('SELECT project_id,kind,model FROM project_models').fetchall()
            projects = db.execute('''SELECT id,title,status,workspace FROM projects WHERE status<>'cancelled'
                ORDER BY updated_at DESC LIMIT 100''').fetchall()
        overrides = {}
        for r in rows:
            overrides.setdefault(str(r['project_id']), {})[r['kind']] = r['model']
        return {'kinds': kinds, 'project_kinds': [{'kind': k, 'label': LABELS[k]} for k in PROJECT_KINDS],
                'allowed': allowed(), 'worker_model': model_registry.default_model()[0],
                'projects': projects, 'overrides': overrides}

    @router.put('')
    def put_setting(body: SettingIn, request: Request):
        dashboard_write(request)
        if body.kind not in KINDS:
            raise HTTPException(422, 'Per-project only: ' + body.kind)
        check_model(body.model)
        with connect() as db:
            if body.model is None:
                db.execute('DELETE FROM model_settings WHERE kind=%s', (body.kind,))
            else:
                db.execute('''INSERT INTO model_settings(kind,model,updated_by) VALUES (%s,%s,%s)
                    ON CONFLICT (kind) DO UPDATE SET model=EXCLUDED.model,updated_by=EXCLUDED.updated_by,updated_at=NOW()''',
                           (body.kind, body.model, actor_of(request)))
            db.commit()
            model, source = setting(db, body.kind)
        return {'kind': body.kind, 'model': model, 'source': source}

    @router.put('/projects/{pid}')
    def put_project(pid: uuid.UUID, body: SettingIn, request: Request):
        dashboard_write(request)
        if body.kind not in PROJECT_KINDS:
            raise HTTPException(422, 'Not a per-project setting: ' + body.kind)
        check_model(body.model)
        with connect() as db:
            if not db.execute('SELECT 1 FROM projects WHERE id=%s', (pid,)).fetchone():
                raise HTTPException(404, 'Project not found')
            if body.model:
                db.execute('''INSERT INTO project_models(project_id,kind,model) VALUES (%s,%s,%s)
                    ON CONFLICT (project_id,kind) DO UPDATE SET model=EXCLUDED.model''', (pid, body.kind, body.model))
            else:
                db.execute('DELETE FROM project_models WHERE project_id=%s AND kind=%s', (pid, body.kind))
            db.commit()
        return {'project_id': pid, 'kind': body.kind, 'model': body.model or ''}

    return router
