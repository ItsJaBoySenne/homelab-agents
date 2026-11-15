"""Costs (HLA_COSTS_V1): where the OpenRouter money goes, and a warning before a project hits its budget.

`GET /ui/api/costs?days=30&workspace=` sums `tasks.openrouter_cost_usd` over the period (UTC days) per day, project,
agent, model and kind of work, lists the most expensive runs and every project's budget use. One pass over the
period's tasks (tasks_created_idx / tasks_workspace_created_idx), grouped in SQL, then folded here.

Kinds: planner (a project's planning call), project_task (runs in a project task's conversation), conversation
(other conversation runs, schedules included), one_shot (Overview / Queue / API tasks) and summary (the rolling
conversation summary, recorded in `tasks.summary_cost_usd` since this module; earlier summary costs stay in their run).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request

import providers
import push

WARN_SHARE = 0.8   # a project's budget warning fires once at 80% (per budget value: raising the budget re-arms it)
KIND_LABELS = {'conversation': 'Conversations', 'project_task': 'Project tasks', 'planner': 'Planning',
               'summary': 'Summaries', 'one_shot': 'One-shot tasks'}


def migrate(db):
    """After projects.migrate (it adds to projects)."""
    db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS summary_cost_usd NUMERIC(18,10)')
    db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS summary_model TEXT')
    # The budget value the 80% warning was sent for; a different budget (raised, new round) re-arms it.
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS budget_warned_usd NUMERIC(10,4)')


def budget_warning(db, project, spent):
    """Called by the orchestrator with a running project's spend: at 80% of its budget, log it and queue a push,
    once per budget value. Returns True when it warned. Runs in the caller's transaction."""
    budget = float(project['budget_usd'] or 0)
    if budget <= 0 or spent < WARN_SHARE * budget:
        return False
    if not db.execute('''UPDATE projects SET budget_warned_usd=budget_usd WHERE id=%s
            AND budget_warned_usd IS DISTINCT FROM budget_usd RETURNING id''', (project['id'],)).fetchone():
        return False
    pct = int(spent / budget * 100)
    import projects  # late: projects imports this module
    projects.log(db, project['id'], 'orchestrator', f'Budget warning: {pct}% used (${spent:.2f} of ${budget:.2f}). '
                 'It pauses at 100%; raise it with Limits… if it needs more.')
    push.queue(db, project['title'], f'Budget {pct}% used: ${spent:.2f} of ${budget:.2f}.', push.project_url(project['id']),
               f'project-{project["id"]}-budget')
    return True


_KIND_SQL = """CASE WHEN t.project_id IS NOT NULL AND t.agent_id IS NULL AND t.task_text LIKE 'Plan project: %%' THEN 'planner'
    WHEN t.project_id IS NOT NULL THEN 'project_task'
    WHEN EXISTS (SELECT 1 FROM agent_thread_messages m WHERE m.task_id=t.id) THEN 'conversation'
    ELSE 'one_shot' END"""


