"""MCP servers through the gateway API (HLA_MCP_V1): a real local MCP server speaking Streamable HTTP (JSON and SSE
answers, sessions, pagination, an isError tool), the real gateway HTTP server and SQLite.

python3 tests/test_mcp.py   (needs paramiko importable; no network)
"""
import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'GATEWAY_CREDENTIAL_KEY': base64.b64encode(os.urandom(32)).decode()})
os.environ.pop('GITHUB_TOKEN', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import mcp, policy  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

TOKEN = 'mcp-tok-' + 'Zq9' * 10
KEY = 'key-' + 'W4v' * 8
AGENT = '11111111-2222-3333-4444-555555555555'

# ---------------------------------------------------------------- the fake MCP server
TOOLS_P1 = [
    {'name': 'echo', 'description': 'Echo text back', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
     'annotations': {'readOnlyHint': True, 'title': 'Echo'}},
    {'name': 'write_note', 'description': 'Write a note', 'inputSchema': {'type': 'object', 'properties': {'body': {'type': 'string'}}}},
    {'name': 'wipe', 'description': 'Delete everything', 'inputSchema': {'type': 'object'}, 'annotations': {'destructiveHint': True}},
]
TOOLS_P2 = [
    {'name': 'fail', 'description': 'Always fails', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
    {'name': 'big', 'description': 'A long answer', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
    {'name': 'leak', 'description': 'Prints its token', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
    {'name': 'bad name!', 'description': 'skipped'},
]
EXTRA = []         # tools added to /mcp after the first refresh
SEEN = []          # (path, method, headers, body)
SESSIONS = set()
COUNTER = {'session': 0}


class FakeMCP(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, status, payload, headers=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def send_sse(self, events):
        body = ''.join('event: message\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        SEEN.append((self.path, body.get('method'), dict(self.headers), body))
        path = self.path.split('?')[0]
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location', 'https://elsewhere.example/mcp'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.path == '/slow':
            time.sleep(2.5); return self.send_json(200, {'jsonrpc': '2.0', 'id': body.get('id'), 'result': {}})
        if path == '/mcp' and self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.send_json(401, {'error': 'unauthorized'})
        if self.path == '/keyed' and self.headers.get('X-API-Key') != KEY:
            return self.send_json(401, {'error': 'unauthorized'})
        method, ident = body.get('method'), body.get('id')
        if method == 'initialize':
            COUNTER['session'] += 1
            sid = 'sess-%d' % COUNTER['session']
            SESSIONS.add(sid)
            return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                                         'serverInfo': {'name': 'fake-mcp', 'version': '0.1'}}}, {'Mcp-Session-Id': sid})
        if self.headers.get('Mcp-Session-Id') not in SESSIONS:
            return self.send_json(404, {'error': 'session not found'})
        if method == 'notifications/initialized':
            if path == '/forgetful':  # drops the session it just made
                return self.send_json(404, {'error': 'session not found'})
            self.send_response(202); self.send_header('Content-Length', '0'); self.end_headers(); return
        if method == 'tools/list':
            if (body.get('params') or {}).get('cursor') == 'page-2':  # the second page comes as an event stream
                return self.send_sse([{'jsonrpc': '2.0', 'method': 'notifications/message', 'params': {'level': 'info', 'data': 'listing'}},
                                      {'jsonrpc': '2.0', 'id': ident, 'result': {'tools': TOOLS_P2}}])
            tools = TOOLS_P1 + EXTRA if path == '/mcp' else TOOLS_P1[:1]
            return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'tools': tools, **({'nextCursor': 'page-2'} if path == '/mcp' else {})}})
        if method == 'tools/call':
            name, args = body['params']['name'], body['params'].get('arguments') or {}
            if name == 'echo':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'echo: ' + str(args.get('text'))}]}})
            if name == 'write_note':
                return self.send_sse([{'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'progress': 1}},
                                      {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'saved'},
                                                                                           {'type': 'image', 'data': 'AAAA', 'mimeType': 'image/png'}]}}])
            if name == 'wipe':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'wiped'}]}})
            if name == 'fail':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'isError': True, 'content': [{'type': 'text', 'text': 'it broke'}]}})
            if name == 'big':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'x' * 50000}]}})
            if name == 'huge_sse':  # one data: line far above 64 KiB
                return self.send_sse([{'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'y' * 80000 + 'END'}]}}])
            if name == 'huge_json':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'z' * 80000}]}})
            if name == 'leak_encoded':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text':
                    'pct ' + TOKEN.replace('-', '%2d') + ' b64 ' + base64.b64encode(TOKEN.encode()).decode()
                    + ' html ' + TOKEN.replace('-', '&#45;') + ' json ' + json.dumps({'t': TOKEN}).replace('-', '\\u002d')
                    + ' ' + 'p' * (mcp.RESULT_CHARS - 300) + TOKEN}]}})
            if name == 'leak':
                return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'result': {'content': [{'type': 'text', 'text': 'my token is ' + TOKEN}]}})
            return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Unknown tool'}})
        return self.send_json(200, {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Method not found'}})


fake = ThreadingHTTPServer(('127.0.0.1', 0), FakeMCP)
threading.Thread(target=fake.serve_forever, daemon=True).start()
FAKE = 'http://127.0.0.1:%d' % fake.server_address[1]

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % httpd.server_address[1]

def call(method, path, body=None, token='a' * 40, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def tool_call(tool, arguments=None, server='fake', task=None):
    return call('POST', '/v1/mcp/call', {'agent_id': AGENT, 'server': server, 'tool': tool, 'arguments': arguments if arguments is not None else {}},
                token='t' * 40, headers={'X-HLA-Task': task} if task else None)

def tools_for(agent=AGENT):
    s, r = call('GET', '/v1/mcp/tools?agent_id=' + agent, token='t' * 40)
    return sorted(t['tool'] for t in r['tools']) if s == 200 else ('error', s, r)

def audit_rows(action=None):
    with srv.db_open() as db:
        rows = db.execute('SELECT server,action,exit_code,truncated,agent_id,detail,task_id FROM audit WHERE action LIKE ? ORDER BY id',
                          (action or '%',)).fetchall()
    return rows

def set_policy(change):
    return call('PUT', '/v1/policy', {'change': change, 'actor': 'test'})

# ---------------------------------------------------------------- SSRF and URL rules (the loopback exception is off)
ap = mcp.address_problem
check(ap('127.0.0.1', True) and ap('::1', True) and ap('::ffff:127.0.0.1', True), 'loopback is refused, also as an IPv4-mapped address')
check(ap('169.254.169.254', True) and ap('fd00:ec2::254', True) and ap('64:ff9b::a9fe:a9fe', True) and ap('fe80::1', True),
      'metadata and link-local addresses are refused even with private allowed (also through NAT64)')
check(ap('0.0.0.0', True) and ap('224.0.0.1', True), 'unspecified and multicast addresses are refused')
check(ap('192.168.1.5', False) and ap('10.0.0.5', False) and ap('100.64.0.1', False) and ap('fd12::1', False)
      and ap('192.168.1.5', True) is None and ap('fd12::1', True) is None, 'private ranges only with allow_private')
check(ap('8.8.8.8', False) is None and ap('2606:4700::1111', False) is None, 'public addresses are fine')

def save(name, body):
    return call('PUT', '/v1/mcp/servers/' + name, {'actor': 'owner', **body})

s, r = save('lan', {'url': FAKE + '/mcp'})
check(s == 400 and r['error'] == 'insecure_url', 'http:// is refused without allow_insecure')
s, r = save('lan', {'url': FAKE + '/mcp', 'allow_private': True, 'allow_insecure': True})
check(s == 400 and r['error'] == 'address_refused', 'a loopback URL is refused even with private and http allowed')
s, r = save('meta', {'url': 'https://169.254.169.254/latest', 'allow_private': True})
check(s == 400 and r['error'] == 'address_refused', 'the metadata address is refused')
s, r = save('meta', {'url': 'https://[fd00:ec2::254]/', 'allow_private': True})
check(s == 400 and r['error'] == 'address_refused', 'the IPv6 metadata address is refused')
s, r = save('lan', {'url': 'https://10.0.0.5/mcp'})
check(s == 400 and r['error'] == 'address_refused' and 'allow private' in r['detail'], 'a private address needs allow_private')
s, r = save('lan', {'url': 'http://8.8.8.8/mcp', 'allow_private': True, 'allow_insecure': True})
check(s == 400 and r['error'] == 'insecure_url', 'plain http to a public address is refused')
s, r = save('lan', {'url': 'https://user:pw@example.com/mcp'})
check(s == 400 and r['error'] == 'invalid_url', 'no user:password in the URL')
s, r = save('lan', {'url': 'https://example.com/mcp?api_key=abc'})
check(s == 400 and r['error'] == 'secret_in_url', 'keys in the query string are refused')
s, r = save('lan', {'url': 'https://localhost/mcp', 'allow_private': True})
check(s == 400 and r['error'] == 'address_refused', 'localhost is refused')
s, r = save('Bad.Name', {'url': 'https://example.com/mcp'})
check(s == 404, 'invalid server names do not route')
s, r = save('lan', {'url': 'https://10.0.0.5/mcp', 'allow_private': True, 'auth': {'type': 'header', 'header': 'Host', 'credential': 'MCP_KEY'}})
check(s == 400 and r['error'] == 'invalid_auth', 'reserved headers cannot carry auth')
s, r = save('lan', {'url': 'https://10.0.0.5/mcp', 'allow_insecure': True})
check(s == 400 and r['error'] == 'insecure_url', 'allow_insecure needs allow_private')

# A name that resolves to a refused address at connect time (DNS answer checked, connection pinned to it).
s, r = save('dns', {'url': 'https://mcp.example.test/mcp'})
check(s == 200 and r['level'] == 0 and r['refreshed'] is False, 'a public name is saved, at level 0')
real_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))] if host == 'mcp.example.test' else real_getaddrinfo(host, *a, **k)
s, r = call('POST', '/v1/mcp/servers/dns/refresh', {'actor': 'owner'})
socket.getaddrinfo = real_getaddrinfo
check(s == 403 and r['error'] == 'address_refused', 'a name resolving to loopback is refused when connecting')
s, r = call('GET', '/v1/mcp/servers/dns')
check(r['last_error'].startswith('address_refused'), 'the refresh error is stored for the dashboard')
check(call('DELETE', '/v1/mcp/servers/dns')[0] == 200, 'deleted')

