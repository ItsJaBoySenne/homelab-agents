"""Push notifications (HLA_PUSH_V1): the installed dashboard app tells you when project work needs a look.

Standard Web Push: each browser or installed app that switches notifications on stores a subscription here. Events
go into an outbox in the same transaction as the change that caused them, and a loop sends them. The payload is
encrypted for the device; it only holds a title, one line and the page to open, never task results.

Events: a task finished, a task is blocked or failed (project paused), a task waits for an approval, a plan is ready,
a project completed or paused on its limits.

The signing key pair (VAPID) is created on first use and kept in the database. Without a subscription nothing is
queued. Subscriptions only go to the known browser push services, so the API never posts to an arbitrary address.
"""
import asyncio
import json
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

SUBJECT = os.getenv('PUSH_SUBJECT', 'https://agents.example.com')
PUSH_HOSTS = ('fcm.googleapis.com', 'android.googleapis.com', 'updates.push.services.mozilla.com',
              'push.services.mozilla.com', 'web.push.apple.com', '.push.apple.com', '.notify.windows.com')
LOOP_SECONDS = 3
MAX_AGE_HOURS = 12        # an event that could not go out in this time is dropped, not sent late
MAX_FAILURES = 10         # consecutive failed sends before a subscription is removed
_deps = {}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
        endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_sent_at TIMESTAMPTZ, failures INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS push_outbox (
        id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), title TEXT NOT NULL, body TEXT NOT NULL,
        url TEXT NOT NULL DEFAULT '/ui/', tag TEXT, sent_at TIMESTAMPTZ)''')
    db.execute('CREATE INDEX IF NOT EXISTS push_outbox_unsent_idx ON push_outbox(id) WHERE sent_at IS NULL')
    db.execute('''CREATE TABLE IF NOT EXISTS push_keys (
        id INTEGER PRIMARY KEY CHECK (id = 1), private_pem TEXT NOT NULL, public_key TEXT NOT NULL)''')


def queue(db, title, body, url='/ui/', tag=None):
    """Queue a notification in the caller's transaction; a no-op while no device has notifications on."""
    db.execute('''INSERT INTO push_outbox(title,body,url,tag) SELECT %s,%s,%s,%s
        WHERE EXISTS (SELECT 1 FROM push_subscriptions)''', (title[:120], body[:240], url, tag))


def project_url(pid):
    return '/ui/#project=' + str(pid)


def _b64url(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def keys(db):
    """(private PEM, public key for the browser), created once."""
    row = db.execute('SELECT private_pem,public_key FROM push_keys WHERE id=1').fetchone()
    if row:
        return row['private_pem'], row['public_key']
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    public = _b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    db.execute('INSERT INTO push_keys(id,private_pem,public_key) VALUES (1,%s,%s) ON CONFLICT (id) DO NOTHING', (pem, public))
    row = db.execute('SELECT private_pem,public_key FROM push_keys WHERE id=1').fetchone()
    return row['private_pem'], row['public_key']


def allowed_endpoint(endpoint):
    parts = urlsplit(endpoint)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and not parts.port and any(
        host == h or (h.startswith('.') and host.endswith(h)) for h in PUSH_HOSTS)


def deliver(sub, payload, private_pem):
    """Send one notification. Returns 'ok', 'gone' (the device unsubscribed) or 'failed'."""
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush
    try:
        webpush({'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']}},
                data=json.dumps(payload), vapid_private_key=Vapid02.from_pem(private_pem.encode()),
                vapid_claims={'sub': SUBJECT}, ttl=MAX_AGE_HOURS * 3600, timeout=10)
        return 'ok'
    except WebPushException as exc:
        status = getattr(exc.response, 'status_code', None)
        return 'gone' if status in (404, 410) else 'failed'
    except Exception:
        return 'failed'


def _record(db, sub, outcome):
    if outcome == 'gone':
        db.execute('DELETE FROM push_subscriptions WHERE endpoint=%s', (sub['endpoint'],))
    elif outcome == 'ok':
        db.execute('UPDATE push_subscriptions SET last_sent_at=NOW(),failures=0 WHERE endpoint=%s', (sub['endpoint'],))
    else:
        db.execute('UPDATE push_subscriptions SET failures=failures+1 WHERE endpoint=%s', (sub['endpoint'],))
        db.execute('DELETE FROM push_subscriptions WHERE endpoint=%s AND failures>=%s', (sub['endpoint'], MAX_FAILURES))


def flush(connect):
    """Send what is queued. Events are claimed first, so a slow push service never holds a lock."""
    with connect() as db:
        db.execute("UPDATE push_outbox SET sent_at=NOW() WHERE sent_at IS NULL AND created_at < NOW() - make_interval(hours => %s)",
                   (MAX_AGE_HOURS,))
        events = db.execute('''UPDATE push_outbox SET sent_at=NOW() WHERE id IN (
            SELECT id FROM push_outbox WHERE sent_at IS NULL ORDER BY id LIMIT 20 FOR UPDATE SKIP LOCKED)
            RETURNING title,body,url,tag''').fetchall()
        if not events:
            db.commit()
            return 0
        subs = db.execute('SELECT endpoint,p256dh,auth FROM push_subscriptions').fetchall()
        private_pem, _ = keys(db) if subs else (None, None)
        db.commit()
    sent = 0
    for sub in subs:
        outcome = 'ok'
        for event in events:
            outcome = deliver(sub, {k: event[k] for k in ('title', 'body', 'url', 'tag')}, private_pem)
            if outcome != 'ok':
                break
            sent += 1
        with connect() as db:
            _record(db, sub, outcome)
            db.commit()
    return sent


async def loop():
    while True:
        try:
            await asyncio.to_thread(flush, _deps['connect'])
        except Exception:
            pass  # the next pass retries
        await asyncio.sleep(LOOP_SECONDS)


class Keys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: Keys | None = None
    label: str = Field(default='', max_length=120)


def build_router(connect, dashboard_read, dashboard_write):
    _deps['connect'] = connect
    router = APIRouter(prefix='/ui/api/push', tags=['push'])

    @router.get('')
    def status(request: Request, endpoint: str = ''):
        dashboard_read(request)
        with connect() as db:
            _, public = keys(db)
            count = db.execute('SELECT count(*) AS n FROM push_subscriptions').fetchone()['n']
            mine = bool(endpoint) and bool(db.execute('SELECT 1 FROM push_subscriptions WHERE endpoint=%s', (endpoint,)).fetchone())
            db.commit()
        return {'public_key': public, 'devices': count, 'subscribed': mine}

    @router.post('/subscribe')
    def subscribe(body: Subscription, request: Request):
        dashboard_write(request)
        if not body.keys or not allowed_endpoint(body.endpoint):
            raise HTTPException(400, 'This browser offered an unknown push service; notifications are not available here')
        with connect() as db:
            db.execute('''INSERT INTO push_subscriptions(endpoint,p256dh,auth,label) VALUES (%s,%s,%s,%s)
                ON CONFLICT (endpoint) DO UPDATE SET p256dh=EXCLUDED.p256dh,auth=EXCLUDED.auth,label=EXCLUDED.label,failures=0''',
                       (body.endpoint, body.keys.p256dh, body.keys.auth, body.label))
            db.commit()
        return {'subscribed': True}

    @router.post('/unsubscribe')
    def unsubscribe(body: Subscription, request: Request):
        dashboard_write(request)
        with connect() as db:
            db.execute('DELETE FROM push_subscriptions WHERE endpoint=%s', (body.endpoint,))
            db.commit()
        return {'subscribed': False}

    @router.post('/test')
    async def test(body: Subscription, request: Request):
        dashboard_write(request)

        def work():
            with connect() as db:
                sub = db.execute('SELECT endpoint,p256dh,auth FROM push_subscriptions WHERE endpoint=%s', (body.endpoint,)).fetchone()
                if not sub:
                    raise HTTPException(404, 'This device is not subscribed')
                private_pem, _ = keys(db)
                db.commit()
            outcome = deliver(sub, {'title': 'Homelab Agents', 'body': 'Notifications work on this device.',
                                    'url': '/ui/', 'tag': 'test'}, private_pem)
            with connect() as db:
                _record(db, sub, outcome)
                db.commit()
            return outcome
        outcome = await asyncio.to_thread(work)
        if outcome != 'ok':
            raise HTTPException(502, 'The push service refused the test notification' + (' (device unsubscribed)' if outcome == 'gone' else ''))
        return {'sent': True}

    return router
