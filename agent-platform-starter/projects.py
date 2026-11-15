"""Multi-agent projects (HLA_PROJECTS_V1).

A project turns one goal into a planned list of tasks, each owned by an agent:
1. A planner call writes the plan (tasks, owner, dependencies, "done when", approval gates). You review, edit and
   approve it before anything runs.
2. The orchestrator starts every task whose dependencies are done (up to `parallel` at once). Each task runs in
   its own conversation with a fresh, small context: the goal, the task, and the handoffs of the tasks it depends on.
   A task whose tool budget runs out continues from its checkpoint (threads.routes auto-continue).
3. A task ends with "TASK STATUS: done|blocked|failed" and a HANDOFF for later tasks. A failure, the budget or an
   approval gate (deploys, real servers) pauses the project until you act.
Costs of every run (and the planning call) are tracked on the project; it pauses at its budget or run limit.
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import time
import uuid

import httpx

import providers
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import approvals
import attachments
import costs
import models
import push
from memory import knowledge as kb
from agent_tool.runtime import GATEWAY
import runs

ORCHESTRATE_SECONDS = 5
MAX_TASK_RETRIES = 4          # extra runs for a task that stopped unfinished without needing the human
MAX_REVIEW_ROUNDS = 2         # a reviewer may send work back this many times before the project pauses
FULL_BRIEF_CHARS = 16000      # longer goals reach tasks as the planner's summary and rules instead of in full
RUN_ERRORS = ('Could not run this request:', 'Task execution failed:')  # a run that failed, not an agent's report
PLAN_MARKER = 'You plan a project for a team of AI agents'
# HLA_SPIN_V1: a task that only re-verifies what it already proved, continuation after continuation, is stopped.
SPIN_CONTINUATIONS = 2        # this many continuations in a row without progress -> one final wrap-up turn
SPIN_SIMILARITY = 0.9         # consecutive checkpoints at least this similar (difflib ratio) say the same thing
SPIN_REPEAT = 0.8             # this share of a run's tool steps already ran in an earlier run: re-verifying
SPIN_RUNS = 8                 # newest replies of the task's conversation that are compared
_deps = {}
_tick_locks = {}  # one per event loop (the app can be restarted in the same process, e.g. in tests)


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS projects (
        id UUID PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, workspace TEXT NOT NULL DEFAULT 'home',
        status TEXT NOT NULL DEFAULT 'planning', planner_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
        budget_usd NUMERIC(10,4) NOT NULL DEFAULT 2, max_runs INTEGER NOT NULL DEFAULT 40, parallel INTEGER NOT NULL DEFAULT 2,
        workdir TEXT NOT NULL, summary TEXT, error TEXT, created_by TEXT NOT NULL DEFAULT 'dashboard_session',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS project_tasks (
        id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        acceptance TEXT NOT NULL DEFAULT '', agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
        depends_on TEXT[] NOT NULL DEFAULT '{}', gate BOOLEAN NOT NULL DEFAULT FALSE, gate_approved_by TEXT,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, thread_id UUID,
        result TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(project_id, key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS project_log (
        id BIGSERIAL PRIMARY KEY, project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), actor TEXT NOT NULL, text TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS project_log_idx ON project_log(project_id, id DESC)')
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS review BOOLEAN NOT NULL DEFAULT FALSE')
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS rules TEXT')
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS pause_reason TEXT')
    # Times a review task sent work back (its attempts also count continuations and retries).
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS review_rounds INTEGER NOT NULL DEFAULT 0')  # shown on the project page
    # attempts at the last manual retry: automatic continuations are counted from there
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS retried_at_attempt INTEGER NOT NULL DEFAULT 0')
    db.execute('ALTER TABLE agent_threads ADD COLUMN IF NOT EXISTS project_id UUID')
    db.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_id UUID')
    db.execute('CREATE INDEX IF NOT EXISTS tasks_project_idx ON tasks(project_id) WHERE project_id IS NOT NULL')
    # Rounds: a completed project takes a new request ("add these features"); its tasks are planned on top of the
    # finished ones, in the same workdir, with the same rules and notes.
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS phase INTEGER NOT NULL DEFAULT 1')
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS phase INTEGER NOT NULL DEFAULT 1')
    # HLA_SPIN_V1: replies before spin_since do not count (a manual retry or send-back starts over); wrap_up marks
    # the one final turn a spinning task gets.
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS spin_since TIMESTAMPTZ')
    db.execute('ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS wrap_up BOOLEAN NOT NULL DEFAULT FALSE')
    # HLA_GITHUB_V1: "pr" = tasks push a branch and open a pull request, the human merges; "direct" = tasks merge into
    # the main branch themselves. Rows from before this column keep "direct" (what they were planned with); new
    # projects choose, "pr" by default (ProjectIn).
    db.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS merge_policy TEXT NOT NULL DEFAULT 'direct'")
    # HLA_ROUTES_V1: the public addresses the project's site must be served at (full host names, optional).
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS staging_domain TEXT')
    db.execute('ALTER TABLE projects ADD COLUMN IF NOT EXISTS production_domain TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS project_prs (
        id BIGSERIAL PRIMARY KEY, project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        task_id UUID REFERENCES project_tasks(id) ON DELETE SET NULL, url TEXT NOT NULL, repo TEXT NOT NULL, number INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'open', title TEXT, checks TEXT, checked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(project_id, url))''')
    db.execute('''CREATE TABLE IF NOT EXISTS project_phases (
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE, phase INTEGER NOT NULL, request TEXT NOT NULL,
        summary TEXT, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (project_id, phase))''')


_access_cache = {}  # agent id -> (time, servers)
ACCESS_TTL = 30


def agent_access(agent_ids, workspace):
    """{agent id: ["sandbox (level 6)", ...]} for the project page; [] = no server tools at all, so the agent can
    only read and write text. Agents whose access is unknown (gateway unreachable) are left out."""
    if workspace != 'home' or os.getenv('SSH_TOOL_ENABLED', 'false').lower() != 'true':
        return {str(a): [] for a in agent_ids}
    token, now, result = os.getenv('TOOL_GATEWAY_TOKEN', ''), time.monotonic(), {}
    for agent_id in agent_ids:
        cached = _access_cache.get(agent_id)
        if not cached or now - cached[0] > ACCESS_TTL:
            try:
                reply = httpx.get(GATEWAY + '/v1/policy/effective', params={'agent_id': str(agent_id)},
                                  headers={'Authorization': 'Bearer ' + token}, timeout=5)
                reply.raise_for_status()
                body = reply.json()
                levels = {**(body.get('services') or {}), **(body.get('servers') or {})}
                cached = (now, sorted(f'{s} (level {p["level"]})' for s, p in levels.items() if p.get('level', 0) >= 1))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
            _access_cache[agent_id] = cached
        result[str(agent_id)] = cached[1]
    return result


def project_tool_budget():
    """Tool calls per turn for project tasks (HLA_PROJECT_TOOL_CALLS, 4-80): building needs more steps than chat."""
    try: return max(4, min(80, int(os.getenv('HLA_PROJECT_TOOL_CALLS', '40'))))
    except ValueError: return 40


def log(db, project_id, actor, text):
    db.execute('INSERT INTO project_log(project_id,actor,text) VALUES (%s,%s,%s)', (project_id, actor, str(text)[:2000]))
    db.execute('UPDATE projects SET updated_at=NOW() WHERE id=%s', (project_id,))


def usage(db, project_id):
    row = db.execute('''SELECT COALESCE(SUM(openrouter_cost_usd),0) AS spent, count(*) AS runs FROM tasks
        WHERE project_id=%s''', (project_id,)).fetchone()
    return float(row['spent']), int(row['runs'])


# ---------------------------------------------------------------- planning

def _agents(db, workspace):
    return db.execute('''SELECT id,name,role,model FROM agents WHERE workspace=%s AND enabled ORDER BY name''', (workspace,)).fetchall()


def earlier_work(done_tasks, limit=9000):
    """What earlier rounds delivered, for the planner of a new round (newest handoffs kept when it is long)."""
    lines = [f'- [{t["key"]}] {t["title"]} ({t["status"]}): ' + ' '.join((t['result'] or '').split())[:700] for t in done_tasks]
    out, size = [], 0
    for line in reversed(lines):
        if size + len(line) > limit:
            out.append(f'- ... {len(lines) - len(out)} earlier tasks left out')
            break
        out.append(line)
        size += len(line)
    return '\n'.join(reversed(out))


def domains_note(project):
    """HLA_ROUTES_V1: the public addresses from the project form, as a rule for the planner and every task."""
    staging, production = project.get('staging_domain'), project.get('production_domain')
    if not staging and not production:
        return ''
    parts = ([f'staging must be served at https://{staging}'] if staging else []) + \
            ([f'production at https://{production}'] if production else [])
    return ('PUBLIC ADDRESSES (rule): ' + '; '.join(parts) + '. When the site\'s container runs on public-website and publishes a '
            'port, request each route with the request_route tool (host, backend_port, environment, reason); the owner approves it '
            'and the gateway writes the Traefik route. Never edit Traefik files yourself.\n')


def plan_prompt(project, agents, feedback=None, round_info=None, knowledge=None):
    # HLA_KB_V2: what is already known (project notes first) so the plan builds on it instead of re-discovering it.
    known = ('\n' + knowledge + '\nUse this knowledge: copy the hosts, paths, conventions and decisions a task needs into its '
             'description, and do not plan tasks that only re-discover what it already says.\n') if knowledge else ''
    team = '\n'.join(f'- {a["name"]}: {a["role"] or "general"}' for a in agents) or '- General worker: general'
    if round_info:  # a new round on a finished project: plan only the new request
        return (PLAN_MARKER + ''' on a self-hosted infrastructure platform. This project already ran; you now plan
ROUND ''' + str(round_info['phase']) + ''': new work on top of what is done. Agents can use server tools within their permissions: shell
commands, file read/write on the disposable sandbox server, diagnostics, Git/Compose deployments.

TEAM:
''' + team + '''

ORIGINAL GOAL / BRIEF (background; it is done):
''' + project['goal'][:FULL_BRIEF_CHARS] + '''

RULES SO FAR:
''' + (project.get('rules') or '- none') + '''

ALREADY DONE (earlier rounds; build on these results, do not redo them):
''' + round_info['done'] + '''

NEW REQUEST FOR THIS ROUND:
''' + round_info['request'] + '''

WORKSPACE: the project lives on the sandbox server (sandbox-vm) in ''' + project['workdir'] + ''' unless the brief says otherwise.
''' + domains_note(project) + known + (('FEEDBACK ON THE PREVIOUS PLAN:\n' + feedback + '\n') if feedback else '') + '''
Return ONLY a JSON object, no other text:
{"summary": "one paragraph: what this round changes and how",
 "rules": ["ALL rules: the rules so far plus any new restriction from the request, one per item"],
 "tasks": [{"key": "short-id", "title": "...", "agent": "<team member name>",
 "description": "concrete instructions", "acceptance": "how to verify it is done", "depends_on": ["earlier keys of THIS plan"],
 "gate": false, "review": false}]}
Plan only the new request. Start from the existing code, docs and deployment (the first task may inspect them). Write each
task description so it is complete on its own (hosts, paths, names, repository and branch from the earlier handoffs).
Rules: 1 to 12 tasks, each doable by one agent in about 30 tool calls. Use new keys, not the keys of done tasks; depends_on
names only tasks of this plan (earlier work is already done). Set "gate": true for every task that deploys to staging or
production or changes a real server outside the sandbox. If the team has more than one agent, add a review task
("review": true) by a different agent than the builder, before any gated deploy.''')
    return (PLAN_MARKER + ''' on a self-hosted infrastructure platform. Agents can use server tools within their
permissions: shell commands, file read/write on the disposable sandbox server, diagnostics, Git/Compose deployments.

TEAM:
''' + team + '''

GOAL:
''' + project['goal'] + '''

WORKSPACE: build and test on the sandbox server (sandbox-vm) in ''' + project['workdir'] + ''' unless the goal says otherwise.
''' + domains_note(project) + known + (('FEEDBACK ON THE PREVIOUS PLAN:\n' + feedback + '\n') if feedback else '') + '''
Return ONLY a JSON object, no other text:
{"summary": "one paragraph: approach and result",
 "rules": ["every restriction and must-follow requirement from the goal, one per item, with hosts/paths/names"],
 "tasks": [{"key": "short-id", "title": "...", "agent": "<team member name>",
 "description": "concrete instructions", "acceptance": "how to verify it is done", "depends_on": ["earlier keys"], "gate": false,
 "review": false}]}
Write each task description so it is complete on its own: copy in the hosts, IPs, paths, names, requirements and
restrictions from the goal that the task needs. Rules: 2 to 12 tasks. Each task must be doable by one agent in about 30 tool calls; split bigger work. depends_on may only
name earlier tasks; tasks without a dependency between them run in parallel. Set "gate": true for every task that deploys
to staging or production or changes a real server outside the sandbox (a human approves before it starts). If the team has
more than one agent, add a review task ("review": true) by a different agent than the one who built the deliverable, before any
gated deploy; it depends on the tasks it checks, which get its feedback and are redone if it finds problems.''')


def parse_plan(text, agents, default_agent, taken=()):
    """Validated plan from the model's reply, or ValueError with a reason."""
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        raise ValueError('the planner returned no JSON plan')
    try:
        data = json.loads(match.group(0), strict=False)  # models put raw newlines/tabs inside JSON strings
    except json.JSONDecodeError as exc:
        raise ValueError(f'the planner returned invalid JSON ({exc.msg} at character {exc.pos})') from None
    tasks = data.get('tasks') if isinstance(data, dict) else None
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
        raise ValueError('the plan must have 1 to 20 tasks')
    by_name = {a['name'].casefold(): a['id'] for a in agents}
    seen, out, renamed = [], [], {}  # renamed: the planner's key -> the stored key (keys of earlier rounds stay taken)
    taken = set(taken)
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or not str(t.get('title') or '').strip():
            raise ValueError(f'task {i + 1} has no title')
        raw = re.sub(r'[^a-z0-9-]+', '-', str(t.get('key') or f'task-{i + 1}').lower()).strip('-')[:40] or f'task-{i + 1}'
        key, n = raw, 1
        while key in seen or key in taken:
            n += 1
            key = f'{raw[:36]}-{n}'
        renamed.setdefault(raw, key)
        deps = [str(d).lower() for d in (t.get('depends_on') or []) if isinstance(d, str)]
        deps = [renamed.get(d, d) for d in (re.sub(r'[^a-z0-9-]+', '-', d).strip('-') for d in deps)]
        out.append({'key': key, 'title': str(t['title']).strip()[:200], 'description': str(t.get('description') or '')[:4000],
                    'acceptance': str(t.get('acceptance') or '')[:1000], 'gate': bool(t.get('gate')), 'review': bool(t.get('review')),
                    'agent_id': by_name.get(str(t.get('agent') or '').casefold(), default_agent),
                    'depends_on': [d for d in deps if d in seen]})  # only earlier tasks: no cycles
        seen.append(key)
    rules = '\n'.join('- ' + str(r).strip() for r in (data.get('rules') or []) if str(r).strip())[:6000]
    return str(data.get('summary') or '')[:2000], out, rules


