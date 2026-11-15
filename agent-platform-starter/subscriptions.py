"""The Subscriptions page API (HLA_ENGINES_V1): sign the Claude Code / Codex CLIs in with the owner's own plan.

GET  /ui/api/subscriptions                    {"engines": [engine]}
POST /ui/api/subscriptions/{id}/login         start the CLI's sign-in (idempotent while one runs) -> login
POST /ui/api/subscriptions/{id}/login/code    {"code": "..."}: the code shown after signing in (Claude) -> login
POST /ui/api/subscriptions/{id}/login/cancel  -> login
POST /ui/api/subscriptions/{id}/logout        -> engine
POST /ui/api/subscriptions/{id}/test          a tiny prompt through the engine, no tools -> {ok, ms, reply, error}

engine = {id: claude|chatgpt, label, installed, version, signed_in, account, plan, models: [{id, label}], login, last_error}
login  = {state: idle|starting|waiting|done|error, url, user_code, needs_code, message}

Reads need a dashboard session; sign-in, code, cancel and logout also need the UI header and an allowed approver (like
policy and connection changes); the test needs the UI header. Nothing here returns credential contents: account and
plan are what the CLIs' status commands report (Claude: email and subscription type; Codex: the login method).
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import engines

log = logging.getLogger('hla.subscriptions')


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=4000)


def _engine_id(engine_id):
    if engine_id not in engines.ENGINES:
        raise HTTPException(404, 'Unknown engine: use claude or chatgpt')
    return engine_id


async def engine_view(engine_id, force=False):
    info = await engines.status(engine_id, force=force)
    return {'id': engine_id, 'label': engines.ENGINES[engine_id]['label'], 'installed': info['installed'],
            'version': info['version'], 'signed_in': info['signed_in'], 'account': info['account'], 'plan': info['plan'],
            'models': engines.model_entries(engine_id), 'login': engines.login_view(engine_id), 'last_error': info['last_error']}


def build_router(dashboard_read, ui_write, require_approver):
    router = APIRouter(prefix='/ui/api/subscriptions', tags=['subscriptions'])

    def approver(request):
        ui_write(request)
        return require_approver(request)

    @router.get('')
    async def list_engines(request: Request):
        dashboard_read(request)
        return {'engines': [await engine_view(e) for e in engines.ENGINES]}

    @router.post('/{engine_id}/login')
    async def login(engine_id: str, request: Request):
        actor = approver(request)
        engine_id = _engine_id(engine_id)
        view = await engines.login_start(engine_id)
        log.info('subscription sign-in started: engine=%s actor=%s state=%s', engine_id, actor, view['state'])
        return view

    @router.post('/{engine_id}/login/code')
    async def login_code(engine_id: str, body: CodeIn, request: Request):
        approver(request)
        engine_id = _engine_id(engine_id)
        try:
            return await engines.login_code(engine_id, body.code)
        except engines.EngineError as exc:
            raise HTTPException(exc.status, exc.message) from None

    @router.post('/{engine_id}/login/cancel')
    async def login_cancel(engine_id: str, request: Request):
        approver(request)
        return await engines.login_cancel(_engine_id(engine_id))

    @router.post('/{engine_id}/logout')
    async def logout(engine_id: str, request: Request):
        actor = approver(request)
        engine_id = _engine_id(engine_id)
        await engines.logout(engine_id)
        log.info('subscription signed out: engine=%s actor=%s', engine_id, actor)
        return await engine_view(engine_id)

    @router.post('/{engine_id}/test')
    async def test(engine_id: str, request: Request):
        ui_write(request)
        engine_id = _engine_id(engine_id)
        info = await engines.status(engine_id)
        if not info['installed']:
            return {'ok': False, 'ms': 0, 'reply': None, 'error': engines.ENGINES[engine_id]['cli'] + ' is not installed'}
        if not info['signed_in']:
            return {'ok': False, 'ms': 0, 'reply': None, 'error': 'Not signed in'}
        return await engines.test(engine_id)

    return router
