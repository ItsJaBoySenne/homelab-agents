"""Model providers (HLA_XAI_V1): OpenRouter or direct xAI by model id; body, cost, errors, streaming, catalog.

python3 tests/test_providers.py   (no database, no network: a fake xAI endpoint through httpx.MockTransport)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop('XAI_API_KEY', None)
os.environ['OPENROUTER_API_KEY'] = 'or-test-key-123456'
import httpx  # noqa: E402
import providers  # noqa: E402
import model_registry  # noqa: E402
from agent_tool import runtime  # noqa: E402
from streaming.provider import generate  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

XAI_KEY = 'xai-SECRET-test-key-0123456789'
SEEN = []          # (host, path, headers, body) of every request
MODE = {'status': 200, 'error': None, 'ticks': 123_456_789, 'stream': False}


def sse(events):
    return ''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n'


def handler(request):
    body = json.loads(request.content or b'{}') if request.method == 'POST' else {}
    SEEN.append((request.url.host, request.url.path, dict(request.headers), body))
    if request.url.host == 'api.x.ai' and request.url.path == '/v1/language-models':
        return httpx.Response(200, json={'models': [
            {'id': 'grok-4-0709', 'aliases': ['grok-4', 'grok-4-latest'], 'input_modalities': ['text', 'image'],
             'prompt_text_token_price': 30000, 'completion_text_token_price': 150000},
            {'id': 'grok-3-mini', 'aliases': [], 'prompt_text_token_price': 3000, 'completion_text_token_price': 5000}]})
    if MODE['status'] != 200:
        return httpx.Response(MODE['status'], json={'code': 'x', 'error': MODE['error'] or 'nope'})
    usage = {'prompt_tokens': 1000, 'completion_tokens': 200, 'total_tokens': 1500,
             'completion_tokens_details': {'reasoning_tokens': 300}}
    if MODE['ticks'] is not None:
        usage['cost_in_usd_ticks'] = MODE['ticks']
    if body.get('stream'):
        events = [
            {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Checking '}}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                                                                 'function': {'name': 'server_diagnostic', 'arguments': '{"server":'}}]}}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"dev"}'}}]}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': usage}]
        return httpx.Response(200, text=sse(events), headers={'content-type': 'text/event-stream'})
    return httpx.Response(200, json={'id': 'x', 'model': body.get('model'), 'usage': usage,
                                     'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'pong'}, 'finish_reason': 'stop'}]})


def client():
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def main():
    # ── resolver ──
    check(providers.name_of('xai/grok-4') == 'xai' and providers.name_of('x-ai/grok-4') == 'openrouter'
          and providers.name_of('anthropic/claude-sonnet-4.5') == 'openrouter', 'resolver: xai/ is direct xAI, x-ai/ stays on OpenRouter')
    try:
        providers.resolve('xai/grok-4'); missing = None
    except providers.ProviderError as exc:
        missing = exc
    check(missing is not None and missing.status == 503 and 'XAI_API_KEY is not set' in missing.message, 'resolver: a missing XAI_API_KEY is a clear error')
    os.environ['XAI_API_KEY'] = XAI_KEY
    p = providers.resolve('xai/grok-4')
    check(p['url'] == 'https://api.x.ai/v1/chat/completions' and p['model'] == 'grok-4' and p['key'] == XAI_KEY and p['label'] == 'xAI',
          'resolver: xAI url, upstream id without the prefix, its own key')
    o = providers.resolve('x-ai/grok-4')
    check(o['url'] == providers.OPENROUTER_CHAT and o['model'] == 'x-ai/grok-4' and o['key'] == 'or-test-key-123456', 'resolver: OpenRouter for everything else')

    # ── request body ──
    payload = {'model': 'xai/grok-4', 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 50, 'temperature': 0.2,
               'tools': [{'type': 'function', 'function': {'name': 't', 'parameters': {}}}], 'tool_choice': 'auto',
               'reasoning': {'effort': 'low'}, 'provider': {'order': ['x']}, 'transforms': ['middle-out'], 'models': ['a'],
               'route': 'fallback', 'plugins': [], 'usage': {'include': True}, 'include_reasoning': True}
    b = providers.body_for(p, payload, stream=True)
    check(not set(providers.OPENROUTER_ONLY) & set(b) and b['model'] == 'grok-4' and b['stream'] is True
          and b['stream_options'] == {'include_usage': True} and b['tools'] and b['tool_choice'] == 'auto' and b['max_tokens'] == 50
          and b['temperature'] == 0.2, 'body: OpenRouter-only fields removed for xAI; tools, limits, stream usage kept')
    check(payload['model'] == 'xai/grok-4' and 'reasoning' in payload, 'body: the caller\'s payload is not changed')
    ob = providers.body_for(o, payload)
    check(ob['reasoning'] == {'effort': 'low'} and ob['provider'] and ob['stream'] is False, 'body: OpenRouter keeps its fields')

    # ── cost ──
    check(abs(providers.cost_of('xai/grok-4', {'cost_in_usd_ticks': 123_456_789}) - 0.0123456789) < 1e-12, 'cost: ticks are 1e-10 USD')
    model_registry._cache.update(at=10**12, rows=[{'id': 'xai/grok-4', 'prompt_price': 3.0, 'completion_price': 15.0, 'enabled': True}], default=None)
    import time as _t
    model_registry._cache['at'] = _t.monotonic()
    c = providers.cost_of('xai/grok-4', {'prompt_tokens': 1000, 'completion_tokens': 200, 'total_tokens': 1500})
    check(abs(c - (1000 * 3 + 500 * 15) / 1e6) < 1e-12, f'cost: from registry prices, reasoning tokens billed as output ({c})')
    check(providers.cost_of('xai/unknown-model', {'prompt_tokens': 10}) is None, 'cost: unknown without ticks or prices')

    # ── non-streaming call ──
    async with client() as cl:
        SEEN.clear()
        r = await providers.complete(cl, payload)
    host, path, headers, sent = SEEN[-1]
    check(host == 'api.x.ai' and path == '/v1/chat/completions' and headers['authorization'] == 'Bearer ' + XAI_KEY
          and sent['model'] == 'grok-4' and 'reasoning' not in sent, 'complete: sent to xAI with its key and a clean body')
    check(r['usage']['cost'] == round(123_456_789 / 1e10, 8) and r['model'] == 'xai/grok-4' and r['choices'][0]['message']['content'] == 'pong',
          'complete: usage.cost in USD from ticks, registry model id')
    MODE['ticks'] = None
    async with client() as cl:
        r = await providers.complete(cl, payload)
    check(abs(r['usage']['cost'] - 0.0105) < 1e-9, f'complete: cost from prices when no ticks ({r["usage"].get("cost")})')
    MODE['ticks'] = 123_456_789

    # ── error mapping ──
    for status, text, want in ((401, 'Incorrect API key provided', 'xAI refused the API key (XAI_API_KEY, HTTP 401)'),
                               (429, 'Your team has run out of credits', 'xAI rate limit reached or no credits left (HTTP 429)'),
                               (400, 'Model grok-9 does not exist', 'xAI answered HTTP 400: Model grok-9 does not exist')):
        MODE.update(status=status, error=text)
        async with client() as cl:
            try:
                await providers.complete(cl, payload); err = None
            except providers.ProviderHTTPError as exc:
                err = exc
        check(err is not None and isinstance(err, httpx.HTTPStatusError) and err.response.status_code == status and want in str(err)
              and XAI_KEY not in str(err), f'errors: HTTP {status} -> "{str(err)[:90]}"')
    MODE.update(status=200, error=None)

    # ── streaming (agent runtime) with tool calls and the usage chunk ──
    shown = []
    async def on_text(t): shown.append(t)
    async with client() as cl:
        SEEN.clear()
        r = await runtime._stream(cl, 'ignored', payload, on_text)
    sent = SEEN[-1][3]
    call = r['choices'][0]['message'].get('tool_calls') or [{}]
    check(sent['stream'] is True and sent['stream_options'] == {'include_usage': True} and 'reasoning' not in sent, 'stream: request to xAI with usage')
    check(call[0].get('id') == 'call_1' and call[0]['function']['name'] == 'server_diagnostic'
          and json.loads(call[0]['function']['arguments']) == {'server': 'dev'} and r['choices'][0]['finish_reason'] == 'tool_calls',
          'stream: tool call reassembled from deltas')
    check(shown and shown[-1] == 'Checking ' and r['usage']['cost'] == round(123_456_789 / 1e10, 8), 'stream: text shown live, cost from the usage chunk')
    async with client() as cl:
        g = await generate(cl, {**payload, 'tools': None}, on_text)
    check(g['usage']['cost'] == round(123_456_789 / 1e10, 8), 'stream (plain runs): cost added too')
    MODE.update(status=429, error='out of credits')
    async with client() as cl:
        try:
            await runtime._stream(cl, 'ignored', payload, on_text); err = None
        except providers.ProviderHTTPError as exc:
            err = exc
    check(err is not None and 'no credits left' in str(err), 'stream: HTTP errors mapped before reading the stream')
    MODE.update(status=200, error=None)

    # ── xAI model list ──
    check(providers.xai_price_per_million(30000) == 3.0 and providers.xai_price_per_million(150000) == 15.0
          and providers.xai_price_per_million(None) is None, 'catalog: xAI token prices (1e-10 USD/token) -> USD per 1M (grok-4 $3/$15)')
    async with client() as cl:
        entries = await providers.xai_models(cl)
    ids = [e['id'] for e in entries]
    g4 = next(e for e in entries if e['id'] == 'xai/grok-4-0709')
    check(ids == ['xai/grok-4-0709', 'xai/grok-4', 'xai/grok-4-latest', 'xai/grok-3-mini'] and g4['prompt_price'] == 3.0
          and g4['completion_price'] == 15.0 and g4['supports_tools'], 'catalog: ids with aliases, prices per 1M')
    real = httpx.AsyncClient
    class Mock(real):
        def __init__(self, *a, **kw):
            kw['transport'] = httpx.MockTransport(handler); super().__init__(*a, **kw)
    model_registry.httpx.AsyncClient = Mock
    models, at, stale, error = await model_registry.xai_catalog(force=True)
    check(error is None and len(models) == 4 and at, 'registry: xAI catalog fetched with the key')
    os.environ.pop('XAI_API_KEY')
    model_registry._xai_catalog.update(at=0.0, models=None)
    models, at, stale, error = await model_registry.xai_catalog(force=True)
    check(models == [] and error == 'XAI_API_KEY not set', 'registry: without the key the xAI list says so')
    model_registry.httpx.AsyncClient = real
    check(XAI_KEY not in json.dumps(entries), 'the key is never part of a result')


asyncio.run(main())
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