async def plan(project_id, feedback=None):
    """Ask the planner for a plan; store it and wait for approval (or record why planning failed)."""
    connect = _deps['connect']

    def load():
        with connect() as db:
            project = db.execute('SELECT * FROM projects WHERE id=%s', (project_id,)).fetchone()
            agents = _agents(db, project['workspace'])
            planner = None
            if project['planner_agent_id']:
                planner = db.execute('SELECT * FROM agents WHERE id=%s', (project['planner_agent_id'],)).fetchone()
            round_info, taken = None, []
            if project['phase'] > 1:
                earlier = db.execute('SELECT key,title,status,result FROM project_tasks WHERE project_id=%s AND phase<%s ORDER BY seq',
                                     (project_id, project['phase'])).fetchall()
                req = db.execute('SELECT request FROM project_phases WHERE project_id=%s AND phase=%s',
                                 (project_id, project['phase'])).fetchone()
                round_info = {'phase': project['phase'], 'request': (req or {}).get('request') or '', 'done': earlier_work(earlier) or '- nothing'}
                taken = [t['key'] for t in earlier]
            # Ranked by the goal (and this round's request); planning must not fail because of the knowledge lookup.
            try:
                knowledge, _ = kb.context(db, project['workspace'], ((round_info or {}).get('request', '') + '\n' + project['goal'])[:20000],
                                          project={'id': project['id'], 'title': project['title']})
            except Exception:
                db.rollback()
                knowledge = ''
            project['plan_model'] = models.choose(db, 'plan', project, planner)  # routed, else the planner's model
            try:  # HLA_ATTACHMENTS_V1: the project's reference files (listing and text within a budget)
                files = attachments.project_block(db, project_id)
            except Exception:
                db.rollback()
                files = ''
            return project, agents, planner, round_info, taken, knowledge, files
    project, agents, planner, round_info, taken, knowledge, files = await asyncio.to_thread(load)
    model = project['plan_model']
    prompt = plan_prompt(project, agents, feedback, round_info, knowledge)
    task_id = uuid.uuid4()
    try:
        async with httpx.AsyncClient() as client:  # HLA_XAI_V1: OpenRouter or xAI by model id
            result = await providers.complete(client, {'model': model, 'max_tokens': 12000, 'reasoning': {'effort': 'low'},
                'messages': [{'role': 'system', 'content': prompt},
                             {'role': 'user', 'content': 'Write the plan.' + ('\n\n' + files if files else '')}]}, timeout=240)
        text = result['choices'][0]['message'].get('content') or ''
        cost = (result.get('usage') or {}).get('cost')
        default_agent = (planner or {}).get('id') or (agents[0]['id'] if agents else None)
        summary, tasks, rules = parse_plan(text, agents, default_agent, taken)
        error = None
    except Exception as exc:  # keep the project and say why; the user can re-plan
        text, cost, summary, tasks, rules = '', None, None, [], None
        error = str(getattr(exc, 'detail', None) or exc)[:300] or type(exc).__name__

    def store():
        with connect() as db:
            db.execute('''INSERT INTO tasks(id,task_text,status,workspace,provider,worker_model,result_text,openrouter_cost_usd,
                finished_at,project_id,error_message) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s)''',
                (task_id, 'Plan project: ' + project['title'], 'failed' if error else 'completed', project['workspace'], providers.name_of(model), model,
                 text[:20000] or None, cost, project_id, error))
            if error:
                db.execute("UPDATE projects SET status='plan_failed',error=%s WHERE id=%s", (error, project_id))
                log(db, project_id, 'planner', 'Planning failed: ' + error)
            else:
                phase = project['phase']
                # Only this round's (unstarted) tasks are replaced; earlier rounds stay as they are.
                db.execute('DELETE FROM project_tasks WHERE project_id=%s AND phase=%s', (project_id, phase))
                first = db.execute('SELECT COALESCE(MAX(seq),0) AS n FROM project_tasks WHERE project_id=%s', (project_id,)).fetchone()['n']
                for seq, t in enumerate(tasks, first + 1):
                    db.execute('''INSERT INTO project_tasks(id,project_id,seq,key,title,description,acceptance,agent_id,depends_on,gate,review,phase)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (uuid.uuid4(), project_id, seq, t['key'], t['title'],
                        t['description'], t['acceptance'], t['agent_id'], t['depends_on'], t['gate'], t['review'], phase))
                if phase > 1:  # the project keeps its first summary; the round gets its own, rules are the merged list
                    db.execute('UPDATE project_phases SET summary=%s WHERE project_id=%s AND phase=%s', (summary, project_id, phase))
                    db.execute("UPDATE projects SET status='awaiting_approval',rules=COALESCE(NULLIF(%s,''),rules),error=NULL WHERE id=%s",
                               (rules, project_id))
                else:
                    db.execute("UPDATE projects SET status='awaiting_approval',summary=%s,rules=%s,error=NULL WHERE id=%s",
                               (summary, rules, project_id))
                log(db, project_id, 'planner', f'Plan ready: {len(tasks)} tasks. Review and approve it to start.')
                push.queue(db, project['title'], f'Plan ready ({len(tasks)} tasks): review and approve it.',
                           push.project_url(project_id), f'project-{project_id}')
            db.commit()
    await asyncio.to_thread(store)


# ---------------------------------------------------------------- orchestration

NO_SUMMARY = 'The model returned no summary for this turn'


_NOTHING = re.compile(r'\W*(nothing|none\b|n/a\b|not (needed|required)|no (action|input|human (action|input))\b)', re.I)


def needs_human(reply):
    """True when the reply asks the human for something: a NEEDS FROM YOU section that is not "nothing"/"none"."""
    reply = reply or ''
    if 'NEEDS FROM YOU' not in reply:
        return False
    asked = reply.rsplit('NEEDS FROM YOU', 1)[1].lstrip(' *:_-').strip()
    return bool(asked) and not _NOTHING.match(asked)


ANY_STATUS = re.compile(r'TASK STATUS\W{0,4}?:\s*([^\n]{0,80})', re.I)
_DONE_WORDS = re.compile(r'(done|complete|completed|finished)\b')
_UNFINISHED = re.compile(r'(in[ -]?progress|partial|partially|ongoing|checkpoint|wip|continuing|incomplete|unfinished|not done)\b')


def task_status(reply):
    """(kind, word) of a reply's last "TASK STATUS:" line, or (None, None) without one. kind is done, blocked, failed,
    unfinished (in progress, partial, wip, ...; also an empty status) or unknown. Markdown, quotes, emoji and punctuation
    around the word are ignored (`done`, **done**, "done", ✅ done); complete/completed/finished count as done. Status
    lines inside the HANDOFF section (e.g. a later task's status quoted there) do not count."""
    reply = reply or ''
    found = list(ANY_STATUS.finditer(reply))
    cut = reply.rfind('HANDOFF:')
    if cut >= 0 and any(m.start() < cut for m in found):
        found = [m for m in found if m.start() < cut]
    if not found:
        return None, None
    text = re.sub(r'^[^A-Za-z]+', '', found[-1].group(1)).lower()
    word = (re.match(r"[a-z][a-z' -]*", text) or [''])[0].strip()
    if not word:
        return 'unfinished', 'empty'
    if _DONE_WORDS.match(word):
        return 'done', word
    verdict = re.match(r'(blocked|failed)\b', word)
    if verdict:
        return verdict.group(1), word
    if _UNFINISHED.match(word):
        return 'unfinished', word
    return 'unknown', word


def invalid_status(reply):
    """The text of a last "TASK STATUS:" line that says the task is unfinished (e.g. "in progress"), else None."""
    kind, word = task_status(reply)
    return word if kind == 'unfinished' else None


def evaluate(reply):
    """('done'|'blocked'|'failed'|'checkpoint'|None, handoff text) from a task's last reply."""
    reply = reply or ''
    kind, _ = task_status(reply)
    handoff = reply.split('HANDOFF:', 1)[1].strip() if 'HANDOFF:' in reply else reply[-2000:].strip()
    if kind == 'unfinished':
        # "TASK STATUS: in progress" is not a verdict: the task is unfinished, so it continues like a checkpoint
        # (without this a long reply would count as done).
        return 'checkpoint', reply[-3000:].strip()
    if kind in ('done', 'blocked', 'failed'):
        # Out of tool calls is not blocked: a checkpoint without anything asked of the human continues.
        if kind != 'done' and 'CHECKPOINT' in reply and not needs_human(reply):
            return 'checkpoint', reply[-3000:].strip()
        return kind, handoff[:3000]
    # No status line, or an unknown word: a checkpoint if it says so, else the caller's fallback (long reply = done).
    if 'CHECKPOINT' in reply or NO_SUMMARY in reply:
        return 'checkpoint', reply[-3000:].strip()
    return None, handoff[:3000]


# ---------------------------------------------------------------- progress between checkpoints (HLA_SPIN_V1)

_ARTIFACT = re.compile(r'\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b'  # commits
                       r'|https?://[^\s)>\]"\']+'                   # URLs (PRs, deployments)
                       r'|(?<![\w.])/(?:[\w.@-]+/)*[\w.@-]+'        # paths
                       r'|\b(?:task|feature|fix|feat)/[\w./-]+')    # branches


def _checkpoint_text(reply):
    """The part of a reply that says what is done and left: from the last CHECKPOINT/status line on, else its end."""
    reply = reply or ''
    at = max(reply.rfind('CHECKPOINT'), reply.rfind('TASK STATUS'))
    text = reply[at:] if at >= 0 else reply[-3000:]
    return '\n'.join(' '.join(line.split()).lower() for line in text.splitlines() if line.strip())[:4000]


def _steps(progress):
    """Signatures of a run's finished tool steps: tool, target and (newer runs) a hash of the arguments."""
    out = []
    for step in progress or []:
        if isinstance(step, dict) and step.get('status') != 'running':
            out.append('|'.join(str(step.get(k) or '') for k in ('tool', 'target', 'sig')))
    return out


def stalled(prev, cur, seen):
    """True when run `cur` made no real progress after `prev`. Runs are {'content', 'progress'}; seen holds the
    artifacts (commits, URLs, paths, branches) and step signatures of every earlier run and is updated."""
    found = set(_ARTIFACT.findall(cur['content'] or ''))
    new_artifacts = found - seen['artifacts']
    steps = _steps(cur.get('progress'))
    repeated = sum(1 for s in steps if s in seen['steps']) / len(steps) if steps else None
    seen['artifacts'] |= found
    seen['steps'] |= set(steps)
    if new_artifacts:  # a new commit, branch, PR or file is progress, whatever the text says
        return False
    similarity = difflib.SequenceMatcher(None, _checkpoint_text(prev['content']), _checkpoint_text(cur['content'])).ratio()
    if repeated is not None and repeated >= SPIN_REPEAT and similarity >= 0.5:
        return True   # the same commands on the same targets again, for much the same checkpoint
    return similarity >= SPIN_SIMILARITY and (repeated is None or repeated >= 0.5)


def spin_count(runs):
    """How many of the newest consecutive continuations (oldest run first in `runs`) made no progress."""
    if len(runs) < 2:
        return 0
    seen = {'artifacts': set(_ARTIFACT.findall(runs[0]['content'] or '')), 'steps': set(_steps(runs[0].get('progress')))}
    flags = [stalled(runs[i - 1], runs[i], seen) for i in range(1, len(runs))]
    count = 0
    for flag in reversed(flags):
        if not flag:
            break
        count += 1
    return count


def task_runs(db, task):
    """The task conversation's replies since its last manual restart, oldest first, with their tool steps. Runs that
    failed (RUN_ERRORS: provider errors, interruptions) are left out: they are not the agent spinning."""
    if not task.get('thread_id'):
        return []
    rows = db.execute('''SELECT m.content,k.progress FROM agent_thread_messages m LEFT JOIN tasks k ON k.id=m.task_id
        WHERE m.thread_id=%s AND m.role='assistant' AND (%s::timestamptz IS NULL OR m.created_at>%s)
          AND NOT (m.content LIKE %s OR m.content LIKE %s)
        ORDER BY m.created_at DESC LIMIT %s''', (task['thread_id'], task.get('spin_since'), task.get('spin_since'),
                                                 *(e + '%' for e in RUN_ERRORS), SPIN_RUNS)).fetchall()
    return list(reversed(rows))


WRAP_UP_NOTE = ('FINAL WRAP-UP TURN: your last {n} continuations made no real progress (the same checkpoint, the same '
                'checks again). This is the last turn for this task. Do NOT re-verify anything you already listed as done '
                'and verified: it is accepted as done. Do only the steps still listed as left to do; if something blocks '
                'them, say exactly what. End with TASK STATUS: done, blocked or failed and a HANDOFF, not with a checkpoint: '
                'another checkpoint stops the task.')


def git_rules(project, task):
    """The GIT paragraph of a task prompt, per the project's merge policy (HLA_GITHUB_V1)."""
    key = task['key']
    common = (f'GIT: other tasks may work in the same repository at the same time. To change code in a git repository, do not '
              f'edit its main checkout: use your own worktree and branch, `git -C <repo> worktree add <repo>.worktrees/{key} '
              f'-b task/{key}` (on a later turn, reuse it if it exists). Build, test and commit there. ')
    tail = 'run only your own tests if another task\'s files break the full suite, and say so.\n\n'
    if project.get('merge_policy') != 'pr':
        return (common + 'When your part is verified, bring your branch up to date with the main branch, rerun the tests, merge '
                'it into the main branch (push if the task or brief says so) and remove the worktree. Put the branch and worktree '
                'path in the HANDOFF; ' + tail)
    return (common + 'This project uses PULL REQUESTS: never merge into the main branch and never push to it; the human merges. '
            'If a task you depend on has an open PR that is not merged yet, start your branch from its branch instead of the '
            'main branch and say so in your PR. When your part is verified, bring your branch up to date (merge or rebase the '
            f'main branch into it), rerun the tests, push the branch (`git push -u origin task/{key}`) and open a pull request '
            'against the main branch with the github tool (create_pr: what changed, how it was tested). A later turn pushes '
            'again; the PR updates itself. Keep the worktree. Put the branch, worktree path and a line `PR: <url>` in the '
            'HANDOFF. If the repository has no GitHub remote, commit on the branch and hand off the branch instead; if the '
            'push or the PR is refused (no github tool, repository not allowed), end blocked with NEEDS FROM YOU saying so. '
            + tail)


def task_prompt(project, task, tasks, notes, agents_by_id, retry_note=None):
    done = {t['key']: t for t in tasks}
    handoffs = '\n'.join(f'- {done[k]["title"]} ({agents_by_id.get(done[k]["agent_id"], "agent")}): {(done[k]["result"] or "")[:1500]}'
                         for k in task['depends_on'] if k in done) or '- none'
    others = '\n'.join(f'  {t["seq"]}. {t["title"]} [{t["status"]}]' for t in tasks)
    if len(project['goal']) <= FULL_BRIEF_CHARS:
        goal = 'GOAL / BRIEF:\n' + project['goal']
    else:  # a very long brief: the planner's summary and rules; the task description carries the details it needs
        goal = ('GOAL (summary of a long brief):\n' + (project.get('summary') or project['goal'][:4000])
                + ('\n\nRULES FROM THE BRIEF (must follow):\n' + project['rules'] if project.get('rules') else ''))
    if task.get('phase', 1) > 1 and project.get('round_request'):
        goal += (f'\n\nCURRENT ROUND ({task["phase"]}): the project above is done; this round adds:\n'
                 + project['round_request'][:FULL_BRIEF_CHARS])
    return (f'PROJECT: {project["title"]}\n{goal}\n\nFULL PLAN:\n{others}\n\n'
            f'YOUR TASK ({task["seq"]} of {len(tasks)}): {task["title"]}\n{task["description"]}\n'
            f'DONE WHEN: {task["acceptance"] or "the task is complete and verified"}\n\n'
            f'WORKSPACE: work on the sandbox server in {project["workdir"]} (create it if missing) unless the task says otherwise. '
            'Write files with server_write_file, not with heredocs.\n\n'
            + (domains_note(project) + '\n' if domains_note(project) else '')
            + git_rules(project, task) +
            f'RESULTS OF THE TASKS YOU DEPEND ON:\n{handoffs}\n\n'
            + (f'PROJECT NOTES (newest first):\n{notes}\n\n' if notes else '')
            + (f'PREVIOUS ATTEMPT: {retry_note}\n\n' if retry_note else '')
            + ('YOU ARE THE REVIEWER: check the deliverables of the tasks above against the goal and their "done when", '
               'yourself (open the files, run the checks). If something must change, end with TASK STATUS: failed and a '
               'numbered list of concrete changes; those tasks are redone with your list and you review again.\n'
               + ('For each PR in their handoffs: read its state, checks and reviews with the github tool (pr_status) and the '
                  'diff with git (`git fetch` and `git diff <main>...<branch>`); you may comment your findings on the PR (comment). '
                  'Do not merge it.\n' if project.get('merge_policy') == 'pr' else '') + '\n'
               if task.get('review') else '')
            + 'Do only this task, and do every part of it you can: if one part needs something you do not have (access, '
              'credentials), finish the other parts first and do not stop early. Make technical decisions yourself (follow the '
              'brief and the project docs, record the decision and why in the docs); ask the human only for access, '
              'credentials or approvals. When it is finished, end with exactly:\n'
              'TASK STATUS: done   (or: blocked / failed, followed by the reason)\n'
              'HANDOFF: what the next tasks need (paths, URLs, commands, decisions, open issues), at most 15 lines.\n'
              'Running out of tool calls or turns is never a reason for "blocked" or NEEDS FROM YOU: end with a CHECKPOINT '
              'and the platform continues the task in a fresh turn. Do not ask for budget, more turns or a task split, and leave '
              'out NEEDS FROM YOU when you need nothing. '
              'Use "blocked" only when nothing more can be done without the human; then add NEEDS FROM YOU: the exact '
              'things they must provide or do (e.g. "add this deploy key to repo X with write access"), one per line.')


def _thread_state(db, task):
    if not task['thread_id']:
        return None, None
    thread = db.execute('SELECT * FROM agent_threads WHERE id=%s', (task['thread_id'],)).fetchone()
    last = db.execute('''SELECT m.content,m.kind,k.pending_command->>'status' AS cmd FROM agent_thread_messages m
        LEFT JOIN tasks k ON k.id=m.task_id WHERE m.thread_id=%s AND m.role='assistant'
        ORDER BY m.created_at DESC LIMIT 1''', (task['thread_id'],)).fetchone()
    return thread, last


def send_back(db, pid, review, tasks, findings, actor):
    """Review loop: the tasks a review checks are redone with its list of changes, then the review runs again."""
    rounds = review['review_rounds'] + 1
    redo = [t for t in tasks if t['key'] in review['depends_on'] and not t.get('review')]
    for t in redo:
        t['status'], t['result'] = 'pending', f'Reviewer feedback (round {rounds}):\n' + findings
        db.execute("UPDATE project_tasks SET status='pending',result=%s,retried_at_attempt=attempts,spin_since=NOW(),wrap_up=FALSE,updated_at=NOW() WHERE id=%s",
                   (t['result'], t['id']))
    review['status'] = 'pending'
    db.execute("UPDATE project_tasks SET status='pending',result=%s,review_rounds=%s,retried_at_attempt=attempts,spin_since=NOW(),wrap_up=FALSE,updated_at=NOW() WHERE id=%s",
               (findings, rounds, review['id']))
    log(db, pid, actor, f'Review "{review["title"]}" asked for changes (round {rounds}); redoing: '
        + (', '.join(f'"{t["title"]}"' for t in redo) or 'nothing it depends on') + '.')


PR_URL = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)')
PR_REFRESH_SECONDS = 60     # a PR's state shown on the project page and in the inbox is at most this old
PR_REFRESH_MAX = 8          # PRs refreshed per page load (the rest on the next one)
INBOX_PR_REFRESH_MAX = 4    # PRs refreshed per inbox request, over all projects (each is a gateway call, up to 4 s)


def handoff_prs(text):
    """[(url, repo, number)] from the "PR: <url>" lines of a handoff."""
    found = []
    for line in (text or '').splitlines():
        if re.search(r'\bPR\b', line):
            for m in PR_URL.finditer(line):
                item = (m.group(0), m.group(1).lower(), int(m.group(2)))
                if item not in found:
                    found.append(item)
    return found


def record_prs(db, pid, task, handoff):
    """HLA_GITHUB_V1: the PRs a finished task names are kept on the project (the first task to name one owns it)."""
    for url, repo, number in handoff_prs(handoff):
        row = db.execute('''INSERT INTO project_prs(project_id,task_id,url,repo,number) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (project_id,url) DO NOTHING RETURNING id''', (pid, task['id'], url, repo, number)).fetchone()
        if row:
            log(db, pid, 'orchestrator', f'"{task["title"]}" opened PR #{number} in {repo}: {url}')


PR_COLUMNS = '''r.id,r.project_id,r.task_id,r.url,r.repo,r.number,r.state,r.title,r.checks,r.checked_at,t.agent_id,
        (r.checked_at IS NULL OR r.checked_at < NOW() - make_interval(secs => %s)) AS stale'''


def project_prs(db, pid):
    rows = db.execute(f'''SELECT {PR_COLUMNS}
        FROM project_prs r LEFT JOIN project_tasks t ON t.id=r.task_id WHERE r.project_id=%s ORDER BY r.id''',
        (PR_REFRESH_SECONDS, pid)).fetchall()
    return refresh_prs(db, rows)


def refresh_prs(db, rows, limit=PR_REFRESH_MAX):
    """Open PRs not checked in the last PR_REFRESH_SECONDS get their state from the gateway (pr_status), at most
    `limit` of them (the rest keep their stored state until a later request); failures are ignored and the stored
    state stays. The caller commits."""
    if os.getenv('SSH_TOOL_ENABLED', 'false').lower() != 'true':
        return rows
    admin, tool = os.getenv('POLICY_ADMIN_TOKEN', ''), os.getenv('TOOL_GATEWAY_TOKEN', '')
    for row in [r for r in rows if r['state'] == 'open' and r['stale']][:limit]:
        body = {'operation': 'pr_status', 'repo': row['repo'], 'number': row['number']}
        # The dashboard reads with the admin token (read-only there); without it, as the agent that opened the PR.
        if len(admin) >= 32:
            token, body['actor'] = admin, 'dashboard'
        else:
            token, body['agent_id'] = tool, str(row['agent_id'] or '')
        data = {}
        try:
            reply = httpx.post(GATEWAY + '/v1/github', json=body, headers={'Authorization': 'Bearer ' + token}, timeout=4)
            if reply.status_code == 200:
                data = reply.json()
        except (httpx.HTTPError, ValueError):
            pass
        state = data.get('state') if data.get('state') in ('open', 'closed', 'merged') else None
        checks = (data.get('checks') or {}).get('state') if isinstance(data.get('checks'), dict) else None
        title = str(data['title'])[:300] if data.get('title') else None
        db.execute('UPDATE project_prs SET state=COALESCE(%s,state),checks=COALESCE(%s,checks),title=COALESCE(%s,title),checked_at=NOW() WHERE id=%s',
                   (state, checks, title, row['id']))
        row.update(state=state or row['state'], checks=checks or row['checks'], title=title or row['title'], stale=False)
    return rows


def capture(db, project, task, handoff):
    """HLA_KB_V2: a finished task's handoff becomes project knowledge (one compact note per task)."""
    try:
        with db.transaction():  # a savepoint: a failed capture must not undo the orchestrator's pass
            name = (db.execute('SELECT name FROM agents WHERE id=%s', (task['agent_id'],)).fetchone() or {}).get('name') if task['agent_id'] else None
            kb.capture_handoff(db, project, task, handoff, name)
    except Exception:
        pass


def spin_stop(db, project, task, reply):
    """HLA_SPIN_V1, before a checkpointed task continues: True when this decided what happens next, i.e. the task
    gets its one wrap-up turn (it continues below with a firm note) or, after a wrap-up turn that only checkpointed
    again, it is stopped as failed and the project pauses."""
    pid, runs = project['id'], task_runs(db, task)
    spins = spin_count(runs)
    if task.get('wrap_up'):
        if spins == 0:  # the wrap-up turn did move forward: back to normal continuations
            db.execute('UPDATE project_tasks SET wrap_up=FALSE WHERE id=%s', (task['id'],))
            task['wrap_up'] = False
            log(db, pid, 'orchestrator', f'"{task["title"]}" made progress in its wrap-up turn but is not finished; continuing it.')
            return False
        last = ' '.join(_checkpoint_text(reply).split())[:300] or 'none'
        why = (f'"{task["title"]}" stopped: no progress in the last {SPIN_CONTINUATIONS} continuations and its wrap-up turn '
               f'only checkpointed again; last checkpoint: {last}')
        task['status'], task['result'] = 'failed', (why + '\n\n' + (reply or '')[-2500:])[:3000]
        db.execute("UPDATE project_tasks SET status='failed',result=%s,wrap_up=FALSE,updated_at=NOW() WHERE id=%s",
                   (task['result'], task['id']))
        reason = why + '. Add a note with what to do differently, then Retry it; or Skip it.'
        db.execute("UPDATE projects SET status='paused',pause_reason=%s WHERE id=%s", (reason[:2000], pid))
        log(db, pid, 'orchestrator', why + '; project paused.')
        push.queue(db, project['title'], 'Stopped (no progress): ' + task['title'], push.project_url(pid), f'task-{task["id"]}')
        return True
    if spins < SPIN_CONTINUATIONS:
        return False
    task['status'], task['wrap_up'] = 'pending', True
    task['retry_note'] = WRAP_UP_NOTE.format(n=spins)
    db.execute("UPDATE project_tasks SET status='pending',wrap_up=TRUE,updated_at=NOW() WHERE id=%s", (task['id'],))
    log(db, pid, 'orchestrator', f'"{task["title"]}" made no progress in its last {spins} continuations (same checkpoint, '
        'repeated checks); starting one final wrap-up turn.')
    return True


def orchestrate(db, launch):
    """One pass over running projects (database work only). Returns runs to start on the event loop."""
    started = []
    for project in db.execute("SELECT * FROM projects WHERE status='running' FOR UPDATE SKIP LOCKED").fetchall():
        pid = project['id']
        tasks = db.execute('SELECT * FROM project_tasks WHERE project_id=%s ORDER BY seq', (pid,)).fetchall()
        if project['phase'] > 1:
            project['round_request'] = (db.execute('SELECT request FROM project_phases WHERE project_id=%s AND phase=%s',
                                                   (pid, project['phase'])).fetchone() or {}).get('request')
        # 1. Tasks whose conversation went idle: read how they ended.
        for task in tasks:
            if task['status'] not in ('running', 'needs_you'):
                continue
            thread, last = _thread_state(db, task)
            if not thread or thread['status'] != 'idle' or not last:
                continue
            if last['cmd'] == 'pending' or last['kind'] == 'approval_request':
                if task['status'] != 'needs_you':
                    db.execute("UPDATE project_tasks SET status='needs_you',updated_at=NOW() WHERE id=%s", (task['id'],))
                    log(db, pid, 'orchestrator', f'"{task["title"]}" waits for your approval in its conversation.')
                    push.queue(db, project['title'], f'Approval needed: {task["title"]}',
                               f'/ui/#thread={thread["id"]}', f'task-{task["id"]}')  # the approval card is in its conversation
                    task['status'] = 'needs_you'
                continue
            outcome, handoff = evaluate(last['content'])
            run_error = outcome is None and (last['content'] or '').startswith(RUN_ERRORS)
            if run_error:
                # The run itself failed (provider error, interrupted): not the agent's verdict, so try again.
                outcome, handoff = 'checkpoint', 'The previous run failed before it could report: ' + last['content'][:500]
            if outcome in ('blocked', 'failed') and not task.get('review') and not needs_human(last['content']):
                # Nothing asked of the human: the agent ran out of steps or hit a fixable problem; a fresh turn continues.
                outcome, handoff = 'checkpoint', (last['content'] or '')[-3000:].strip()
            bad = invalid_status(last['content'])
            if bad:
                log(db, pid, 'orchestrator', f'"{task["title"]}" reported "TASK STATUS: {bad[:30]}", which is not done, blocked '
                    'or failed: treated as a checkpoint.')
            # A failed run is not a turn of the agent: it neither counts as spinning nor uses up a wrap-up turn.
            if outcome == 'checkpoint' and not run_error and spin_stop(db, project, task, last['content']):
                continue
            if outcome == 'checkpoint' and task['attempts'] - task['retried_at_attempt'] <= MAX_TASK_RETRIES:
                task['status'] = 'pending'  # continue it below (counts as another attempt)
                # The checkpoint is the last reply in this same conversation, which the history keeps verbatim: point to it
                # instead of copying it into the instructions again.
                task['retry_note'] = ('You stopped at a checkpoint (your last reply in the conversation above). Continue from it; '
                                      'do not repeat steps that already succeeded.')
                db.execute("UPDATE project_tasks SET status='pending',updated_at=NOW() WHERE id=%s", (task['id'],))
                log(db, pid, 'orchestrator', f'"{task["title"]}" stopped at a checkpoint; continuing it.')
                continue
            if outcome == 'done' or (outcome is None and len(last['content'] or '') > 300):
                task['status'], task['result'] = 'done', handoff
                db.execute("UPDATE project_tasks SET status='done',result=%s,updated_at=NOW() WHERE id=%s", (handoff, task['id']))
                capture(db, project, task, handoff)
                record_prs(db, pid, task, handoff)
                log(db, pid, 'orchestrator', f'Done: "{task["title"]}"' + ('' if outcome else ' (no status line; check its handoff)'))
                push.queue(db, project['title'], f'✓ Done: {task["title"]}', push.project_url(pid), f'task-{task["id"]}')
            elif task.get('review') and outcome == 'failed' and task['review_rounds'] < MAX_REVIEW_ROUNDS:
                send_back(db, pid, task, tasks, handoff, 'orchestrator')
            else:
                # blocked: the agent needs something from you (see NEEDS FROM YOU); failed/unfinished: it could not do it.
                state = 'blocked' if outcome == 'blocked' else 'failed'
                text = last['content'] if outcome == 'blocked' else handoff
                task['status'], task['result'] = state, text[-3000:]
                db.execute('UPDATE project_tasks SET status=%s,result=%s,updated_at=NOW() WHERE id=%s', (state, task['result'], task['id']))
                why = f'"{task["title"]}" ' + ('is blocked and needs you' if state == 'blocked' else
                    f'still asks for changes after {task["review_rounds"]} review rounds' if task.get('review') and outcome == 'failed' else
                    f'ended as {"unfinished after " + str(task["attempts"]) + " attempts" if outcome in (None, "checkpoint") else outcome}') + \
                    '. Provide what it needs (add a note), then Retry it; or Skip it.'
                db.execute("UPDATE projects SET status='paused',pause_reason=%s WHERE id=%s", (why, pid))
                log(db, pid, 'orchestrator', why.replace('. Provide', '; project paused. Provide', 1))
                push.queue(db, project['title'], ('Needs you: ' if state == 'blocked' else 'Stopped: ') + task['title'],
                           push.project_url(pid), f'task-{task["id"]}')
        if db.execute('SELECT status FROM projects WHERE id=%s', (pid,)).fetchone()['status'] != 'running':
            continue
        # 2. Finished?
        if tasks and all(t['status'] in ('done', 'skipped') for t in tasks):
            db.execute("UPDATE projects SET status='completed' WHERE id=%s", (pid,))
            log(db, pid, 'orchestrator', 'All tasks are done. Project completed.')
            push.queue(db, project['title'], '✓ Project completed: all tasks are done.', push.project_url(pid), f'project-{pid}')
            continue
        # 3. Budget and run limit.
        spent, run_count = usage(db, pid)
        costs.budget_warning(db, project, spent)  # once at 80% per budget value
        if spent >= float(project['budget_usd']) or run_count >= project['max_runs']:
            why = (f'Budget or run limit reached (${spent:.2f} of ${float(project["budget_usd"]):.2f}, '
                   f'{run_count} of {project["max_runs"]} runs). Raise it with Limits…, then Resume.')
            db.execute("UPDATE projects SET status='paused',pause_reason=%s WHERE id=%s", (why, pid))
            log(db, pid, 'orchestrator', 'Paused: ' + why)
            push.queue(db, project['title'], 'Paused: budget or run limit reached.', push.project_url(pid), f'project-{pid}')
            continue
        # 4. Start ready tasks, up to `parallel` at once; gated tasks wait for a human.
        done_keys = {t['key'] for t in tasks if t['status'] in ('done', 'skipped')}
        active = sum(1 for t in tasks if t['status'] in ('running', 'needs_you'))
        agents_by_id = {a['id']: a['name'] for a in db.execute('SELECT id,name FROM agents').fetchall()}
        notes = '\n'.join(f'- {n["actor"]}: {n["text"]}' for n in db.execute(
            "SELECT actor,text FROM project_log WHERE project_id=%s AND actor NOT IN ('orchestrator','planner') ORDER BY id DESC LIMIT 8",
            (pid,)).fetchall())
        for task in tasks:
            if active >= project['parallel']:
                break
            if task['status'] != 'pending' or not set(task['depends_on']) <= done_keys:
                continue
            if task['gate'] and not task['gate_approved_by']:
                db.execute("UPDATE project_tasks SET status='waiting_gate',updated_at=NOW() WHERE id=%s", (task['id'],))
                log(db, pid, 'orchestrator', f'"{task["title"]}" needs your approval before it starts.')
                continue
            thread = None
            if task['thread_id']:
                thread = db.execute('SELECT * FROM agent_threads WHERE id=%s FOR UPDATE', (task['thread_id'],)).fetchone()
            if thread is None:
                tid = uuid.uuid4()
                db.execute('INSERT INTO agent_threads(id,title,workspace,agent_id,project_id) VALUES (%s,%s,%s,%s,%s)',
                           (tid, (project['title'] + ': ' + task['title'])[:160], project['workspace'], task['agent_id'], pid))
                db.execute('UPDATE project_tasks SET thread_id=%s WHERE id=%s', (tid, task['id']))
                thread = db.execute('SELECT * FROM agent_threads WHERE id=%s FOR UPDATE', (tid,)).fetchone()
            if thread['status'] != 'idle':
                continue
            if task.get('wrap_up') and not (task.get('retry_note') or '').startswith('FINAL WRAP-UP TURN'):
                # The wrap-up flag is stored; the note is rebuilt from it when the task could not start in the pass that
                # flagged it (e.g. no free parallel slot). Set before the model choice: a wrap-up turn is a continuation.
                task['retry_note'] = WRAP_UP_NOTE.format(n=SPIN_CONTINUATIONS)
            prompt = task_prompt(project, task, tasks, notes, agents_by_id, task.get('retry_note') or (
                ('Your previous attempt ended with: ' + task['result'][:1500]) if task['result'] and task['attempts'] else None))
            launched = launch(db, thread, prompt, {'max_output_tokens': None, 'model_override': models.for_project_task(db, project, task)},
                              kind='project_task',
                              opts={'approved_risk': True, 'tool_budget': project_tool_budget()})
            db.execute("UPDATE project_tasks SET status='running',attempts=attempts+1,updated_at=NOW() WHERE id=%s", (task['id'],))
            log(db, pid, 'orchestrator', f'Started "{task["title"]}" ({agents_by_id.get(task["agent_id"], "agent")}).')
            started.append(launched)
            active += 1
    return started


async def tick():
    lock = _tick_locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
    async with lock:  # the loop and run-finished hooks must not start the same task twice
        def work():
            with _deps['connect']() as db:
                launched = orchestrate(db, _deps['launch'])
                db.commit()
                return launched
        for launched in await asyncio.to_thread(work):
            _deps['start'](launched)


async def loop():
    while True:
        try:
            await tick()
        except Exception:
            pass  # the next pass retries
        await asyncio.sleep(ORCHESTRATE_SECONDS)


def may_continue(db, thread):
    """Automatic continuation inside a project task only while its project runs and is within budget."""
    if not thread.get('project_id'):
        return True
    project = db.execute('SELECT status,budget_usd,max_runs FROM projects WHERE id=%s', (thread['project_id'],)).fetchone()
    if project and project['status'] == 'completed':
        return True  # the project is finished: its task threads are the user's own conversations now
    if not project or project['status'] != 'running':
        return False
    spent, run_count = usage(db, thread['project_id'])
    if spent >= float(project['budget_usd']) or run_count >= project['max_runs']:
        return False
    # HLA_SPIN_V1: a wrap-up turn is one turn, and a task that stopped making progress goes back to the orchestrator
    # (which gives it the wrap-up turn) instead of using up its automatic continuations.
    task = db.execute('SELECT id,thread_id,spin_since,wrap_up FROM project_tasks WHERE thread_id=%s', (thread['id'],)).fetchone()
    return not task or (not task['wrap_up'] and spin_count(task_runs(db, task)) < SPIN_CONTINUATIONS)


def checkpoint_continues(db, thread, reply):
    """A reply that stopped on its own with a CHECKPOINT (tool budget left) and asks the human nothing continues
    automatically in the user's own conversations: threads outside a project, or of a completed project. A running
    project's tasks are continued by the orchestrator instead."""
    if evaluate(reply)[0] != 'checkpoint' or needs_human(reply):
        return False
    if not thread.get('project_id'):
        return True
    project = db.execute('SELECT status FROM projects WHERE id=%s', (thread['project_id'],)).fetchone()
    return bool(project) and project['status'] == 'completed'


def after_run(thread_id):
    """A run finished: let the orchestrator react now instead of at its next pass."""
    try:
        asyncio.get_running_loop().create_task(tick())
    except RuntimeError:
        pass


# ---------------------------------------------------------------- API

HOST_PATTERN = r'^$|^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$'


# ---------------------------------------------------------------- project actions (the API and the Chief, chief.py)

def create(connect, title, goal, actor, *, workspace='home', planner_agent_id=None, budget_usd=2.0, max_runs=40, parallel=2,
           merge_policy='direct', staging_domain=None, production_domain=None, attachment_ids=()):
    """Insert a project in planning (the caller starts plan(pid)); returns its id."""
    pid = uuid.uuid4()
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40] or 'project'
    workdir = f'/home/svc-hla-agent/projects/{slug}-{str(pid)[:6]}'
    with connect() as db:
        if planner_agent_id and not db.execute('SELECT 1 FROM agents WHERE id=%s AND workspace=%s',
                                               (planner_agent_id, workspace)).fetchone():
            raise HTTPException(422, 'Planner agent must be in the project workspace')
        db.execute('''INSERT INTO projects(id,title,goal,workspace,planner_agent_id,budget_usd,max_runs,parallel,workdir,created_by,merge_policy,
            staging_domain,production_domain) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (pid, title.strip(), goal.strip(), workspace,
            planner_agent_id, budget_usd, max_runs, parallel, workdir, actor, merge_policy, staging_domain or None, production_domain or None))
        files = attachments.check_staged(db, list(attachment_ids or []))
        attachments.link(db, [f['id'] for f in files], project_id=pid)
        log(db, pid, actor, 'Project created; planning.' + (f' {len(files)} reference file(s) attached.' if files else ''))
        db.commit()
    return pid