# ---------------------------------------------------------------- registry with a credential (the test allows loopback)
mcp._test_allow_loopback = True
check(call('PUT', '/v1/credentials/MCP_TOKEN', {'value': TOKEN, 'actor': 'owner'})[0] == 200, 'credential stored')
check(call('PUT', '/v1/credentials/MCP_KEY', {'value': KEY, 'actor': 'owner'})[0] == 200, 'second credential stored')
body = {'url': FAKE + '/mcp', 'allow_private': True, 'allow_insecure': True, 'auth': {'type': 'bearer', 'credential': 'MCP_TOKEN'},
        'description': 'Fake notes server', 'timeout': 30}
s, r = save('fake', body)
check(s == 200 and r['service'] == 'mcp:fake' and r['level'] == 0 and r['auth'] == {'type': 'bearer', 'credential': 'MCP_TOKEN', 'header': None}
      and r['credential_stored'] is True and TOKEN not in json.dumps(r), 'server saved: credential by name, level 0, no value in the reply')
s, r = call('PUT', '/v1/mcp/servers/fake', body, token='t' * 40)
check(s == 401, 'the tool token cannot register servers')
check(call('GET', '/v1/mcp/servers', token='t' * 40)[0] == 401, 'the tool token cannot read the registry')
check(call('POST', '/v1/mcp/servers/fake/refresh', {}, token='t' * 40)[0] == 401, 'the tool token cannot refresh')
check(call('DELETE', '/v1/mcp/servers/fake', token='t' * 40)[0] == 401, 'the tool token cannot delete')

