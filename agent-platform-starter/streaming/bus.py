"""In-process live updates for running tasks (HLA_LIVE_V1).

The API is a single process, so a run and the browsers following it share memory: streamed text is kept here
and pushed to SSE subscribers immediately, instead of being written to Postgres and polled. The database still
holds the durable state (steps, status, final and periodically saved partial text); a subscriber re-reads it
when a run reports a state change, and every few seconds as a safety net.
"""
import asyncio

_live = {}  # task_id -> {'text': str | None, 'text_v': int, 'state_v': int, 'event': asyncio.Event}


def _entry(task_id):
    key = str(task_id)
    entry = _live.get(key)
    if entry is None:
        entry = _live[key] = {'text': None, 'text_v': 0, 'state_v': 0, 'event': asyncio.Event()}
    return entry


def _wake(entry):
    event = entry['event']
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        event.set()
    else:  # called from a worker thread: hand the wake-up to the event loop that owns the waiters
        owner = getattr(event, '_loop', None)
        if owner is not None and owner.is_running():
            owner.call_soon_threadsafe(event.set)


def set_text(task_id, text):
    entry = _entry(task_id)
    entry['text'] = text
    entry['text_v'] += 1
    _wake(entry)


def changed(task_id):
    """Steps, status or saved text changed in the database."""
    entry = _entry(task_id)
    entry['state_v'] += 1
    _wake(entry)


def snapshot(task_id):
    entry = _live.get(str(task_id))
    return (entry['text'], entry['text_v'], entry['state_v']) if entry else (None, 0, 0)


async def wait(task_id, seen, timeout):
    """Wait until text or state moved past `seen` = (text_v, state_v), or the timeout passes."""
    entry = _entry(task_id)
    if (entry['text_v'], entry['state_v']) != seen:
        return
    entry['event'].clear()
    try:
        await asyncio.wait_for(entry['event'].wait(), timeout)
    except asyncio.TimeoutError:
        pass


def finish(task_id):
    """Drop the in-memory text once the final state is in the database; wake subscribers to read it."""
    entry = _live.pop(str(task_id), None)
    if entry:
        entry['state_v'] += 1
        entry['event'].set()