def set_status(connect, pid, allowed, new, message, actor):
    """Change a project's status (409 when it is not in `allowed`); returns its running/waiting task threads."""
    with connect() as db:
        project = db.execute('SELECT * FROM projects WHERE id=%s FOR UPDATE', (pid,)).fetchone()
        if not project:
            raise HTTPException(404, 'Project not found')
        if project['status'] not in allowed:
            raise HTTPException(409, f'Project is {project["status"]}')
        if new == 'running' and project['status'] == 'paused':
            # Resuming over the limits would pause again at once: say so instead.
            spent, run_count = usage(db, pid)
            if spent >= float(project['budget_usd']) or run_count >= project['max_runs']:
                raise HTTPException(409, f'The project is at its limit (${spent:.2f} of ${float(project["budget_usd"]):.2f}, '
                                         f'{run_count} of {project["max_runs"]} runs). Raise it with Limits… first.')
        db.execute('UPDATE projects SET status=%s,pause_reason=%s WHERE id=%s',
                   (new, ('Paused by ' + actor + '.') if new == 'paused' else None, pid))
        log(db, pid, actor, message)
        db.commit()
        return db.execute('SELECT thread_id FROM project_tasks WHERE project_id=%s AND status IN (%s,%s)',
                          (pid, 'running', 'needs_you')).fetchall()