SEEN.clear()
s, r = call('POST', '/v1/mcp/servers/fake/refresh', {'actor': 'owner'})
names = [t['name'] for t in r.get('tools', [])]
check(s == 200 and names == ['echo', 'write_note', 'wipe', 'fail', 'big', 'leak'] and r['tool_count'] == 6,
      f'refresh lists every page (JSON page, then an SSE page) and skips invalid names ({names})')
methods = [m for _, m, _, _ in SEEN]
check(methods == ['initialize', 'notifications/initialized', 'tools/list', 'tools/list'], f'handshake order {methods}')
init_h, list_h = SEEN[0][2], SEEN[2][2]
check('Mcp-Session-Id' not in init_h and list_h.get('Mcp-Session-Id') == 'sess-1' and list_h.get('MCP-Protocol-Version') == '2025-06-18',
      'session id and protocol version are sent after initialize')
check('application/json' in list_h.get('Accept', '') and 'text/event-stream' in list_h.get('Accept', ''), 'Accept names JSON and SSE')
check(init_h.get('Authorization') == 'Bearer ' + TOKEN, 'the bearer header is filled from the credential store')
check(SEEN[3][3]['params'] == {'cursor': 'page-2'}, 'the cursor is passed for the next page')
levels = {t['name']: t['required_level'] for t in r['tools']}
check(levels == {'echo': 4, 'write_note': 4, 'wipe': 6, 'fail': 4, 'big': 4, 'leak': 4},
      f'default levels: 4, destructiveHint 6; readOnlyHint does not lower a level {levels}')