def report(db, days=30, workspace='', now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    since = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    ws = workspace or ''
    # A 90-day scan crosses Postgres' JIT threshold; compiling costs more than this small aggregate saves.
    db.execute('SET LOCAL jit = off')
    groups =db.execute(f'''SELECT (t.created_at AT TIME ZONE 'UTC')::date AS day, t.project_id, t.agent_id,
            COALESCE(t.worker_model,'') AS model, COALESCE(t.summary_model,'') AS summary_model, {_KIND_SQL} AS kind,
            count(*)::int AS runs, COALESCE(SUM(t.openrouter_cost_usd),0)::float AS cost,
            COALESCE(SUM(t.summary_cost_usd),0)::float AS summary_cost,
            count(*) FILTER (WHERE t.summary_cost_usd > 0)::int AS summaries,
            COALESCE(SUM(t.input_tokens),0)::bigint AS input_tokens, COALESCE(SUM(t.output_tokens),0)::bigint AS output_tokens,
            COALESCE(SUM(t.typesafe_input_tokens),0)::bigint AS ts_input, COALESCE(SUM(t.typesafe_output_tokens),0)::bigint AS ts_output
        FROM tasks t WHERE t.created_at >= %(since)s AND (%(ws)s = '' OR t.workspace = %(ws)s)
        GROUP BY 1,2,3,4,5,6''', {'since': since, 'ws': ws}).fetchall()

    totals = {'cost': 0.0, 'runs': 0, 'input_tokens': 0, 'output_tokens': 0, 'typesafe_input_tokens': 0, 'typesafe_output_tokens': 0,
              'subscription_runs': 0}
    daily = {first + timedelta(days=i): {'cost': 0.0, 'runs': 0} for i in range(days)}
    by = {'project': {}, 'agent': {}, 'model': {}, 'kind': {}}

    def add(table, key, cost, runs):
        entry = by[table].setdefault(key, {'cost': 0.0, 'runs': 0})
        entry['cost'] += cost
        entry['runs'] += runs

    for g in groups:
        totals['cost'] += g['cost']
        totals['runs'] += g['runs']
        if providers.is_subscription(g['model']):
            totals['subscription_runs'] += g['runs']
        totals['input_tokens'] += g['input_tokens']
        totals['output_tokens'] += g['output_tokens']
        totals['typesafe_input_tokens'] += g['ts_input']
        totals['typesafe_output_tokens'] += g['ts_output']
        if g['day'] in daily:
            daily[g['day']]['cost'] += g['cost']
            daily[g['day']]['runs'] += g['runs']
        add('project', g['project_id'], g['cost'], g['runs'])
        add('agent', g['agent_id'], g['cost'], g['runs'])
        # The summary call is part of its run's cost; per kind and model it counts on its own.
        own = g['cost'] - g['summary_cost']
        add('model', g['model'], own, g['runs'])
        add('kind', g['kind'], own, g['runs'])
        if g['summary_cost'] or g['summaries']:
            add('model', g['summary_model'] or g['model'], g['summary_cost'], g['summaries'])
            add('kind', 'summary', g['summary_cost'], g['summaries'])

    project_ids = [k for k in by['project'] if k]
    agent_ids = [k for k in by['agent'] if k]
    titles = {r['id']: r['title'] for r in db.execute('SELECT id,title FROM projects WHERE id = ANY(%s)', (project_ids,)).fetchall()} if project_ids else {}
    names = {r['id']: r['name'] for r in db.execute('SELECT id,name FROM agents WHERE id = ANY(%s)', (agent_ids,)).fetchall()} if agent_ids else {}

    def rows(table, label):
        out = [{**label(k), 'cost': round(v['cost'], 6), 'runs': v['runs']} for k, v in by[table].items()]
        return sorted(out, key=lambda r: (-r['cost'], -r['runs']))

    # Top runs: pick the ids first, then read the (small) text of only those ten.
    top = db.execute(f'''SELECT t.id,t.created_at,t.status,t.worker_model AS model,t.openrouter_cost_usd::float AS cost,
            t.project_id,t.agent_id,a.name AS agent,p.title AS project,{_KIND_SQL} AS kind,
            CASE WHEN position('LATEST USER MESSAGE:' IN t.task_text) > 0 THEN right(t.task_text, 300) ELSE left(t.task_text, 300) END AS text,
            substring(t.task_text FROM 'YOUR TASK [(][0-9]+ of [0-9]+[)]: ([^[:cntrl:]]{1,200})') AS task_title
        FROM (SELECT id FROM tasks WHERE created_at >= %(since)s AND (%(ws)s = '' OR workspace = %(ws)s)
              AND openrouter_cost_usd > 0 ORDER BY openrouter_cost_usd DESC LIMIT 10) best
        JOIN tasks t ON t.id=best.id LEFT JOIN agents a ON a.id=t.agent_id LEFT JOIN projects p ON p.id=t.project_id
        ORDER BY t.openrouter_cost_usd DESC''', {'since': since, 'ws': ws}).fetchall()
    for r in top:
        text = r['text'] or ''
        # A project task's prompt ends in its reporting rules: name the task instead.
        title = r.pop('task_title', None)
        r['text'] = ('Task: ' + title) if title else text.rsplit('LATEST USER MESSAGE:', 1)[-1].strip()[:200]

    budgets = db.execute('''SELECT p.id,p.title,p.status,p.budget_usd::float AS budget,
            COALESCE((SELECT SUM(k.openrouter_cost_usd) FROM tasks k WHERE k.project_id=p.id),0)::float AS spent,
            p.budget_warned_usd IS NOT DISTINCT FROM p.budget_usd AS warned
        FROM projects p WHERE p.status<>'cancelled' AND (%s = '' OR p.workspace = %s)
        ORDER BY p.updated_at DESC LIMIT 100''', (ws, ws)).fetchall()
    for b in budgets:
        b['pct'] = round(b['spent'] / b['budget'] * 100, 1) if b['budget'] else 0.0
    budgets.sort(key=lambda b: -b['pct'])

    totals['cost'] = round(totals['cost'], 6)
    totals['avg_per_day'] = round(totals['cost'] / days, 6)
    totals['avg_per_run'] = round(totals['cost'] / totals['runs'], 6) if totals['runs'] else 0.0
    return {
        'days': days, 'workspace': ws, 'since': first.isoformat(), 'until': today.isoformat(), 'totals': totals,
        'daily': [{'day': d.isoformat(), 'cost': round(v['cost'], 6), 'runs': v['runs']} for d, v in daily.items()],
        'by_project': rows('project', lambda k: {'id': k, 'title': titles.get(k, 'Deleted project') if k else 'No project'}),
        'by_agent': rows('agent', lambda k: {'id': k, 'name': names.get(k, 'Deleted agent') if k else 'No agent'}),
        # HLA_ENGINES_V1: billing 'subscription' for runs on a Claude / ChatGPT plan (cost 0 here, covered by the plan).
        'by_model': rows('model', lambda k: {'model': k or 'unknown', 'provider': providers.label_of(k) if k else '',
                                             'billing': providers.billing_of(k) if k else 'usage'}),
        'by_kind': rows('kind', lambda k: {'kind': k, 'label': KIND_LABELS.get(k, k)}),
        'top_runs': top, 'budgets': budgets,
    }


def build_router(connect, dashboard_read):
    router = APIRouter(prefix='/ui/api', tags=['costs'])

    @router.get('/costs')
    def costs(request: Request, days: int = Query(default=30, ge=1, le=366),
              workspace: str = Query(default='', pattern='^(|home|work)$')):
        dashboard_read(request)
        with connect() as db:
            return report(db, days, workspace)

    return router
