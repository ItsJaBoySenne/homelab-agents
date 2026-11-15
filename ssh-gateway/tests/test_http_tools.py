"""HTTP tools through the gateway API (HLA_HTTP_TOOLS_V1): real HTTP server and SQLite; DNS and the outgoing HTTPS
request replaced by stand-ins. Definitions, levels, SSRF refusals, credential filling and masking, audit.

python3 tests/test_http_tools.py   (needs paramiko importable; no network)
"""
import base64
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'GATEWAY_CREDENTIAL_KEY': base64.b64encode(os.urandom(32)).decode()})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_api.server as srv  # noqa: E402
from gateway import http_tools as ht, credentials as creds  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

SECRET = 'wk_' + 'Z9q' * 10   # the weather API key
DNS = {'api.weather.test': ['93.184.216.34'], 'internal.lan': ['192.168.10.50'], 'rebind.test': ['93.184.216.40', '127.0.0.1'],
       'meta.test': ['169.254.169.254'], 'cgnat.test': ['100.64.1.1'], 'mapped.test': ['::ffff:10.0.0.1'], 'v6.test': ['2606:4700::1111']}
LOOKUPS, SENT = [], []
def fake_resolve(host, port):
    LOOKUPS.append(host)
    if host not in DNS: raise socket.gaierror('no such host')
    return DNS[host]
REPLY = {}
def fake_send(method, host, port, address, target, headers, data, timeout, limit):
    SENT.append({'method': method, 'host': host, 'port': port, 'address': address, 'target': target, 'headers': dict(headers),
                 'data': json.loads(data) if data else None, 'timeout': timeout, 'limit': limit})
    if REPLY.get('raise'): raise REPLY['raise']
    body = REPLY.get('body', json.dumps({'temp': 11, 'echo_key': SECRET if REPLY.get('echo') else None}).encode())
    return REPLY.get('status', 200), {'content-type': REPLY.get('type', 'application/json'), **REPLY.get('headers', {})}, body[:limit + 1]
ht._resolve, ht._send = fake_resolve, fake_send

httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % httpd.server_address[1]
AGENT = '11111111-2222-3333-4444-555555555555'
ADMIN, TOOL = 'a' * 40, 't' * 40

def call(method, path, body=None, token=ADMIN):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def policy(change):
    return call('PUT', '/v1/policy', {'change': change, 'actor': 'test'})

def use(tool, token=TOOL, **params):
    body = {'tool': tool, 'params': params, **({'agent_id': AGENT} if token == TOOL else {'actor': 'owner'})}
    return call('POST', '/v1/http_tool', body, token=token)

WEATHER = {'description': 'Current weather for a city. Use for questions about the weather.', 'method': 'GET',
           'base_url': 'https://api.weather.test/v2', 'path': '/cities/{city}/now',
           'params': [{'name': 'city', 'type': 'string', 'description': 'city name'},
                      {'name': 'units', 'in': 'query', 'type': 'string', 'enum': ['metric', 'imperial']},
                      {'name': 'days', 'in': 'query', 'type': 'integer'}],
           'headers': {'X-Client': 'hla'}, 'auth': {'credential': 'WEATHER_KEY'}, 'actor': 'owner'}

# --- Definitions: admin token only, validated ---
s, r = call('PUT', '/v1/http_tools/weather_now', WEATHER, token=TOOL)
check(s == 401, 'the tool token cannot define HTTP tools')
s, r = call('PUT', '/v1/http_tools/weather_now', WEATHER)
check(s == 200 and r['name'] == 'weather_now' and r['level'] == 1 and r['timeout'] == 15 and r['auth'] ==
      {'in': 'header', 'name': 'Authorization', 'prefix': 'Bearer ', 'credential': 'WEATHER_KEY'}
      and r['params'][0] == {'name': 'city', 'in': 'path', 'type': 'string', 'required': True, 'description': 'city name'},
      'GET tool saved: level 1 by default, path parameter required, auth defaults to Authorization: Bearer')
