"""Fake Claude Code / Codex CLIs for tests and the UI preview (HLA_ENGINES_V1). No network, no real accounts.

They mimic what engines.py uses of the real CLIs (claude 2.1.325, codex-cli 0.169.0): --version, the status / login /
logout commands and their output, `claude -p --output-format stream-json` and `codex exec --json` events, and an MCP
client that starts the configured relay (engine_mcp_relay.py) and calls its tools.

The scenario of a run comes from $HOME/fake.json (HOME is the engine's directory, set by engines.py):
  {"steps": [{"text": "..."}, {"call": {"name": "server_diagnostic", "arguments": {...}}},
             {"calls": [{...}, {...}]}, {"hang": 60}, {"error": "message"}], "usage": {...}}
Each run writes $HOME/last_run.json (argv, environment, prompt, system prompt, relay tool names, tool results) and
$HOME/pid. Sign-in: claude's code is good#fakestate123; codex completes when $HOME/approve-device exists (or after the
seconds in $HOME/auto-approve: the UI preview).
"""
import json
import os
import subprocess
import sys
import threading
import time

HOME = os.environ.get('HOME', '.')
STATE = 'fakestate123'


def out(obj):
    sys.stdout.write(json.dumps(obj) + '\n')
    sys.stdout.flush()


def scenario():
    try:
        with open(os.path.join(HOME, 'fake.json')) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'steps': [{'text': 'pong'}]}


