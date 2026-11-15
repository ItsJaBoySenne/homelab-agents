"""Scheduled conversation runs (HLA_SCHEDULES_V1).

A schedule posts a fixed prompt into its conversation every N minutes, as if sent from the composer (marked
'scheduled'), so the reply, tool steps and any approval card appear in that conversation. The API is a single
process: one loop checks for due schedules every 30 s. A run that is due while the conversation is busy waits
for the next check; missed runs (API down) are not replayed, the schedule simply continues from now.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import approvals

INTERVALS = (60, 360, 720, 1440, 10080)  # hourly, 6 h, 12 h, daily, weekly
CHECK_SECONDS = 30


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS schedules (
        id UUID PRIMARY KEY, thread_id UUID NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
        prompt TEXT NOT NULL, interval_minutes INTEGER NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE,
        next_run_at TIMESTAMPTZ NOT NULL, last_run_at TIMESTAMPTZ, last_task_id UUID,
        created_by TEXT NOT NULL DEFAULT 'dashboard_session', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    db.execute('CREATE INDEX IF NOT EXISTS schedules_due_idx ON schedules(next_run_at) WHERE enabled')


class ScheduleIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    interval_minutes: int
    first_run_at: datetime | None = None   # default: one interval from now


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    run_now: bool | None = None


def run_due(connect, launch, now=None):
    """Launch every due schedule whose conversation is idle (database work only, safe in a worker thread).
    Returns the launched runs; the caller starts them on the event loop."""
    now = now or datetime.now(timezone.utc)
    started = []
    with connect() as db:
        due = db.execute('''SELECT s.*, t.status AS thread_status FROM schedules s JOIN agent_threads t ON t.id=s.thread_id
            WHERE s.enabled AND s.next_run_at<=%s ORDER BY s.next_run_at FOR UPDATE OF s SKIP LOCKED''', (now,)).fetchall()
        for schedule in due:
            thread = db.execute('SELECT * FROM agent_threads WHERE id=%s FOR UPDATE', (schedule['thread_id'],)).fetchone()
            if not thread or thread['status'] != 'idle':
                continue  # busy: try again at the next check
            launched = launch(db, thread, schedule['prompt'], {'max_output_tokens': None}, kind='scheduled')
            # The next run counts from the planned time; after downtime, from now (no burst of catch-up runs).
            nxt = schedule['next_run_at'] + timedelta(minutes=schedule['interval_minutes'])
            if nxt <= now:
                nxt = now + timedelta(minutes=schedule['interval_minutes'])
            db.execute('UPDATE schedules SET next_run_at=%s,last_run_at=%s,last_task_id=%s WHERE id=%s',
                       (nxt, now, launched[1], schedule['id']))
            started.append(launched)
        db.commit()
    return started


async def check(connect, launch, start):
    for launched in await asyncio.to_thread(run_due, connect, launch):
        start(launched)


async def loop(connect, launch, start):
    while True:
        try:
            await check(connect, launch, start)
        except Exception:
            pass  # a failing check must not stop scheduling; the next one retries
        await asyncio.sleep(CHECK_SECONDS)


def build_router(connect, dashboard_read, dashboard_write, launch, start):
    router = APIRouter(prefix='/ui/api', tags=['schedules'])

    @router.get('/threads/{thread_id}/schedules')
    def list_schedules(thread_id: uuid.UUID, request: Request):
        dashboard_read(request)
        with connect() as db:
            rows = db.execute('''SELECT s.*, k.status AS last_status FROM schedules s LEFT JOIN tasks k ON k.id=s.last_task_id
                WHERE s.thread_id=%s ORDER BY s.created_at''', (thread_id,)).fetchall()
        return {'schedules': rows, 'intervals': INTERVALS}

    @router.post('/threads/{thread_id}/schedules')
    def create_schedule(thread_id: uuid.UUID, body: ScheduleIn, request: Request):
        dashboard_write(request)
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        if body.interval_minutes not in INTERVALS:
            raise HTTPException(422, 'interval_minutes must be one of ' + ', '.join(map(str, INTERVALS)))
        now = datetime.now(timezone.utc)
        first = body.first_run_at or now + timedelta(minutes=body.interval_minutes)
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if not now - timedelta(minutes=5) <= first <= now + timedelta(days=30):
            raise HTTPException(422, 'first_run_at must be within the next 30 days')
        with connect() as db:
            if not db.execute('SELECT 1 FROM agent_threads WHERE id=%s', (thread_id,)).fetchone():
                raise HTTPException(404, 'Thread not found')
            if db.execute('SELECT count(*) AS n FROM schedules WHERE thread_id=%s', (thread_id,)).fetchone()['n'] >= 5:
                raise HTTPException(409, 'A conversation can have at most 5 schedules')
            ident = uuid.uuid4()
            db.execute('''INSERT INTO schedules(id,thread_id,prompt,interval_minutes,next_run_at,created_by)
                VALUES (%s,%s,%s,%s,%s,%s)''', (ident, thread_id, body.prompt.strip(), body.interval_minutes, first, actor))
            db.commit()
        return {'id': ident, 'next_run_at': first}

    @router.patch('/schedules/{schedule_id}')
    async def update_schedule(schedule_id: uuid.UUID, body: SchedulePatch, request: Request):
        dashboard_write(request)
        await asyncio.to_thread(_update, schedule_id, body)
        if body.run_now:
            await check(connect, launch, start)  # starts at once if the conversation is idle
        return {'id': schedule_id}

    def _update(schedule_id, body):
        with connect() as db:
            row = db.execute('SELECT * FROM schedules WHERE id=%s', (schedule_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Schedule not found')
            if body.enabled is not None:
                # Resuming a paused schedule starts counting from now, not from a time that passed meanwhile.
                nxt = row['next_run_at']
                if body.enabled and not row['enabled'] and nxt <= datetime.now(timezone.utc):
                    nxt = datetime.now(timezone.utc) + timedelta(minutes=row['interval_minutes'])
                db.execute('UPDATE schedules SET enabled=%s,next_run_at=%s WHERE id=%s', (body.enabled, nxt, schedule_id))
            if body.run_now:
                db.execute('UPDATE schedules SET next_run_at=NOW(),enabled=TRUE WHERE id=%s', (schedule_id,))
            db.commit()

    @router.delete('/schedules/{schedule_id}')
    def delete_schedule(schedule_id: uuid.UUID, request: Request):
        dashboard_write(request)
        with connect() as db:
            if not db.execute('DELETE FROM schedules WHERE id=%s RETURNING id', (schedule_id,)).fetchone():
                raise HTTPException(404, 'Schedule not found')
            db.commit()
        return {'deleted': schedule_id}

    return router