s, r = call('PUT', '/v1/http_tools/ticket_create', {'description': 'Open a ticket.', 'method': 'POST', 'base_url': 'https://api.weather.test',
            'path': '/tickets', 'params': [{'name': 'title', 'type': 'string', 'required': True}, {'name': 'urgent', 'type': 'boolean'}]})
check(s == 200 and r['level'] == 4 and r['params'][0]['in'] == 'body', 'write tool: level 4 by default, parameters go in the JSON body')
BAD = [({'base_url': 'http://api.weather.test'}, 'https only'),
       ({'base_url': 'https://user:pw@api.weather.test'}, 'no user info in the URL'),
       ({'base_url': 'https://api.weather.test/?x=1'}, 'no query in the base URL'),
       ({'base_url': 'https://api.weather.test/../x'}, 'no .. in the base URL'),
       ({'path': '/a/../b', 'params': []}, 'no .. in the path'),
       ({'path': '/cities/{town}'}, 'placeholders must match path parameters'),
       ({'path': '/x', 'params': [{'name': 'a', 'in': 'body'}]}, 'GET tools take no body parameters'),
       ({'method': 'TRACE'}, 'method allowlist'),
       ({'headers': {'Host': 'evil.test'}}, 'Host header cannot be set'),
       ({'headers': {'X-Key': '{credential:WEATHER_KEY}'}}, 'credentials only through auth'),
       ({'headers': {'X-A': 'a\r\nX-B: b'}}, 'no header injection'),
       ({'base_url': 'https://ssh-gateway-api:8099'}, 'not the gateway API (a host of this stack)'),
       ({'base_url': 'https://database'}, 'not the database (a host of this stack)'),
       ({'timeout': 31}, 'timeout at most 30 s'),
       ({'max_response': 300000}, 'max_response at most 256 KiB'),
       ({'level': 7}, 'level 1..6'),
       ({'auth': {'credential': 'lower'}}, 'auth names an UPPER_CASE credential'),
       ({'params': [{'name': 'city'}, {'name': 'city', 'in': 'query'}]}, 'parameters declared once'),
       ({'params': [{'name': 'city'}, {'name': 'n', 'in': 'query', 'type': 'integer', 'enum': ['a']}]}, 'enum values match the type')]
for header in ('Authorization', 'Cookie', 'X-API-Key', 'X-Auth-Token', 'Private-Token', 'X-Client-Secret', 'Api-Password'):
    s, r = call('PUT', '/v1/http_tools/bad_tool', {**WEATHER, 'headers': {header: 'raw-value-123456'}})
    check(s == 400 and r['error'] == 'invalid_tool' and 'credential store' in r['detail'] and 'auth' in r['detail'],
          'static header ' + header + ' refused, pointing to the credential picker')
for change, label in BAD:
    s, r = call('PUT', '/v1/http_tools/bad_tool', {**WEATHER, **change})
    check(s == 400 and r['error'] == 'invalid_tool', 'refused: ' + label)
s, r = call('PUT', '/v1/http_tools/Bad-Name', WEATHER)
check(s == 400 and 'snake_case' in r['detail'], 'tool names are snake_case')
s, r = call('GET', '/v1/http_tools')
check(s == 200 and [t['name'] for t in r['tools']] == ['ticket_create', 'weather_now'] and r['tools'][1]['credential_stored'] is False
      and r['credentials_configured'], 'listing shows the tools and that WEATHER_KEY is not stored yet')
check(call('GET', '/v1/http_tools', token=TOOL)[0] == 401, 'the tool token cannot read definitions')