async def control(connect, pid, action, actor):
    """pause | resume | cancel, as the project page's buttons; returns the new status."""
    if action == 'resume':
        await asyncio.to_thread(set_status, connect, pid, ('paused',), 'running', 'Resumed.', actor)
        await tick()
        return 'running'
    if action == 'pause':
        await asyncio.to_thread(set_status, connect, pid, ('running',), 'paused',
                                'Paused. Running tasks finish their current reply.', actor)
        return 'paused'
    if action != 'cancel':
        raise HTTPException(422, 'Unknown action')
    await asyncio.to_thread(set_status, connect, pid, ('planning', 'plan_failed', 'awaiting_approval', 'running', 'paused'),
                            'cancelled', 'Cancelled; running tasks were stopped.', actor)

    def stop_threads():
        with connect() as db:
            ids = [r['current_task_id'] for r in db.execute(
                "SELECT current_task_id FROM agent_threads WHERE project_id=%s AND status='running'", (pid,)).fetchall()]
            db.execute("UPDATE project_tasks SET status='cancelled' WHERE project_id=%s AND status NOT IN ('done','skipped')", (pid,))
            db.commit()
            return ids
    for task_id in await asyncio.to_thread(stop_threads):
        runs.cancel(task_id)
    return 'cancelled'


class ProjectIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    goal: str = Field(min_length=10, max_length=50000)
    workspace: str = Field(default='home', pattern='^(home|work)$')
    planner_agent_id: uuid.UUID | None = None
    budget_usd: float = Field(default=2.0, ge=0.05, le=100)
    max_runs: int = Field(default=40, ge=1, le=500)
    parallel: int = Field(default=2, ge=1, le=4)
    # Direct merges by default: PR mode needs a way for the sandbox to push branches (its deploy key is read-only and the
    # gateway has no push yet), and a write key on the sandbox lets agents push any branch, main included.
    merge_policy: str = Field(default='direct', pattern='^(pr|direct)$')  # pull requests (opt-in), or merge into main directly
    # HLA_ROUTES_V1: optional public addresses (full host names, e.g. shop-staging.example.com).
    staging_domain: str | None = Field(default=None, max_length=253, pattern=HOST_PATTERN)
    production_domain: str | None = Field(default=None, max_length=253, pattern=HOST_PATTERN)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)  # HLA_ATTACHMENTS_V1: staged reference files


