"""Model registry (HLA_MODEL_REGISTRY_V1): the models agents may use, managed on the dashboard's Models page.

`models_registry` holds one row per OpenRouter model id (label, enabled, notes, context length, prices per 1M tokens,
tool support, who added it). Only enabled rows count: agent create/edit, conversation model overrides, model routing
(models.allowed) and every run validate against them. `models_registry_settings` holds the default model (used by runs
without an agent or override, the planner and summary fallbacks), replacing OPENROUTER_WORKER_MODEL when set.

The environment seeds and backs it up: on startup an empty registry is filled from AGENT_ALLOWED_MODELS and
OPENROUTER_WORKER_MODEL, and while the registry is empty those variables are the allowlist again. Without a default
set on the dashboard, OPENROUTER_WORKER_MODEL is the default, else the first enabled model.

The catalog (`GET /ui/api/models/catalog`) is OpenRouter's public model list, cached for an hour; no key is sent.
Reads go through a short in-process cache (writes here clear it), since validation runs on every request. When the
database cannot be read, the last good read is used; without one, nothing is allowed (the environment list is only the
allowlist for a registry known to be empty, never a guess while the database is down).
"""
import os
import re
import time
from decimal import Decimal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

CATALOG_URL = 'https://openrouter.ai/api/v1/models'
CHAT_URL = 'https://openrouter.ai/api/v1/chat/completions'
CATALOG_TTL = 3600       # seconds the OpenRouter catalog is reused
CACHE_TTL = 5            # seconds registry reads are reused (other processes see a change within this)
# OpenRouter ids: vendor/model, optionally a :variant (e.g. openai/gpt-4o:free, ~anthropic/claude-sonnet-latest).
ID_RE = re.compile(r'^~?[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._+-]{0,120}(:[A-Za-z0-9._-]{1,40})?$')
ROUTING_ENV = {'plan': 'HLA_MODEL_PLAN', 'review': 'HLA_MODEL_REVIEW', 'continue': 'HLA_MODEL_CONTINUE',
               'summary': 'HLA_SUMMARY_MODEL'}
ROUTING_LABELS = {'plan': 'Planning', 'build': 'Project tasks', 'review': 'Review tasks', 'continue': 'Continuations',
                  'summary': 'Conversation summaries'}

_deps = {'connect': None}
_cache = {'at': 0.0, 'rows': None, 'default': None}
_last_good = {'rows': None, 'default': None}   # survives invalidate(): used while the database cannot be read
_catalog = {'at': 0.0, 'models': None}
_xai_catalog = {'at': 0.0, 'models': None}   # HLA_XAI_V1: xAI's own model list (needs XAI_API_KEY)


def _providers():
    import providers  # late: providers reads registry prices from this module
    return providers


def env_models():
    """AGENT_ALLOWED_MODELS (else OPENROUTER_WORKER_MODEL) as a list: the seed and the fallback allowlist."""
    raw = os.getenv('AGENT_ALLOWED_MODELS', os.getenv('OPENROUTER_WORKER_MODEL', ''))
    return [x.strip() for x in raw.split(',') if x.strip()]


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS models_registry (
        id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT TRUE,
        notes TEXT NOT NULL DEFAULT '', context_length INTEGER, prompt_price NUMERIC(14,6), completion_price NUMERIC(14,6),
        supports_tools BOOLEAN, added_by TEXT, added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_synced_at TIMESTAMPTZ)''')
    # HLA_ATTACHMENTS_V1: whether the model accepts images (catalog input modalities); NULL = unknown.
    db.execute('ALTER TABLE models_registry ADD COLUMN IF NOT EXISTS supports_images BOOLEAN')
    db.execute('''CREATE TABLE IF NOT EXISTS models_registry_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    if not db.execute('SELECT 1 FROM models_registry LIMIT 1').fetchone():
        seed = env_models()
        worker = os.getenv('OPENROUTER_WORKER_MODEL', '').strip()
        if worker and worker not in seed:
            seed.append(worker)
        for model in seed:
            db.execute('''INSERT INTO models_registry(id,added_by) VALUES (%s,'environment') ON CONFLICT DO NOTHING''', (model,))
    invalidate()


def configure(connect):
    _deps['connect'] = connect
    invalidate()


def invalidate():
    _cache.update(at=0.0, rows=None, default=None)