# --- Policy: the http_tools service and per-tool levels ---
s, snap = call('GET', '/v1/policy')
check(snap['services'].get('http_tools') == {'level': 1} and snap['http_tools']['tools']['weather_now']['level'] == 1,
      'http_tools is a service at level 1; the policy lists the tools and their levels')
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
check(eff['services']['http_tools']['tools'] == [], 'an agent without a level gets no tools')
s, r = use('weather_now', city='Ghent')
check(s == 403 and r['error'] == 'policy_denied' and not SENT, 'an agent without a level cannot call')
policy({'agents': {AGENT: {'name': 'Helper', 'level': 4}}})
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
tools = {t['name']: t for t in eff['services']['http_tools']['tools']}
check(list(tools) == ['weather_now'] and 'base_url' not in tools['weather_now'] and 'auth' not in tools['weather_now']
      and tools['weather_now']['params'][1]['enum'] == ['metric', 'imperial'],
      'effective policy: tools at the agent level (min of agent 4 and service 1), with parameters but no URL or credential')

# --- Calls: credential missing, then filled only inside the gateway ---
s, r = use('weather_now', city='Ghent')
check(s == 503 and r['error'] == 'credential_missing' and not SENT, 'a missing credential is explained before anything is sent')
with srv.db_open() as db:
    creds.put(db, 'WEATHER_KEY', SECRET, kind='api_key', actor='owner')
s, r = use('weather_now', city='Ghent/West', units='metric', days=2)
sent = SENT[-1]
check(s == 200 and r['http_status'] == 200 and r['ok'] and r['json']['temp'] == 11, 'the call returns status and parsed JSON')
check(sent['target'] == '/v2/cities/Ghent%2FWest/now?units=metric&days=2' and sent['host'] == 'api.weather.test' and sent['address'] == '93.184.216.34',
      'path parameters are URL-encoded into one segment, query appended, connection pinned to the checked address')
check(sent['headers']['Authorization'] == 'Bearer ' + SECRET and sent['headers']['X-Client'] == 'hla', 'the credential is filled in by the gateway')
check(SECRET not in json.dumps(r), 'the reply never carries the credential')
REPLY['echo'] = True
s, r = use('weather_now', city='Ghent')
check(s == 200 and SECRET not in json.dumps(r) and r['json']['echo_key'] == '[credential:WEATHER_KEY]', 'a credential echoed by the API is masked')
REPLY.pop('echo')
# Encoded forms of the credential in an answer: JSON-escaped, percent-encoded (either case), HTML entities, base64,
# as a JSON key, and cut off at the end of a truncated answer.
ENC = {'json_esc': SECRET.replace('_', '\\u005f'), 'pct_upper': SECRET.replace('_', '%5F'), 'pct_lower': SECRET.replace('_', '%5f'),
       'html': SECRET.replace('_', '&#95;'), 'html_hex': SECRET.replace('_', '&#x5F;'), 'b64': base64.b64encode(SECRET.encode()).decode(),
       'b64url': base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip('=')}
REPLY.update(body=json.dumps({**ENC, SECRET: 'as a key'}).replace('\\\\u005f', '\\u005f').encode())
s, r = use('weather_now', city='Ghent')
dump = json.dumps(r)
check(s == 200 and SECRET not in dump and all(v not in dump for v in ENC.values()) and '[credential:WEATHER_KEY]' in r['json']
      and all(r['json'][k] == '[credential:WEATHER_KEY]' for k in ENC if k != 'json_esc'),
      'encoded credential forms and credentials used as keys are masked (' + dump[:300] + ')')
REPLY.update(body=b'x' * 2040 + SECRET.encode(), type='text/plain')
call('PUT', '/v1/http_tools/cut_page', {'description': 'x', 'base_url': 'https://api.weather.test', 'path': '/cut', 'max_response': 2048,
     'auth': {'credential': 'WEATHER_KEY'}})
s, r = use('cut_page')
check(s == 200 and r['truncated'] and SECRET[:8] not in r['text'] and r['text'].endswith('[credential:WEATHER_KEY]'),
      'a credential cut off by the response cap is masked from its first characters')
