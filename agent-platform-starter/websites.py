"""Websites on the website VM (HLA_WEBSITES_V1): the dashboard view, owner overrides and a background monitor.

The SSH gateway builds the inventory (GET /v1/websites, gateway/websites.py): every container on public-website, the
Traefik routes that reach them, and an HTTP check of each public URL through Traefik. This module reads it with
POLICY_ADMIN_TOKEN when that is set, otherwise TOOL_GATEWAY_TOKEN (like the audit page), and adds what only the platform
knows: the owner's overrides (environment, display name, hidden, notes) and the last status per site.

GET  /ui/api/websites            the inventory with overrides and status history merged in
GET  /ui/api/websites/summary    counts from the last check (cheap: no gateway call), for the Overview tile
POST /ui/api/websites/refresh    read everything again now (admin token; the gateway allows it every 15 s, sooner gives
                                 the result of that last read with a note)
PUT  /ui/api/websites/{id}       {environment: production|staging|test|null, display_name, hidden, notes}

Monitor: every HLA_WEBSITE_CHECK_MINUTES (default 5, 0 = off) the inventory is read and the status per site stored
(website_status). When a production site goes down, or comes back, it is logged and a push notification is queued;
production sites that are down are listed in "Needs you". A failed read changes nothing ("couldn't check").
Environment: a hla.environment label on the containers wins, then the owner's override, then the gateway's guess.
"""
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

import approvals
import push