def _load():
    if _cache['rows'] is not None and time.monotonic() - _cache['at'] < CACHE_TTL:
        return _cache['rows'], _cache['default']
    connect = _deps['connect']
    if connect is None:
        return [], None
    try:
        with connect() as db:
            rows = db.execute('SELECT * FROM models_registry ORDER BY lower(coalesce(nullif(label,\'\'),id))').fetchall()
            row = db.execute("SELECT value FROM models_registry_settings WHERE key='default_model'").fetchone()
    except Exception:
        # The database is down: the last good read, else unknown (None) and nothing is allowed. Falling back to the
        # environment here could re-allow models the owner disabled in the registry.
        return _last_good['rows'], _last_good['default']
    _cache.update(at=time.monotonic(), rows=rows, default=(row or {}).get('value') or None)
    _last_good.update(rows=rows, default=_cache['default'])
    return rows, _cache['default']


def available():
    """False while the registry cannot be read and there is no earlier read to go by."""
    return _load()[0] is not None


def subscription_entries():
    """HLA_ENGINES_V1: catalog entries of the subscription models (claude-sub/..., chatgpt-sub/...): no per-token
    price (billing 'subscription'), tools through the platform's relay, text only; available = engine signed in."""
    import engines
    out = []
    for engine, spec in engines.ENGINES.items():
        signed = engines.signed_in_fast(engine)
        for m in engines.model_entries(engine):
            out.append({'id': m['id'], 'name': m['label'], 'context_length': None, 'prompt_price': 0.0, 'completion_price': 0.0,
                        'supported_parameters': ['tools', 'tool_choice'], 'supports_tools': True, 'supports_images': False,
                        'description': ('Runs on your ' + spec['label'].split(' (')[0] + ' plan through the official ' + spec['cli']
                                        + ' (Subscriptions page). No per-token cost; the plan\'s usage limits apply.'),
                        'provider': engines.PROVIDER_OF[engine], 'billing': 'subscription', 'available': signed})
    return out


def subscription_ids(rows):
    """Subscription models offered without a registry row: those of signed-in engines. A registry row for the same id
    (added on the Models page) decides instead, so the owner can switch one off."""
    try:
        import engines
        have = {r['id'] for r in rows or []}
        return [m['id'] for e in engines.ENGINES if engines.signed_in_fast(e) for m in engines.model_entries(e) if m['id'] not in have]
    except Exception:  # noqa: BLE001 - engines unavailable: no subscription models
        return []


def enabled_ids():
    """The models agents, conversations and routing may use: enabled registry rows, or the env list while it is empty
    (none while the registry cannot be read at all); plus the subscription models of signed-in engines."""
    rows, _ = _load()
    if rows is None:
        return []
    if not rows:
        return env_models() + subscription_ids(rows)
    return [r['id'] for r in rows if r['enabled']] + subscription_ids(rows)


def is_allowed(model):
    return bool(model) and model in enabled_ids()


def default_model():
    """(model, source): the dashboard's default, else OPENROUTER_WORKER_MODEL (while it is enabled in the registry),
    else the first enabled model."""
    rows, chosen = _load()
    if rows is None:
        return '', 'unavailable'
    enabled = enabled_ids()
    if chosen and chosen in enabled:
        return chosen, 'dashboard'
    env = os.getenv('OPENROUTER_WORKER_MODEL', '').strip()
    if env and (env in enabled or not rows):
        return env, 'env'
    if enabled:
        return enabled[0], 'registry'   # OPENROUTER_WORKER_MODEL is unset, or disabled/removed in the registry
    return env, ('env' if env else '')


def check(model):
    """Raise 422 unless the model is enabled in the registry."""
    if not is_allowed(model):
        if not available():
            raise HTTPException(503, 'The model registry cannot be read right now (database unavailable); try again shortly')
        raise HTTPException(422, 'Model is not enabled in the model registry (Models page): ' + str(model)[:160])


# ---------------------------------------------------------------- OpenRouter catalog

