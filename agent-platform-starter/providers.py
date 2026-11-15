"""Model providers (HLA_XAI_V1): every chat completion goes through here, so any registry model works everywhere
(agents, conversation overrides, planner, review and summary routing, the Models page test).

  - `xai/<model>` (e.g. xai/grok-4) goes directly to xAI (https://api.x.ai/v1, key XAI_API_KEY).
  - `claude-sub/<model>` and `chatgpt-sub/<model>` (HLA_ENGINES_V1) run on the owner's Claude / ChatGPT plan through the
    vendors' official CLIs (engines.py): no API key, usage.cost 0 and usage.billing 'subscription'.
  - Everything else, including OpenRouter's own `x-ai/...` ids, goes to OpenRouter (key OPENROUTER_API_KEY).

Keys come from the router environment only; they are never logged, returned or put in an error message.
xAI replies are normalised to what the rest of the platform expects from OpenRouter: `usage.cost` in USD (from xAI's
`cost_in_usd_ticks`, 1 tick = 1e-10 USD, else from the registry's per-1M prices), so budgets, the Costs page and limits
keep working. OpenRouter-only request fields are removed before a request goes to xAI.
"""
import contextlib
import json
import os

import httpx

OPENROUTER_CHAT = 'https://openrouter.ai/api/v1/chat/completions'
XAI_BASE = 'https://api.x.ai/v1'
XAI_CHAT = XAI_BASE + '/chat/completions'
XAI_MODELS = XAI_BASE + '/language-models'
XAI_PREFIX = 'xai/'
TICKS_PER_USD = 10_000_000_000      # xAI's cost_in_usd_ticks: 1 tick = 1e-10 USD
# Request fields only OpenRouter understands; xAI refuses or ignores them.
OPENROUTER_ONLY = ('provider', 'transforms', 'models', 'route', 'plugins', 'usage', 'reasoning', 'include_reasoning')
LABELS = {'openrouter': 'OpenRouter', 'xai': 'xAI', 'claude-sub': 'Claude (subscription)', 'chatgpt-sub': 'ChatGPT (subscription)'}
ENV_KEYS = {'openrouter': 'OPENROUTER_API_KEY', 'xai': 'XAI_API_KEY'}
# HLA_ENGINES_V1: providers without an API key: an official CLI signed in with the owner's subscription.
SUBSCRIPTION = {'claude-sub': 'claude', 'chatgpt-sub': 'chatgpt'}