GATEWAY_URL = 'http://ssh-gateway-api:8099'
ENVIRONMENTS = ('production', 'staging', 'test')
STATUSES = ('up', 'protected', 'degraded', 'down', 'unknown')
FIRST_CHECK_SECONDS = 90  # after startup, so a restart does not wait a full interval
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$')
log = logging.getLogger('hla.websites')
_deps = {}
_state = {'attempt': None, 'error': None, 'ok': None}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS website_overrides (
        id TEXT PRIMARY KEY, environment TEXT CHECK (environment IN ('production','staging','test')),
        display_name TEXT, hidden BOOLEAN NOT NULL DEFAULT FALSE, notes TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_by TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS website_status (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, environment TEXT NOT NULL, base_environment TEXT NOT NULL DEFAULT 'production',
        environment_label BOOLEAN NOT NULL DEFAULT FALSE, url TEXT, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        since TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_checked TIMESTAMPTZ NOT NULL DEFAULT NOW())''')


def check_minutes():
    try:
        return max(0, min(1440, int(os.getenv('HLA_WEBSITE_CHECK_MINUTES', '5'))))
    except ValueError:
        return 5


def _admin_token():
    token = os.getenv('POLICY_ADMIN_TOKEN', '')
    return token if len(token) >= 32 else None


def _token():
    token = _admin_token() or os.getenv('TOOL_GATEWAY_TOKEN', '')
    if len(token) < 32:
        raise GatewayError(503, 'Set TOOL_GATEWAY_TOKEN (or POLICY_ADMIN_TOKEN) so the dashboard can read the SSH gateway')
    return token


class GatewayError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


async def fetch(refresh=False):
    """The gateway's inventory, or GatewayError with a sentence the page can show."""
    token = _admin_token() if refresh else _token()
    if refresh and not token:
        raise GatewayError(503, 'Refreshing needs POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway; the view still refreshes itself every minute')
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(GATEWAY_URL + '/v1/websites', params={'refresh': '1'} if refresh else None, timeout=240,
                                        headers={'Authorization': 'Bearer ' + token})
        except httpx.TimeoutException:
            raise GatewayError(504, 'The SSH gateway did not finish the website check in time') from None
        except httpx.HTTPError:
            raise GatewayError(502, 'SSH gateway unreachable; the websites could not be checked') from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and isinstance(body.get('sites'), list):
        return body
    if response.status_code == 429:
        raise GatewayError(429, f"Refreshed a moment ago; try again in {body.get('retry_after', 15)} s")
    if response.status_code == 404:
        raise GatewayError(502, 'The SSH gateway is older than the dashboard: update it to see the websites')
    if response.status_code == 401:
        raise GatewayError(502, 'The SSH gateway refused the dashboard token: it must match .env.gateway')
    raise GatewayError(502, f"The SSH gateway could not check the websites ({body.get('error') or 'HTTP ' + str(response.status_code)})")


def _overrides(db):
    return {r['id']: r for r in db.execute('SELECT * FROM website_overrides').fetchall()}


def effective_environment(base, label, override):
    if label:
        return base, 'label'
    if override in ENVIRONMENTS:
        return override, 'override'
    return base, 'guess'


def merge(data, overrides):
    """Sites with the owner's overrides applied (the gateway's own values stay available)."""
    out = []
    for s in data.get('sites') or []:
        o = overrides.get(s['id']) or {}
        label = s.get('environment_source') == 'label'
        env, source = effective_environment(s.get('environment') or 'production', label, o.get('environment'))
        out.append({**s, 'gateway_name': s.get('name'), 'name': (o.get('display_name') or '').strip() or s.get('name'),
                    'base_environment': s.get('environment'), 'environment': env, 'environment_source': source,
                    'environment_override': o.get('environment'), 'hidden': bool(o.get('hidden')), 'notes': o.get('notes') or ''})
    return out


def _first_url(site):
    return next((u['url'] for u in site.get('urls') or [] if u.get('url')), None) or (site.get('lan_urls') or [None])[0]


def record(db, sites, complete=True):
    """Store the status per site; returns the changes worth telling (production sites that went down or came back).
    complete: the gateway read containers and routes, so sites missing from it are gone."""
    db.execute("SELECT pg_advisory_xact_lock(hashtext('hla_websites'))")  # one writer: no double notifications
    prev = {r['id']: r for r in db.execute('SELECT * FROM website_status').fetchall()}
    events = []
    for s in sites:
        status = s.get('status') if s.get('status') in STATUSES else 'unknown'
        if status == 'unknown':
            continue  # not known this time: keep what was known
        old = prev.get(s['id'])
        values = (s['name'][:200], s['environment'], s.get('base_environment') or s['environment'], s.get('environment_source') == 'label',
                  _first_url(s), status, (s.get('status_reason') or '')[:400])
        if old is None:
            db.execute('''INSERT INTO website_status(id,name,environment,base_environment,environment_label,url,status,reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', (s['id'], *values))
        else:
            db.execute('''UPDATE website_status SET name=%s,environment=%s,base_environment=%s,environment_label=%s,url=%s,status=%s,reason=%s,
                last_checked=NOW(),since=CASE WHEN status=%s THEN since ELSE NOW() END WHERE id=%s''', (*values, status, s['id']))
        watched = s['environment'] == 'production' and not s.get('hidden')
        was = old['status'] if old else None
        if watched and status == 'down' and was != 'down':
            events.append(('down', s))
        elif watched and was == 'down' and status in ('up', 'protected', 'degraded'):
            events.append(('up', s))
    if complete:
        seen = [s['id'] for s in sites]
        db.execute('DELETE FROM website_status WHERE NOT (id = ANY(%s))', (seen,))
    for kind, s in events:
        url = _first_url(s) or s['name']
        if kind == 'down':
            log.warning('Production website down: %s (%s): %s', s['name'], url, s.get('status_reason'))
            push.queue(db, 'Website down: ' + s['name'], url + ' · ' + (s.get('status_reason') or 'not answering'), '/ui/#websites', 'website-' + s['id'])
        else:
            log.warning('Production website back up: %s (%s)', s['name'], url)
            push.queue(db, 'Website back up: ' + s['name'], url + ' answers again', '/ui/#websites', 'website-' + s['id'])
    return events


def _complete(data):
    return not data.get('containers_error') and data.get('routes_source') not in (None, 'none')


async def check_now(connect, refresh=False):
    """Read the inventory, store the statuses; (data, merged sites, events). Raises GatewayError."""
    _state['attempt'] = time.time()
    try:
        data = await fetch(refresh)
    except GatewayError as exc:
        if exc.status != 429:
            _state['error'] = exc.message
        raise
    with connect() as db:
        sites = merge(data, _overrides(db))
        events = record(db, sites, _complete(data))
        stored = {r['id']: r for r in db.execute('SELECT id,since,last_checked FROM website_status').fetchall()}
        db.commit()
    for s in sites:
        row = stored.get(s['id'])
        s['status_since'] = row['since'] if row else None
        s['last_checked'] = row['last_checked'] if row else None
    _state.update(error=None, ok=time.time())
    return data, sites, events


def inbox_items(db):
    """HLA_INBOX_V1: production websites that are down (from the last check)."""
    rows = db.execute('''SELECT s.id,s.name,s.url,s.reason,s.since FROM website_status s LEFT JOIN website_overrides o ON o.id=s.id
        WHERE s.status='down' AND s.environment='production' AND NOT COALESCE(o.hidden,FALSE) ORDER BY s.since''').fetchall()
    return [{'kind': 'website', 'title': 'Production website down', 'context': r['name'],
             'detail': ' · '.join(x for x in (r['url'], r['reason']) if x)[:240], 'since': r['since'], 'website_id': r['id'], 'url': r['url']}
            for r in rows]


def summary(db):
    rows = db.execute('''SELECT s.status,s.environment,s.last_checked FROM website_status s LEFT JOIN website_overrides o ON o.id=s.id
        WHERE NOT COALESCE(o.hidden,FALSE)''').fetchall()
    counts = {k: 0 for k in STATUSES}
    by_env = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
        env = by_env.setdefault(r['environment'], {k: 0 for k in STATUSES})
        env[r['status']] = env.get(r['status'], 0) + 1
    last = max((r['last_checked'] for r in rows), default=None)
    return {'total': len(rows), 'counts': counts, 'by_environment': by_env, 'last_checked': last,
            'error': _state['error'], 'monitor_minutes': check_minutes()}


async def loop():
    """The monitor: every HLA_WEBSITE_CHECK_MINUTES (0 = off), cheap and failure-tolerant."""
    await asyncio.sleep(FIRST_CHECK_SECONDS)
    while True:
        minutes = check_minutes()
        if minutes and 'connect' in _deps:
            try:
                await check_now(_deps['connect'])
            except GatewayError:
                pass  # recorded in _state: "couldn't check"
            except Exception as exc:
                _state['error'] = 'Website check failed: ' + type(exc).__name__
        await asyncio.sleep((minutes or 1) * 60)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _view(data, sites):
    visible = [s for s in sites if not s['hidden']]
    counts = {k: 0 for k in STATUSES}
    by_env = {}
    for s in visible:
        counts[s['status']] = counts.get(s['status'], 0) + 1
        env = by_env.setdefault(s['environment'], {k: 0 for k in STATUSES})
        env[s['status']] = env.get(s['status'], 0) + 1
    return {'available': True, 'sites': sites, 'counts': counts, 'by_environment': by_env,
            **{k: data.get(k) for k in ('routes_source', 'routes_error', 'routes_files', 'routes_file_errors', 'containers_error',
                                        'checks_error', 'checks_truncated', 'other_routes', 'checked_at', 'traefik_api', 'dynamic_dir',
                                        'cached', 'age')},
            'refresh_allowed': bool(_admin_token()), 'monitor_minutes': check_minutes()}


def build_router(connect, dashboard_read, dashboard_write):
    _deps['connect'] = connect
    router = APIRouter(prefix='/ui/api/websites', tags=['websites'])

    @router.get('')
    async def listing(request: Request):
        dashboard_read(request)
        try:
            data, sites, _ = await check_now(connect)
        except GatewayError as exc:
            with connect() as db:
                stored = db.execute('''SELECT s.*,o.hidden FROM website_status s LEFT JOIN website_overrides o ON o.id=s.id
                    ORDER BY s.environment,s.name''').fetchall()
            return {'available': False, 'error': exc.message, 'sites': [], 'stored': stored, 'refresh_allowed': bool(_admin_token()),
                    'monitor_minutes': check_minutes()}
        return _view(data, sites)

    @router.get('/summary')
    def overview(request: Request):
        dashboard_read(request)
        with connect() as db:
            return summary(db)

    @router.post('/refresh')
    async def refresh(request: Request):
        dashboard_write(request)
        try:
            data, sites, _ = await check_now(connect, refresh=True)
        except GatewayError as exc:
            if exc.status != 429:
                raise HTTPException(exc.status if exc.status in (503, 504) else 502, exc.message) from None
            # The gateway read everything less than 15 s ago: that result is fresh enough.
            try:
                data, sites, _ = await check_now(connect)
            except GatewayError as again:
                raise HTTPException(again.status if again.status in (503, 504) else 502, again.message) from None
            return {**_view(data, sites), 'note': f"Checked {data.get('age') or 0} s ago; showing that result"}
        return _view(data, sites)

    @router.put('/{site_id}')
    async def override(site_id: str, request: Request):
        dashboard_write(request)
        if not _ID.match(site_id):
            raise HTTPException(422, 'Unknown website id')
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'Send the changes as an object')
        env = body.get('environment')
        if env not in (None, '', *ENVIRONMENTS):
            raise HTTPException(422, 'environment must be production, staging, test or empty (use the guess)')
        name = body.get('display_name')
        if name is not None and (not isinstance(name, str) or len(name) > 120):
            raise HTTPException(422, 'The display name is at most 120 characters')
        notes = body.get('notes')
        if notes is not None and (not isinstance(notes, str) or len(notes) > 2000):
            raise HTTPException(422, 'Notes are at most 2000 characters')
        hidden = body.get('hidden')
        if hidden is not None and not isinstance(hidden, bool):
            raise HTTPException(422, 'hidden is true or false')
        actor = approvals.approver_status(request)['actor'] or 'dashboard_session'
        with connect() as db:
            current = db.execute('SELECT * FROM website_overrides WHERE id=%s FOR UPDATE', (site_id,)).fetchone() or {}
            row = {'environment': current.get('environment'), 'display_name': current.get('display_name'),
                   'hidden': bool(current.get('hidden')), 'notes': current.get('notes') or ''}
            if 'environment' in body:
                row['environment'] = env or None
            if name is not None:
                row['display_name'] = name.strip() or None
            if notes is not None:
                row['notes'] = notes.strip()
            if hidden is not None:
                row['hidden'] = hidden
            db.execute('''INSERT INTO website_overrides(id,environment,display_name,hidden,notes,updated_at,updated_by)
                VALUES (%s,%s,%s,%s,%s,NOW(),%s) ON CONFLICT (id) DO UPDATE SET environment=excluded.environment,
                display_name=excluded.display_name,hidden=excluded.hidden,notes=excluded.notes,updated_at=NOW(),updated_by=excluded.updated_by''',
                       (site_id, row['environment'], row['display_name'], row['hidden'], row['notes'], actor[:120]))
            # The stored status follows at once (the inbox and the Overview tile read it).
            st = db.execute('SELECT base_environment,environment_label FROM website_status WHERE id=%s', (site_id,)).fetchone()
            if st:
                env_now, _ = effective_environment(st['base_environment'], st['environment_label'], row['environment'])
                db.execute('UPDATE website_status SET environment=%s,name=COALESCE(%s,name) WHERE id=%s', (env_now, row['display_name'], site_id))
            db.commit()
        return {'id': site_id, **row, 'updated_by': actor}

    return router
