"""Re-check stale knowledge facts by running their diagnostic again (HLA_FACT_RECHECK_V1).

Facts captured from a gateway diagnostic (memory/store.py, key diag:<server>:<diagnostic>[:<target>]) record
everything needed to run the same check again: server, diagnostic name, target and the agent that ran it. A
re-check sends exactly that read-only diagnostic (level 1) to the gateway as that agent, so the gateway's policy
still decides. Success refreshes the fact (a changed value keeps the previous one); a failure leaves it stale
and records why. Other facts (changes, deploys, commands, Proxmox) are history or come from elsewhere and are
never re-run.

HLA_FACT_RECHECK=weekly runs it once a week (at most MAX_PER_RUN facts, oldest expiry first); any other value
leaves it off. "Re-check now" on the Knowledge page runs the same pass on demand.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

import approvals

from agent_tool.runtime import DIAGNOSTICS, TARGETED_DIAGNOSTICS, GATEWAY
from memory.store import DIAGNOSTIC_TTL, NOT_CAPTURED, capture_tool

MAX_PER_RUN = 20
WEEK = timedelta(days=7)
CHECK_SECONDS = 3600
# Read-only diagnostics that produce facts and can be run again from the fact alone.
RERUNNABLE = {n for n in (*DIAGNOSTICS, *TARGETED_DIAGNOSTICS) if n in DIAGNOSTIC_TTL and n not in NOT_CAPTURED}
_lock = asyncio.Lock()
_deps = {}


def enabled():
    return os.getenv('HLA_FACT_RECHECK', '').strip().lower() == 'weekly'


def migrate(db):
    db.execute('ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS rechecked_at TIMESTAMPTZ')
    db.execute('ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS recheck_outcome TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS fact_recheck_runs (id UUID PRIMARY KEY, trigger TEXT NOT NULL, actor TEXT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        checked INTEGER NOT NULL DEFAULT 0, confirmed INTEGER NOT NULL DEFAULT 0, changed INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0, note TEXT)''')
    db.execute('''UPDATE fact_recheck_runs SET finished_at=NOW(),note='interrupted by an API restart'
        WHERE finished_at IS NULL''')


def target_of(row):
    """(diagnostic, target or None) when this fact can be re-run safely, else None."""
    key = row.get('key') or ''
    if row.get('source') != 'ssh-gateway:diagnostic' or not key.startswith('diag:'):
        return None
    parts = key.split(':', 3)
    if len(parts) < 3 or parts[1] != row.get('server') or parts[1] == 'sandbox':
        return None
    name, target = parts[2], (parts[3] if len(parts) == 4 else None)
    if name not in RERUNNABLE or (name in TARGETED_DIAGNOSTICS) != bool(target):
        return None
    return name, target


ELIGIBLE = """workspace='home' AND source='ssh-gateway:diagnostic' AND kind='diagnostic' AND confidence='verified'
    AND key LIKE 'diag:%%' AND expires_at<=NOW()"""


def candidates(db, limit=MAX_PER_RUN, min_age=timedelta(hours=1)):
    """Stale diagnostic facts, oldest expiry first, skipping ones re-checked within min_age."""
    rows = db.execute('SELECT id,key,fact,source,server,agent_id,task_id FROM agent_memories WHERE ' + ELIGIBLE +
                      ' AND (rechecked_at IS NULL OR rechecked_at<%s) ORDER BY expires_at LIMIT %s',
                      (datetime.now(timezone.utc) - min_age, limit * 3)).fetchall()
    return [r for r in rows if target_of(r)][:limit]


def _mark(db, fact_id, outcome):
    db.execute('UPDATE agent_memories SET rechecked_at=NOW(),recheck_outcome=%s WHERE id=%s', (outcome[:200], fact_id))


async def recheck(connect, trigger='manual', actor=None, limit=MAX_PER_RUN, min_age=timedelta(hours=1), client=None):
    """One pass. Returns the run summary. Only one pass runs at a time (RuntimeError otherwise)."""
    if _lock.locked():
        raise RuntimeError('A re-check is already running')
    async with _lock:
        run_id = uuid.uuid4()
        with connect() as db:
            db.execute('INSERT INTO fact_recheck_runs(id,trigger,actor) VALUES (%s,%s,%s)', (run_id, trigger, actor))
            rows = candidates(db, limit, min_age)
            db.commit()
        counts = {'checked': 0, 'confirmed': 0, 'changed': 0, 'failed': 0}
        note = None
        token = os.getenv('TOOL_GATEWAY_TOKEN', '')
        if rows and len(token) < 32:
            note = 'TOOL_GATEWAY_TOKEN is not set'
            rows = []
        own = client is None
        client = client or httpx.AsyncClient()
        try:
            for row in rows:
                name, target = target_of(row)
                agent = row['agent_id'] or os.getenv('SSH_TOOL_AGENT_ID') or None
                counts['checked'] += 1
                if not agent:
                    outcome = 'failed: no agent recorded to run it as'
                else:
                    call = {'agent_id': str(agent), 'server': row['server'], 'mode': 'diagnostic', 'diagnostic': name,
                            **({'target': target} if target else {})}
                    outcome = await _run(client, token, call, row, connect)
                counts['confirmed' if outcome == 'confirmed' else 'changed' if outcome == 'changed' else 'failed'] += 1
                with connect() as db:
                    _mark(db, row['id'], outcome)
                    db.commit()
        finally:
            if own:
                await client.aclose()
            with connect() as db:
                db.execute('''UPDATE fact_recheck_runs SET finished_at=NOW(),checked=%s,confirmed=%s,changed=%s,failed=%s,note=%s
                    WHERE id=%s''', (counts['checked'], counts['confirmed'], counts['changed'], counts['failed'], note, run_id))
                db.commit()
        return {'id': str(run_id), 'trigger': trigger, **counts, 'note': note}


async def _run(client, token, call, row, connect):
    try:
        response = await client.post(GATEWAY + '/v1/execute', headers={'Authorization': 'Bearer ' + token}, json=call, timeout=65)
        reply = response.json()
    except (httpx.HTTPError, ValueError):
        return 'failed: gateway unreachable'
    if response.status_code != 200:
        return 'failed: ' + str(reply.get('error') or 'HTTP ' + str(response.status_code))[:80]
    if reply.get('timed_out'):
        return 'failed: timed out'
    if reply.get('exit_code') != 0:
        return 'failed: exit ' + str(reply.get('exit_code'))
    with connect() as db:
        key = capture_tool(db, 'home', call, reply, task_id=row['task_id'], agent_id=call['agent_id'])
        if key != row['key']:
            db.rollback()
            return 'failed: output not stored (empty or may contain a secret)'
        new = db.execute('SELECT fact FROM agent_memories WHERE workspace=%s AND key=%s', ('home', key)).fetchone()
        db.commit()
    return 'confirmed' if new and new['fact'] == row['fact'] else 'changed'


def status(db):
    last = db.execute('''SELECT trigger,actor,started_at,finished_at,checked,confirmed,changed,failed,note FROM fact_recheck_runs
        ORDER BY started_at DESC LIMIT 1''').fetchone()
    eligible = [r for r in db.execute('SELECT key,source,server FROM agent_memories WHERE ' + ELIGIBLE).fetchall() if target_of(r)]
    stale = db.execute("SELECT count(*) AS n FROM agent_memories WHERE workspace='home' AND confidence='verified' AND expires_at<=NOW() "
                       "AND kind IS DISTINCT FROM 'task'").fetchone()['n']
    return {'schedule': 'weekly' if enabled() else 'off', 'running': _lock.locked(), 'last_run': last,
            'eligible': len(eligible), 'stale': stale, 'max_per_run': MAX_PER_RUN}


def due(db, now=None):
    """The weekly pass is due when no scheduled pass finished in the last 7 days."""
    now = now or datetime.now(timezone.utc)
    last = db.execute("SELECT max(started_at) AS at FROM fact_recheck_runs WHERE trigger='weekly'").fetchone()['at']
    return last is None or last <= now - WEEK


async def loop():
    """HLA_FACT_RECHECK=weekly: check hourly whether a week passed since the last scheduled pass."""
    while True:
        try:
            if enabled() and not _lock.locked():
                with _deps['connect']() as db:
                    go = due(db)
                if go:
                    await recheck(_deps['connect'], 'weekly', min_age=timedelta(days=1))
        except Exception:
            pass  # the next check retries
        await asyncio.sleep(CHECK_SECONDS)


def build_router(connect, dashboard_read, dashboard_write):
    _deps['connect'] = connect
    router = APIRouter(prefix='/ui/api/facts', tags=['facts'])

    @router.get('/recheck')
    def recheck_status(request: Request):
        dashboard_read(request)
        with connect() as db:
            return status(db)

    @router.post('/recheck')
    async def recheck_now(request: Request):
        """Run a pass now. Waits up to 20 s for it; a longer pass continues and GET reports when it is done."""
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        if _lock.locked():
            raise HTTPException(409, 'A re-check is already running')
        run = asyncio.create_task(recheck(connect, 'manual', actor, min_age=timedelta(minutes=10)))
        _deps['last_task'] = run  # keep a reference while it runs
        try:
            return {'status': 'finished', **await asyncio.wait_for(asyncio.shield(run), 20)}
        except asyncio.TimeoutError:
            return {'status': 'started'}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
