"""Single-process, text-only worker. Interrupted jobs need manual review (never auto retry)."""
import os
import time
import uuid

import httpx
import psycopg
from psycopg.rows import dict_row

DB = os.environ['DATABASE_URL']
TOKEN = os.environ['AGENT_PLATFORM_TOKEN']
URL = os.getenv('AGENT_ROUTER_INTERNAL_URL', 'http://agent-router:8080')


def claim():
    with psycopg.connect(DB, row_factory=dict_row) as db:
        with db.transaction():
            job = db.execute("""SELECT * FROM queued_jobs WHERE status='queued'
                  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if job:
                db.execute("UPDATE queued_jobs SET status='running',started_at=NOW() WHERE id=%s",(job['id'],))
        return job


def finish(job, status, task_id=None, result=None, error=None):
    with psycopg.connect(DB) as db:
        db.execute("""UPDATE queued_jobs SET status=%s,api_task_id=%s,
                     result_text=%s,error_message=%s,finished_at=NOW() WHERE id=%s""",
                   (status,task_id,result,error,job['id']))
        db.commit()


def work(job):
    payload={'task':job['task_text'],'workspace':job['workspace'],
             'agent_id':str(job['agent_id']) if job['agent_id'] else None,
             'max_output_tokens':job['max_output_tokens'],
             'reasoning_effort':job['reasoning_effort']}
    try:
        with httpx.Client(timeout=180.0) as client:
            response=client.post(URL+'/tasks',json=payload,headers={'Authorization':'Bearer '+TOKEN})
            try: data=response.json()
            except ValueError: data={}
            if not response.is_success:
                finish(job,'failed',error=f'API HTTP {response.status_code}: {str(data.get("detail","Unknown error"))[:350]}')
                return
            state=data.get('status','failed')
            allowed={'completed','incomplete','failed','human_approval_required','expert_review_recommended'}
            state=state if state in allowed else 'failed'
            task_id=data.get('id')
            finish(job,state,task_id=uuid.UUID(task_id) if task_id else None,
                   result=data.get('result'),error=data.get('error') or data.get('reason'))
    except Exception as exc:
        # Do not auto-replay: upstream might have charged before our timeout.
        finish(job,'needs_review',error=f'Interrupted or uncertain upstream outcome: {type(exc).__name__}')


def main():
    while True:
        try:
            job=claim()
            if job: work(job)
            else: time.sleep(3)
        except Exception as exc:
            print(f'Queue worker error: {type(exc).__name__}: {exc}',flush=True)
            time.sleep(5)

if __name__=='__main__':
    main()