class ProjectPatch(BaseModel):
    budget_usd: float | None = Field(default=None, ge=0.05, le=1000)  # totals grow with every round
    max_runs: int | None = Field(default=None, ge=1, le=5000)
    parallel: int | None = Field(default=None, ge=1, le=4)
    merge_policy: str | None = Field(default=None, pattern='^(pr|direct)$')  # only while no task runs
    staging_domain: str | None = Field(default=None, max_length=253, pattern=HOST_PATTERN)  # '' clears it
    production_domain: str | None = Field(default=None, max_length=253, pattern=HOST_PATTERN)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    acceptance: str | None = Field(default=None, max_length=1000)
    agent_id: uuid.UUID | None = None
    gate: bool | None = None
    review: bool | None = None


class RoundIn(BaseModel):
    request: str = Field(min_length=10, max_length=50000)
    budget_usd: float = Field(default=2.0, ge=0.05, le=100)   # for this round, on top of what is spent
    max_runs: int = Field(default=40, ge=1, le=500)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=3, max_length=20000)
    source: str | None = Field(default=None, max_length=500)


def build_router(connect, dashboard_read, dashboard_write, launch, start):
    _deps.update(connect=connect, launch=launch, start=start)
    router = APIRouter(prefix='/ui/api/projects', tags=['projects'])

    def project_row(db, pid, lock=False):
        row = db.execute('SELECT * FROM projects WHERE id=%s' + (' FOR UPDATE' if lock else ''), (pid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Project not found')
        return row

    @router.get('')
    def list_projects(request: Request):
        dashboard_read(request)
        with connect() as db:
            rows = db.execute('''SELECT p.id,p.title,p.status,p.workspace,p.created_at,p.updated_at,p.budget_usd,
                (SELECT count(*) FROM project_tasks t WHERE t.project_id=p.id)::int AS task_count,
                (SELECT count(*) FROM project_tasks t WHERE t.project_id=p.id AND t.status IN ('done','skipped'))::int AS done_count,
                (SELECT count(*) FROM project_tasks t WHERE t.project_id=p.id AND t.status IN ('waiting_gate','needs_you','failed','blocked'))::int AS attention,
                (SELECT COALESCE(SUM(openrouter_cost_usd),0) FROM tasks k WHERE k.project_id=p.id) AS spent
                FROM projects p ORDER BY p.updated_at DESC LIMIT 100''').fetchall()
        return {'projects': rows}

    @router.post('')
    async def create_project(body: ProjectIn, request: Request):
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        pid = await asyncio.to_thread(create, connect, body.title, body.goal, actor, **body.model_dump(exclude={'title', 'goal'}))
        runs.spawn(plan(pid))
        return {'id': pid, 'status': 'planning'}

    @router.get('/{pid}')
    def get_project(pid: uuid.UUID, request: Request):
        dashboard_read(request)
        with connect() as db:
            project = project_row(db, pid)
            tasks = db.execute('''SELECT t.*,a.name AS agent_name,th.status AS thread_status,th.current_task_id
                FROM project_tasks t LEFT JOIN agents a ON a.id=t.agent_id LEFT JOIN agent_threads th ON th.id=t.thread_id
                WHERE t.project_id=%s ORDER BY t.seq''', (pid,)).fetchall()
            entries = db.execute('SELECT created_at,actor,text FROM project_log WHERE project_id=%s ORDER BY id DESC LIMIT 60', (pid,)).fetchall()
            rounds = db.execute('SELECT phase,request,summary,created_by,created_at FROM project_phases WHERE project_id=%s ORDER BY phase',
                                (pid,)).fetchall()
            spent, run_count = usage(db, pid)
            agents = _agents(db, project['workspace'])
            prs = project_prs(db, pid)
            db.commit()  # refreshed PR states
        for t in tasks:  # shown on the task card
            t['prs'] = [{k: r[k] for k in ('url', 'repo', 'number', 'state', 'title', 'checks')} for r in prs if r['task_id'] == t['id']]
        access = agent_access({t['agent_id'] for t in tasks if t['agent_id']}, project['workspace'])
        return {'project': project, 'tasks': tasks, 'log': entries, 'rounds': rounds, 'spent': spent, 'runs': run_count,
                'agents': [{'id': a['id'], 'name': a['name']} for a in agents], 'access': access,
                'prs': [{k: r[k] for k in ('url', 'repo', 'number', 'state', 'title', 'checks', 'task_id')} for r in prs]}

    async def change_status(request, pid, allowed, new, message, *, approver=False):
        dashboard_write(request)
        actor = approvals.require_approver(request) if approver else (approvals.approver_status(request)['actor'] or 'dashboard_session')
        rows = await asyncio.to_thread(set_status, connect, pid, allowed, new, message, actor)
        return actor, rows

    @router.post('/{pid}/approve')
    async def approve_plan(pid: uuid.UUID, request: Request):
        actor, _ = await change_status(request, pid, ('awaiting_approval',), 'running', 'Plan approved; starting.', approver=True)
        await tick()
        return {'status': 'running', 'actor': actor}

    def actor_of(request):
        dashboard_write(request)
        return approvals.approver_status(request)['actor'] or 'dashboard_session'

    @router.post('/{pid}/resume')
    async def resume(pid: uuid.UUID, request: Request):
        return {'status': await control(connect, pid, 'resume', actor_of(request))}

    @router.post('/{pid}/pause')
    async def pause(pid: uuid.UUID, request: Request):
        return {'status': await control(connect, pid, 'pause', actor_of(request))}

    @router.post('/{pid}/cancel')
    async def cancel(pid: uuid.UUID, request: Request):
        return {'status': await control(connect, pid, 'cancel', actor_of(request))}

    @router.post('/{pid}/continue')
    async def next_round(pid: uuid.UUID, body: RoundIn, request: Request):
        """A completed project takes new work: a new round is planned on top of the finished tasks."""
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'

        def work():
            with connect() as db:
                project = project_row(db, pid, lock=True)
                if project['status'] != 'completed':
                    raise HTTPException(409, 'New work can be added once the project is completed')
                phase = project['phase'] + 1
                spent, run_count = usage(db, pid)
                db.execute('INSERT INTO project_phases(project_id,phase,request,created_by) VALUES (%s,%s,%s,%s)',
                           (pid, phase, body.request.strip(), actor))
                db.execute('''UPDATE projects SET phase=%s,status='planning',error=NULL,pause_reason=NULL,budget_usd=%s,max_runs=%s,
                    updated_at=NOW() WHERE id=%s''', (phase, round(spent + body.budget_usd, 4), run_count + body.max_runs, pid))
                log(db, pid, actor, f'Round {phase}: ' + ' '.join(body.request.split())[:200]
                    + f' (budget +${body.budget_usd:.2f}, +{body.max_runs} runs); planning.')
                db.commit()
                return phase
        phase = await asyncio.to_thread(work)
        runs.spawn(plan(pid))
        return {'status': 'planning', 'phase': phase}

    @router.post('/{pid}/replan')
    async def replan(pid: uuid.UUID, request: Request, body: Note | None = None):
        await change_status(request, pid, ('plan_failed', 'awaiting_approval'), 'planning',
                            'Re-planning' + (': ' + body.text if body else '.'))
        runs.spawn(plan(pid, body.text if body else None))
        return {'status': 'planning'}

    @router.patch('/{pid}')
    def update_limits(pid: uuid.UUID, body: ProjectPatch, request: Request):
        dashboard_write(request)
        with connect() as db:
            project = project_row(db, pid, lock=True)
            if body.merge_policy is not None and body.merge_policy != project['merge_policy'] and (project['status'] == 'running'
                    or db.execute("SELECT 1 FROM project_tasks WHERE project_id=%s AND status IN ('running','needs_you')", (pid,)).fetchone()):
                raise HTTPException(409, 'The merge policy can be changed while no task is running (pause the project first)')
            for field in ('budget_usd', 'max_runs', 'parallel', 'merge_policy', 'staging_domain', 'production_domain'):
                value = getattr(body, field)
                if value is not None:
                    db.execute(f'UPDATE projects SET {field}=%s WHERE id=%s', (value or None, pid))
            log(db, pid, approvals.approver_status(request)['actor'] or 'dashboard_session',
                'Settings changed: ' + ', '.join(f'{k}={v}' for k, v in body.model_dump().items() if v is not None))
            db.commit()
        return {'id': pid}

    @router.patch('/{pid}/tasks/{task_id}')
    def edit_task(pid: uuid.UUID, task_id: uuid.UUID, body: TaskPatch, request: Request):
        dashboard_write(request)
        with connect() as db:
            if project_row(db, pid)['status'] not in ('awaiting_approval', 'paused'):
                raise HTTPException(409, 'Tasks can be edited while the plan awaits approval or the project is paused')
            task = db.execute('SELECT * FROM project_tasks WHERE id=%s AND project_id=%s', (task_id, pid)).fetchone()
            if not task:
                raise HTTPException(404, 'Task not found')
            if task['phase'] < project_row(db, pid)['phase']:
                raise HTTPException(409, 'Tasks of an earlier round cannot be changed')
            fields = {k: v for k, v in body.model_dump().items() if v is not None}
            for key, value in fields.items():
                db.execute(f'UPDATE project_tasks SET {key}=%s,updated_at=NOW() WHERE id=%s AND project_id=%s', (value, task_id, pid))
            if 'agent_id' in fields and fields['agent_id'] != task['agent_id'] and task['thread_id']:
                # A new agent starts in a fresh conversation (the old one belongs to the previous agent);
                # the previous result still reaches it as "previous attempt".
                db.execute('UPDATE project_tasks SET thread_id=NULL WHERE id=%s', (task_id,))
                name = (db.execute('SELECT name FROM agents WHERE id=%s', (fields['agent_id'],)).fetchone() or {}).get('name', 'another agent')
                log(db, pid, approvals.approver_status(request)['actor'] or 'dashboard_session', f'"{task["title"]}" reassigned to {name}.')
            db.commit()
        return {'id': task_id}

    @router.delete('/{pid}/tasks/{task_id}')
    def delete_task(pid: uuid.UUID, task_id: uuid.UUID, request: Request):
        dashboard_write(request)
        with connect() as db:
            if project_row(db, pid)['status'] != 'awaiting_approval':
                raise HTTPException(409, 'Tasks can be removed while the plan awaits approval')
            row = db.execute('DELETE FROM project_tasks WHERE id=%s AND project_id=%s AND phase=%s RETURNING key',
                             (task_id, pid, project_row(db, pid)['phase'])).fetchone()
            if row:  # nothing may wait for a removed task
                db.execute('UPDATE project_tasks SET depends_on=array_remove(depends_on,%s) WHERE project_id=%s', (row['key'], pid))
            db.commit()
        return {'deleted': task_id}

    @router.post('/{pid}/tasks/{task_id}/{action}')
    async def task_action(pid: uuid.UUID, task_id: uuid.UUID, action: str, request: Request):
        if action not in ('retry', 'skip', 'approve_gate', 'redo'):
            raise HTTPException(404, 'Unknown action')
        note = ''
        if action in ('redo', 'retry'):
            try: note = str((await request.json() or {}).get('note') or '').strip()[:4000]
            except (ValueError, AttributeError): note = ''
        dashboard_write(request)
        actor = approvals.require_approver(request) if action == 'approve_gate' else (
            approvals.approver_status(request)['actor'] or 'dashboard_session')

        def work():
            with connect() as db:
                project = project_row(db, pid, lock=True)
                task = db.execute('SELECT * FROM project_tasks WHERE id=%s AND project_id=%s FOR UPDATE', (task_id, pid)).fetchone()
                if not task:
                    raise HTTPException(404, 'Task not found')
                if action == 'approve_gate':
                    if task['status'] != 'waiting_gate':
                        raise HTTPException(409, 'This task is not waiting for approval')
                    db.execute("UPDATE project_tasks SET gate_approved_by=%s,status='pending' WHERE id=%s", (actor, task_id))
                    log(db, pid, actor, f'Approved "{task["title"]}" to start.')
                elif action == 'retry':
                    if task['status'] not in ('failed', 'blocked', 'needs_you', 'cancelled'):
                        raise HTTPException(409, 'Only failed, blocked, cancelled or waiting tasks can be retried')
                    if task['review'] and task['status'] == 'failed' and task['result']:
                        # A review that still asks for changes: one more round (redo what it checks, then review again).
                        tasks = db.execute('SELECT * FROM project_tasks WHERE project_id=%s ORDER BY seq', (pid,)).fetchall()
                        send_back(db, pid, task, tasks, task['result'], actor)
                    elif note:
                        # The note goes first so it survives the prompt's cut of the previous result.
                        result = ('Retried by ' + actor + ' with this note:\n' + note
                                  + '\n\nYour previous result:\n' + (task['result'] or '')[:2000])
                        db.execute("UPDATE project_tasks SET status='pending',result=%s,retried_at_attempt=attempts,spin_since=NOW(),wrap_up=FALSE,updated_at=NOW() WHERE id=%s",
                                   (result, task_id))
                        log(db, pid, actor, f'Retrying "{task["title"]}": ' + note[:200])
                    else:
                        db.execute("UPDATE project_tasks SET status='pending',retried_at_attempt=attempts,spin_since=NOW(),wrap_up=FALSE WHERE id=%s", (task_id,))
                        log(db, pid, actor, f'Retrying "{task["title"]}".')
                elif action == 'redo':
                    if task['status'] != 'done':
                        raise HTTPException(409, 'Only finished tasks can be sent back')
                    result = ('Sent back for rework by ' + actor + (':\n' + note if note else '.')
                              + '\n\nYour previous handoff:\n' + (task['result'] or '')[:2000])
                    db.execute("UPDATE project_tasks SET status='pending',result=%s,retried_at_attempt=attempts,spin_since=NOW(),wrap_up=FALSE,updated_at=NOW() WHERE id=%s",
                               (result, task_id))
                    log(db, pid, actor, f'Sent "{task["title"]}" back for rework' + (': ' + note[:200] if note else '.'))
                else:
                    if task['status'] in ('running', 'done'):
                        raise HTTPException(409, 'A running or finished task cannot be skipped')
                    db.execute("UPDATE project_tasks SET status='skipped' WHERE id=%s", (task_id,))
                    log(db, pid, actor, f'Skipped "{task["title"]}".')
                if action != 'approve_gate' and (project['status'] == 'paused' or action == 'redo' and project['status'] == 'completed'):
                    db.execute("UPDATE projects SET status='running',pause_reason=NULL WHERE id=%s", (pid,))
                db.commit()
        await asyncio.to_thread(work)
        await tick()
        return {'id': task_id, 'action': action}

    # HLA_KB_V2: project knowledge. Tasks and the planner of this project get it first in their context.
    @router.get('/{pid}/knowledge')
    def list_project_knowledge(pid: uuid.UUID, request: Request, q: str = ''):
        dashboard_read(request)
        with connect() as db:
            project_row(db, pid)
            return {'entries': kb.list_entries(db, project=str(pid), q=q)}

    @router.post('/{pid}/knowledge')
    def add_project_knowledge(pid: uuid.UUID, body: KnowledgeIn, request: Request):
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        ident = uuid.uuid4()
        with connect() as db:
            project = project_row(db, pid)
            db.execute('''INSERT INTO knowledge(id,workspace,title,body,source,project_id,origin,author)
                VALUES (%s,%s,%s,%s,%s,%s,'human',%s)''', (ident, project['workspace'], body.title.strip(), body.body.strip(),
                body.source, pid, actor))
            db.commit()
        return {'id': ident, 'status': 'created'}

    @router.put('/{pid}/knowledge/{kid}')
    def edit_project_knowledge(pid: uuid.UUID, kid: uuid.UUID, body: KnowledgeIn, request: Request):
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        with connect() as db:
            project_row(db, pid)
            # Edited by the human: theirs from now on, so a redone task no longer overwrites it.
            row = db.execute('''UPDATE knowledge SET title=%s,body=%s,source=%s,origin='human',author=%s,updated_at=NOW()
                WHERE id=%s AND project_id=%s RETURNING id''', (body.title.strip(), body.body.strip(), body.source, actor, kid, pid)).fetchone()
            db.commit()
        if not row:
            raise HTTPException(404, 'Knowledge entry not found in this project')
        return {'id': kid, 'status': 'updated'}

    @router.delete('/{pid}/knowledge/{kid}')
    def delete_project_knowledge(pid: uuid.UUID, kid: uuid.UUID, request: Request):
        dashboard_write(request)
        with connect() as db:
            row = db.execute('DELETE FROM knowledge WHERE id=%s AND project_id=%s RETURNING id', (kid, pid)).fetchone()
            db.commit()
        if not row:
            raise HTTPException(404, 'Knowledge entry not found in this project')
        return {'status': 'deleted'}

    @router.post('/{pid}/notes')
    def add_note(pid: uuid.UUID, body: Note, request: Request):
        dashboard_write(request)
        with connect() as db:
            project_row(db, pid)
            log(db, pid, approvals.approver_status(request)['actor'] or 'you', body.text.strip())
            db.commit()
        return {'ok': True}

    return router