REPLY.clear()
call('DELETE', '/v1/http_tools/cut_page')
for params, err in (({}, 'city is required'), ({'city': 'x', 'units': 'kelvin'}, 'must be one of'), ({'city': 'x', 'days': '2'}, 'must be a integer'),
                    ({'city': 'x', 'evil': 1}, 'unknown parameter'), ({'city': '..'}, 'cannot be empty, . or ..'), ({'city': 5}, 'must be a string')):
    s, r = use('weather_now', **params)
    check(s == 400 and r['error'] == 'invalid_params' and err in r['detail'], 'parameters checked: ' + err)
s, r = use('ticket_create', title='x')
check(s == 403 and 'needs level 4' in r['detail'], 'write tool at level 1 on the service: refused')
policy({'servers': {'http_tools': {'level': 4}}})
s, r = use('ticket_create', title='Printer broken', urgent=True)
check(s == 200 and SENT[-1]['method'] == 'POST' and SENT[-1]['data'] == {'title': 'Printer broken', 'urgent': True}
      and SENT[-1]['headers']['Content-Type'] == 'application/json' and SENT[-1]['target'] == '/tickets', 'level 4: write tool posts a JSON body')

# --- SSRF: the resolved addresses decide, also for hosts the owner typed ---
def tool_at(name, host, **extra):
    s, r = call('PUT', '/v1/http_tools/' + name, {'description': 'x', 'base_url': 'https://' + host, 'path': '/', **extra})
    assert s == 200, r
for name, host, label in (('lan', 'internal.lan', 'a private address'), ('rebind', 'rebind.test', 'a name that also resolves to loopback'),
                          ('meta', 'meta.test', 'the metadata address'), ('cgnat', 'cgnat.test', 'a CGNAT address'),
                          ('mapped', 'mapped.test', 'an IPv4-mapped private address'), ('literal', '10.1.2.3', 'a private IP literal')):
    DNS.setdefault('10.1.2.3', ['10.1.2.3'])
    tool_at(name + '_api', host)
    n = len(SENT)
    s, r = use(name + '_api')
    check(s == 403 and r['error'] == 'address_refused' and len(SENT) == n, 'SSRF: refused ' + label + ' (' + r.get('detail', '') + ')')
tool_at('lan_api', 'internal.lan', allow_private=True)
s, r = use('lan_api')
check(s == 200 and SENT[-1]['address'] == '192.168.10.50', 'allow_private reaches a LAN address')
for name, host in (('meta_api', 'meta.test'), ('rebind_api', 'rebind.test')):
    tool_at(name, host, allow_private=True)
    s, r = use(name)
    check(s == 403 and r['error'] == 'address_refused', 'allow_private still refuses loopback and metadata: ' + host)
tool_at('v6_api', 'v6.test')
check(use('v6_api')[0] == 200, 'a public IPv6 address is fine')
tool_at('nowhere_api', 'nowhere.test')
s, r = use('nowhere_api')
check(s == 502 and r['error'] == 'dns_failed', 'an unknown host is a DNS failure')
# Several addresses: kept in DNS order, every one checked, the next tried only when a connection cannot be made.
DNS.update({'order.test': ['93.184.216.60', '2606:4700::1', '93.184.216.5'], 'mixed.test': ['93.184.216.61', '::ffff:127.0.0.1']})
tool_at('order_api', 'order.test')
s, r = use('order_api')
check(s == 200 and SENT[-1]['address'] == '93.184.216.60', 'the first address in DNS order is used (not the sorted first)')
real_send = ht._send
def flaky_send(method, host, port, address, *rest):
    if address == '93.184.216.60':
        SENT.append({'address': address, 'failed': True})
        raise ht._ConnectFailed('ConnectionRefusedError')
    return real_send(method, host, port, address, *rest)