check(not any(t['new'] or t['review'] for t in r['tools']) and r['review_count'] == 0, 'the first snapshot of a server marks no tool as new')
check(r['server_info'] == {'name': 'fake-mcp', 'version': '0.1'} and r['refreshed_at'] and r['last_error'] is None, 'server info and time stored')
s, r = call('GET', '/v1/mcp/servers/fake')
check(r['tools'][0]['input_schema']['properties']['text'] == {'type': 'string'} and r['tools'][0]['annotations'] == {'readOnlyHint': True, 'title': 'Echo'},
      'the detail has input schemas and annotations')
s, r = call('GET', '/v1/mcp/servers')
check(s == 200 and [x['name'] for x in r['servers']] == ['fake'] and 'input_schema' not in r['servers'][0]['tools'][0]
      and r['credentials'] == ['MCP_KEY', 'MCP_TOKEN'] and TOKEN not in json.dumps(r), 'the listing has credential names, never values')
s, r = call('GET', '/v1/policy')
check(r['services'].get('mcp:fake') == {'level': 0} and r['mcp'] == {'tools': {}}, 'mcp:fake is a service on the policy page')

# ---------------------------------------------------------------- levels, overrides, unhinged
set_policy({'agents': {AGENT: {'name': 'Infra', 'level': 5}}})
check(tools_for() == [], 'service at level 0: no tools')
s, r = tool_call('echo', {'text': 'hi'})
check(s == 403 and r['error'] == 'policy_denied', 'service at level 0: calls refused')
check(audit_rows('agent_mcp_denied')[-1][:2] == ('mcp:fake', 'agent_mcp_denied'), 'a refused call is audited')
set_policy({'servers': {'mcp:fake': {'level': 1}}})
check(tools_for() == [], 'level 1: no tools by default (readOnlyHint from the server is not trusted)')
set_policy({'mcp': {'tools': {'fake': {t: {'level': 1} for t in ('echo', 'fail', 'big', 'leak')}}}})
check(tools_for() == ['big', 'echo', 'fail', 'leak'], 'level 1: tools the owner set to level 1')
set_policy({'servers': {'mcp:fake': {'level': 4}}})
check(tools_for() == ['big', 'echo', 'fail', 'leak', 'write_note'], 'level 4: also tools without hints; destructive ones need 6')
check(tool_call('wipe')[1].get('error') == 'policy_denied', 'a destructive tool is refused at level 4')
set_policy({'agents': {AGENT: {'name': 'Infra', 'level': 2}}})
check(tools_for() == ['big', 'echo', 'fail', 'leak'], 'the agent level caps it (min of both)')
set_policy({'agents': {AGENT: {'name': 'Infra', 'level': 5}}, 'unhinged': {'enabled': True, 'hours': 1}})
check('wipe' in tools_for(), 'unhinged mode: a level-5 agent may use destructive tools')
set_policy({'servers': {'mcp:fake': {'level': 0}}})
check(tools_for() == [], 'unhinged mode does not reach a service at level 0')
set_policy({'servers': {'mcp:fake': {'level': 4}}, 'unhinged': {'enabled': False}})
s, r = set_policy({'mcp': {'tools': {'fake': {'wipe': {'level': 4}, 'echo': {'disabled': True}}}}})
check(s == 200 and r['mcp']['tools']['fake'] == {'echo': {'disabled': True}, 'wipe': {'level': 4}, 'fail': {'level': 1}, 'big': {'level': 1},
      'leak': {'level': 1}}, 'overrides stored in the policy')
