"""Managed Traefik routes (gateway/routes.py): validation, deterministic rendering, conflicts with hand-written routers,
the write + verify + rollback flow, apr1 hashes, DNS checks, agent requests and the /v1/routes endpoints.

The traefik host is simulated: the write script really runs in bash against a temporary managed file and backup folder,
and a fake Traefik API "loads" the managed file (parsed like the file provider would) next to hand-written routers.

python3 tests/test_routes.py   (needs paramiko importable; nothing is executed remotely)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
DYN = TMP + '/dynamic'
BAK = TMP + '/managed-backups'
os.makedirs(DYN)
os.makedirs(BAK)
MANAGED = DYN + '/hla-managed.yml'
Path(MANAGED).write_text('# created by root\nhttp: {}\n')
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40,
                   'TRAEFIK_MANAGED_FILE': MANAGED, 'TRAEFIK_ROUTE_BACKUP_DIR': BAK})
for k in ('TRAEFIK_API_URL', 'TRAEFIK_CERT_RESOLVER', 'PUBLIC_IPS', 'WEBSITE_BACKEND_HOSTS', 'INFISICAL_GENERATED_PROJECT_ID'):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import routes as R, websites as W  # noqa: E402
from gateway.cli import db_open  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

PASSWORD = 'Zq8LwP3vXn5Rt7Ky9Hs2Jd4Fg6Mb1Nc0'
SECRETS = (PASSWORD, 'HANDHASH', '10.66.0.0')  # never in any output, audit row or dashboard reply

# ── apr1 vectors (computed with `openssl passwd -apr1 -salt ...`) ─────────────────────────────────────────────────
check(R.apr1('password', 'abcdefgh') == '$apr1$abcdefgh$FBwExRW4dCc8aL.OvjpIE1', 'apr1: openssl vector 1')
check(R.apr1('hunter2 x', 'Ab1/.z') == '$apr1$Ab1/.z$IXKScU4WPFZf60ksC0Ksk1', 'apr1: openssl vector 2 (short salt, space)')
check(R.apr1('', '12345678') == '$apr1$12345678$sHuPAw7VA9xjRbJz7zKV7/', 'apr1: empty password')
check(R.apr1('Ünïcode-0123456789abcdefghijk', 'salt0001') == '$apr1$salt0001$utbPP9ivR82nJGrrxZD8e.', 'apr1: long UTF-8 password')
h1, h2 = R.apr1('x'), R.apr1('x')
check(h1 != h2 and re.fullmatch(r'\$apr1\$[./0-9A-Za-z]{8}\$[./0-9A-Za-z]{22}', h1), 'apr1: random 8-character salt')
if shutil.which('openssl'):
    salt = h1.split('$')[2]
    out = subprocess.run(['openssl', 'passwd', '-apr1', '-salt', salt, 'x'], capture_output=True, text=True).stdout.strip()
    check(out == h1, 'apr1: matches openssl for a random salt')

# ── fake traefik host ────────────────────────────────────────────────────────────────────────────────────────────
HAND_ROUTERS = [
    {'name': 'nw2-staging@file', 'provider': 'file', 'rule': 'Host(`nw2-staging.example.com`)', 'entryPoints': ['websecure'],
     'middlewares': ['nw-staging-lan@file', 'nw-staging-auth@file'], 'service': 'nw-staging', 'tls': {'certResolver': 'letsencrypt'}, 'status': 'enabled'},
    {'name': 'paperclip@file', 'provider': 'file', 'rule': 'Host(`paperclip.example.net`)', 'entryPoints': ['websecure'],
     'middlewares': ['lan-only@file'], 'service': 'paperclip', 'tls': {'certResolver': 'letsencrypt'}, 'status': 'enabled'},
    {'name': 'shop-old@file', 'provider': 'file', 'rule': 'Host(`www.northwind.example`)', 'entryPoints': ['websecure'],
     'service': 'shop-old', 'tls': {'certResolver': 'le-dns'}, 'status': 'enabled'},
    {'name': 'agents@docker', 'provider': 'docker', 'rule': 'Host(`agents.example.com`)', 'entryPoints': ['websecure'],
     'service': 'agents', 'tls': {}, 'status': 'enabled'},
    {'name': 'api@internal', 'provider': 'internal', 'rule': 'PathPrefix(`/api`)', 'entryPoints': ['traefik'], 'service': 'api@internal', 'status': 'enabled'},
]
HAND_MWS = [{'name': 'nw-staging-auth@file', 'provider': 'file', 'type': 'basicauth', 'basicAuth': {'users': ['admin:$apr1$HANDHASH']}, 'status': 'enabled'},
            {'name': 'nw-staging-lan@file', 'provider': 'file', 'type': 'ipallowlist', 'ipAllowList': {'sourceRange': ['10.66.0.0/16']}, 'status': 'enabled'},
            {'name': 'lan-only@file', 'provider': 'file', 'type': 'ipallowlist', 'status': 'enabled'}]
HAND_SVCS = [{'name': n + '@file', 'provider': 'file', 'status': 'enabled'} for n in ('nw-staging', 'paperclip', 'shop-old')]
MODE = {'api_down': False, 'router_error': False, 'stale': False, 'break_other': False, 'api_down_after_write': False, 'ssh_down': False}
CALLS = []
LOADED = {'text': Path(MANAGED).read_text()}  # what the fake Traefik has loaded


def api_view():
    try:
        cfg = W.parse_yaml(LOADED['text'])
    except W.YamlError:
        cfg = {}
    http = cfg.get('http') or {}
    routers, svcs, mws = [dict(r) for r in HAND_ROUTERS], list(HAND_SVCS), list(HAND_MWS)
    for name, r in (http.get('routers') or {}).items():
        item = {'name': name + '@file', 'provider': 'file', 'rule': r.get('rule'), 'entryPoints': r.get('entryPoints'), 'service': r.get('service'),
                'middlewares': [m + '@file' for m in r.get('middlewares') or []], 'tls': r.get('tls'), 'status': 'enabled'}
        if MODE['router_error']:
            item.update(status='disabled', error=['middleware "nope@file" does not exist'])
        routers.append(item)
    for name in http.get('services') or {}:
        svcs.append({'name': name + '@file', 'provider': 'file', 'status': 'enabled'})
    for name, m in (http.get('middlewares') or {}).items():
        mws.append({'name': name + '@file', 'provider': 'file', 'type': next(iter(m)).lower(), **m, 'status': 'enabled'})
    if MODE['break_other'] and 'hla-shop' in (http.get('routers') or {}):
        for r in routers:
            if r['name'] == 'paperclip@file':
                r.update(status='disabled', error=['middleware "lan-only@file" defined multiple times'])
    return {'routers': routers, 'services': svcs, 'middlewares': mws,
            'entrypoints': [{'name': 'web'}, {'name': 'websecure'}, {'name': 'traefik'}, {'name': 'websecure-alt'}]}


def fake_execute(server, command, timeout=30, max_output=12000, stdin=None):
    CALLS.append((server, command, stdin))
    if MODE['ssh_down']:
        raise RuntimeError('SSH host fingerprint mismatch')
    assert server == 'traefik', server
    if 'HLA_PREV' in command and MODE.get('half_write') and 'M=write' in command:  # the file changed, then the check failed
        old = Path(MANAGED).read_bytes()
        Path(MANAGED).write_text('http:\n  routers: {broken\n')
        return {'exit_code': 3, 'timed_out': False, 'truncated': False, 'stderr': '',
                'stdout': 'HLA_PREV ' + base64.b64encode(old).decode() + '\nHLA_ERR the managed file does not hold the new content after writing\n'}
    if 'HLA_PREV' in command:  # the write script: run it for real against the temporary folders
        res = subprocess.run(['bash', '-c', command], input=stdin, capture_output=True, timeout=30)
        if not MODE['stale'] and os.path.exists(MANAGED):
            LOADED['text'] = Path(MANAGED).read_text()  # Traefik's watcher picks the file up
        return {'exit_code': res.returncode, 'timed_out': False, 'stdout': res.stdout.decode(), 'stderr': res.stderr.decode(), 'truncated': False}
    m = re.search(r'B=(HLA_[0-9a-f]+)', command)
    b = m.group(1)
    down = MODE['api_down'] or (MODE['api_down_after_write'] and any('HLA_PREV' in c[1] for c in CALLS))
    if down:
        out = f'{b} API routers 7\ncurl: (7) Failed to connect to 127.0.0.1 port 8080\n{b} END\n{b} DONE\n'
    else:
        view = api_view()
        out = ''.join(f'{b} API {k} 0\n{json.dumps(view[k])}\n{b} END\n' for k in ('routers', 'services', 'middlewares', 'entrypoints')) + f'{b} DONE\n'
    return {'exit_code': 0, 'timed_out': False, 'stdout': out, 'stderr': '', 'truncated': False}


T = [0.0]
R.clock = lambda: T[0]
R.sleep = lambda s: T.__setitem__(0, T[0] + s)
DNS = {'shop-staging.example.com': ['203.0.113.10'], 'lan.example.com': ['192.168.30.103'], 'away.example.com': ['203.0.113.9'],
       'v6.example.com': ['203.0.113.10', '2001:db8::1']}
def fake_resolve(host):
    if host == 'slow.example.com':
        import time; time.sleep(5)
    if host not in DNS:
        import socket; raise socket.gaierror(-2, 'Name or service not known')
    return DNS[host]
R.resolver = fake_resolve
R.DNS_TIMEOUT = 0.5
PW_CALLS = []
def fake_password(name, host, actor):
    PW_CALLS.append((name, host, actor))
    return PASSWORD
R.ensure_password = fake_password

# ── validation ───────────────────────────────────────────────────────────────────────────────────────────────────
DOMAINS = ['example.com', 'example.net']
def build(name='shop', **body):
    base = {'host': 'shop.example.com', 'environment': 'production', 'backend_port': 8081}
    base.update(body)
    return R.build_row(name, base, None, DOMAINS, 'owner', 1)
def refused(fn, error):
    try:
        fn()
    except R.RouteError as exc:
        return exc.error == error
    return False
row = build()
check(row['backend'] == 'http://192.168.30.11:8081' and row['protection'] == 'public' and row['basic_user'] is None, 'production defaults to public')
st = build('st', host='shop-staging.example.com', environment='staging')
check(st['protection'] == 'lan_only+basic_auth' and st['basic_user'] == 'hla' and st['basic_secret'] == 'TRAEFIK_BASIC_ST', 'staging defaults to lan_only+basic_auth')
bad_hosts = ['shop.example.com`) || Host(`evil.com', 'shop.example.com"', "shop'.example.com", 'shop .example.com', 'Shop..example.com',
             '-shop.example.com', 'shop.example.com\nx: 1', 'shop.example.org', 'x' * 70 + '.example.com']
check(all(refused(lambda h=h: build(host=h), 'invalid_host') or refused(lambda h=h: build(host=h), 'unknown_domain') for h in bad_hosts),
      'hosts with quotes, backticks, spaces, newlines, bad labels or another domain are refused')
check(refused(lambda: R.build_row('shop', {'host': 'shop.example.com', 'backend_port': 80}, None, [], 'o', 1), 'no_domains'), 'no domains configured: refused')
check(all(refused(lambda n=n: build(n), 'invalid_name') for n in ['Shop', 'shop_1', '-shop', 'a' * 41, 'shop/x', 'shop"', '']), 'bad route names are refused')
bad_backends = [{'backend': 'http://192.168.30.12:80'}, {'backend': 'http://evil.com:80'}, {'backend': 'https://192.168.30.11:443'},
                {'backend': 'http://192.168.30.11:80/x'}, {'backend_port': 0}, {'backend_port': 70000}, {'backend_port': '80; rm'},
                {'backend_port': True}, {'backend_port': 80, 'backend_host': '192.168.10.202'}]
check(all(refused(lambda b=b: build(**b), 'invalid_backend') or refused(lambda b=b: build(**b), 'backend_not_allowed') for b in bad_backends),
      'backends off the website VM, with paths, other schemes or bad ports are refused')
check(build(backend='http://public-website:8090')['backend'] == 'http://public-website:8090', 'backend by the website VM name')
os.environ['WEBSITE_BACKEND_HOSTS'] = 'web2.lan'
check(build(backend='http://web2.lan:81')['backend'] == 'http://web2.lan:81', 'WEBSITE_BACKEND_HOSTS adds backend hosts')
os.environ.pop('WEBSITE_BACKEND_HOSTS')
check(all(refused(lambda p=p: build(path_prefix=p), 'invalid_path') for p in ['app', '/a`b', '/a b', '/a"', '/x\ny: 1', '/' + 'a' * 120]),
      'bad path prefixes are refused')
check(build(path_prefix='/app/')['path_prefix'] == '/app' and build(path_prefix='/')['path_prefix'] == '', 'path prefix normalised')
check(refused(lambda: build(protection='basic_auth', basic_user='ad:min'), 'invalid_user') and refused(lambda: build(protection='open'), 'invalid_protection')
      and refused(lambda: build(environment='test'), 'invalid_environment'), 'bad user, protection and environment refused')
check(refused(lambda: build(entrypoints=['traefik']), 'invalid_entrypoints') and refused(lambda: build(entrypoints=['a b']), 'invalid_entrypoints')
      and refused(lambda: build(cert_resolver='le"x'), 'invalid_cert_resolver'), 'traefik entry point and bad names refused')

# ── rendering ────────────────────────────────────────────────────────────────────────────────────────────────────
st['basic_hash'] = R.apr1(PASSWORD, 'saltsalt')
lan = build('lan', host='lan.example.com', protection='lan_only', entrypoints=['websecure-alt'])
rows_a = {'shop': row, 'st': st, 'lan': lan}
rows_b = {'lan': lan, 'shop': row, 'st': st}
text = R.render(rows_a, 'letsencrypt')
check(text == R.render(rows_b, 'letsencrypt') and text == R.render(dict(rows_a), 'letsencrypt'), 'rendering is deterministic (order-independent)')
check(text.startswith('# managed by the Homelab Agents platform — do not edit by hand; changes are overwritten'), 'header comment')
cfg = W.parse_yaml(text)
mws = cfg['http']['middlewares']
check(set(cfg['http']['routers']) == {'hla-shop', 'hla-st', 'hla-lan'} and all(k.startswith('hla-') for k in mws), 'hla- prefix on every name')
check(mws['hla-st-lan']['ipAllowList']['sourceRange'] == ['192.168.0.0/16', '10.0.0.0/8'] and 'ipStrategy' not in mws['hla-st-lan']['ipAllowList'],
      'lan_only: exactly 192.168.0.0/16 and 10.0.0.0/8, default ipStrategy')
check(not any(R.FORBIDDEN_RANGE.search(l) for l in text.split('\n') if not l.lstrip().startswith('#')) and '172.16.0.0/12' in text,
      'no 172.16.0.0/12 outside the comment that explains why')
check(cfg['http']['routers']['hla-st']['middlewares'] == ['hla-st-lan', 'hla-st-auth'] and
      mws['hla-st-auth']['basicAuth']['users'] == ['hla:' + st['basic_hash']], 'basic auth: user and apr1 hash, LAN check first')
check(PASSWORD not in text, 'the password never reaches the file')
check(cfg['http']['routers']['hla-lan']['entryPoints'] == ['websecure', 'websecure-alt'] and cfg['http']['routers']['hla-shop']['tls'] == {'certResolver': 'letsencrypt'},
      'entry points and the cert resolver')
check(all(re.match(r'^\s*("[^"]*"|[A-Za-z]+):( "|$| true$)|^\s*- ("|url: ")|^\s*#|^http: \{\}$|^$', l) for l in text.split('\n')),
      'every value is quoted (only fixed booleans are not)')
check(R.check_rendered(text, rows_a, 'letsencrypt'), 'the rendered file passes the local check')
tampered = text.replace('"10.0.0.0/8"', '"172.16.0.0/12"')
check(refused(lambda: R.check_rendered(tampered, rows_a, 'letsencrypt'), 'render_failed'), 'a 172.16/12 allow-list fails the local check')
dupe = text.replace('  services:\n', '  services:\n    "hla-shop":\n      loadBalancer: {}\n', 1)
check(R._duplicate_keys(dupe) and refused(lambda: R.check_rendered(dupe, rows_a, 'letsencrypt'), 'render_failed'), 'duplicate keys fail the local check')
check(R.render({}, 'le').endswith('http: {}\n') and W.parse_yaml(R.render({}, 'le')) == {'http': {}}, 'no routes: a valid empty file')
dis = dict(row, status='disabled')
check('hla-shop' not in R.render({'shop': dis}, 'le'), 'disabled routes are not rendered')
try:
    R.render({'x': dict(st, basic_hash=None)}, 'le'); check(False, 'basic auth without hash refused')
except R.RouteError:
    check(True, 'basic auth without a hash is never rendered')
try:
    os.environ['TRAEFIK_ROUTE_BACKUP_DIR'] = DYN + '/backups'; R.backup_dir(); check(False, 'backup dir in dynamic refused')
except R.RouteError as exc:
    check('outside' in exc.detail, 'a backup folder inside the dynamic folder is refused (Traefik would load it)')
os.environ['TRAEFIK_ROUTE_BACKUP_DIR'] = BAK

# ── conflicts ────────────────────────────────────────────────────────────────────────────────────────────────────
state = R.traefik_state(fake_execute)
check(state['ok'] and len(state['routers']) == 5 and R.resolver_in_use(state) == ('letsencrypt', 'routers'), 'API state; the most used cert resolver')
os.environ['TRAEFIK_CERT_RESOLVER'] = 'le-dns'
check(R.resolver_in_use(state) == ('le-dns', 'setting'), 'TRAEFIK_CERT_RESOLVER wins')
os.environ.pop('TRAEFIK_CERT_RESOLVER')
c = R.conflicts(build('vp', host='nw2-staging.example.com'), {}, state)
check(c and 'nw2-staging' in c[0] and 'hand-written' in c[0], 'a host served by a hand-written router is a conflict')
c = R.conflicts(build('ag', host='agents.example.com'), {}, state)
check(c and 'agents (docker)' in c[0], 'a host served by a docker-label router is a conflict')
HAND_ROUTERS.append({'name': 'hla-clash@file', 'provider': 'file', 'rule': 'Host(`clash.example.net`)', 'service': 'x', 'status': 'enabled'})
state2 = R.traefik_state(fake_execute)
c = R.conflicts(build('clash', host='other.example.com'), {}, state2)
check(c and 'router named hla-clash already exists' in c[0], 'a name clash with a hand-written router is a conflict (names are global)')
HAND_ROUTERS.pop()
check(R.conflicts(build('x2', host='shop.example.com'), {'shop': row}, state) and not R.conflicts(row, {'shop': row}, state),
      'two managed routes on one host conflict; a route does not conflict with itself')
others = R.other_routers(state, set())
check([o['name'] for o in others] == ['agents', 'paperclip', 'nw2-staging', 'shop-old'] or {o['name'] for o in others} == {'agents', 'paperclip', 'nw2-staging', 'shop-old'},
      'other routers listed (internal ones left out)')
check('HANDHASH' not in json.dumps(others) and '10.66' not in json.dumps(others), 'other routers: no middleware values')

# ── DNS ──────────────────────────────────────────────────────────────────────────────────────────────────────────
d = R.dns_check('shop-staging.example.com', DOMAINS)
check(d['status'] == 'ok', 'DNS: points at the public IP')
d = R.dns_check('new.example.com', DOMAINS)
check(d['status'] == 'missing' and d['record'].startswith('new A 203.0.113.10') and 'certificate' in d['message'], 'DNS: missing, with the record to add')
d = R.dns_check('away.example.com', DOMAINS)
check(d['status'] == 'elsewhere' and '203.0.113.9' in d['message'] and 'away A 203.0.113.10' in d['message'], 'DNS: points elsewhere')
d = R.dns_check('lan.example.com', DOMAINS, 'lan_only')
check(d['status'] == 'lan' and '192.168.30.103' in d['message'], 'DNS: the LAN override to Traefik is accepted')
d = R.dns_check('v6.example.com', DOMAINS)
check(d['status'] == 'elsewhere' and 'AAAA' in d['message'], 'DNS: an AAAA record elsewhere is reported')
d = R.dns_check('slow.example.com', DOMAINS)
check(d['status'] == 'error' and 'timed out' in d['message'], 'DNS: a slow lookup times out')
os.environ['PUBLIC_IPS'] = '203.0.113.9, 203.0.113.10'
R._dns_cache.clear()
check(R.dns_check('away.example.com', DOMAINS)['status'] == 'ok', 'PUBLIC_IPS sets the expected addresses')
os.environ.pop('PUBLIC_IPS'); R._dns_cache.clear()
check(R.dns_check('example.com', DOMAINS)['record'].startswith('@ A '), 'DNS: the apex record is @')

# ── write + verify + rollback ────────────────────────────────────────────────────────────────────────────────────
AUD = []
def audit(action, detail, ok):
    AUD.append((action, detail, ok))
db = db_open()
R.set_domains(db, ['example.com', 'EXAMPLE.net', 'example.com'])
check(R.domains(db) == ['example.com', 'example.net'], 'domains are stored lower-case and once')
check(refused(lambda: R.set_domains(db, ['not a domain']), 'invalid_domain') and refused(lambda: R.set_domains(db, 'x'), 'invalid_domains'), 'bad domains refused')
before_file = Path(MANAGED).read_text()
out = R.save(db, fake_execute, 'shop-staging', {'host': 'shop-staging.example.com', 'environment': 'staging', 'backend_port': 8082}, 'owner', audit, create=True)
check(out['created'] and out['route']['protection'] == 'lan_only+basic_auth' and 'basic_hash' not in out['route'], 'staging route created (no hash in the reply)')
on_disk = Path(MANAGED).read_text()
check('hla-shop-staging' in on_disk and PASSWORD not in on_disk and LOADED['text'] == on_disk, 'the managed file holds the route; Traefik loaded it')
check(Path(BAK + '/hla-managed.yml.prev').read_text() == before_file and len(list(Path(BAK).glob('hla-managed.yml.2*'))) == 1,
      'the previous content is kept outside the dynamic folder (.prev and a timestamped copy)')
check(sorted(os.listdir(DYN)) == ['hla-managed.yml'], 'nothing else is created in the dynamic folder')
hashed = out['route'] and db.execute("SELECT basic_hash FROM managed_routes WHERE name='shop-staging'").fetchone()[0]
check(R.apr1(PASSWORD, hashed.split('$')[2]) == hashed and PW_CALLS[-1][0] == 'TRAEFIK_BASIC_SHOP_STAGING', 'the hash is apr1 of the Infisical password')
check(AUD[-1][0] == 'route_created' and 'shop-staging.example.com' in AUD[-1][1] and 'http://192.168.30.11:8082' in AUD[-1][1]
      and 'lan_only+basic_auth' in AUD[-1][1] and '{{secret:TRAEFIK_BASIC_SHOP_STAGING}}' in AUD[-1][1], 'route_created audited with host, backend, protection')
check(db.execute('SELECT count(*) FROM route_files').fetchone()[0] == 1, 'the written file is kept in the gateway database too')
check(refused(lambda: R.save(db, fake_execute, 'shop-staging', {'host': 'x.example.com', 'backend_port': 1}, 'o', audit, create=True), 'route_exists'),
      'create does not overwrite an existing route')

# edit protection
n_pw = len(PW_CALLS)
out = R.save(db, fake_execute, 'shop-staging', {'protection': 'basic_auth'}, 'owner', audit)
cfg = W.parse_yaml(Path(MANAGED).read_text())
check(not out['created'] and cfg['http']['routers']['hla-shop-staging']['middlewares'] == ['hla-shop-staging-auth']
      and 'hla-shop-staging-lan' not in cfg['http']['middlewares'] and len(PW_CALLS) == n_pw, 'edit protection: same hash kept, allow-list gone')
check(AUD[-1][0] == 'route_updated' and 'basic_auth' in AUD[-1][1], 'route_updated audited')

# host conflict refused before anything is written
n = len(CALLS)
check(refused(lambda: R.save(db, fake_execute, 'vp', {'host': 'nw2-staging.example.com', 'backend_port': 8090}, 'o', audit), 'route_conflict')
      and not any('HLA_PREV' in c[1] for c in CALLS[n:]), 'a conflicting host is refused before any write')

# Traefik rejects the router -> rollback
good = Path(MANAGED).read_text()
MODE['router_error'] = True
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'owner', audit, create=True); check(False, 'rollback on router error')
except R.RouteError as exc:
    check(exc.error == 'traefik_rejected' and 'does not exist' in exc.detail and 'restored' in exc.detail, 'router error: refused with Traefik\'s error')
MODE['router_error'] = False
check(Path(MANAGED).read_text() == good and 'shop' not in R.load_rows(db), 'router error: the previous file is back, the database unchanged')
check(AUD[-1][0] == 'route_rollback' and 'previous file restored' in AUD[-1][1] and AUD[-1][2] is False, 'the rollback is audited')

# Traefik never picks the file up -> rollback after the wait
MODE['stale'] = True
T0 = T[0]
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'owner', audit); check(False, 'rollback when stale')
except R.RouteError as exc:
    check(exc.error == 'traefik_rejected' and 'did not load' in exc.detail, 'not loaded within the wait: rolled back')
MODE['stale'] = False
LOADED['text'] = Path(MANAGED).read_text()
check(T[0] - T0 >= R.VERIFY_SECONDS and Path(MANAGED).read_text() == good, 'waited ~10 s, then restored')

# API unreachable after the write -> rollback
MODE['api_down_after_write'] = True
CALLS.clear()
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'owner', audit); check(False, 'rollback on API down')
except R.RouteError as exc:
    check(exc.error == 'traefik_rejected' and 'not reachable' in exc.detail, 'API unreachable after the write: rolled back')
MODE['api_down_after_write'] = False
check(Path(MANAGED).read_text() == good and 'shop' not in R.load_rows(db), 'API unreachable: previous file restored, database unchanged')

# another router breaks -> rollback
MODE['break_other'] = True
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'owner', audit); check(False, 'rollback on broken neighbour')
except R.RouteError as exc:
    check('paperclip' in exc.detail, 'a hand-written router that stops working rolls the change back')
MODE['break_other'] = False
LOADED['text'] = Path(MANAGED).read_text()

# API unreachable before -> refused, nothing written
MODE['api_down'] = True
n = len(CALLS)
check(refused(lambda: R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'o', audit), 'traefik_api_unreachable')
      and not any('HLA_PREV' in c[1] for c in CALLS[n:]), 'API unreachable before: refused, nothing written')
MODE['api_down'] = False

# a missing managed file or backup folder: clear message, nothing changed
os.rename(MANAGED, MANAGED + '.off')
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'o', audit); check(False, 'missing file')
except R.RouteError as exc:
    check(exc.error == 'write_failed' and 'create it once as root' in exc.detail, 'missing managed file: says how to create it')
os.rename(MANAGED + '.off', MANAGED)
os.chmod(BAK, 0o500)
try:
    if os.access(BAK, os.W_OK):
        check(True, 'backup folder not writable (skipped: running as root)')
    else:
        R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'o', audit); check(False, 'ro backup')
except R.RouteError as exc:
    check('backup folder' in exc.detail and Path(MANAGED).read_text() == good, 'backup folder not writable: refused, file unchanged')
os.chmod(BAK, 0o700)

# the write fails after the old content was read: it is put back
MODE['half_write'] = True
try:
    R.save(db, fake_execute, 'shop', {'host': 'shop.example.com', 'backend_port': 8081}, 'o', audit); check(False, 'half write')
except R.RouteError as exc:
    check(exc.error == 'write_failed' and 'does not hold' in exc.detail, 'a failed write after reading the old file: refused with the reason')
MODE['half_write'] = False
check(Path(MANAGED).read_text() == good and AUD[-1][0] == 'route_rollback' and 'previous file restored' in AUD[-1][1] and 'shop' not in R.load_rows(db),
      'a failed write is rolled back to the previous file (audited)')

# a damaged upload is refused by the script itself
script = R.write_script('0' * 64, 'write')
res = subprocess.run(['bash', '-c', script], input=b'content', capture_output=True)
check(b'arrived damaged' in res.stdout and Path(MANAGED).read_text() == good and not list(Path(BAK).glob('.incoming*')), 'sha256 mismatch: not written, temp removed')
check(refused(lambda: R.write_script('x', 'write'), 'bad_write') and refused(lambda: R.write_script('0' * 64, 'rm'), 'bad_write'), 'write script takes only a sha and a fixed mode')
check("'" + MANAGED + "'" in script and len(script) < 2048, 'paths are quoted; the command fits the gateway limit')

# delete
out = R.delete(db, fake_execute, 'shop-staging', 'owner', audit)
check(out['deleted'] and 'hla-shop-staging' not in Path(MANAGED).read_text() and AUD[-1][0] == 'route_deleted', 'delete: removed and audited')
check(refused(lambda: R.delete(db, fake_execute, 'nope', 'o', audit), 'unknown_route'), 'delete unknown: 404')
check(len(list(Path(BAK).glob('hla-managed.yml.2*'))) >= 1 and all(PASSWORD not in a[1] for a in AUD), 'backups kept; no password in audit details')

# ── agent requests (unit) ────────────────────────────────────────────────────────────────────────────────────────
n = len(CALLS)
req = R.file_request(db, {'host': 'blog-staging.example.com', 'backend_port': 8085, 'environment': 'staging', 'reason': 'blog preview'}, 'agent-1')
check(req['status'] == 'pending' and req['protection'] == 'lan_only+basic_auth' and len(CALLS) == n, 'a request is stored pending; Traefik untouched')
again = R.file_request(db, {'host': 'blog-staging.example.com', 'backend_port': 8086, 'reason': 'new port'}, 'agent-1')
check(again['id'] == req['id'] and again['updated'], 'a second request for the same host updates the pending one')
check(refused(lambda: R.file_request(db, {'host': 'blog.example.org', 'backend_port': 1, 'reason': 'x'}, 'a'), 'unknown_domain')
      and refused(lambda: R.file_request(db, {'host': 'b.example.com', 'backend_port': 1}, 'a'), 'reason_required'), 'requests are validated')

# ── endpoints ────────────────────────────────────────────────────────────────────────────────────────────────────
import gateway_api.server as srv  # noqa: E402
srv.execute = fake_execute
httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT = '11111111-1111-4111-8111-111111111111'
TASK = '33333333-3333-4333-8333-333333333333'
def call(method, path, body=None, token='a' * 40, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)
def audits():
    with sqlite3.connect(os.environ['GATEWAY_DB']) as d: return d.execute('SELECT action,actor,agent_id,detail,task_id,server FROM audit ORDER BY id').fetchall()
R.reset()
check(call('GET', '/v1/routes', token='t' * 40)[0] == 401 and call('PUT', '/v1/routes/x', {'host': 'x'}, token='t' * 40)[0] == 401
      and call('DELETE', '/v1/routes/x', token='t' * 40)[0] == 401 and call('POST', '/v1/routes/check-dns', {'host': 'x'}, token='t' * 40)[0] == 401
      and call('PUT', '/v1/routes/domains', {'domains': []}, token='t' * 40)[0] == 401, 'route admin endpoints need the admin token')
code, body = call('PUT', '/v1/routes/domains', {'domains': ['example.com', 'example.net', 'northwind.example'], 'actor': 'owner'})
check(code == 200 and body['domains'] == ['example.com', 'example.net', 'northwind.example'] and audits()[-1][0] == 'route_domains_saved', 'domains saved (audited)')
n = len(CALLS)
code, body = call('GET', '/v1/routes/domains')
check(code == 200 and body['domains'] == ['example.com', 'example.net', 'northwind.example'] and len(CALLS) == n
      and call('GET', '/v1/routes/domains', token='t' * 40)[0] == 401, 'GET /v1/routes/domains: cheap (no SSH), admin token')
code, body = call('PUT', '/v1/routes/shop', {'host': 'shop-staging.example.com', 'environment': 'staging', 'backend_port': 8082, 'create': True, 'actor': 'owner'})
check(code == 200 and body['route']['host'] == 'shop-staging.example.com' and body['route']['basic_secret'] == 'TRAEFIK_BASIC_SHOP', 'PUT creates a route')
row = audits()[-1]
check(row[0] == 'route_created' and row[1] == 'owner' and row[5] == 'routes' and 'lan_only+basic_auth' in row[3], 'audited as route_created with the actor')
code, body = call('GET', '/v1/routes')
r0 = body['routes'][0]
check(code == 200 and r0['traefik']['status'] == 'enabled' and r0['dns']['status'] == 'ok' and r0['domain'] == 'example.com' and r0['subdomain'] == 'shop-staging',
      'GET lists the route with Traefik state, DNS and domain split')
check(body['cert_resolver'] == 'letsencrypt' and {o['name'] for o in body['other_routers']} >= {'nw2-staging', 'paperclip'}
      and body['managed_file'] == MANAGED and body['backup_dir'] == BAK, 'GET: cert resolver, read-only other routers, file locations')
check(not any(s in json.dumps(body) for s in SECRETS) and '$apr1$' not in json.dumps(body), 'GET never shows hashes, passwords or middleware values')
code, body = call('PUT', '/v1/routes/vp', {'host': 'nw2-staging.example.com', 'backend_port': 8090, 'actor': 'owner'})
check(code == 409 and body['error'] == 'route_conflict' and 'nw2-staging' in body['detail'], 'PUT: conflict with a hand-written router -> 409')
code, body = call('PUT', '/v1/routes/bad', {'host': 'bad.example.com', 'backend': 'http://10.0.0.5:80', 'actor': 'owner'})
check(code == 400 and body['error'] == 'backend_not_allowed', 'PUT: backend off the website VM -> 400')
code, body = call('POST', '/v1/routes/check-dns', {'host': 'new.example.com'})
check(code == 200 and body['status'] == 'missing' and body['record'].startswith('new A 203.0.113.10'), 'check-dns endpoint')
# agent requests
code, body = call('POST', '/v1/routes/request', {'agent_id': AGENT, 'host': 'api-staging.example.com', 'backend_port': 8087, 'reason': 'x'})
check(code == 401, 'agent requests use the tool token (admin token refused)')
code, body = call('POST', '/v1/routes/request', {'agent_id': AGENT, 'host': 'api-staging.example.com', 'backend_port': 8087, 'reason': 'x'},
                  token='t' * 40, headers={'X-HLA-Task': TASK})
check(code == 403 and body['error'] == 'policy_denied' and audits()[-1][0] == 'agent_route_denied' and audits()[-1][2] == AGENT, 'agent without level 4 on traefik: refused and audited')
call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'name': 'Web', 'level': 4}}, 'servers': {'traefik': {'level': 3}}}, 'actor': 't'})
check(call('POST', '/v1/routes/request', {'agent_id': AGENT, 'host': 'api-staging.example.com', 'backend_port': 8087, 'reason': 'x'}, token='t' * 40)[0] == 403,
      'level 3 on traefik is not enough')
call('PUT', '/v1/policy', {'change': {'servers': {'traefik': {'level': 4}}}, 'actor': 't'})
file_before = Path(MANAGED).read_text()
n = len(CALLS)
code, body = call('POST', '/v1/routes/request', {'agent_id': AGENT, 'host': 'api-staging.example.com', 'backend_port': 8087, 'environment': 'staging',
                                                 'reason': 'the API container runs on 8087'}, token='t' * 40, headers={'X-HLA-Task': TASK})
check(code == 200 and body['status'] == 'pending' and 'owner' in body['message'], 'level 4: the request is stored pending')
check(len(CALLS) == n and Path(MANAGED).read_text() == file_before, 'an agent request never touches Traefik')
row = audits()[-1]
check(row[0] == 'agent_route_requested' and row[2] == AGENT and row[4] == TASK, 'requests are audited with the agent and task')
rid = body['id']
code, body = call('GET', '/v1/routes/requests?status=pending')
check(code == 200 and [r['id'] for r in body['requests']] == [rid, req['id']] and body['requests'][0]['task_id'] == TASK
      and body['requests'][0]['suggested_name'] == 'api-staging-example-com', 'pending requests listed for the dashboard')
check(call('GET', '/v1/routes/requests', token='t' * 40)[0] == 401, 'the request list needs the admin token')
check(call('POST', f'/v1/routes/requests/{rid}/approve', {'actor': 'x'}, token='t' * 40)[0] == 401, 'approving needs the admin token')
code, body = call('POST', f'/v1/routes/requests/{rid}/approve', {'actor': 'owner', 'protection': 'lan_only'})
check(code == 200 and body['route']['host'] == 'api-staging.example.com' and body['route']['protection'] == 'lan_only'
      and 'hla-api-staging-example-com' in Path(MANAGED).read_text(), 'approve creates the route (with the owner\'s protection)')
check(any(a[0] == 'route_created' and 'request #' in a[3] and a[1] == 'owner' for a in audits()[-2:]), 'approval audited with the request number')
code, body = call('POST', f'/v1/routes/requests/{rid}/approve', {'actor': 'owner'})
check(code == 409 and body['error'] == 'request_decided', 'a decided request cannot be approved again')
code, body = call('POST', f'/v1/routes/requests/{req["id"]}/reject', {'actor': 'owner', 'note': 'not now'})
check(code == 200 and body['status'] == 'rejected' and audits()[-1][0] == 'route_request_rejected', 'reject (audited)')
code, body = call('GET', '/v1/routes/requests?status=pending')
check(body['requests'] == [], 'nothing pending after the decisions')
code, body = call('DELETE', '/v1/routes/shop', headers={'X-HLA-Actor': 'owner'})
check(code == 200 and body['deleted'] and audits()[-1][0] == 'route_deleted' and audits()[-1][1] == 'owner', 'DELETE removes the route (audited)')
check(call('DELETE', '/v1/routes/shop')[0] == 404, 'DELETE of an unknown route: 404')
MODE['ssh_down'] = True
R.reset()
code, body = call('GET', '/v1/routes')
check(code == 200 and body['traefik_ok'] is False and 'could not reach' in body['traefik_error'], 'GET with the traefik host down still answers, with the reason')
MODE['ssh_down'] = False
all_audit = json.dumps(audits())
check(not any(s in all_audit for s in SECRETS) and '$apr1$' not in all_audit, 'no password, hash or middleware value in the audit log')
httpd.shutdown()

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