ht._send = flaky_send
s, r = use('order_api')
check(s == 200 and [x['address'] for x in SENT[-2:]] == ['93.184.216.60', '93.184.216.5'], 'when the first cannot be reached the next (IPv4 first) is tried')
call('PUT', '/v1/http_tools/order_write', {'description': 'x', 'method': 'POST', 'base_url': 'https://order.test', 'path': '/w', 'level': 4})
def broken_send(method, host, port, address, *rest):
    SENT.append({'address': address})
    raise OSError('connection reset after the request')
ht._send = broken_send
n = len(SENT)
s, r = use('order_write')
check(s == 502 and r['error'] == 'request_failed' and len(SENT) == n + 1, 'a failure after sending is not retried on another address')
ht._send = real_send
tool_at('mixed_api', 'mixed.test', allow_private=True)
s, r = use('mixed_api')
check(s == 403 and r['error'] == 'address_refused', 'every address is checked: a mapped loopback among them refuses the call')
# Embedded IPv4 forms (gateway/netcheck.py), also through DNS answers.
for addr in ('::127.0.0.1', '::ffff:0:127.0.0.1', '64:ff9b::a9fe:a9fe', '64:ff9b:1::a9fe:a9fe', '2002:7f00:1::1', '2002:a9fe:a9fe::1',
             '2001:0:4136:e378:8000:63bf:80ff:fffe', 'fec0::1', '2001:db8::5', '192.0.2.1', '198.51.100.7', '203.0.113.9', '198.19.1.1',
             '192.0.0.170', '100.100.100.200', 'fd00:ec2::254'):
    check(ht.check_address(addr, True) is not None, 'refused even with allow_private: ' + addr)
check(ht.check_address('2002:c0a8:0101::1', False) and ht.check_address('2002:c0a8:0101::1', True) is None
      and ht.check_address('2001:0:4136:e378:8000:63bf:f5f6:f6f6', False) and ht.check_address('64:ff9b::808:808', False) is None,
      'private addresses inside 6to4 / Teredo need allow_private; public ones inside NAT64 are fine')
DNS.update({'teredo.test': ['2001:0:4136:e378:8000:63bf:80ff:fffe'], 'sixtofour.test': ['2002:7f00:1::1']})
for host in ('teredo.test', 'sixtofour.test'):
    tool_at(host.split('.')[0] + '_api', host, allow_private=True)
    s, r = use(host.split('.')[0] + '_api')
    check(s == 403 and r['error'] == 'address_refused', 'a DNS answer hiding loopback in ' + host + ' is refused')
for addr, ok in (('8.8.8.8', True), ('10.0.0.1', False), ('172.16.0.1', False), ('127.0.0.1', False), ('0.0.0.0', False), ('::1', False),
                 ('fe80::1', False), ('fc00::1', False), ('224.0.0.1', False), ('240.0.0.1', False), ('169.254.169.254', False)):
    check((ht.check_address(addr, False) is None) == ok, 'address check: ' + addr + (' allowed' if ok else ' refused'))

# --- Responses: redirects not followed, truncation, errors without credentials ---
REPLY.update(status=302, headers={'location': 'https://elsewhere.test/x'}, body=b'', type='text/html')
s, r = use('weather_now', city='Ghent')
check(s == 200 and r['http_status'] == 302 and 'not followed' in r['note'] and 'elsewhere.test' in r['note'] and len(SENT) and SENT[-1]['host'] == 'api.weather.test',
      'redirects are reported, never followed')
REPLY.clear()
call('PUT', '/v1/http_tools/big_page', {'description': 'x', 'base_url': 'https://api.weather.test', 'path': '/big', 'max_response': 2048})
REPLY.update(body=b'A' * 5000, type='text/plain')
s, r = use('big_page')
check(s == 200 and r['truncated'] and len(r['text']) == 2048 and 'truncated at 2048' in r['note'] and SENT[-1]['limit'] == 2048, 'responses are capped with a note')
REPLY.clear()
REPLY['raise'] = OSError('connection refused with ' + SECRET)
s, r = use('weather_now', city='Ghent')
check(s == 502 and r['error'] == 'request_failed' and SECRET not in json.dumps(r), 'a failed request is explained without the credential')
REPLY['raise'] = socket.timeout()
s, r = use('weather_now', city='Ghent')
check(s == 504 and r['error'] == 'timeout', 'a timeout is reported')
REPLY.clear()