def _per_million(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return round(price * 1_000_000, 6) if price >= 0 else None  # -1 marks a variable price ("auto" routers)


def _accepts_images(modalities):
    return 'image' in modalities if isinstance(modalities, list) and modalities else None


def _catalog_entry(item):
    params = item.get('supported_parameters') or []
    pricing = item.get('pricing') or {}
    return {'id': item.get('id'), 'name': item.get('name') or item.get('id'), 'context_length': item.get('context_length'),
            'prompt_price': _per_million(pricing.get('prompt')), 'completion_price': _per_million(pricing.get('completion')),
            'supported_parameters': params, 'supports_tools': 'tools' in params,
            'supports_images': _accepts_images((item.get('architecture') or {}).get('input_modalities')),
            'description': (item.get('description') or '')[:280]}


async def catalog(force=False):
    """(models, fetched_at, stale). Raises 502 when OpenRouter cannot be reached and nothing is cached."""
    now = time.time()
    if not force and _catalog['models'] is not None and now - _catalog['at'] < CATALOG_TTL:
        return _catalog['models'], _catalog['at'], False
    try:
        async with httpx.AsyncClient(timeout=20) as client:  # public endpoint: no API key is sent
            response = await client.get(CATALOG_URL)
            response.raise_for_status()
            data = response.json().get('data')
        if not isinstance(data, list):
            raise ValueError('unexpected reply')
        models = [_catalog_entry(m) for m in data if isinstance(m, dict) and isinstance(m.get('id'), str)]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        if _catalog['models'] is not None:
            return _catalog['models'], _catalog['at'], True  # an older copy beats no catalog
        detail = f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        raise HTTPException(502, f'The OpenRouter model list could not be fetched ({detail}). Try again later, or add the model id by hand.') from None
    _catalog.update(at=now, models=models)
    return models, now, False


async def xai_catalog(force=False):
    """(models, fetched_at, stale, error): xAI's GET /v1/language-models as catalog entries (ids xai/<model>), cached
    CATALOG_TTL. error is a short reason (no key, refused, unreachable) when there is nothing to show."""
    import providers
    now = time.time()
    if not force and _xai_catalog['models'] is not None and now - _xai_catalog['at'] < CATALOG_TTL:
        return _xai_catalog['models'], _xai_catalog['at'], False, None
    if not providers.key_configured('xai'):
        return [], None, False, 'XAI_API_KEY not set'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            models = await providers.xai_models(client)
    except (providers.ProviderError, httpx.HTTPError, ValueError, TypeError) as exc:
        if _xai_catalog['models'] is not None:
            return _xai_catalog['models'], _xai_catalog['at'], True, None
        return [], None, False, (exc.message if isinstance(exc, providers.ProviderError) else 'xAI unreachable: ' + type(exc).__name__)
    _xai_catalog.update(at=now, models=models)
    return models, now, False, None


def _metadata(entry):
    return (entry['context_length'] if isinstance(entry.get('context_length'), int) else None,
            entry.get('prompt_price'), entry.get('completion_price'), entry.get('supports_tools'))


# ---------------------------------------------------------------- usage

def usage(db):
    """{model: [what uses it]}: agents, dashboard routing settings, project overrides, the default; env defaults too."""
    used = {}
    for a in db.execute('SELECT id,name,model,enabled FROM agents ORDER BY name').fetchall():
        used.setdefault(a['model'], []).append({'type': 'agent', 'id': str(a['id']), 'name': a['name'], 'enabled': a['enabled'],
                                                'blocks': True, 'text': 'agent ' + a['name']})
    for r in db.execute("SELECT kind,model FROM model_settings WHERE model<>''").fetchall():
        used.setdefault(r['model'], []).append({'type': 'routing', 'kind': r['kind'], 'blocks': True,
                                                'text': 'routing: ' + ROUTING_LABELS.get(r['kind'], r['kind'])})
    stored = {r['kind'] for r in db.execute('SELECT kind FROM model_settings').fetchall()}
    for kind, var in ROUTING_ENV.items():
        value = os.getenv(var, '').strip()
        if value and kind not in stored:
            used.setdefault(value, []).append({'type': 'env', 'kind': kind, 'env': var, 'blocks': False,
                                               'text': 'routing default ' + var + ' (environment)'})
    for r in db.execute('''SELECT m.kind,m.model,p.id,p.title FROM project_models m JOIN projects p ON p.id=m.project_id
            ORDER BY p.title''').fetchall():
        used.setdefault(r['model'], []).append({'type': 'project', 'id': str(r['id']), 'kind': r['kind'], 'name': r['title'], 'blocks': True,
                                                'text': 'project ' + r['title'] + ' (' + ROUTING_LABELS.get(r['kind'], r['kind']).lower() + ')'})
    row = db.execute("SELECT value FROM models_registry_settings WHERE key='default_model'").fetchone()
    if row and row['value']:
        used.setdefault(row['value'], []).append({'type': 'default', 'blocks': True, 'text': 'the default model'})
    return used


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


# ---------------------------------------------------------------- API (Models page)

class ModelIn(BaseModel):
    id: str = Field(min_length=3, max_length=160)
    label: str = Field(default='', max_length=120)
    notes: str = Field(default='', max_length=2000)
    enabled: bool = True


class ModelPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class DefaultIn(BaseModel):
    model: str | None = Field(default=None, max_length=160)   # null or '' = back to OPENROUTER_WORKER_MODEL


def build_router(connect, dashboard_read, dashboard_write, require_approver, approver_status):
    configure(connect)
    router = APIRouter(prefix='/ui/api/models', tags=['models'])

    def row_out(r, used):
        out = {k: _num(v) for k, v in r.items()}
        out['used_by'] = used.get(r['id'], [])
        return out

    def get_row(db, model_id):
        row = db.execute('SELECT * FROM models_registry WHERE id=%s', (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Model not in the registry: ' + model_id[:160])
        return row

    @router.get('')
    def list_models(request: Request):
        dashboard_read(request)
        with connect() as db:
            rows = db.execute("SELECT * FROM models_registry ORDER BY lower(coalesce(nullif(label,''),id))").fetchall()
            used = usage(db)
        model, source = default_model()
        stored = next((r for r in rows if r['id'] == model), None)
        subs = subscription_entries()
        auto = set(subscription_ids(rows))
        p = _providers()
        return {'models': enabled_ids(),   # the ids the pickers offer (as before the registry)
                'registry': [{**row_out(r, used), 'billing': p.billing_of(r['id'])} for r in rows],
                'labels': {**{m['id']: m['name'] for m in subs}, **{r['id']: r['label'] for r in rows if r['label']}},
                'default': {'model': model, 'source': source, 'label': (stored or {}).get('label') or ''},
                'source': 'registry' if rows else 'env',
                'env': {'allowed': env_models(), 'worker_model': os.getenv('OPENROUTER_WORKER_MODEL', '')},
                # HLA_ENGINES_V1: subscription models (Subscriptions page); offered while their engine is signed in.
                'subscription': [{'id': m['id'], 'label': m['name'], 'provider': m['provider'], 'billing': 'subscription',
                                  'available': m['available'], 'offered': m['id'] in auto or m['id'] in {r['id'] for r in rows if r['enabled']},
                                  'used_by': used.get(m['id'], [])} for m in subs],
                'providers': {'openrouter': {'label': 'OpenRouter', 'configured': p.key_configured('openrouter')},
                              'xai': {'label': 'xAI', 'configured': p.key_configured('xai'), 'prefix': p.XAI_PREFIX},
                              **{name: {'label': p.LABELS[name], 'configured': p.key_configured(name), 'prefix': name + '/',
                                        'billing': 'subscription'} for name in p.SUBSCRIPTION}},
                'approver': approver_status(request)}

    @router.get('/catalog')
    async def get_catalog(request: Request, q: str = '', refresh: bool = False, limit: int = 60, source: str = 'openrouter'):
        dashboard_read(request)
        error = None
        if source == 'xai':
            models, fetched, stale, error = await xai_catalog(force=refresh)
        elif source == 'subscription':   # HLA_ENGINES_V1: Claude / ChatGPT plans through their CLIs
            models, fetched, stale = subscription_entries(), time.time(), False
        else:
            models, fetched, stale = await catalog(force=refresh)
        words = [w for w in q.lower().split() if w][:8]
        found = [m for m in models if all(w in (m['id'] + ' ' + m['name']).lower() for w in words)]
        known = set(r['id'] for r in (_load()[0] or []))
        limit = min(max(limit, 1), 200)
        return {'models': [{**m, 'in_registry': m['id'] in known} for m in found[:limit]], 'total': len(found),
                'catalog_size': len(models), 'fetched_at': fetched, 'stale': stale,
                'source': source if source in ('xai', 'subscription') else 'openrouter',
                'error': error}

    @router.post('')
    async def add_model(body: ModelIn, request: Request):
        dashboard_write(request)
        actor = require_approver(request)
        model_id = body.id.strip()
        if not ID_RE.match(model_id):
            raise HTTPException(422, 'Not a model id: use vendor/model, e.g. anthropic/claude-sonnet-4.5 (OpenRouter) or xai/grok-4 (direct xAI)')
        entry, warning = None, None
        if _providers().is_subscription(model_id):   # HLA_ENGINES_V1
            entry = next((m for m in subscription_entries() if m['id'] == model_id), None)
            if entry is None:
                warning = 'Not one of the listed subscription models: check the name (a test call shows whether the CLI accepts it).'
            elif not entry['available']:
                warning = 'The engine is not signed in: sign in on the Subscriptions page; runs fail until then.'
        elif model_id.startswith(_providers().XAI_PREFIX):
            models, _, _, error = await xai_catalog()
            entry = next((m for m in models if m['id'] == model_id), None)
            if error:
                warning = error + ': added without prices; runs fail until the key is set.'
            elif entry is None:
                warning = 'Not in the xAI model list: check the id (a test call shows whether it works).'
        else:
            try:
                models, _, _ = await catalog()
                entry = next((m for m in models if m['id'] == model_id), None)
                if entry is None:
                    warning = 'Not in the OpenRouter catalog: check the id (a test call shows whether it works).'
            except HTTPException:
                warning = 'The OpenRouter catalog is unreachable; added without prices or context length.'
        meta = _metadata(entry) if entry else (None, None, None, None)
        label = body.label.strip() or (entry['name'] if entry else '')
        with connect() as db:
            row = db.execute('''INSERT INTO models_registry(id,label,enabled,notes,context_length,prompt_price,completion_price,
                    supports_tools,added_by,last_synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NOW() END)
                    ON CONFLICT (id) DO NOTHING RETURNING *''',
                             (model_id, label, body.enabled, body.notes.strip(), *meta, actor, entry is not None)).fetchone()
            if not row:
                raise HTTPException(409, 'Already in the registry: ' + model_id)
            if entry and entry.get('supports_images') is not None:
                db.execute('UPDATE models_registry SET supports_images=%s WHERE id=%s', (entry['supports_images'], model_id))
            db.commit()
            used = usage(db)
        invalidate()
        if entry and not entry['supports_tools']:
            warning = 'This model does not support tool calls: agents with server tools need them.'
        return {**row_out(row, used), 'warning': warning, 'in_catalog': entry is not None}

    @router.post('/sync')
    async def sync_models(request: Request):
        """Refresh label-less names, prices, context and tool support of every registry model from the catalog."""
        dashboard_write(request)
        models, _, stale = await catalog(force=True)
        by_id = {m['id']: m for m in models}
        xai_models, _, xai_stale, _ = await xai_catalog(force=True)
        by_id.update({m['id']: m for m in xai_models})
        stale = stale or xai_stale
        updated, missing = 0, []
        with connect() as db:
            for r in db.execute('SELECT id,label FROM models_registry').fetchall():
                entry = by_id.get(r['id'])
                if not entry:
                    missing.append(r['id'])
                    continue
                db.execute('''UPDATE models_registry SET context_length=%s,prompt_price=%s,completion_price=%s,supports_tools=%s,
                    supports_images=COALESCE(%s,supports_images),
                    label=CASE WHEN label='' THEN %s ELSE label END,last_synced_at=NOW() WHERE id=%s''',
                           (*_metadata(entry), entry.get('supports_images'), entry['name'], r['id']))
                updated += 1
            db.commit()
        invalidate()
        return {'updated': updated, 'missing': missing, 'stale': stale}

    @router.put('/default')
    def put_default(body: DefaultIn, request: Request):
        dashboard_write(request)
        actor = require_approver(request)
        model = (body.model or '').strip()
        with connect() as db:
            # HLA_ENGINES_V1: a subscription model offered without a registry row (its engine is signed in) may be the
            # default too, like it may be picked for agents and routing; a registry row, when there is one, decides.
            offered_sub = _providers().is_subscription(model) and model in enabled_ids()
            if model:
                if not offered_sub:
                    row = get_row(db, model)
                    if not row['enabled']:
                        raise HTTPException(422, 'Enable the model before making it the default')
                db.execute('''INSERT INTO models_registry_settings(key,value,updated_by) VALUES ('default_model',%s,%s)
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=NOW()''', (model, actor))
            else:
                db.execute("DELETE FROM models_registry_settings WHERE key='default_model'")
            db.commit()
        invalidate()
        chosen, source = default_model()
        return {'model': chosen, 'source': source}

    @router.post('/{model_id:path}/test')
    async def test_model(model_id: str, request: Request):
        """One tiny completion (max 16 tokens) through the model's provider (OpenRouter or xAI); not recorded as a task."""
        dashboard_write(request)
        providers = _providers()
        if not (providers.is_subscription(model_id) and model_id in [m['id'] for m in subscription_entries()]):
            with connect() as db:   # subscription models may be tested without a registry row (HLA_ENGINES_V1)
                get_row(db, model_id)
        try:
            providers.resolve(model_id)
        except providers.ProviderError as exc:
            raise HTTPException(exc.status, exc.message) from None
        label = providers.label_of(model_id)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                data = await providers.complete(client, {'model': model_id, 'max_tokens': 16,
                                                         'messages': [{'role': 'user', 'content': 'Reply with one word: pong'}]})
            latency = int((time.monotonic() - started) * 1000)
            choice = (data.get('choices') or [{}])[0]
            reply = ((choice.get('message') or {}).get('content') or '').strip()
            cost = (data.get('usage') or {}).get('cost')
            return {'ok': True, 'latency_ms': latency, 'reply': reply[:120] or '(empty reply)', 'error': None,
                    'cost': cost if isinstance(cost, (int, float)) else None, 'model': data.get('model') or model_id}
        except (providers.ProviderHTTPError, ValueError) as exc:  # messages made by providers.py, never upstream request details
            return {'ok': False, 'latency_ms': int((time.monotonic() - started) * 1000), 'error': str(exc)[:300], 'reply': None, 'cost': None}
        except httpx.HTTPError as exc:
            # Never include upstream exception text (may carry request details).
            return {'ok': False, 'latency_ms': int((time.monotonic() - started) * 1000), 'reply': None, 'cost': None,
                    'error': label + ' unreachable: ' + type(exc).__name__}

    @router.patch('/{model_id:path}')
    def patch_model(model_id: str, body: ModelPatch, request: Request):
        dashboard_write(request)
        actor = None
        if body.enabled is not None:
            actor = require_approver(request)   # enabling or disabling changes what agents may run on
        with connect() as db:
            row = get_row(db, model_id)
            warning = None
            if body.enabled is False and row['enabled']:
                current = db.execute("SELECT value FROM models_registry_settings WHERE key='default_model'").fetchone()
                if current and current['value'] == model_id:
                    raise HTTPException(409, 'This is the default model: choose another default first')
                enabled_left = db.execute('SELECT COUNT(*) AS n FROM models_registry WHERE enabled AND id<>%s', (model_id,)).fetchone()['n']
                if not enabled_left:
                    raise HTTPException(409, 'At least one model must stay enabled')
                users = [u['text'] for u in usage(db).get(model_id, []) if u['type'] in ('agent', 'routing', 'project', 'env')]
                if users:
                    warning = 'Still used by ' + ', '.join(users) + ': agents on it cannot run and routing skips it until it is enabled again.'
            row = db.execute('''UPDATE models_registry SET label=COALESCE(%s,label),notes=COALESCE(%s,notes),enabled=COALESCE(%s,enabled)
                WHERE id=%s RETURNING *''', (body.label.strip() if body.label is not None else None,
                                             body.notes.strip() if body.notes is not None else None, body.enabled, model_id)).fetchone()
            db.commit()
            used = usage(db)
        invalidate()
        return {**row_out(row, used), 'warning': warning, 'changed_by': actor}

    @router.delete('/{model_id:path}')
    def delete_model(model_id: str, request: Request):
        dashboard_write(request)
        require_approver(request)
        with connect() as db:
            get_row(db, model_id)
            blocking = [u['text'] for u in usage(db).get(model_id, []) if u['blocks']]
            if blocking:
                raise HTTPException(409, 'Still used by ' + ', '.join(blocking) + '. Change those first.')
            if not db.execute('SELECT 1 FROM models_registry WHERE enabled AND id<>%s LIMIT 1', (model_id,)).fetchone():
                raise HTTPException(409, 'At least one model must stay enabled')
            db.execute('DELETE FROM models_registry WHERE id=%s', (model_id,))
            db.commit()
        invalidate()
        return {'id': model_id, 'status': 'deleted'}

    return router
