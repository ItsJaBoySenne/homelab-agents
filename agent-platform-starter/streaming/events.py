"""SSE for a task: live text from the in-process bus, durable state from Postgres (HLA_LIVE_V1).

Client reconnection does not cancel tasks. The database is read when the run reports a state change and at
least every few seconds, off the event loop, so many followers do not stall other requests.
"""
import asyncio
import json
import time
from fastapi.responses import StreamingResponse

from streaming import bus

TERMINAL={'completed','incomplete','failed','human_approval_required','expert_review_recommended','cancelled','rejected','expired'}
POLL_SECONDS=3.0       # safety net when a change was not announced on the bus
KEEPALIVE_SECONDS=15.0
MAX_SECONDS=1800       # reconnect afterwards if needed


def sse(kind,payload):
    return 'event: '+kind+'\ndata: '+json.dumps(payload,default=str)+'\n\n'


def event_response(database_connection, task_id):
    def read():
        with database_connection() as db:
            return db.execute('SELECT status,partial_text,result_text,error_message,progress FROM tasks WHERE id=%s',(task_id,)).fetchone()

    async def events():
        last_text=None
        last_steps=[]  # an empty step list is not news
        seen=(-1,-1)
        row=None
        last_read=last_sent=0.0
        started=time.monotonic()
        while time.monotonic()-started<MAX_SECONDS:
            live_text,text_v,state_v=bus.snapshot(task_id)
            now=time.monotonic()
            if row is None or state_v!=seen[1] or now-last_read>=POLL_SECONDS:
                row=await asyncio.to_thread(read)
                last_read=now
                if not row:
                    yield sse('error',{'message':'Task not found'});return
            seen=(text_v,state_v)
            terminal=row['status'] in TERMINAL
            # Live text is fresher than the periodically saved copy; once finished, the database is authoritative.
            text=(row['result_text'] or row['partial_text'] or '') if terminal or live_text is None else live_text
            steps=row['progress'] or []
            if steps!=last_steps:
                last_steps=steps
                yield sse('progress',{'steps':steps});last_sent=now
            if text!=last_text:
                last_text=text
                yield sse('content',{'text':text});last_sent=now
            if terminal:
                yield sse('done',{'status':row['status'],'error':row['error_message']});return
            if now-last_sent>=KEEPALIVE_SECONDS:
                yield ': keepalive\n\n';last_sent=now
            await bus.wait(task_id,seen,POLL_SECONDS)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})