class ProviderError(ValueError):
    """A provider problem with a message safe to show (never a key); not retried."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class ProviderHTTPError(httpx.HTTPStatusError):
    """httpx.HTTPStatusError with a clear message, so the existing retry and error handling keep working."""


def name_of(model):
    model = str(model or '')
    for name in SUBSCRIPTION:
        if model.startswith(name + '/'):
            return name
    return 'xai' if model.startswith(XAI_PREFIX) else 'openrouter'


def is_subscription(model_or_name):
    """True for a subscription model id (claude-sub/..., chatgpt-sub/...) or provider name."""
    value = str(model_or_name or '')
    return value in SUBSCRIPTION or name_of(value) in SUBSCRIPTION


def billing_of(model):
    return 'subscription' if is_subscription(model) else 'usage'


def label_of(model):
    return LABELS[name_of(model)]


def key_configured(name):
    if name in SUBSCRIPTION:   # no key: the engine's CLI is installed and signed in
        import engines
        return engines.signed_in_fast(SUBSCRIPTION[name])
    return bool(os.getenv(ENV_KEYS[name], '').strip())


def resolve(model):
    """{name, label, url, key, model (the provider's own id)} or ProviderError when the key is missing."""
    name = name_of(model)
    if name in SUBSCRIPTION:
        import engines
        engine = SUBSCRIPTION[name]
        if not engines.binary(engine):
            raise ProviderError(503, engines.ENGINES[engine]['cli'] + ' is not installed in the router image (README: '
                                     'Subscription engines)')
        if not engines.signed_in_fast(engine):
            raise ProviderError(503, LABELS[name] + ' is not signed in: sign in on the Subscriptions page')
        return {'name': name, 'label': LABELS[name], 'url': None, 'key': '', 'model': engines.upstream(model),
                'engine': engine, 'billing': 'subscription'}
    key = os.getenv(ENV_KEYS[name], '').strip()
    if not key:
        raise ProviderError(503, f'{ENV_KEYS[name]} is not set in the router environment: add it to .env (back the file up '
                                 'first) and recreate agent-router' if name == 'xai' else 'Missing server setting: OPENROUTER_API_KEY')
    upstream = model[len(XAI_PREFIX):] if name == 'xai' else model
    return {'name': name, 'label': LABELS[name], 'url': XAI_CHAT if name == 'xai' else OPENROUTER_CHAT, 'key': key, 'model': upstream}


def body_for(provider, payload, stream=False):
    """The request body for this provider: the upstream model id; for xAI without OpenRouter-only fields."""
    body = dict(payload)
    body['model'] = provider['model']
    if provider['name'] == 'xai':
        for field in OPENROUTER_ONLY:
            body.pop(field, None)
    if stream:
        body['stream'] = True
        body['stream_options'] = {'include_usage': True}
    else:
        body.pop('stream_options', None)
        body['stream'] = False
    return body


def _registry_prices(model):
    try:
        import model_registry
        rows, _ = model_registry._load()
    except Exception:  # noqa: BLE001 - no prices is fine: the cost stays unknown
        return None, None
    row = next((r for r in rows or [] if r.get('id') == model), None)
    if not row:
        return None, None
    num = lambda v: float(v) if v is not None else None
    return num(row.get('prompt_price')), num(row.get('completion_price'))


def cost_of(model, usage):
    """USD of one xAI call: cost_in_usd_ticks when present, else tokens x the registry's per-1M prices, else None."""
    usage = usage or {}
    ticks = usage.get('cost_in_usd_ticks')
    if isinstance(ticks, (int, float)) and not isinstance(ticks, bool) and ticks >= 0:
        return ticks / TICKS_PER_USD
    prompt_price, completion_price = _registry_prices(model)
    if prompt_price is None and completion_price is None:
        return None
    prompt = usage.get('prompt_tokens') or 0
    output = usage.get('completion_tokens') or 0
    # Reasoning tokens are billed as output; xAI reports them apart (total = prompt + completion + reasoning).
    total = usage.get('total_tokens')
    if isinstance(total, int) and total - prompt > output:
        output = total - prompt
    return prompt * (prompt_price or 0) / 1_000_000 + output * (completion_price or 0) / 1_000_000


def finish(result, model):
    """Normalise a reply (in place): usage.cost in USD for xAI, the registry model id instead of the upstream one;
    subscription engines cost nothing per call (usage.cost 0, usage.billing 'subscription')."""
    if is_subscription(model) and isinstance(result, dict):
        if not isinstance(result.get('usage'), dict):
            result['usage'] = {}
        result['usage'].update(cost=0.0, billing='subscription')
        result['model'] = model
        return result
    if name_of(model) != 'xai' or not isinstance(result, dict):
        return result
    usage = result.get('usage')
    if isinstance(usage, dict) and not isinstance(usage.get('cost'), (int, float)):
        cost = cost_of(model, usage)
        if cost is not None:
            usage['cost'] = round(cost, 8)
    result['model'] = model
    return result


def error_message(name, status, data):
    """A clear message for an HTTP error of a provider."""
    label = LABELS.get(name, name)
    detail = ''
    if isinstance(data, dict):
        err = data.get('error')
        detail = (err.get('message') if isinstance(err, dict) else err) or data.get('message') or data.get('code') or ''
    detail = str(detail or '').strip()[:300]
    if status in (401, 403):
        return f'{label} refused the API key ({ENV_KEYS.get(name, "key")}, HTTP {status})' + (f': {detail}' if detail else '')
    if status == 429:
        return f'{label} rate limit reached or no credits left (HTTP 429)' + (f': {detail}' if detail else '')
    if status == 404:
        return f'{label} does not know this model (HTTP 404)' + (f': {detail}' if detail else '')
    return f'{label} answered HTTP {status}' + (f': {detail}' if detail else '')


def _raise_for(provider, response, data):
    message = error_message(provider['name'], response.status_code, data)
    raise ProviderHTTPError(message, request=response.request, response=response)


def _json(raw):
    try:
        data = json.loads(raw or b'{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def complete(client, payload, timeout=httpx.USE_CLIENT_DEFAULT):
    """One non-streaming chat completion with the provider of payload['model']; the reply normalised by finish()."""
    model = payload.get('model')
    provider = resolve(model)
    if provider['name'] in SUBSCRIPTION:   # HLA_ENGINES_V1: the CLI, no HTTP call
        import engines
        return finish(await engines.complete(payload, timeout=timeout if isinstance(timeout, (int, float)) else None), model)
    response = await client.post(provider['url'], headers={'Authorization': 'Bearer ' + provider['key']},
                                 json=body_for(provider, payload), timeout=timeout)
    data = _json(response.content)
    if response.status_code >= 400:
        _raise_for(provider, response, data)
    if data.get('error') and not data.get('choices'):
        err = data['error']
        raise ValueError(provider['label'] + ' error: ' + str(err.get('message') if isinstance(err, dict) else err)[:200])
    return finish(data, model)


@contextlib.asynccontextmanager
async def stream(client, payload, timeout=httpx.USE_CLIENT_DEFAULT):
    """A streaming chat completion: yields (provider, response) after the status was checked. Not for subscription
    engines: they stream through engines.complete(on_text=...) (streaming/provider.py, agent_tool/runtime.py)."""
    provider = resolve(payload.get('model'))
    if provider['name'] in SUBSCRIPTION:
        raise ProviderError(500, 'Subscription engines do not stream over HTTP')
    async with client.stream('POST', provider['url'], headers={'Authorization': 'Bearer ' + provider['key']},
                             json=body_for(provider, payload, stream=True), timeout=timeout) as response:
        if response.status_code >= 400:
            _raise_for(provider, response, _json(await response.aread()))
        yield provider, response


# ---------------------------------------------------------------- xAI model list (Models page)

def xai_price_per_million(value):
    """xAI's *_token_price fields are USD ticks (1e-10 USD) per token, i.e. cents per 100M tokens: /10000 = USD per 1M.
    grok-4: prompt_text_token_price 30000 -> $3, completion_text_token_price 150000 -> $15."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return round(value / 10_000, 6)


def xai_catalog_entries(data):
    """Entries in the Models page catalog shape from GET /v1/language-models."""
    out = []
    for m in (data or {}).get('models') or []:
        if not isinstance(m, dict) or not isinstance(m.get('id'), str):
            continue
        aliases = [a for a in m.get('aliases') or [] if isinstance(a, str)]
        modalities = m.get('input_modalities') or []
        entry = {'name': 'xAI: ' + m['id'] + (' (' + ', '.join(aliases[:3]) + ')' if aliases else ''),
                 'context_length': m.get('context_length') if isinstance(m.get('context_length'), int) else None,
                 'prompt_price': xai_price_per_million(m.get('prompt_text_token_price')),
                 'completion_price': xai_price_per_million(m.get('completion_text_token_price')),
                 'supported_parameters': ['tools', 'tool_choice', 'max_tokens', 'temperature'], 'supports_tools': True,
                 'description': 'Direct xAI API (XAI_API_KEY). Input: ' + ', '.join(map(str, modalities)) if modalities else 'Direct xAI API (XAI_API_KEY).',
                 'provider': 'xai', 'supports_images': ('image' in modalities) if modalities else None}
        out.append({**entry, 'id': XAI_PREFIX + m['id']})
        for alias in aliases:
            out.append({**entry, 'id': XAI_PREFIX + alias, 'name': 'xAI: ' + alias + ' (alias of ' + m['id'] + ')'})
    return out


async def xai_models(client):
    """GET /v1/language-models with the key -> catalog entries; ProviderError without a key, HTTP errors mapped."""
    provider = resolve(XAI_PREFIX + 'models')
    response = await client.get(XAI_MODELS, headers={'Authorization': 'Bearer ' + provider['key']}, timeout=20)
    data = _json(response.content)
    if response.status_code >= 400:
        raise ProviderError(502, error_message('xai', response.status_code, data))
    return xai_catalog_entries(data)