check(tools_for() == ['big', 'fail', 'leak', 'wipe', 'write_note'], 'override: wipe at 4, echo disabled')
s, r = tool_call('echo', {'text': 'hi'})
check(s == 403 and r['error'] == 'tool_disabled', 'a disabled tool is refused')
s, r = call('GET', '/v1/mcp/servers/fake')
check({t['name']: (t['required_level'], t['override']) for t in r['tools']}['wipe'] == (4, {'level': 4}), 'the dashboard sees override and effective level')
check(set_policy({'mcp': {'tools': {'fake': {'echo': {'level': 0}}}}})[0] == 400, 'override level 0 is refused (use disabled)')
check(set_policy({'mcp': {'tools': {'other': {'echo': {'level': 1}}}}})[0] == 400, 'overrides only for registered servers')
check(set_policy({'mcp': {'tools': {'fake': {'echo': {'level': 2, 'disabled': False}}}}})[0] == 400, 'override shape is strict')
s, r = set_policy({'mcp': {'tools': {'fake': {'wipe': None, 'echo': None, 'fail': None, 'big': None, 'leak': None}}}})
check(s == 200 and r['mcp']['tools'] == {}, 'null resets a tool to its default')
set_policy({'mcp': {'tools': {'fake': {t: {'level': 1} for t in ('echo', 'fail', 'big', 'leak')}}}})
check(set_policy({'servers': {'mcp:nope': {'level': 1}}})[0] == 400, 'levels only for registered MCP servers')

# ---------------------------------------------------------------- calls
s, r = tool_call('echo', {'text': 'hi'}, task='22222222-3333-4444-5555-666666666666')
check(s == 200 and r == {'service': 'mcp:fake', 'server': 'fake', 'tool': 'echo', 'is_error': False, 'content': 'echo: hi', 'truncated': False},
      f'a JSON answer ({r})')
row = audit_rows('agent_mcp_read')[-1]
check(row[0] == 'mcp:fake' and row[2] == 0 and row[4] == AGENT and 'echo · args: text · {"text": "hi"} · ok' in row[5]
      and row[6] == '22222222-3333-4444-5555-666666666666', f'audited with server, tool, argument names, preview and task ({row})')
s, r = tool_call('write_note', {'body': 'note'})
check(s == 200 and r['content'].startswith('saved\n[image image/png') and not r['is_error'], 'an SSE answer after a progress notification')
check(audit_rows('agent_mcp_call')[-1][2] == 0, 'a level-4 tool is audited as agent_mcp_call')
s, r = tool_call('fail')
check(s == 200 and r['is_error'] is True and r['content'] == 'it broke', 'isError is passed through as a tool error')
check(audit_rows('agent_mcp_read')[-1][2] == 1 and 'isError' in audit_rows('agent_mcp_read')[-1][5], 'isError is on the audit row')
s, r = tool_call('big')
check(s == 200 and r['truncated'] and len(r['content']) < 20100 and r['content'].endswith('more characters cut by the gateway]'), 'long results are cut with a note')
check(audit_rows('agent_mcp_read')[-1][3] == 1, 'the cut is on the audit row')
s, r = tool_call('leak')
check(s == 200 and TOKEN not in json.dumps(r) and '[credential:MCP_TOKEN]' in r['content'], 'a credential in a tool result is masked')
s, r = tool_call('echo', {'text': 'use ' + TOKEN})
detail = audit_rows('agent_mcp_read')[-1][5]
check(TOKEN not in json.dumps(r) and TOKEN not in detail and 'MCP_TOKEN]' in detail, 'credentials are masked in the audit preview')
s, r = tool_call('echo', ['not', 'an', 'object'])
check(s == 400 and r['error'] == 'invalid_arguments', 'arguments must be an object')
check(tool_call('nope')[0] == 404 and tool_call('echo', server='ghost')[0] == 404, 'unknown tools and servers are 404')
check(call('POST', '/v1/mcp/call', {'agent_id': AGENT, 'server': 'fake', 'tool': 'echo'}, token='a' * 40)[0] == 401, 'the admin token does not call tools')

# Session expiry: the server forgets the session; the gateway initializes again once.
before = COUNTER['session']
SESSIONS.clear()
s, r = tool_call('echo', {'text': 'again'})
check(s == 200 and r['content'] == 'echo: again' and COUNTER['session'] == before + 1, 'an expired session (404) is initialized again')
s, r = tool_call('echo', {'text': 'cached'})
check(COUNTER['session'] == before + 1, 'the session is reused')