class Relay:
    """A minimal MCP client over stdio, like the CLIs' own."""

    def __init__(self, command, args, env):
        self.proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     env={**os.environ, **env}, text=True, bufsize=1)
        self.lock, self.ident, self.waiting, self.answers = threading.Lock(), 0, {}, {}
        threading.Thread(target=self._read, daemon=True).start()
        self.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'fake', 'version': '1'}})
        self.notify('notifications/initialized')

    def _read(self):
        for line in self.proc.stdout:
            msg = json.loads(line)
            event = self.waiting.get(msg.get('id'))
            if event:
                self.answers[msg['id']] = msg
                event.set()

    def notify(self, method):
        with self.lock:
            self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': method}) + '\n')
            self.proc.stdin.flush()

    def request(self, method, params=None):
        with self.lock:
            self.ident += 1
            ident = self.ident
            self.waiting[ident] = threading.Event()
            self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}) + '\n')
            self.proc.stdin.flush()
        self.waiting[ident].wait(600)
        return self.answers.get(ident, {}).get('result') or {}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def run(kind, argv, relay_spec, system):
    prompt = sys.stdin.read()
    with open(os.path.join(HOME, 'pid'), 'w') as fh:
        fh.write(str(os.getpid()))
    record = {'argv': argv, 'env': dict(os.environ), 'prompt': prompt, 'system': system, 'cwd': os.getcwd(),
              'cwd_files': sorted(os.listdir('.')), 'tools': [], 'results': []}
    relay = Relay(*relay_spec) if relay_spec else None
    if relay:
        record['tools'] = [t['name'] for t in relay.request('tools/list').get('tools', [])]
        record['schemas'] = {t['name']: t['inputSchema'] for t in relay.request('tools/list').get('tools', [])}

    def save():
        with open(os.path.join(HOME, 'last_run.json'), 'w') as fh:
            json.dump(record, fh)
    save()
    if kind == 'claude':
        out({'type': 'system', 'subtype': 'init', 'tools': [], 'mcp_servers': [{'name': 'hla', 'status': 'connected'}] if relay else [],
             'model': 'fake'})
    else:
        out({'type': 'thread.started', 'thread_id': 'fake'})
        out({'type': 'turn.started'})
    spec = scenario()
    final = []
    n = 0

    def call(c):
        result = relay.request('tools/call', {'name': c['name'], 'arguments': c.get('arguments') or {}})
        text = ''.join(p.get('text', '') for p in result.get('content') or [])
        record['results'].append({'name': c['name'], 'text': text, 'isError': result.get('isError')})
        save()

    for step in spec.get('steps', []):
        n += 1
        if 'text' in step:
            final.append(step['text'])
            if kind == 'claude':
                for i in range(0, len(step['text']), 4):
                    out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0,
                                                           'delta': {'type': 'text_delta', 'text': step['text'][i:i + 4]}}})
                out({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': step['text']}]}})
            else:
                out({'type': 'item.completed', 'item': {'id': f'item_{n}', 'type': 'agent_message', 'text': step['text']}})
        elif 'call' in step or 'calls' in step:
            calls = step.get('calls') or [step['call']]
            if not relay:
                record['results'].append({'error': 'no relay configured'})
                continue
            threads = [threading.Thread(target=call, args=(c,)) for c in calls]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        elif 'hang' in step:
            time.sleep(step['hang'])
        elif 'error' in step:
            if kind == 'claude':
                out({'type': 'result', 'subtype': 'success', 'is_error': True, 'result': step['error']})
            else:
                out({'type': 'turn.failed', 'error': {'message': step['error']}})
            if relay:
                relay.close()
            return 1
    if relay:
        relay.close()
    usage = spec.get('usage') or {}
    if kind == 'claude':
        out({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': final[-1] if final else '',
             'usage': {'input_tokens': usage.get('input', 120), 'cache_read_input_tokens': 30, 'output_tokens': usage.get('output', 40)},
             'total_cost_usd': 0.0123})
    else:
        out({'type': 'turn.completed', 'usage': {'input_tokens': usage.get('input', 150), 'cached_input_tokens': 0,
                                                 'output_tokens': usage.get('output', 50)}})
    return 0


def claude(argv):
    creds = os.path.join(HOME, '.claude', '.credentials.json')
    if argv[:1] == ['--version']:
        print('2.1.325 (Claude Code)')
        return 0
    if argv[:2] == ['auth', 'status']:
        if os.path.exists(creds):
            print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                              'email': 'owner@example.com', 'orgName': "Owner's Organization", 'subscriptionType': 'max'}))
            return 0
        print(json.dumps({'loggedIn': False, 'authMethod': 'none', 'apiProvider': 'firstParty'}))
        return 1
    if argv[:2] == ['auth', 'login']:
        print('Opening browser to sign in…', flush=True)
        print('If the browser didn\'t open, visit: \x1b[1mhttps://claude.com/cai/oauth/authorize?code=true&client_id=fake&state='
              + STATE + '\x1b[0m', flush=True)
        while True:
            sys.stdout.write('Paste code here if prompted > ')
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                return 1
            code = line.strip()
            if code == 'good#' + STATE:
                os.makedirs(os.path.dirname(creds), exist_ok=True)
                with open(creds, 'w') as fh:
                    fh.write('{"claudeAiOauth": {"accessToken": "sk-ant-oat01-FAKE-SECRET"}}')
                print('Login successful.', flush=True)
                return 0
            if '#' not in code:
                print('Invalid code. Please make sure the full code was copied.', flush=True)
            else:
                print('That code is not from this sign-in. Paste the code shown after opening the address above.', flush=True)
    if argv[:2] == ['auth', 'logout']:
        if os.path.exists(creds):
            os.remove(creds)
        print('Successfully logged out from your Anthropic account.')
        return 0
    if '-p' in argv:
        relay_spec, system = None, None
        if '--mcp-config' in argv:
            with open(argv[argv.index('--mcp-config') + 1]) as fh:
                servers = json.load(fh).get('mcpServers') or {}
            if 'hla' in servers:
                s = servers['hla']
                relay_spec = (s['command'], s.get('args') or [], s.get('env') or {})
        if '--system-prompt-file' in argv:
            with open(argv[argv.index('--system-prompt-file') + 1]) as fh:
                system = fh.read()
        if not os.path.exists(creds):
            out({'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'Not logged in · Please run /login'})
            return 1
        return run('claude', argv, relay_spec, system)
    print('unknown fake claude command', argv, file=sys.stderr)
    return 2


def _toml(value):
    value = value.strip()
    if value.startswith('{'):   # {HLA_RELAY_FILE="..."}
        inner = value[1:-1].strip()
        key, _, val = inner.partition('=')
        return {key.strip(): json.loads(val)}
    return json.loads(value)


def codex(argv):
    codex_home = os.environ.get('CODEX_HOME') or os.path.join(HOME, '.codex')
    creds = os.path.join(codex_home, 'auth.json')
    print('WARNING: proceeding, even though we could not create PATH aliases: fake', file=sys.stderr)
    if argv[:1] == ['--version']:
        print('codex-cli 0.169.0')
        return 0
    if argv[:2] == ['login', 'status']:
        if os.path.exists(creds):
            print('Logged in using ChatGPT.')
            return 0
        print('Not logged in')
        return 1
    if argv[:1] == ['login'] and '--device-auth' in argv:
        print('\nWelcome to Codex [v\x1b[90m0.169.0\x1b[0m]\n\nFollow these steps to sign in with ChatGPT using device code authorization:\n')
        print('1. Open this link in your browser and sign in to your account\n   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n')
        print('2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n   \x1b[94mFAKE-12345\x1b[0m\n', flush=True)
        marker = os.path.join(HOME, 'approve-device')
        try:   # the UI preview: $HOME/auto-approve holds the seconds after which the "owner" approves
            with open(os.path.join(HOME, 'auto-approve')) as fh:
                auto = time.time() + float(fh.read().strip() or 0)
        except (OSError, ValueError):
            auto = None
        for _ in range(6000):
            if os.path.exists(marker) or (auto is not None and time.time() >= auto):
                if os.path.exists(marker):
                    os.remove(marker)
                os.makedirs(codex_home, exist_ok=True)
                with open(creds, 'w') as fh:
                    fh.write('{"tokens": {"access_token": "FAKE-SECRET"}}')
                print('Successfully logged in', flush=True)
                return 0
            time.sleep(0.05)
        return 1
    if argv[:1] == ['logout']:
        if os.path.exists(creds):
            os.remove(creds)
        print('Successfully logged out.')
        return 0
    if argv[:1] == ['exec']:
        conf, system = {}, None
        for i, a in enumerate(argv):
            if a == '-c' and i + 1 < len(argv):
                key, _, value = argv[i + 1].partition('=')
                conf[key] = value
        if 'developer_instructions' in conf:
            system = json.loads(conf['developer_instructions'])
        relay_spec = None
        if 'mcp_servers.hla.command' in conf:
            relay_spec = (json.loads(conf['mcp_servers.hla.command']), json.loads(conf['mcp_servers.hla.args']),
                          _toml(conf['mcp_servers.hla.env']))
        if not os.path.exists(creds):
            out({'type': 'turn.failed', 'error': {'message': 'unexpected status 401 Unauthorized'}})
            return 1
        return run('codex', argv, relay_spec, system)
    print('unknown fake codex command', argv, file=sys.stderr)
    return 2


if __name__ == '__main__':
    name = os.path.basename(sys.argv[1])
    sys.exit(claude(sys.argv[2:]) if name == 'claude' else codex(sys.argv[2:]))
