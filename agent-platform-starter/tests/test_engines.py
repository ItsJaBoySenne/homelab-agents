"""Subscription engines (HLA_ENGINES_V1): Claude Code / Codex CLIs as providers, the tool relay, sign-in, the API.

python3 tests/test_engines.py
  Part 1 needs nothing (fake CLIs in tests/fake_engines, no network, no real accounts). Part 2 (dashboard API, registry,
  a dashboard task on a subscription model, Costs) needs a throwaway Postgres like the other backend suites:
  TEST_DATABASE_URL=... I_UNDERSTAND_THIS_WIPES_TEST_DB=1 python3 tests/test_engines.py
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAKES = ROOT / 'tests' / 'fake_engines'
ENGINES_DIR = tempfile.mkdtemp(prefix='mm-engines-', dir='/tmp')
# Router secrets with unique values: none of them may reach an engine process.
SECRETS = {'TOOL_GATEWAY_TOKEN': 'gw-SECRET-' + 'g' * 40, 'OPENROUTER_API_KEY': 'or-SECRET-0123456789',
           'XAI_API_KEY': 'xai-SECRET-0123456789', 'POLICY_ADMIN_TOKEN': 'admin-SECRET-' + 'a' * 40,
           'DASHBOARD_PASSWORD': 'pw', 'AGENT_PLATFORM_TOKEN': 't' * 64, 'TYPESAFE_API_KEY': 'typesafe-SECRET-1',
           'HLA_TEST_DB_PASSWORD': 'db-SECRET-pass'}
os.environ.update(SECRETS)
os.environ.update({'HLA_ENGINES_DIR': ENGINES_DIR, 'HLA_CLAUDE_BIN': str(FAKES / 'claude'),
                   'HLA_CODEX_BIN': str(FAKES / 'codex'), 'HLA_ENGINE_TIMEOUT': '60'})
if os.environ.get('TEST_DATABASE_URL'):
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']

import httpx  # noqa: E402
import engines  # noqa: E402
import providers  # noqa: E402
import model_registry  # noqa: E402
import subscriptions  # noqa: E402
from agent_tool import runtime  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)


def home(engine):
    return engines.home(engine)


def scenario(engine, steps, **extra):
    (home(engine) / 'fake.json').write_text(json.dumps({'steps': steps, **extra}))


def last_run(engine):
    return json.loads((home(engine) / 'last_run.json').read_text())


def sign_in(engine):
    path = home(engine) / engines.ENGINES[engine]['credentials'][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fake": true}')
    engines.invalidate(engine)


def sign_out(engine):
    (home(engine) / engines.ENGINES[engine]['credentials'][0]).unlink(missing_ok=True)
    engines.invalidate(engine)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # a zombie (exited, not yet reaped by its parent) counts as gone
        return Path(f'/proc/{pid}/stat').read_text().split(') ')[1].split()[0] != 'Z'
    except OSError:
        return False


GATEWAY = []   # every request the "gateway" received


def gateway(request):
    body = json.loads(request.content or b'{}') if request.method == 'POST' else dict(request.url.params)
    GATEWAY.append({'path': request.url.path, 'body': body, 'task': request.headers.get('x-hla-task'),
                    'auth': request.headers.get('authorization', '')})
    if request.url.path == '/v1/execute':
        return httpx.Response(200, json={'server': body.get('server'), 'exit_code': 0, 'stdout': '/dev/sda1 42% /',
                                         'diagnostic': body.get('diagnostic')})
    return httpx.Response(404, json={'error': 'not_found'})


def gateway_client():
    return httpx.AsyncClient(transport=httpx.MockTransport(gateway))


DIAG = {'server': 'dev', 'diagnostic': 'disk'}
PERMS = {'dev': {'level': 1}}

# ================================================================ part 1: no database

# ── ids and routing ──
check(providers.name_of('claude-sub/opus') == 'claude-sub' and providers.name_of('chatgpt-sub/gpt-6.1-sol') == 'chatgpt-sub'
      and providers.name_of('anthropic/claude-sonnet-4.5') == 'openrouter' and providers.name_of('xai/grok-4') == 'xai',
      'routing: claude-sub/ and chatgpt-sub/ are subscription engines; anthropic/... stays OpenRouter')
check(providers.LABELS['claude-sub'] == 'Claude (subscription)' and providers.LABELS['chatgpt-sub'] == 'ChatGPT (subscription)',
      'labels: provider labels for the subscription engines')
check(engines.engine_of('claude-sub/sonnet') == 'claude' and engines.engine_of('chatgpt-sub/gpt-5.5') == 'chatgpt'
      and engines.engine_of('openai/gpt-5') is None, 'engines: model id -> engine')
try:
    engines.upstream('claude-sub/--tools=Bash'); bad = False
except providers.ProviderError:
    bad = True
check(bad and engines.upstream('claude-sub/opus') == 'opus', 'engines: only plain model names reach the CLI (no option injection)')
sign_out('claude')
try:
    providers.resolve('claude-sub/sonnet'); err = None
except providers.ProviderError as exc:
    err = exc
check(err is not None and err.status == 503 and 'not signed in' in err.message and not providers.key_configured('claude-sub'),
      'resolve: a signed-out engine is a clear 503 (key_configured false)')
os.environ['HLA_CODEX_BIN'] = '/nonexistent/codex'
try:
    providers.resolve('chatgpt-sub/gpt-6.1-sol'); err = None
except providers.ProviderError as exc:
    err = exc
check(err is not None and 'not installed' in err.message, 'resolve: a missing CLI says it is not installed')
os.environ['HLA_CODEX_BIN'] = str(FAKES / 'codex')
sign_in('claude')
p = providers.resolve('claude-sub/sonnet')
check(p['name'] == 'claude-sub' and p['key'] == '' and p['url'] is None and p['model'] == 'sonnet' and p['billing'] == 'subscription'
      and providers.key_configured('claude-sub'), 'resolve: signed in -> no key, no URL, the CLI model name')
r = providers.finish({'choices': [{'message': {'content': 'x'}}], 'usage': {'prompt_tokens': 5, 'cost': 1.5}}, 'claude-sub/opus')
check(r['usage']['cost'] == 0.0 and r['usage']['billing'] == 'subscription' and providers.billing_of('claude-sub/opus') == 'subscription'
      and providers.billing_of('openai/gpt-5') == 'usage', 'cost: subscription replies cost 0 with billing subscription')
check(not __import__('attachments').model_sees_images('claude-sub/opus'), 'attachments: subscription engines get text only')
check([m['id'] for m in engines.model_entries('claude')][:2] == ['claude-sub/sonnet', 'claude-sub/opus']
      and all(m['label'].endswith('(subscription)') for m in engines.model_entries('chatgpt')), 'models: ids and labels per engine')
os.environ['HLA_CLAUDE_SUB_MODELS'] = 'sonnet=Sonnet 5,opus,bad model'
check([m['id'] for m in engines.model_entries('claude')] == ['claude-sub/sonnet', 'claude-sub/opus'], 'models: env list overrides, bad names dropped')
del os.environ['HLA_CLAUDE_SUB_MODELS']
entries = model_registry.subscription_entries()
check({e['id'] for e in entries} >= {'claude-sub/sonnet', 'chatgpt-sub/gpt-6.1-sol'} and all(e['prompt_price'] == 0 and e['billing'] == 'subscription'
      and e['supports_tools'] for e in entries) and next(e for e in entries if e['id'] == 'claude-sub/opus')['available'],
      'registry catalog: subscription models, price 0, tools, available while signed in')
check('claude-sub/opus' in model_registry.subscription_ids([]) and 'chatgpt-sub/gpt-5.5' not in model_registry.subscription_ids([])
      and 'claude-sub/opus' not in model_registry.subscription_ids([{'id': 'claude-sub/opus'}]),
      'registry: signed-in engines offered automatically; a registry row decides for its own id')


async def part1():
    # ── one-shot completion (planner, summaries, model test, streaming chat) ──
    scenario('claude', [{'text': 'Hello from the plan.'}])
    shown = []
    async def on_delta(text): shown.append(text)
    from streaming.provider import generate
    async with gateway_client() as client:
        r = await generate(client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'system', 'content': 'Be brief.'},
                                                                                {'role': 'user', 'content': 'Say hello'}]}, on_delta)
        check(r['choices'][0]['message']['content'] == 'Hello from the plan.' and shown and shown[-1] == 'Hello from the plan.'
              and len(shown) > 1, 'streaming: CLI text deltas reach on_delta (cumulative)')
        check(r['usage']['cost'] == 0.0 and r['usage']['billing'] == 'subscription' and r['usage']['prompt_tokens'] == 150
              and r['usage']['completion_tokens'] == 40 and r['model'] == 'claude-sub/sonnet', 'usage: CLI tokens mapped, cost 0')
        run = last_run('claude')
        check(run['system'] == 'Be brief.' and run['prompt'] == 'Say hello', 'prompt: system messages -> --system-prompt-file, user text -> stdin')
        r = await providers.complete(client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'user', 'content': 'x'}]})
        check(r['choices'][0]['message']['content'] == 'Hello from the plan.' and not GATEWAY, 'providers.complete: routed to the engine, no HTTP')

    argv = last_run('claude')['argv']
    def after(flag): return argv[argv.index(flag) + 1] if flag in argv else None
    check('-p' in argv and after('--tools') == '' and '--strict-mcp-config' in argv and after('--permission-mode') == 'dontAsk'
          and after('--permission-prompts') == 'none' and after('--output-format') == 'stream-json' and after('--model') == 'sonnet'
          and '--no-session-persistence' in argv and '--disable-slash-commands' in argv,
          'flags (claude): print mode, --tools "" (no built-ins), strict MCP config, dontAsk, stream-json')
    dis = argv[argv.index('--disallowedTools') + 1:]
    check(all(t in dis for t in ('Bash', 'Edit', 'Write', 'Read', 'WebFetch', 'WebSearch')) and '--allowedTools' not in argv,
          'flags (claude): built-ins denied by name too; no relay or allowed tools without platform tools')

    # ── the tool relay: the CLI's tool call runs through the platform's loop ──
    GATEWAY.clear()
    scenario('claude', [{'text': 'Checking the disk. '}, {'call': {'name': 'server_diagnostic', 'arguments': DIAG}},
                        {'call': {'name': 'server_diagnostic', 'arguments': {'server': 'prod', 'diagnostic': 'disk'}}},
                        {'call': {'name': 'server_execute_approved', 'arguments': {'server': 'dev', 'command': 'rm -rf /'}}},
                        {'text': 'Disk is at 42%.'}])
    progress, evidence, texts, timings = [], [], [], []
    async def on_progress(step): progress.append(step)
    async def on_evidence(call, reply): evidence.append((call, reply))
    async def on_text(text): texts.append(text)
    task_id, agent_id = uuid.uuid4(), uuid.uuid4()
    async with gateway_client() as client:
        result = await runtime.completion_with_tools(
            client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'user', 'content': 'How full is the disk on dev?'}]}, '',
            agent_id=agent_id, permissions=PERMS, on_progress=on_progress, on_evidence=on_evidence, on_text=on_text,
            task_id=task_id, on_timing=timings.append)
    run = last_run('claude')
    executes = [g for g in GATEWAY if g['path'] == '/v1/execute']
    check(len(executes) == 1 and executes[0]['body'] == {**DIAG, 'mode': 'diagnostic', 'agent_id': str(agent_id)}
          and executes[0]['task'] == str(task_id) and executes[0]['auth'] == 'Bearer ' + SECRETS['TOOL_GATEWAY_TOKEN'],
          'relay: the allowed call ran through the gateway (agent id, task header for the audit) by the platform')
    check(json.loads(run['results'][0]['text'])['stdout'] == '/dev/sda1 42% /' and not run['results'][0]['isError'],
          'relay: the gateway result went back to the CLI')
    check(json.loads(run['results'][1]['text']).get('error') == 'invalid_or_disallowed_tool_arguments',
          'relay: a server the agent may not use is refused by the platform (never sent to the gateway)')
    check(run['tools'] == ['server_diagnostic', 'knowledge_search', 'knowledge_save']
          or set(run['tools']) == {t['function']['name'] for t in runtime.build_tools(PERMS)[0]},
          'relay: offers exactly the tools build_tools() made for this run')
    check('server_execute_approved' not in run['tools'] and json.loads(run['results'][2]['text']).get('error') == 'unknown_tool',
          'relay: a tool not offered in this run cannot be called')
    done = [s for s in progress if 'status' not in s or s.get('status') != 'running']
    check(len(done) == 2 and done[0]['tool'] == 'server_diagnostic' and done[0]['target'].startswith('dev')
          and any(s.get('status') == 'running' for s in progress), 'progress: steps recorded like any model (running, then done)')
    check(len(evidence) == 1 and evidence[0][0]['diagnostic'] == 'disk', 'facts: the verified gateway result became evidence')
    message = result['choices'][0]['message']
    check(message['content'] == 'Disk is at 42%.' and result['choices'][0]['finish_reason'] == 'stop', 'result: the CLI final answer')
    check(result['usage']['cost'] == 0.0 and result['usage']['billing'] == 'subscription' and result['usage']['prompt_tokens'] > 0,
          'result: usage cost 0, billing subscription')
    check(texts and texts[-1] == 'Disk is at 42%.', 'streaming: text of each round reaches on_text')
    check(any(t.get('stage') == 'model' for t in timings) and any(t.get('stage') == 'tool' for t in timings), 'timings recorded')
    env = run['env']
    leaked = [k for k, v in SECRETS.items() if k in env or any(v in str(x) for x in env.values())]
    check(not leaked and 'DATABASE_URL' not in env and set(env) - {'PWD', 'SHLVL', '_', 'OLDPWD'} <= {
          'PATH', 'HOME', 'LANG', 'TERM', 'NO_COLOR', 'BROWSER', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'TZ',
          'DISABLE_AUTOUPDATER', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'MCP_TIMEOUT', 'MCP_TOOL_TIMEOUT'},
          'env: scrubbed; no router secret (names or values) reaches the CLI ' + str(leaked))
    check(env['HOME'] == str(home('claude')) and run['cwd_files'] == [] and run['cwd'].startswith(ENGINES_DIR + '/runs/')
          and env['TMPDIR'].startswith(ENGINES_DIR + '/runs/'), 'sandbox: HOME is the engine dir, cwd an empty per-run dir')
    check(not any((Path(ENGINES_DIR) / 'runs').iterdir()), 'cleanup: the run directory (socket, configs) is removed')
    argv = run['argv']
    check(argv[argv.index('--allowedTools') + 1] == 'mcp__hla' and '--mcp-config' in argv,
          'flags (claude): the relay is the only MCP server, its tools allowed')
    check(oct(os.stat(home('claude')).st_mode & 0o777) == '0o700', 'credentials dir is chmod 700')

    # ── parallel calls of one turn come back as one model reply ──
    GATEWAY.clear()
    scenario('claude', [{'calls': [{'name': 'server_diagnostic', 'arguments': DIAG},
                                   {'name': 'server_diagnostic', 'arguments': {'server': 'dev', 'diagnostic': 'system'}}]},
                        {'text': 'Both checked.'}])
    rounds = []
    async with gateway_client() as client:
        result = await runtime.completion_with_tools(
            client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'user', 'content': 'check'}]}, '',
            agent_id=agent_id, permissions=PERMS, on_timing=rounds.append)
    model_rounds = [t for t in rounds if t.get('stage') == 'model']
    check(len([g for g in GATEWAY if g['path'] == '/v1/execute']) == 2 and model_rounds[0]['tool_calls'] == 2
          and result['choices'][0]['message']['content'] == 'Both checked.', 'relay: parallel calls arrive as one reply with two tool calls')

    # ── the platform's tool budget limits the CLI ──
    GATEWAY.clear()
    scenario('claude', [{'call': {'name': 'server_diagnostic', 'arguments': DIAG}} for _ in range(7)] + [{'text': 'Report.'}])
    async with gateway_client() as client:
        result = await runtime.completion_with_tools(
            client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'user', 'content': 'loop'}]}, '',
            agent_id=agent_id, permissions=PERMS, tool_budget=4)
    check(len([g for g in GATEWAY if g['path'] == '/v1/execute']) == 4 and result['hla']['budget_exhausted']
          and result['choices'][0]['message']['content'], 'budget: max tool calls enforced by the platform (4), then a report')

    # ── cancellation kills the CLI's process group ──
    scenario('claude', [{'text': 'Working'}, {'hang': 120}])
    (home('claude') / 'pid').unlink(missing_ok=True)
    async def long_run():
        async with gateway_client() as client:
            return await runtime.completion_with_tools(
                client, {'model': 'claude-sub/sonnet', 'messages': [{'role': 'user', 'content': 'hang'}]}, '',
                agent_id=agent_id, permissions=PERMS)
    job = asyncio.create_task(long_run())
    for _ in range(100):
        if (home('claude') / 'pid').exists() and (home('claude') / 'pid').read_text():
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    pid = int((home('claude') / 'pid').read_text())
    check(alive(pid), 'cancel: the CLI runs')
    job.cancel()
    try:
        await job
    except asyncio.CancelledError:
        pass
    for _ in range(60):
        if not alive(pid): break
        await asyncio.sleep(0.05)
    check(not alive(pid), 'cancel: stopping the run kills the CLI process')
    check(not any((Path(ENGINES_DIR) / 'runs').iterdir()), 'cancel: run directory removed')

    # ── errors: plan limit, not signed in ──
    scenario('claude', [{'error': "Claude AI usage limit reached|1763300000"}])
    try:
        await engines.complete({'model': 'claude-sub/opus', 'messages': [{'role': 'user', 'content': 'x'}]}); err = None
    except providers.ProviderError as exc:
        err = exc
    check(err is not None and 'plan usage limit reached' in err.message, 'errors: plan limit reached is reported as such (no retry loop)')
    sign_out('claude')
    async with gateway_client() as client:
        try:
            await providers.complete(client, {'model': 'claude-sub/opus', 'messages': [{'role': 'user', 'content': 'x'}]}); err = None
        except providers.ProviderError as exc:
            err = exc
    check(err is not None and 'not signed in' in err.message, 'errors: a signed-out engine fails before starting the CLI')

    # ── Codex: same relay, its own flags ──
    sign_in('chatgpt')
    GATEWAY.clear()
    scenario('chatgpt', [{'call': {'name': 'server_diagnostic', 'arguments': DIAG}}, {'text': 'Codex says 42%.'}], usage={'input': 300, 'output': 70})
    async with gateway_client() as client:
        result = await runtime.completion_with_tools(
            client, {'model': 'chatgpt-sub/gpt-6.1-sol', 'messages': [{'role': 'system', 'content': 'Agent "rules".\nTwo lines.'},
                                                                       {'role': 'user', 'content': 'disk?'}]}, '',
            agent_id=agent_id, permissions=PERMS)
    run = last_run('chatgpt')
    argv = run['argv']
    disabled = [argv[i + 1] for i, a in enumerate(argv) if a == '--disable']
    check(argv[0] == 'exec' and '--json' in argv and argv[argv.index('-s') + 1] == 'read-only' and argv[argv.index('-m') + 1] == 'gpt-6.1-sol'
          and 'approval_policy="never"' in argv and 'web_search="disabled"' in argv and argv[-1] == '-'
          and '--ignore-user-config' in argv and argv[argv.index('-C') + 1] == run['cwd'],
          'flags (codex): exec --json, read-only sandbox, no approvals, no web search, empty workdir, prompt on stdin')
    check({'shell_tool', 'unified_exec', 'apps', 'plugins', 'browser_use', 'computer_use', 'image_generation', 'multi_agent'} <= set(disabled),
          'flags (codex): shell and the other optional tools disabled')
    check('Agent "rules".\nTwo lines.' in run['system'] and run['prompt'] == 'disk?',
          'prompt (codex): system text as developer_instructions (TOML string), user text on stdin')
    check(len([g for g in GATEWAY if g['path'] == '/v1/execute']) == 1 and json.loads(run['results'][0]['text'])['exit_code'] == 0
          and result['choices'][0]['message']['content'] == 'Codex says 42%.' and result['usage']['prompt_tokens'] == 300
          and result['usage']['cost'] == 0.0, 'relay (codex): tool via the platform, answer and usage mapped')
    env = run['env']
    check(not [k for k, v in SECRETS.items() if k in env or any(v in str(x) for x in env.values())] and env['CODEX_HOME'] == str(home('chatgpt') / '.codex'),
          'env (codex): scrubbed; CODEX_HOME in the engine dir')
    scenario('chatgpt', [{'error': "You've hit your usage limit. Try again later."}])
    try:
        await engines.complete({'model': 'chatgpt-sub/gpt-5.5', 'messages': [{'role': 'user', 'content': 'x'}]}); err = None
    except providers.ProviderError as exc:
        err = exc
    check(err is not None and 'plan usage limit reached' in err.message and err.message.startswith('ChatGPT (subscription)'),
          'errors (codex): usage limit reported clearly')

    # ── prompts for longer conversations ──
    system, prompt = engines.split_messages([{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'first'},
                                             {'role': 'assistant', 'content': 'answer'},
                                             {'role': 'user', 'content': [{'type': 'text', 'text': 'look'}, {'type': 'image_url', 'image_url': {'url': 'data:x'}}]}])
    check(system == 'S' and 'first' in prompt and 'answer' in prompt and prompt.rstrip().endswith('[an attached image is not available to this model]')
          and 'Latest message' in prompt, 'prompt: history flattened, latest message last, images noted')
    names = engines.relay_names([{'function': {'name': 'mcp__' + 'x' * 70}}, {'function': {'name': 'server_diagnostic'}}])
    check(all(len(a) <= 50 for a in names) and 'server_diagnostic' in names and ('mcp__' + 'x' * 70) in names.values(),
          'relay names: long names get short aliases (CLI tool names max 64 with the mcp__hla__ prefix)')
    check(engines.redact('token sk-ant-oat01-abcdefghijklmnop done') == 'token [redacted] done', 'redact: token-like strings never shown')


asyncio.run(part1())


# ── sign-in flows, logout, API contract (FastAPI app with only this router) ──
def build_api(approver_ok=True):
    api = FastAPI()
    def read(request):
        if request.headers.get('x-session') != 'ok':
            raise HTTPException(401, 'Sign in first')
    def write(request):
        read(request)
        if request.headers.get('x-agent-ui') != '1':
            raise HTTPException(403, 'Missing UI request header')
    def approver(request):
        if not approver_ok:
            raise HTTPException(403, 'not an approver')
        return 'tester'
    api.include_router(subscriptions.build_router(read, write, approver))
    return api


ENGINE_KEYS = {'id', 'label', 'installed', 'version', 'signed_in', 'account', 'plan', 'models', 'login', 'last_error'}
LOGIN_KEYS = {'state', 'url', 'user_code', 'needs_code', 'message'}
H = {'x-session': 'ok', 'x-agent-ui': '1'}
sign_out('claude'); sign_out('chatgpt')
with TestClient(build_api()) as c:
    check(c.get('/ui/api/subscriptions').status_code == 401, 'api: reads need a session')
    check(c.post('/ui/api/subscriptions/claude/login', headers={'x-session': 'ok'}).status_code == 403, 'api: writes need the UI header')
    check(c.post('/ui/api/subscriptions/gemini/login', headers=H).status_code == 404, 'api: unknown engine 404')
    data = c.get('/ui/api/subscriptions', headers=H).json()
    check(set(data) == {'engines'} and [e['id'] for e in data['engines']] == ['claude', 'chatgpt']
          and all(set(e) == ENGINE_KEYS and set(e['login']) == LOGIN_KEYS for e in data['engines']), 'api: GET shape (contract)')
    cl, cx = data['engines']
    check(cl['label'] == 'Claude (subscription)' and cx['label'] == 'ChatGPT (subscription)' and cl['installed'] and cl['version'] == '2.1.325'
          and cx['version'] == '0.169.0' and not cl['signed_in'] and cl['login']['state'] == 'idle'
          and cl['models'][0] == {'id': 'claude-sub/sonnet', 'label': 'Claude Sonnet (subscription)'}, 'api: engine fields')

    # Claude: URL, pasted code (wrong, then right), signed in.
    v = c.post('/ui/api/subscriptions/claude/login', headers=H).json()
    check(set(v) == LOGIN_KEYS and v['state'] in ('starting', 'waiting'), 'login (claude): started')
    for _ in range(100):
        v = c.get('/ui/api/subscriptions', headers=H).json()['engines'][0]['login']
        if v['state'] == 'waiting': break
        time.sleep(0.05)
    check(v['state'] == 'waiting' and v['needs_code'] and v['url'].startswith('https://claude.com/cai/oauth/authorize') and '\x1b' not in v['url'],
          'login (claude): waiting with the sign-in URL, needs the pasted code')
    again = c.post('/ui/api/subscriptions/claude/login', headers=H).json()
    check(again['state'] == 'waiting' and again['url'] == v['url'], 'login: idempotent while one runs')
    check(c.post('/ui/api/subscriptions/claude/login/code', headers=H, json={'code': 'has space'}).status_code == 422, 'login code: malformed refused')
    v = c.post('/ui/api/subscriptions/claude/login/code', headers=H, json={'code': 'wrong#other'}).json()
    check(set(v) == LOGIN_KEYS and not v['needs_code'], 'login code: sent (contract shape)')
    for _ in range(100):
        v = c.get('/ui/api/subscriptions', headers=H).json()['engines'][0]['login']
        if v['needs_code']: break
        time.sleep(0.05)
    check(v['state'] == 'waiting' and v['needs_code'] and 'not from this sign-in' in (v['message'] or ''), 'login code: a wrong code asks again')
    c.post('/ui/api/subscriptions/claude/login/code', headers=H, json={'code': 'good#fakestate123'})
    for _ in range(100):
        e = c.get('/ui/api/subscriptions', headers=H).json()['engines'][0]
        if e['login']['state'] in ('done', 'error'): break
        time.sleep(0.05)
    check(e['login']['state'] == 'done' and e['signed_in'] and e['account'] == 'owner@example.com' and e['plan'] == 'max',
          'login (claude): done, signed in; account and plan from `claude auth status`')
    body = json.dumps(c.get('/ui/api/subscriptions', headers=H).json())
    check('FAKE-SECRET' not in body and 'sk-ant' not in body, 'api: never returns credential contents')

    # Test button.
    scenario('claude', [{'text': 'pong'}])
    t = c.post('/ui/api/subscriptions/claude/test', headers=H).json()
    check(set(t) == {'ok', 'ms', 'reply', 'error'} and t['ok'] and t['reply'] == 'pong' and isinstance(t['ms'], int), 'api: test (contract shape)')
    t = c.post('/ui/api/subscriptions/chatgpt/test', headers=H).json()
    check(t == {'ok': False, 'ms': 0, 'reply': None, 'error': 'Not signed in'}, 'api: test of a signed-out engine')

    # Codex: device code, no paste; the owner finishes in the browser.
    v = c.post('/ui/api/subscriptions/chatgpt/login', headers=H).json()
    for _ in range(100):
        v = c.get('/ui/api/subscriptions', headers=H).json()['engines'][1]['login']
        if v['state'] == 'waiting': break
        time.sleep(0.05)
    check(v['state'] == 'waiting' and v['url'] == 'https://auth.openai.com/codex/device' and v['user_code'] == 'FAKE-12345' and not v['needs_code'],
          'login (codex): device URL and one-time code, nothing to paste')
    check(c.post('/ui/api/subscriptions/chatgpt/login/code', headers=H, json={'code': 'x'}).status_code == 409, 'login code: refused when none is expected')
    (home('chatgpt') / 'approve-device').write_text('1')
    for _ in range(100):
        e = c.get('/ui/api/subscriptions', headers=H).json()['engines'][1]
        if e['login']['state'] in ('done', 'error'): break
        time.sleep(0.05)
    check(e['login']['state'] == 'done' and e['signed_in'] and e['plan'] == 'ChatGPT', 'login (codex): done when the device code is approved')

    # Cancel and logout.
    e = c.post('/ui/api/subscriptions/chatgpt/logout', headers=H).json()
    check(set(e) == ENGINE_KEYS and not e['signed_in'] and not (home('chatgpt') / '.codex' / 'auth.json').exists(), 'logout (codex): engine object, signed out')
    c.post('/ui/api/subscriptions/chatgpt/login', headers=H)
    for _ in range(100):
        if c.get('/ui/api/subscriptions', headers=H).json()['engines'][1]['login']['state'] == 'waiting': break
        time.sleep(0.05)
    proc = engines._logins['chatgpt'].proc
    v = c.post('/ui/api/subscriptions/chatgpt/login/cancel', headers=H).json()
    time.sleep(0.3)
    check(set(v) == LOGIN_KEYS and v['state'] == 'idle' and v['message'] == 'Sign-in cancelled.' and proc.returncode is not None,
          'login cancel: idle again, the sign-in process stopped')
    e = c.post('/ui/api/subscriptions/claude/logout', headers=H).json()
    check(not e['signed_in'] and not (home('claude') / '.claude' / '.credentials.json').exists() and e['login']['state'] == 'idle',
          'logout (claude): `claude auth logout`, credentials gone')
    os.environ['HLA_CLAUDE_BIN'] = '/nonexistent/claude'
    engines.invalidate()
    e = c.get('/ui/api/subscriptions', headers=H).json()['engines'][0]
    v = c.post('/ui/api/subscriptions/claude/login', headers=H).json()
    check(not e['installed'] and e['version'] is None and v['state'] == 'error' and 'not installed' in v['message'], 'not installed: reported, sign-in refused')
    os.environ['HLA_CLAUDE_BIN'] = str(FAKES / 'claude')
    engines.invalidate()
    engines._logins['claude'].__init__()
with TestClient(build_api(approver_ok=False)) as c:
    check(c.post('/ui/api/subscriptions/claude/login', headers=H).status_code == 403 and c.get('/ui/api/subscriptions', headers=H).status_code == 200,
          'api: sign-in needs an approver; reading does not')

if not os.environ.get('TEST_DATABASE_URL') or os.environ.get('I_UNDERSTAND_THIS_WIPES_TEST_DB') != '1':
    print('\nPart 2 skipped: set TEST_DATABASE_URL and I_UNDERSTAND_THIS_WIPES_TEST_DB=1 for the API tests')
    print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
    sys.exit(1 if failures else 0)

# ================================================================ part 2: the app against Postgres
os.environ.update({'HLA_RISK_REVIEW': 'false', 'OPENROUTER_WORKER_MODEL': 'base/model', 'AGENT_ALLOWED_MODELS': 'base/model',
                   'HLA_WEBSITE_CHECK_MINUTES': '0'})
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as db:
    db.execute('DROP SCHEMA public CASCADE'); db.execute('CREATE SCHEMA public')

OR_CALLS = []
async def handler(request):
    if 'openrouter' in request.url.host and request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': []})
    if 'openrouter' in request.url.host:
        OR_CALLS.append(request.url.path)
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'openrouter'}, 'finish_reason': 'stop'}],
                                         'usage': {'cost': 0.01}})
    if 'typesafe' in request.url.host:
        return httpx.Response(200, json={'answers': {'expert': {'noul': 0.1}, 'risky': {'noul': 0.05}}, 'usage': {}})
    if request.url.host == 'ssh-gateway-api':
        return httpx.Response(200, json={'servers': {}, 'services': {}})
    return httpx.Response(404, json={})

_Real = httpx.AsyncClient
class MockAsync(_Real):
    def __init__(self, *a, **kw):
        kw['transport'] = httpx.MockTransport(handler)
        super().__init__(*a, **kw)
httpx.AsyncClient = MockAsync

import app as appmod  # noqa: E402


def q(sql, *args):
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        return db.execute(sql, args).fetchall()


def wait_for(fn, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if fn(): return True
        time.sleep(0.1)
    return False


UI = {'x-agent-ui': '1'}
sign_out('claude'); sign_out('chatgpt')
engines._status.clear()
with TestClient(appmod.app) as c:
    check(c.get('/ui/api/subscriptions').status_code == 401, 'app: /ui/api/subscriptions needs a dashboard session')
    c.post('/ui/api/login', json={'password': 'pw'})
    check(c.post('/ui/api/subscriptions/claude/login').status_code == 403, 'app: sign-in needs the UI header')
    data = c.get('/ui/api/subscriptions').json()
    check([e['id'] for e in data['engines']] == ['claude', 'chatgpt'] and not data['engines'][0]['signed_in'], 'app: subscriptions listed')
    models = c.get('/ui/api/models').json()
    check(not any(m.startswith('claude-sub/') for m in models['models']) and models['providers']['claude-sub']['configured'] is False
          and len(models['subscription']) >= 4, 'registry: signed out -> not in the pickers, listed as subscription models')
    sign_in('claude')
    model_registry.invalidate()
    models = c.get('/ui/api/models').json()
    check('claude-sub/sonnet' in models['models'] and 'chatgpt-sub/gpt-6.1-sol' not in models['models']
          and models['labels']['claude-sub/sonnet'] == 'Claude Sonnet (subscription)' and models['providers']['claude-sub']['configured'],
          'registry: signed in -> the engine models are in every picker, with labels')
    cat = c.get('/ui/api/models/catalog', params={'source': 'subscription', 'q': 'opus'}).json()
    check(cat['source'] == 'subscription' and cat['models'][0]['id'] == 'claude-sub/opus' and cat['models'][0]['billing'] == 'subscription'
          and cat['models'][0]['prompt_price'] == 0, 'registry catalog: source=subscription')
    added = c.post('/ui/api/models', headers=UI, json={'id': 'claude-sub/opus'}).json()
    check(added['id'] == 'claude-sub/opus' and added['in_catalog'] and float(added['prompt_price']) == 0 and not added['warning'],
          'registry: a subscription model can be added like any other (price 0)')
    c.patch('/ui/api/models/claude-sub/opus', headers=UI, json={'enabled': False})
    model_registry.invalidate()
    check('claude-sub/opus' not in c.get('/ui/api/models').json()['models'], 'registry: a disabled row switches a subscription model off')
    scenario('claude', [{'text': 'pong'}])
    t = c.post('/ui/api/models/claude-sub/sonnet/test', headers=UI).json()
    check(t['ok'] and t['reply'] == 'pong' and t['cost'] == 0.0, 'models page: Test runs through the engine (cost 0)')

    d = c.put('/ui/api/models/default', headers=UI, json={'model': 'claude-sub/haiku'})
    check(d.status_code == 200 and d.json() == {'model': 'claude-sub/haiku', 'source': 'dashboard'},
          'registry: an offered subscription model (no registry row) can be the default model ' + d.text[:200])
    # A dashboard task on a subscription model: streamed from the CLI, recorded with cost 0.
    c.put('/ui/api/models/default', headers=UI, json={'model': 'base/model'})
    scenario('claude', [{'text': 'The answer from Claude.'}])
    r = c.post('/ui/api/tasks/start-stream', headers=UI, json={'task': 'Say something', 'model_override': 'claude-sub/sonnet'})
    tid = r.json().get('id')
    wait_for(lambda: q('SELECT status FROM tasks WHERE id=%s', tid)[0]['status'] != 'running')
    row = q('SELECT * FROM tasks WHERE id=%s', tid)[0]
    check(r.status_code == 200 and row['status'] == 'completed' and row['provider'] == 'claude-sub' and row['worker_model'] == 'claude-sub/sonnet'
          and row['result_text'] == 'The answer from Claude.' and float(row['openrouter_cost_usd']) == 0 and row['input_tokens'] == 150
          and not OR_CALLS, 'task: runs on the engine, provider claude-sub, cost 0, no OpenRouter call')
    costs = c.get('/ui/api/costs', params={'days': 7}).json()
    sub = next((m for m in costs['by_model'] if m['model'] == 'claude-sub/sonnet'), None)
    check(sub and sub['billing'] == 'subscription' and sub['provider'] == 'Claude (subscription)' and sub['cost'] == 0
          and costs['totals']['subscription_runs'] >= 1, 'costs: subscription runs shown with billing subscription, cost 0')
    sign_out('claude')
    model_registry.invalidate()
    r = c.post('/ui/api/tasks/start-stream', headers=UI, json={'task': 'Again', 'model_override': 'claude-sub/sonnet'})
    refused = r.status_code in (422, 503)
    if r.status_code == 200:   # started in the background: the run must fail without starting the CLI
        tid = r.json().get('id')
        wait_for(lambda: q('SELECT status FROM tasks WHERE id=%s', tid)[0]['status'] != 'running')
        row = q('SELECT status,error_message FROM tasks WHERE id=%s', tid)[0]
        refused = row['status'] == 'failed'
    check(refused and not OR_CALLS and 'Again' not in last_run('claude')['prompt'],
          'task: a signed-out engine is refused (model no longer offered), the CLI never starts')

print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
