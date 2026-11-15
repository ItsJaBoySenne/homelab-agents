"""Streaming chat completion (OpenRouter or xAI, see providers.py); no credentials are exposed to the browser."""
import json

import httpx

import providers


async def generate(client: httpx.AsyncClient, payload: dict, on_delta):
    if providers.is_subscription(payload.get('model')):
        # HLA_ENGINES_V1: a subscription engine streams its CLI's text (no HTTP, no tools here).
        import engines
        providers.resolve(payload.get('model'))  # installed and signed in, else a clear ProviderError
        return providers.finish(await engines.complete(payload, on_text=on_delta), payload.get('model'))
    text_parts=[]
    usage={}
    finish_reason=None
    async with providers.stream(client, payload) as (provider, response):
        async for line in response.aiter_lines():
            if not line.startswith('data: '):
                continue
            raw=line[6:].strip()
            if not raw or raw=='[DONE]':
                continue
            event=json.loads(raw)
            if event.get('error'):
                raise ValueError(provider['label'] + ' stream error')
            if event.get('usage'):
                usage=event['usage']
            for choice in event.get('choices') or []:
                finish_reason=choice.get('finish_reason') or finish_reason
                delta=(choice.get('delta') or {}).get('content')
                if isinstance(delta,str) and delta:
                    text_parts.append(delta)
                    await on_delta(''.join(text_parts))
    return providers.finish({'choices':[{'message':{'content':''.join(text_parts)},'finish_reason':finish_reason}], 'usage':usage},
                            payload.get('model'))