# New tools on a later refresh stay off until the owner reviews them; changed descriptions are flagged.
TOOLS_P1[1]['description'] = 'Write a note (now also deletes old ones)'
EXTRA[:] = [{'name': 'brand_new', 'description': 'Appeared later', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
            {'name': 'huge_sse', 'description': 'A very long SSE line', 'inputSchema': {'type': 'object'}},
            {'name': 'huge_json', 'description': 'A very long JSON answer', 'inputSchema': {'type': 'object'}},
            {'name': 'leak_encoded', 'description': 'Prints its token encoded', 'inputSchema': {'type': 'object'}}]
s, r = call('POST', '/v1/mcp/servers/fake/refresh', {'actor': 'owner'})
tools = {t['name']: t for t in r['tools']}
check(s == 200 and tools['brand_new']['new'] and tools['brand_new']['review'] and tools['brand_new']['required_level'] is None
      and r['review_count'] == 4 and not tools['echo']['new'], 'a tool that appears on a later refresh is new and off until reviewed')
check(tools['write_note']['changed'] == ['description'] and not tools['echo']['changed'], 'a changed description is flagged on the refresh')
detail = audit_rows('mcp_refresh')[-1][5]
check('new, disabled until reviewed: brand_new, huge_sse, huge_json, leak_encoded' in detail and 'changed: write_note (description)' in detail,
      f'the refresh audit row names new and changed tools ({detail})')
check('brand_new' not in tools_for(), 'a new tool is not offered to agents')
s, r = tool_call('brand_new')
check(s == 403 and r['error'] == 'tool_not_reviewed', 'a new tool is refused until reviewed')
s, r = call('POST', '/v1/mcp/servers/fake/refresh', {'actor': 'owner'})
check({t['name']: t['review'] for t in r['tools']}['brand_new'] is True, 'a new tool stays new on the next refresh until reviewed')
set_policy({'mcp': {'tools': {'fake': {'brand_new': {'level': 4}, 'huge_sse': {'level': 4}, 'huge_json': {'level': 4}, 'leak_encoded': {'level': 4}}}}})
s, r = call('GET', '/v1/mcp/servers/fake')
tools = {t['name']: t for t in r['tools']}
check(not tools['brand_new']['new'] and not tools['brand_new']['review'] and tools['brand_new']['required_level'] == 4 and r['review_count'] == 0,
      'enabling a new tool (an override) is its review')
check('brand_new' in tools_for(), 'a reviewed tool is offered')
s, r = call('POST', '/v1/mcp/servers/fake/refresh', {'actor': 'owner'})
check(not {t['name']: t['new'] for t in r['tools']}['brand_new'], 'a reviewed tool is not new again on the next refresh')

# Long lines: an SSE data: line and a JSON answer far above 64 KiB.
s, r = tool_call('huge_sse')
check(s == 200 and r['truncated'] and r['content'].startswith('yyyy'), f'a >70 KB result over SSE is read whole ({s} {r.get("error")})')
s, r = tool_call('huge_json')
check(s == 200 and r['truncated'] and r['content'].startswith('zzzz'), 'a >70 KB result over JSON is read whole')
mcp.MAX_RESPONSE = 50000
s, r = tool_call('huge_sse')
mcp.MAX_RESPONSE = 2 * 1024 * 1024
check(s == 502 and r['error'] == 'mcp_response_too_large', 'a long SSE line is still bounded by the size cap')

# Encoded and cut credential values in a result are masked before the gateway cuts it.
s, r = tool_call('leak_encoded')
check(s == 200 and r['content'].count('[credential:MCP_TOKEN]') >= 4 and TOKEN[:10] not in json.dumps(r)
      and TOKEN.replace('-', '%2d') not in r['content'] and base64.b64encode(TOKEN.encode()).decode()[:12] not in r['content'],
      'percent-encoded, base64, HTML- and JSON-escaped token forms are masked in MCP results')

# A server that drops its session right after initialize: a proper error, stored for the dashboard.
s, r = save('forgetful', {'url': FAKE + '/forgetful', 'allow_private': True, 'allow_insecure': True})
s, r = call('POST', '/v1/mcp/servers/forgetful/refresh', {'actor': 'owner'})
check(s == 502 and r['error'] == 'mcp_session_lost', f'a session lost during initialize is an MCP error ({s} {r})')
check(call('GET', '/v1/mcp/servers/forgetful')[1]['last_error'].startswith('mcp_session_lost'), 'the refresh stores it as last_error')
call('DELETE', '/v1/mcp/servers/forgetful')

# Several addresses: the first cannot be reached, the next checked one is used.
real_getaddrinfo = socket.getaddrinfo
port = fake.server_address[1]
socket.getaddrinfo = lambda host, *a, **k: ([(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.2', port)),
                                              (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
                                             if host == 'multi.test' else real_getaddrinfo(host, *a, **k))
save('multi', {'url': 'http://multi.test:%d/keyed' % port, 'allow_private': True, 'allow_insecure': True,
               'auth': {'type': 'header', 'header': 'X-API-Key', 'credential': 'MCP_KEY'}})
s, r = call('POST', '/v1/mcp/servers/multi/refresh', {'actor': 'owner'})
socket.getaddrinfo = real_getaddrinfo
check(s == 200 and r['tool_count'] == 1, f'the next address is tried when the first refuses the connection ({s} {r.get("error")})')
call('DELETE', '/v1/mcp/servers/multi')

# The URL itself as a credential (a token in the path), and token-looking path segments masked in the view.
URL_TOKEN = 'u7Kq' * 8
check(call('PUT', '/v1/credentials/MCP_URL', {'value': FAKE + '/s/' + URL_TOKEN + '/mcp?key=' + URL_TOKEN, 'actor': 'owner'})[0] == 200, 'URL credential stored')
s, r = save('urlcred', {'url_credential': 'MCP_URL', 'allow_private': True, 'allow_insecure': True})
check(s == 200 and r['url'] is None and r['url_credential'] == 'MCP_URL' and r['url_credential_stored'] is True and URL_TOKEN not in json.dumps(r),
      'a server URL can come from a credential (keys in its query allowed there)')
s, r = call('POST', '/v1/mcp/servers/urlcred/refresh', {'actor': 'owner'})
check(s == 200 and r['tool_count'] == 1 and SEEN[-1][0].startswith('/s/' + URL_TOKEN), 'the gateway connects to the URL from the credential')
check(URL_TOKEN not in json.dumps(audit_rows('mcp_%')), 'the URL credential never reaches the audit log')
s, r = save('urlcred', {'url_credential': 'not a name'})
check(s == 400 and r['error'] == 'invalid_url', 'the URL credential is a credential name')
s, r = save('pathtok', {'url': 'https://mcp.example.com/s/abc123/key/' + 'Zz9' * 9 + '/mcp'})
check(s == 200 and r['url'] == 'https://mcp.example.com/s/[redacted]/key/[redacted]/mcp', f'token-looking path segments are masked in the view ({r.get("url")})')
check('abc123' not in audit_rows('mcp_server_saved')[-1][5] and '[redacted]' in audit_rows('mcp_server_saved')[-1][5], 'and in the audit row')
check(mcp.display_url('https://h.example/v1/' + 'a1B2c3D4e5F6g7H8i9J0k1L2' + '/sse?x=1') == 'https://h.example/v1/[redacted]/sse?x=1'
      and mcp.display_url('https://h.example/api/mcp-server/notion') == 'https://h.example/api/mcp-server/notion', 'only random-looking segments are masked')
for n in ('urlcred', 'pathtok'):
    call('DELETE', '/v1/mcp/servers/' + n)

# Address tricks (gateway/netcheck.py), also as URLs.
mcp._test_allow_loopback = False
for addr in ('::127.0.0.1', '::ffff:0:127.0.0.1', '64:ff9b:1::a9fe:a9fe', '2002:7f00:1::1', '2001:0:4136:e378:8000:63bf:80ff:fffe',
             'fec0::1', '2001:db8::1', '192.0.2.10', '198.18.0.1', '203.0.113.5', '255.255.255.255'):
    check(ap(addr, True), 'refused even with private allowed: ' + addr)
check(ap('2002:0a00:0001::1', False) and ap('2002:0a00:0001::1', True) is None and ap('2002:0808:0808::1', False) is None,
      '6to4: the embedded IPv4 address decides')
for url in ('https://ssh-gateway-api:8099/v1', 'https://database/', 'https://agent-router/x', 'https://[::ffff:7f00:1]/mcp', 'https://[2002:a9fe:a9fe::1]/mcp'):
    s, r = save('trick', {'url': url, 'allow_private': True})
    check(s == 400 and r['error'] == 'address_refused', 'refused URL ' + url)
mcp._test_allow_loopback = True

# Size cap of an answer.
mcp.MAX_RESPONSE = 4096
s, r = tool_call('big')
mcp.MAX_RESPONSE = 2 * 1024 * 1024
check(s == 502 and r['error'] == 'mcp_response_too_large', 'answers above the size cap are refused')
check('mcp_response_too_large' in audit_rows('agent_mcp_read')[-1][5], 'a failed call is audited with its error')

# ---------------------------------------------------------------- header auth, missing credential, redirects, timeouts
s, r = save('keyed', {'url': FAKE + '/keyed', 'allow_private': True, 'allow_insecure': True, 'auth': {'type': 'header', 'header': 'X-API-Key', 'credential': 'MCP_KEY'}})
check(s == 200 and r['auth']['header'] == 'X-API-Key', 'custom header auth saved')
s, r = call('POST', '/v1/mcp/servers/keyed/refresh', {'actor': 'owner'})
check(s == 200 and r['tool_count'] == 1 and SEEN[-1][2].get('X-API-Key') == KEY, 'the custom header carries the credential')
save('keyed', {'auth': {'type': 'header', 'header': 'X-API-Key', 'credential': 'MISSING_ONE'}})
s, r = call('POST', '/v1/mcp/servers/keyed/refresh', {'actor': 'owner'})
check(s == 503 and r['error'] == 'credential_missing', 'a missing credential is reported')
save('keyed', {'auth': {'type': 'none'}})
s, r = call('POST', '/v1/mcp/servers/keyed/refresh', {'actor': 'owner'})
check(s == 502 and r['error'] == 'mcp_unauthorized', 'a 401 from the server reads as refused credentials')
s, r = save('redir', {'url': FAKE + '/redirect', 'allow_private': True, 'allow_insecure': True})
s, r = call('POST', '/v1/mcp/servers/redir/refresh', {'actor': 'owner'})
check(s == 502 and r['error'] == 'mcp_redirect', 'redirects are not followed')
save('slow', {'url': FAKE + '/slow', 'allow_private': True, 'allow_insecure': True, 'timeout': 5})
with srv.db_open() as db:
    try:
        mcp.request(db, mcp.server(db, 'slow'), 'tools/list', {}, timeout=1); slow = None
    except mcp.MCPError as exc:
        slow = exc.error
check(slow == 'mcp_timeout', f'a slow server times out ({slow})')
s, r = save('fake', {'enabled': False})
check(s == 200 and r['enabled'] is False and r['tool_count'] == 10, 'a server can be switched off (its settings are kept)')
check(tools_for() == [] and tool_call('echo', {'text': 'x'})[1]['error'] == 'server_disabled', 'a switched-off server offers and runs nothing')
save('fake', {'enabled': True})
s, r = save('fake', {'url': FAKE + '/mcp?v=2'})
check(r['refreshed'] is False and r['tool_count'] == 0 and tools_for() == [], 'a new URL drops the old tool list until refreshed')
check(save('fake', {'timeout': 500})[0] == 400, 'timeouts are capped at 120 s')

saved = [a for (_, a, *_r) in audit_rows('mcp_%')]
check('mcp_server_saved' in saved and 'mcp_refresh' in saved, 'registry changes and refreshes are audited')
s, r = call('GET', '/v1/audit?server=mcp:fake&facets=1')
check(s == 200 and r['rows'] and all(x['server'] == 'mcp:fake' for x in r['rows']) and 'mcp:fake' in r['facets']['servers'],
      'the audit log filters on mcp:<name>')

# ---------------------------------------------------------------- delete
set_policy({'mcp': {'tools': {'fake': {'wipe': {'level': 4}}}}})
s, r = call('DELETE', '/v1/mcp/servers/fake', headers={'X-HLA-Actor': 'owner'})
check(s == 200 and r['deleted'], 'deleted')
s, r = call('GET', '/v1/policy')
check('mcp:fake' not in r['services'] and 'fake' not in r['mcp']['tools'], 'delete removes the service level and the overrides')
check(call('DELETE', '/v1/mcp/servers/fake')[0] == 404, 'deleting twice is a 404')
check(audit_rows('mcp_server_deleted')[-1][0] == 'mcp:fake', 'delete is audited')
# Saving it again starts from level 0, not from the old level.
save('fake', body)
check(call('GET', '/v1/policy')[1]['services']['mcp:fake'] == {'level': 0}, 'a re-created server starts at level 0 again')

httpd.shutdown(); fake.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