# --- approval_required: only at level 6 or unhinged ---
call('PUT', '/v1/http_tools/door_unlock', {'description': 'Unlock the door.', 'method': 'POST', 'base_url': 'https://api.weather.test',
     'path': '/door', 'approval_required': True, 'level': 4})
s, r = use('door_unlock')
check(s == 403 and 'level 6 or unhinged' in r['detail'], 'approval_required: refused at level 4')
s, eff = call('GET', '/v1/policy/effective?agent_id=' + AGENT)
check('door_unlock' not in {t['name'] for t in eff['services']['http_tools']['tools']}, 'approval_required tools are not offered below level 6')
policy({'unhinged': {'enabled': True, 'hours': 1}, 'agents': {AGENT: {'name': 'Helper', 'level': 5}}})
check(use('door_unlock')[0] == 200, 'approval_required: allowed in unhinged mode')
policy({'unhinged': {'enabled': False}, 'agents': {AGENT: {'name': 'Helper', 'level': 4}}})

# --- Disabled tools, admin test, delete ---
call('PUT', '/v1/http_tools/weather_now', {**WEATHER, 'enabled': False})
check(use('weather_now', city='x')[1].get('error') == 'unknown_tool', 'a disabled tool cannot be called')
call('PUT', '/v1/http_tools/weather_now', WEATHER)
policy({'agents': {AGENT: None}})
s, r = use('weather_now', token=ADMIN, city='Ghent')
check(s == 200 and r['http_status'] == 200 and SECRET not in json.dumps(r), 'admin token: the dashboard tests a tool (no agent level needed)')
check(call('POST', '/v1/http_tool', {'tool': 'weather_now', 'params': {'city': 'x'}})[1]['error'] == 'actor_required', 'admin test names its actor')
check(call('POST', '/v1/http_tool', {'tool': 'weather_now', 'params': {}}, token='x' * 40)[0] == 401, 'other tokens refused')
s, r = call('DELETE', '/v1/http_tools/big_page', token=TOOL)
check(s == 401, 'the tool token cannot delete tools')
s, r = call('DELETE', '/v1/http_tools/big_page')
check(s == 200 and r['deleted'] and call('GET', '/v1/http_tools/big_page')[0] == 404, 'tool deleted')

# --- Audit: every call with tool, method, host and path; never the credential or query values ---
with sqlite3.connect(os.environ['GATEWAY_DB']) as db:
    rows = db.execute("SELECT action,detail,exit_code,agent_id,actor FROM audit WHERE server='http_tools' ORDER BY id").fetchall()
    dump = json.dumps(db.execute('SELECT * FROM audit').fetchall())
actions = {a for a, *_ in rows}
check({'http_tool_saved', 'http_tool_deleted', 'agent_http_read', 'agent_http_change', 'agent_http_denied', 'admin_http_test'} <= actions,
      f'saves, deletes, reads, changes, denials and dashboard tests are audited ({sorted(actions)})')
read = next(d for a, d, *_ in rows if a == 'agent_http_read' and 'Ghent%2FWest' in d)
check(read == 'weather_now GET api.weather.test/v2/cities/Ghent%2FWest/now ?units,days -> 200', 'audit detail: method, host, path, query names, status (' + read + ')')
check(any(a == 'agent_http_read' and 'address_refused' in d for a, d, *_ in rows), 'SSRF refusals are audited')
check(any(a == 'admin_http_test' and actor == 'owner' and agent is None for a, d, code, agent, actor in rows), 'dashboard tests are audited with the actor')
check(SECRET not in dump and 'metric' not in dump, 'no credential and no query values in the audit log')

httpd.shutdown()
print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
