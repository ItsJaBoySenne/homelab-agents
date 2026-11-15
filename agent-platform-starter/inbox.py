"""The "Needs you" inbox and conversation housekeeping (HLA_INBOX_V1).

GET /ui/api/inbox lists everything that waits for the human, most urgent kind first and newest first within it:
- `command`: a shell command a conversation reply asked to run (approve or reject it in the conversation)
- `approval`: a request the risk review paused, in a conversation or as a task launched from Overview
  (more than two such tasks are one item with `count` and `task_ids`)
- `plan`: a project plan waiting for review, or a plan that failed
- `gate`: a project task that needs approval to start
- `blocked` / `failed`: a project task that stopped and needs a decision
- `paused`: a project paused by a limit or by hand
- `pr`: a pull request still open on a project whose tasks are all done ("PR ready to merge", links to GitHub)
- `website`: a production website on public-website that is down at the last check (websites.py)
- `route_request`: an agent asked for a public route (domain/subdomain); Approve creates it through the gateway (public_routes.py)
A project task that waits for an approval in its own conversation shows up once, as that conversation's item.

PATCH /ui/api/threads/{id} renames or (un)archives a conversation; DELETE removes it with its messages and
schedules (the tasks it ran stay in Task history). Project conversations stay with their project.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import approvals


def migrate(db):
    db.execute('ALTER TABLE agent_threads ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ')


class ThreadPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None


def _snippet(text, n=160):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def collect(db):
    """Items waiting for the human, newest kinds first: what blocks work right now comes before reviews."""
    approvals.expire_stale(db)
    items = []
    # Conversations: a pending command card or a paused (risk) request, in any conversation (also archived ones).
    rows = db.execute('''SELECT t.id AS thread_id,t.title AS thread_title,t.project_id,p.title AS project_title,
            m.kind,k.id AS task_id,k.created_at,k.pending_command,k.risk_probability,k.task_text
        FROM agent_thread_messages m JOIN tasks k ON k.id=m.task_id JOIN agent_threads t ON t.id=m.thread_id
        LEFT JOIN projects p ON p.id=t.project_id
        WHERE (m.kind='approval_request' AND k.status='human_approval_required')
           OR (m.kind IS DISTINCT FROM 'approval_request' AND m.role='assistant' AND k.pending_command->>'status'='pending')
        ORDER BY k.created_at''').fetchall()
    seen = set()
    for r in rows:
        key = (r['task_id'], r['kind'] == 'approval_request')
        if key in seen:
            continue
        seen.add(key)
        where = (r['project_title'] + ' · ' if r['project_title'] else '') + r['thread_title']
        if r['kind'] == 'approval_request':
            items.append({'kind': 'approval', 'title': 'Request paused by the risk review', 'context': where,
                          'detail': _snippet(str(r['task_text']).split('LATEST USER MESSAGE:')[-1]),
                          'risk': r['risk_probability'], 'since': r['created_at'], 'task_id': r['task_id'],
                          'thread_id': r['thread_id'], 'project_id': r['project_id']})
        else:
            cmd = r['pending_command'] or {}
            title = ('Proxmox request waiting for approval' if cmd.get('kind') == 'proxmox'  # HLA_PROXMOX_V1
                     else 'Omada change waiting for approval' if cmd.get('kind') == 'omada'  # HLA_OMADA_V1
                     else 'Command waiting for approval') + (' on ' + cmd['server'] if cmd.get('server') else '')
            items.append({'kind': 'command', 'title': title,
                          'context': where, 'detail': _snippet(cmd.get('command'), 200), 'since': r['created_at'],
                          'task_id': r['task_id'], 'thread_id': r['thread_id'], 'project_id': r['project_id']})
    # Tasks launched outside a conversation (Overview, API) that wait for a decision.
    # More than two become one item that opens Task history filtered on them (they are often old and alike).
    solo = db.execute('''SELECT k.id,k.task_text,k.created_at,k.risk_probability FROM tasks k
            WHERE k.status='human_approval_required'
              AND NOT EXISTS (SELECT 1 FROM agent_thread_messages m WHERE m.task_id=k.id)
            ORDER BY k.created_at DESC LIMIT 200''').fetchall()
    if len(solo) > 2:
        items.append({'kind': 'approval', 'title': f'{len(solo)} tasks paused by the risk review', 'context': 'Task history',
                      'detail': 'Newest: ' + _snippet(solo[0]['task_text'], 120), 'since': solo[0]['created_at'],
                      'task_ids': [r['id'] for r in solo], 'count': len(solo)})
    else:
        for r in solo:
            items.append({'kind': 'approval', 'title': 'Task paused by the risk review', 'context': 'Task history',
                          'detail': _snippet(r['task_text']), 'risk': r['risk_probability'], 'since': r['created_at'], 'task_id': r['id']})
    # Projects: plans to review, failed plans, paused projects.
    for p in db.execute('''SELECT id,title,status,pause_reason,error,updated_at FROM projects
            WHERE status IN ('awaiting_approval','plan_failed','paused') ORDER BY updated_at''').fetchall():
        if p['status'] == 'awaiting_approval':
            n = db.execute("SELECT count(*) AS n FROM project_tasks WHERE project_id=%s AND status='pending'", (p['id'],)).fetchone()['n']
            items.append({'kind': 'plan', 'title': 'Plan ready for review', 'context': p['title'],
                          'detail': f'{n} task{"" if n == 1 else "s"} planned. Nothing runs until you approve.', 'since': p['updated_at'],
                          'project_id': p['id']})
        elif p['status'] == 'plan_failed':
            items.append({'kind': 'plan', 'title': 'Planning failed', 'context': p['title'], 'detail': _snippet(p['error'] or 'Re-plan to try again.'),
                          'since': p['updated_at'], 'project_id': p['id'], 'failed': True})
        else:
            items.append({'kind': 'paused', 'title': 'Project paused', 'context': p['title'],
                          'detail': _snippet(p['pause_reason'] or 'Resume it when you are ready.'), 'since': p['updated_at'], 'project_id': p['id']})
    # Project tasks that wait for a decision (needs_you is the conversation item above).
    for t in db.execute('''SELECT t.id,t.title,t.status,t.result,t.updated_at,t.thread_id,p.id AS project_id,p.title AS project_title
            FROM project_tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.status IN ('waiting_gate','blocked','failed') AND p.status NOT IN ('cancelled','completed')
            ORDER BY t.updated_at''').fetchall():
        kind = 'gate' if t['status'] == 'waiting_gate' else t['status']
        title = {'gate': 'Task needs approval to start', 'blocked': 'Task blocked: needs you', 'failed': 'Task stopped'}[kind]
        detail = t['title'] if kind == 'gate' else t['title'] + (' · ' + _snippet(t['result'], 140) if t['result'] else '')
        items.append({'kind': kind, 'title': title, 'context': t['project_title'], 'detail': detail, 'since': t['updated_at'],
                      'project_id': t['project_id'], 'project_task_id': t['id'], 'thread_id': t['thread_id']})
    items.extend(open_prs(db))
    import websites  # HLA_WEBSITES_V1
    items.extend(websites.inbox_items(db))
    import public_routes  # HLA_ROUTES_V1
    items.extend(public_routes.inbox_items(db))
    order = {'command': 0, 'approval': 0, 'website': 1, 'gate': 1, 'route_request': 2, 'blocked': 2, 'failed': 2, 'plan': 3, 'pr': 3, 'paused': 4}
    # Most urgent kind first; within a kind the newest first (what you were just doing).
    items.sort(key=lambda i: (order[i['kind']], -(i['since'] or datetime.min.replace(tzinfo=timezone.utc)).timestamp()))
    return items


def open_prs(db):
    """HLA_GITHUB_V1: PRs to merge, once every task of their project is done. States are refreshed through the
    gateway at most once a minute per PR and for at most INBOX_PR_REFRESH_MAX PRs per request over all projects (each
    is a synchronous gateway call); the others show their stored state until a later request."""
    import projects  # late: projects pulls in the agent runtime
    rows = db.execute(f'''SELECT {projects.PR_COLUMNS},p.title AS project_title,p.updated_at AS project_updated_at
        FROM project_prs r JOIN projects p ON p.id=r.project_id LEFT JOIN project_tasks t ON t.id=r.task_id
        WHERE p.status<>'cancelled' AND r.state='open'
          AND NOT EXISTS (SELECT 1 FROM project_tasks x WHERE x.project_id=p.id AND x.status NOT IN ('done','skipped'))
        ORDER BY p.updated_at,r.id''', (projects.PR_REFRESH_SECONDS,)).fetchall()
    items = []
    for r in projects.refresh_prs(db, rows, limit=projects.INBOX_PR_REFRESH_MAX):
        if r['state'] == 'open':
            items.append({'kind': 'pr', 'title': 'PR ready to merge', 'context': r['project_title'],
                          'detail': f'PR #{r["number"]} · {r["repo"]}' + (' · ' + _snippet(r['title'], 100) if r['title'] else '')
                                    + (' · checks ' + r['checks'] if r['checks'] and r['checks'] != 'none' else ''),
                          'url': r['url'], 'since': r['project_updated_at'], 'project_id': r['project_id']})
    return items


def build_router(connect, dashboard_read, dashboard_write):
    router = APIRouter(prefix='/ui/api', tags=['inbox'])

    @router.get('/inbox')
    def inbox(request: Request):
        dashboard_read(request)
        with connect() as db:
            items = collect(db)
            db.commit()  # expire_stale may have expired old approvals
            running = db.execute("SELECT count(*) AS n FROM agent_threads WHERE status='running' AND project_id IS NULL").fetchone()['n']
        # count: things waiting (a grouped item counts each task in it)
        return {'items': items, 'count': sum(i.get('count', 1) for i in items), 'running_conversations': running}

    def thread_row(db, ident, lock=False):
        row = db.execute('SELECT * FROM agent_threads WHERE id=%s' + (' FOR UPDATE' if lock else ''), (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Conversation not found')
        return row

    @router.patch('/threads/{thread_id}')
    def update_thread(thread_id: uuid.UUID, body: ThreadPatch, request: Request):
        dashboard_write(request)
        if body.title is None and body.archived is None:
            raise HTTPException(422, 'Nothing to change')
        with connect() as db:
            row = thread_row(db, thread_id, lock=True)
            if body.title is not None:
                if not body.title.strip():
                    raise HTTPException(422, 'The title cannot be blank')
                db.execute('UPDATE agent_threads SET title=%s WHERE id=%s', (body.title.strip(), thread_id))
            if body.archived is not None:
                if body.archived and row['project_id']:
                    raise HTTPException(409, 'This conversation belongs to a project; it is listed on the project')
                db.execute('UPDATE agent_threads SET archived_at=' + ('NOW()' if body.archived else 'NULL') + ' WHERE id=%s', (thread_id,))
            row = db.execute('SELECT id,title,archived_at FROM agent_threads WHERE id=%s', (thread_id,)).fetchone()
            db.commit()
        return row

    @router.delete('/threads/{thread_id}')
    def delete_thread(thread_id: uuid.UUID, request: Request):
        dashboard_write(request)
        with connect() as db:
            row = thread_row(db, thread_id, lock=True)
            if row['project_id']:
                raise HTTPException(409, 'This conversation belongs to a project and cannot be deleted on its own')
            if row['status'] != 'idle':
                raise HTTPException(409, 'The agent is still replying. Stop the reply first, then delete the conversation.')
            # Messages and schedules go with it (ON DELETE CASCADE); the tasks it ran stay in Task history.
            db.execute('DELETE FROM agent_threads WHERE id=%s', (thread_id,))
            db.commit()
        return {'deleted': str(thread_id)}

    return router
