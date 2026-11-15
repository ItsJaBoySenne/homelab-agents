"""In-process registry of background runs so they can be stopped (HLA_RUNS_V1).

The API runs as a single uvicorn process, so a dict is the whole registry. Cancelling interrupts the
run at its next await: model calls stop immediately; a command already sent to the SSH gateway keeps
running on the server until it finishes or hits the gateway's own timeout.
"""
import asyncio

from streaming import bus

_RUNS = {}


def spawn(coro, key=None):
    """Start a background run and keep a strong reference (asyncio only keeps weak ones)."""
    run = asyncio.create_task(coro)
    token = key if key is not None else run
    _RUNS[token] = run
    run.add_done_callback(lambda _: _RUNS.pop(token, None))
    return run


def cancel(key):
    run = _RUNS.get(key)
    if run is None or run.done():
        return False
    run.cancel()
    return True


def mark_cancelled(connect, task_id):
    """Keep whatever the model produced so far, and record that the user stopped the run."""
    live_text = bus.snapshot(task_id)[0]  # newer than the copy saved every few seconds
    with connect() as db:
        if live_text:
            db.execute("UPDATE tasks SET partial_text=%s WHERE id=%s AND status='running'", (live_text, task_id))
        db.execute("""UPDATE tasks SET status='cancelled', finished_at=NOW(),
            result_text=NULLIF(partial_text,''), error_message='Stopped by user'
            WHERE id=%s AND status='running'""", (task_id,))
        db.commit()
    bus.finish(task_id)
