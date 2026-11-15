"""Website inventory (gateway/websites.py): containers on public-website, Traefik routes (API, then config files),
matching, environments, HTTP checks and status, caching and the /v1/websites endpoint. SSH is replaced by canned
output; the three remote scripts also run for real in bash against stand-in docker/curl/openssl commands.

python3 tests/test_websites.py   (needs paramiko importable; nothing is executed remotely)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({'GATEWAY_DB': TMP + '/gw.sqlite3', 'TOOL_GATEWAY_TOKEN': 't' * 40, 'POLICY_ADMIN_TOKEN': 'a' * 40})
os.environ.pop('TRAEFIK_API_URL', None); os.environ.pop('TRAEFIK_DYNAMIC_DIR', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import websites as w  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

SECRETS = ('SECRETHASH', 'apr1', 'hunter2', 'TOPSECRETDB', '10.66.0.0')  # must never appear in any output

# ── canned remote output ─────────────────────────────────────────────────────────────────────────────────────────
def ps(name, image, state, status, labels):
    return 'P ' + json.dumps({'Names': name, 'Image': image, 'State': state, 'Status': status, 'Labels': ','.join(f'{k}={v}' for k, v in labels.items()),
                              'Command': '"docker-entrypoint.sh"', 'Ports': ''})
def insp(name, image, state, labels, bindings, health=None, exit_code=0, started='2026-11-02T08:00:00.123Z'):
    live = {k: [dict(b, HostIp=b.get('HostIp') or '0.0.0.0')] for k, v in bindings.items() for b in v} if state == 'running' else {}
    return 'I ' + json.dumps({'name': '/' + name, 'image': image, 'state': state, 'health': health, 'started': started,
                              'finished': '0001-01-01T00:00:00Z', 'exit': exit_code, 'restarts': 0, 'ports': live, 'bindings': bindings, 'labels': labels})
def compose(project, service, extra=None):
    return {'com.docker.compose.project': project, 'com.docker.compose.service': service,
            'com.docker.compose.project.working_dir': '/opt/' + project, 'com.docker.compose.project.config_files': '/opt/' + project + '/compose.yaml',
            'org.opencontainers.image.source': 'https://github.com/x/y', 'DB_PASSWORD_HINT': 'TOPSECRETDB', **(extra or {})}
WEB = '192.168.30.11'
CONTAINERS = [
    ('production-web-1', 'ghcr.io/example/site:1.4.2', 'running', 'Up 7 days (healthy)', compose('production', 'web'), {'8080/tcp': [{'HostIp': WEB, 'HostPort': '8081'}]}, 'healthy', 0),
    ('production-db-1', 'postgres:17', 'running', 'Up 7 days', compose('production', 'db'), {}, None, 0),
    ('production-migrate-1', 'ghcr.io/example/site:1.4.2', 'exited', 'Exited (0) 7 days ago', compose('production', 'migrate'), {}, None, 0),
    ('northwind-staging-web-1', 'ghcr.io/nw/web:staging', 'running', 'Up 2 hours', compose('northwind-staging', 'web',
        {'traefik.http.middlewares.x.basicauth.users': 'admin:$apr1$SECRETHASH'}), {'8080/tcp': [{'HostIp': WEB, 'HostPort': '8082'}]}, None, 0),
    ('alpha-web-1', 'ghcr.io/example/site:alpha', 'running', 'Up 3 hours (unhealthy)', compose('alpha', 'web'), {'8080/tcp': [{'HostIp': '', 'HostPort': '8083'}]}, 'unhealthy', 0),
    ('shop-web-1', 'ghcr.io/example/shop:2.0', 'exited', 'Exited (137) 1 hour ago', compose('shop', 'web', {'hla.environment': 'production'}),
     {'80/tcp': [{'HostIp': WEB, 'HostPort': '8085'}]}, None, 137),
    ('uptime', 'louislam/uptime-kuma:1', 'running', 'Up 30 days', {}, {'3001/tcp': [{'HostIp': '', 'HostPort': '3001'}]}, None, 0),
    ('internal-only', 'redis:7', 'running', 'Up 30 days', {}, {'6379/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '6379'}]}, None, 0),
]
def containers_out():
    lines = [ps(n, i, s, st, l) for n, i, s, st, l, b, h, e in CONTAINERS] + [insp(n, i, s, l, b, h, e) for n, i, s, st, l, b, h, e in CONTAINERS]
    return '\n'.join(lines + ['HLA_DONE']) + '\n'

ROUTERS = [
    {'name': 'production@file', 'provider': 'file', 'rule': 'Host(`example.net`) || Host(`www.example.net`)', 'entryPoints': ['websecure'],
     'service': 'production', 'tls': {'certResolver': 'letsencrypt'}, 'status': 'enabled'},
    {'name': 'nw-staging@file', 'provider': 'file', 'rule': 'Host(`staging.northwind.example`)', 'entryPoints': ['websecure'],
     'middlewares': ['staging-auth', 'staging-ipallow@file'], 'service': 'nw-staging', 'tls': {'certResolver': 'letsencrypt'}},
    {'name': 'alpha@file', 'provider': 'file', 'rule': 'Host(`alpha.example.net`) && PathPrefix(`/app`)', 'entryPoints': ['websecure'],
     'service': 'alpha', 'tls': {}},
    {'name': 'shop@file', 'provider': 'file', 'rule': 'Host(`shop.example.net`)', 'entryPoints': ['websecure'], 'service': 'shop', 'tls': {}},
    {'name': 'ghost@file', 'provider': 'file', 'rule': 'Host(`old.example.net`)', 'entryPoints': ['websecure'], 'service': 'ghost', 'tls': {}},
    {'name': 'wild@file', 'provider': 'file', 'rule': 'HostRegexp(`{sub:[a-z]+}.example.net`)', 'entryPoints': ['websecure'], 'service': 'production'},
    {'name': 'evil@file', 'provider': 'file', 'rule': 'Host(`x.example.net;touch /tmp/pwned`)', 'entryPoints': ['websecure'], 'service': 'production'},
    {'name': 'agents@file', 'provider': 'file', 'rule': 'Host(`agents.example.com`)', 'entryPoints': ['websecure'], 'service': 'agents'},
    {'name': 'api@internal', 'provider': 'internal', 'rule': 'PathPrefix(`/api`)', 'entryPoints': ['traefik'], 'service': 'api@internal'},
]
SERVICES = [{'name': n + '@file', 'provider': 'file', 'loadBalancer': {'servers': [{'url': u}], 'passHostHeader': True}}
            for n, u in (('production', f'http://{WEB}:8081'), ('nw-staging', f'http://{WEB}:8082/'), ('alpha', 'http://public-website:8083'),
                         ('shop', f'http://{WEB}:8085'), ('ghost', f'http://{WEB}:8099'), ('agents', 'http://192.168.10.202:8088'))]
MIDDLEWARES = [{'name': 'staging-auth@file', 'provider': 'file', 'type': 'basicauth', 'basicAuth': {'users': ['admin:$apr1$SECRETHASH']}},
               {'name': 'staging-ipallow@file', 'provider': 'file', 'type': 'ipallowlist', 'ipAllowList': {'sourceRange': ['10.66.0.0/16']}}]
DYNAMIC = {
    'nw-staging.yml': """# staging for Northwind
http:
  routers:
    nw-staging:
      rule: "Host(`staging.northwind.example`)"
      entryPoints:
        - websecure
      middlewares:
        - staging-auth
        - staging-ipallow
      service: nw-staging
      tls:
        certResolver: letsencrypt
  middlewares:
    staging-auth:
      basicAuth:
        users:
          - "admin:$apr1$SECRETHASH"   # hunter2
    staging-ipallow:
      ipAllowList:
        sourceRange: ["10.66.0.0/16", '192.168.0.0/16']
  services:
    nw-staging:
      loadBalancer:
        servers:
        - url: http://192.168.30.11:8082
""",
    'production.yaml': """---
http:
  routers:
    production:
      rule: Host(`example.net`) || Host(`www.example.net`)
      entryPoints: [websecure]
      service: production
      tls: {certResolver: letsencrypt}
  services:
    production:
      loadBalancer: {servers: [{url: "http://192.168.30.11:8081"}]}
""",
    'alpha.toml': """[http.routers.alpha]
rule = "Host(`alpha.example.net`)"
entryPoints = ["websecure"]
service = "alpha"
[http.routers.alpha.tls]
[[http.services.alpha.loadBalancer.servers]]
url = "http://192.168.30.11:8083"
""",
    'broken.yml': "http:\n\trouters: {}\n",
    'odd.yml': "http:\n  routers:\n    plain:\n      rule: 'Host(`plain.example.net`)'\n      entryPoints:\n      - web\n      service: plain\n"
               "      note: |\n        a block scalar # with a hash\n        more\n  services:\n    plain:\n      loadBalancer:\n        servers:\n"
               "          -\n            url: http://192.168.30.11:3001\n",
}
CHECKS = {('https', 'example.net'): '200 0.214 0', ('https', 'www.example.net'): '301 0.120 0', ('https', 'staging.northwind.example'): '401 0.090 0',
          ('https', 'alpha.example.net'): '200 4.200 0', ('https', 'shop.example.net'): '502 0.050 0', ('https', 'old.example.net'): '000 8.001 0',
          ('http', 'plain.example.net'): '200 0.1 0'}
CERT = {'www.example.net': 'Nov 20 10:00:00 2026 GMT'}

MODE = {'api': True, 'docker': True, 'files': dict(DYNAMIC), 'fail_ssh': set(), 'checks_ok': True}
CALLS = []
def fake_execute(server, command, timeout=30, max_output=12000, stdin=None):
    CALLS.append((server, command, stdin, timeout))
    check_len = len(command) <= 2048
    if not check_len: raise ValueError('Invalid command')
    if server in MODE['fail_ssh']: raise RuntimeError('SSH host fingerprint mismatch')
    out = {'exit_code': 0, 'timed_out': False, 'truncated': False, 'stderr': ''}
    if server == 'public-website':
        if not MODE['docker']:
            return {**out, 'stdout': 'HLA_ERR permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock\n'}
        return {**out, 'stdout': containers_out()}
    b = command.split('B=', 1)[1].split(';', 1)[0].split('\n', 1)[0].strip()
    if 'api/http/routers' in command:
        if MODE['api']:
            body = '\n'.join(f'{b} API {k} 0\n{json.dumps(v)}\n{b} END' for k, v in (('routers', ROUTERS), ('services', SERVICES), ('middlewares', MIDDLEWARES)))
        else:
            body = f'{b} API routers 7\ncurl: (7) Failed to connect to 127.0.0.1 port 8080 after 0 ms: Couldn\'t connect to server\n{b} END\n'
            if MODE['files'] is None: body += f'{b} NODIR\n'
            else: body += ''.join(f'{b} FILE /opt/traefik/dynamic/{n}\n{t}\n{b} END\n' for n, t in sorted(MODE['files'].items()))
        return {**out, 'stdout': body + f'\n{b} DONE\n'}
    if 'ssl_verify_result' in command:
        if not MODE['checks_ok']: return {**out, 'exit_code': None, 'timed_out': True, 'stdout': ''}
        lines = []
        for n, line in enumerate(stdin.decode().splitlines(), 1):
            scheme, host, path = line.split()
            res = CHECKS.get((scheme, host), '000 0 0')
            end = CERT.get(host, 'Mar  1 00:00:00 2027 GMT') if scheme == 'https' else ''
            lines.append(f'{b} {n} {res} {end}'.rstrip())
        return {**out, 'stdout': '\n'.join(reversed(lines)) + f'\n{b} DONE\n'}  # parallel checks finish in any order
    raise AssertionError('unexpected command')

def run():
    CALLS.clear()
    return w.inventory(fake_execute, now=time.mktime((2026, 11, 9, 12, 0, 0, 0, 0, 0)))

# ── the whole inventory from the Traefik API ─────────────────────────────────────────────────────────────────────
data = run()
dump = json.dumps(data)
sites = {s['id']: s for s in data['sites']}
check(not any(x in dump for x in SECRETS), 'nothing secret leaves the gateway (basicAuth hashes, allow-list ranges, other labels)')
check(data['routes_source'] == 'api' and data['routes_error'] is None and data['containers_error'] is None, 'routes from the Traefik API')
check([c[0] for c in CALLS].count('public-website') == 1 and [c[0] for c in CALLS].count('traefik') == 2, 'one SSH call to public-website, two to traefik (routes, checks)')
check(all(len(cmd) <= 2048 for _, cmd, _, _ in CALLS) and all('Env' not in cmd for s, cmd, _, _ in CALLS if s == 'public-website'),
      'scripts fit the command limit; the container environment is never asked for')
check(set(sites) == {'production', 'northwind-staging', 'alpha', 'shop', 'uptime', 'internal-only', 'route:ghost'}, f'sites by compose project ({sorted(sites)})')
p = sites['production']
check([u['url'] for u in p['urls']][:2] == ['https://example.net/', 'https://www.example.net/'] and p['environment'] == 'production'
      and p['environment_source'] == 'guess', 'production: both host names, environment guessed')
check(p['status'] == 'degraded' and 'certificate expires in 10 days' in p['status_reason'], f"a certificate close to expiry degrades the site ({p['status_reason']})")
check({c['name'] for c in p['containers']} == {'production-web-1', 'production-db-1', 'production-migrate-1'} and
      'certificate' in p['status_reason'] and 'migrate' not in p['status_reason'], 'a finished one-shot container next to running ones is no problem')
check(p['urls'][0]['http_status'] == 200 and p['urls'][0]['seconds'] == 0.214 and p['urls'][0]['cert_days'] > 100 and p['urls'][1]['cert_days'] == 10,
      'HTTP code, response time and certificate days per URL')
pc = {c['name']: c for c in p['containers']}
check(p['lan_urls'] == ['http://192.168.30.11:8081'] and pc['production-db-1']['ports'] == []
      and '192.168.30.11:8081->8080/tcp' in pc['production-web-1']['ports'] and pc['production-web-1']['health'] == 'healthy'
      and pc['production-web-1']['started'].startswith('2026-11-02') and pc['production-web-1']['image'] == 'ghcr.io/example/site:1.4.2',
      'LAN URL, published ports, health, start time and image')
check(p['working_dir'] == '/opt/production' and 'labels' not in json.dumps(p['containers']), 'compose working dir kept; container labels are not passed on')
s = sites['northwind-staging']
check(s['environment'] == 'staging' and s['status'] == 'protected' and s['urls'][0]['protected'] and 'login required (HTTP 401)' in s['status_reason'],
      'staging behind basicAuth: 401 counts as up (protected)')
check(s['routes'][0]['middlewares'] == [{'name': 'staging-auth', 'type': 'basicauth'}, {'name': 'staging-ipallow', 'type': 'ipallowlist'}]
      and s['routes'][0]['cert_resolver'] == 'letsencrypt', 'middlewares by name and type only')
a = sites['alpha']
check(a['environment'] == 'test' and a['status'] == 'degraded' and 'unhealthy' in a['status_reason'] and 'slow (4.2 s)' in a['status_reason']
      and a['urls'][0]['url'] == 'https://alpha.example.net/app', 'alpha: test, unhealthy and slow (path prefix kept, matched via the host name public-website)')
sh = sites['shop']
check(sh['environment'] == 'production' and sh['environment_source'] == 'label' and sh['status'] == 'down' and 'HTTP 502' in sh['status_reason']
      and 'exited (code 137)' in sh['status_reason'], 'shop: label sets production; stopped container and 502 make it down (stopped containers keep their port bindings)')
g = sites['route:ghost']
check(g['unmatched'] and g['status'] == 'down' and 'no answer' in g['status_reason'] and 'no container' in g['status_reason'] and not g['containers'],
      'a route to public-website without a container is listed (and is down here)')
u = sites['uptime']
check(not u['public'] and u['status'] == 'up' and u['lan_urls'] == ['http://192.168.30.11:3001'] and 'LAN only' in u['status_reason'],
      'a container without a route: no public URL, LAN URL shown')
check(sites['internal-only']['lan_urls'] == [], 'ports bound to 127.0.0.1 are not LAN URLs')
check(data['other_routes'] == 1, 'routes to other machines are counted, not listed (internal Traefik routers skipped)')
plan = [line.split()[1] for line in CALLS[-1][2].decode().splitlines()]
check('x.example.net;touch' not in ' '.join(plan) and not any(';' in h for h in plan) and len(plan) == len(set(plan)) and 'agents.example.com' not in plan,
      f'only valid host names of this VM are checked, once each ({plan})')
wild = [x for x in p['urls'] if x['router'] == 'wild']
check(wild and wild[0]['url'] is None and 'HostRegexp' in wild[0]['reason'], 'HostRegexp routes are shown but not checked')
check([s['environment'] for s in data['sites']] == sorted([s['environment'] for s in data['sites']], key={'production': 0, 'staging': 1, 'test': 2}.get),
      'sites sorted production, staging, test')

# ── fallback: the dynamic config files ───────────────────────────────────────────────────────────────────────────
MODE['api'] = False
data = run(); dump = json.dumps(data); sites = {s['id']: s for s in data['sites']}
check(data['routes_source'] == 'files' and 'Traefik API not reachable' in data['routes_error'] and '/opt/traefik/dynamic' in data['routes_error'],
      f"API down: routes read from the files, and it says so ({data['routes_error']})")
check(not any(x in dump for x in SECRETS) and 'block scalar' not in dump, 'file contents never leave the gateway (hashes, ranges, comments)')
check(sites['northwind-staging']['status'] == 'protected' and sites['northwind-staging']['routes'][0]['middlewares'][0] == {'name': 'staging-auth', 'type': 'basicauth'},
      'YAML: staging route with its middlewares')
check([u['url'] for u in sites['production']['urls']] == ['https://example.net/', 'https://www.example.net/'], 'YAML flow style ([..], {..}) and document markers')
check(sites['alpha']['urls'][0]['url'] == 'https://alpha.example.net/' and sites['alpha']['routes'][0]['tls'], 'TOML files (empty tls table = TLS)')
check(sites['uptime']['urls'][0]['url'] == 'http://plain.example.net/' and sites['uptime']['status'] == 'up', 'plain-http route on the web entry point, "-" list items on their own line')
check(data['routes_file_errors'] == [{'file': '/opt/traefik/dynamic/broken.yml', 'error': 'tab indentation on line 2'}] and
      len(data['routes_files']) == 4, 'a broken file is reported by name and line, the others still count')
MODE['files'] = None
data = run()
check(data['routes_source'] == 'none' and 'does not exist or is not readable' in data['routes_error'] and all(not s['urls'] for s in data['sites']),
      'no API and no folder: no routes, a clear reason, containers still listed')
MODE['files'] = {}
check('no readable .yml/.toml files' in run()['routes_error'], 'an empty folder is reported')
MODE.update(api=True, files=dict(DYNAMIC))

# ── no docker access, SSH failures, failed checks ────────────────────────────────────────────────────────────────
MODE['docker'] = False
data = run(); sites = {s['id']: s for s in data['sites']}
check('permission denied' in data['containers_error'] and 'public-website' in data['containers_error'], 'no docker access: containers_error says why')
check(all(s['unmatched'] for s in data['sites']) and 'route:production' in sites and sites['route:production']['status'] == 'degraded',
      'without containers every route to the VM is still shown, as a route without a known container')
MODE['docker'] = True
MODE['fail_ssh'] = {'traefik'}
data = run()
check(data['routes_source'] == 'none' and 'could not reach traefik: RuntimeError (SSH host fingerprint mismatch)' in data['routes_error']
      and len(data['sites']) == 6 and data['checks_error'] is None, 'traefik unreachable: containers only, with the reason')
MODE['fail_ssh'] = set(); MODE['checks_ok'] = False
data = run(); sites = {s['id']: s for s in data['sites']}
check(data['checks_error'] == 'HTTP checks did not finish in time' and sites['northwind-staging']['status'] == 'up'
      and 'did not finish' in sites['northwind-staging']['status_reason'] and sites['northwind-staging']['urls'][0]['http_status'] is None,
      'checks timed out: container state decides, URLs say why they were not checked')
MODE['checks_ok'] = True

# ── classification and helpers ───────────────────────────────────────────────────────────────────────────────────
us = lambda code, sec=0.2, days=90, verify=0, prot=False, tls=True: w.url_status({'http_status': code, 'seconds': sec, 'verify': verify, 'cert_days': days}, prot, tls)
check(us(200)[0] == 'up' and us(302)[0] == 'up' and us(401, prot=True)[0] == 'protected' and us(403, prot=True)[0] == 'protected'
      and us(401)[0] == 'degraded' and us(404)[0] == 'degraded' and us(503)[0] == 'down' and us(0)[0] == 'down', 'HTTP codes classified')
check(us(200, sec=3.5)[0] == 'degraded' and us(200, days=13)[0] == 'degraded' and us(200, days=14)[0] == 'up' and us(200, days=-1) == ('down', 'certificate expired')
      and us(200, verify=19)[0] == 'degraded' and us(200, days=3, tls=False)[0] == 'up' and us(401, sec=5, prot=True)[0] == 'degraded',
      'slow, expiring, expired and untrusted certificates')
check(w.url_status(None, False, True)[0] == 'unknown', 'not checked = unknown')
cs = lambda state, health=None, code=0, others=False: w.container_status({'name': 'c', 'state': state, 'health': health, 'exit_code': code}, others)[0]
check(cs('running') == 'up' and cs('running', 'healthy') == 'up' and cs('running', 'unhealthy') == 'degraded' and cs('restarting') == 'down'
      and cs('exited', code=1) == 'down' and cs('exited', code=1, others=True) == 'degraded' and cs('exited', code=0, others=True) == 'up', 'container states classified')
check(w.guess_environment('northwind-staging') == 'staging' and w.guess_environment('alpha') == 'test' and w.guess_environment('site', 'beta.example.net') == 'test'
      and w.guess_environment('shop-dev') == 'test' and w.guess_environment('production', 'developer.example.be') == 'production'
      and w.guess_environment('site-test2') == 'test' and w.guess_environment('latest') == 'production', 'environment heuristics')
check(w.label_environment('Prod') == 'production' and w.label_environment('weird') is None, 'environment labels')
for host, ok in (('example.net', True), ('www.northwind.example', True), ('EXAMPLE.NET', False), ('a.be', True), ('localhost', False), ('x.be;rm -rf /', False),
                 ('-bad.be', False), ('x..be', False), ('$(id).be', False), ("a'.be", False)):
    check(bool(w.HOSTNAME.match(host)) == ok, f'host name check: {host!r} {"accepted" if ok else "refused"}')
check(w.rule_hosts('Host(`A.Example.be`, `b.example.be`) && (PathPrefix(`/x`) || Path(`/y`))')[:2] == (['a.example.be', 'b.example.be'], ['/x', '/y']),
      'rule parser: several hosts, lowercased, path prefixes')
check(w.rule_hosts('HostRegexp(`^shop\\.example\\.net$`)')[0] == ['shop.example.net'] and w.rule_hosts(None) == ([], [], False), 'rule parser: literal HostRegexp, no rule')
os.environ['TRAEFIK_API_URL'] = "http://127.0.0.1:8080'; rm -rf /; '"
try: w.api_url(); check(False, 'a dangerous TRAEFIK_API_URL is refused')
except ValueError: check(True, 'a dangerous TRAEFIK_API_URL is refused')
data = run()
check(data['routes_source'] == 'none' and 'TRAEFIK_API_URL' in data['routes_error'] and not any('rm -rf' in c[1] for c in CALLS), 'and nothing runs on traefik with it')
os.environ['TRAEFIK_API_URL'] = 'http://traefik.lan:8081/'; os.environ['TRAEFIK_DYNAMIC_DIR'] = '/srv/traefik/conf.d'
script = w.routes_script('HLA_b')
check("A='http://traefik.lan:8081'" in script and "D='/srv/traefik/conf.d'" in script, 'TRAEFIK_API_URL and TRAEFIK_DYNAMIC_DIR are used, quoted')
os.environ['TRAEFIK_DYNAMIC_DIR'] = '/srv/../etc'
try: w.dynamic_dir(); check(False, 'a dynamic dir with .. is refused')
except ValueError: check(True, 'a dynamic dir with .. is refused')
os.environ.pop('TRAEFIK_API_URL'); os.environ.pop('TRAEFIK_DYNAMIC_DIR')

# The YAML subset: odd but valid input parses; garbage raises YamlError with a line number, never content.
odd = w.parse_yaml("a:\n- x\n- {k: 'v, w', n: [1, 2]}\n-\n  deep: true\nb: 'it''s'   # c\nc: \"q # not a comment\"\nd:\ne: ~\n")
check(odd == {'a': ['x', {'k': 'v, w', 'n': [1, 2]}, {'deep': True}], 'b': "it's", 'c': 'q # not a comment', 'd': None, 'e': None}, f'YAML subset ({odd})')
for bad in ('a: 1\n   b: 2\n', 'just text\n', 'a:\n\t- x\n'):
    try: w.parse_yaml(bad); check(False, 'bad YAML raises')
    except w.YamlError as exc: check('line' in str(exc) and 'text' not in str(exc), f'bad YAML raises with a line number ({exc})')
check(w.parse_yaml('') == {} and w.parse_yaml('# only a comment\n---\n') == {}, 'empty files are empty')
check(w.routes_from_config([{'http': 'nonsense'}, {'http': {'routers': {'r': 'x', 'q': {'rule': 5}}}}, {}]) [0]['hosts'] == [], 'nonsense configs do not crash')
check(w.routes_from_api('not a list', None, 5) == [] and w.routes_from_api([{'name': 3}, 'x', {'name': 'a@file'}], [], []) [0]['name'] == 'a',
      'nonsense API answers do not crash')
check(w.parse_containers('garbage\n') == ([], 'No answer from Docker on public-website (output cut off or the command failed)'), 'containers: no answer')
check(w.parse_containers('P {not json\nI {"name":"/a","state":"running","labels":null}\nHLA_DONE\n')[0][0]['name'] == 'a', 'containers: broken lines skipped')
# Docker-provider labels become routes of their own container.
lab = [{'name': 'blog-web-1', 'traefik_labels': {'traefik.http.routers.blog.rule': 'Host(`blog.example.net`)', 'traefik.http.routers.blog.entrypoints': 'websecure',
        'traefik.http.routers.blog.middlewares': 'auth@docker', 'traefik.http.routers.blog.tls.certresolver': 'le', 'traefik.http.middlewares.auth.basicauth': ''}}]
r = w.routes_from_labels(lab)
check(r and r[0]['hosts'] == ['blog.example.net'] and r[0]['container'] == 'blog-web-1' and r[0]['protected'] and r[0]['cert_resolver'] == 'le', 'docker labels: routes with middlewares')
lbl = w.parse_containers(insp('blog-web-1', 'ghost:5', 'running', {'traefik.http.routers.blog.rule': 'Host(`blog.example.net`)',
                                                                  'traefik.http.middlewares.auth.basicauth.users': 'u:$apr1$SECRETHASH'}, {}) + '\nHLA_DONE\n')[0][0]
check(lbl['traefik_labels'] == {'traefik.http.routers.blog.rule': 'Host(`blog.example.net`)', 'traefik.http.middlewares.auth.basicauth': ''},
      'docker labels: middleware values dropped, their kind kept')

# ── the scripts, for real, against stand-in commands ─────────────────────────────────────────────────────────────
bin_dir = Path(tempfile.mkdtemp()); log = bin_dir / 'log'
def stub(name, body):
    f = bin_dir / name; f.write_text('#!/bin/sh\n' + body); f.chmod(f.stat().st_mode | stat.S_IEXEC)
stub('sudo', 'exit 1\n')
stub('curl', f'echo "curl $*" >> {log}\ncase "$*" in *api/http*) echo "curl: (7) Failed to connect"; exit 7;; esac\necho "200 0.050 0"\n')
stub('openssl', 'case "$1" in x509) echo "notAfter=Jan 10 12:00:00 2030 GMT";; esac\n')
stub('docker', f'echo "docker $*" >> {log}\ncase "$1 $2" in\n "ps -q") exit 0;;\n "ps -a") '
     + "echo '" + json.dumps({'Names': 'site-web-1', 'Image': 'nginx', 'State': 'running', 'Status': 'Up 1 hour'}) + "';;\n"
     + ' "ps -aq") echo abc123;;\n inspect*) ' + "echo '" + insp('site-web-1', 'nginx', 'running', compose('site', 'web'), {'80/tcp': [{'HostIp': '', 'HostPort': '8090'}]})
     + "';;\nesac\n")
env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH']}
proc = subprocess.run(['bash', '-c', w.CONTAINERS_SCRIPT], capture_output=True, text=True, env=env, timeout=30)
got, err = w.parse_containers(proc.stdout)
check(err is None and got and got[0]['name'] == 'site-web-1' and got[0]['ports'][0]['host_port'] == 8090 and 'inspect --format' in log.read_text()
      and '.Config.Env' not in log.read_text(), 'containers script runs (docker ps + inspect, no environment)')
stub('docker', 'echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1\n')
proc = subprocess.run(['bash', '-c', w.CONTAINERS_SCRIPT], capture_output=True, text=True, env=env, timeout=30)
check(w.parse_containers(proc.stdout)[1].startswith('Docker could not be read on public-website: permission denied'), 'containers script: no docker access reported')
conf = Path(tempfile.mkdtemp()) / 'dyn dir'; conf.mkdir()
(conf / 'nw-staging.yml').write_text(DYNAMIC['nw-staging.yml']); (conf / 'notes.txt').write_text('ignored')
os.environ['TRAEFIK_DYNAMIC_DIR'] = str(conf)
proc = subprocess.run(['bash', '-c', w.routes_script('HLA_t')], capture_output=True, text=True, env=env, timeout=30)
info = w.read_routes(proc.stdout, 'HLA_t')
check(info['source'] == 'files' and [r['hosts'] for r in info['routes']] == [['staging.northwind.example']] and info['files'] == [str(conf / 'nw-staging.yml')]
      and 'Failed to connect' in info['error'], 'routes script: API refused, the folder is read (only .yml/.yaml/.toml)')
os.environ['TRAEFIK_DYNAMIC_DIR'] = str(conf) + '-missing'
proc = subprocess.run(['bash', '-c', w.routes_script('HLA_t')], capture_output=True, text=True, env=env, timeout=30)
check(w.read_routes(proc.stdout, 'HLA_t')['source'] == 'none' and 'NODIR' in proc.stdout, 'routes script: a missing folder is reported')
os.environ.pop('TRAEFIK_DYNAMIC_DIR')
marker = bin_dir / 'pwned'
log.write_text('')
lines = f'https example.net /\nhttps x.be;touch {marker} /\nhttps $(touch {marker}).be /\nftp a.be /\nhttp plain.example.net /a`id`\nhttps www.example.net /shop\n'
proc = subprocess.run(['bash', '-c', w.checks_script('HLA_c')], input=lines, capture_output=True, text=True, env=env, timeout=60)
res = w.read_checks(proc.stdout, 'HLA_c', {1, 2, 3, 4, 5, 6}, time.time())
check(not marker.exists() and set(res) == {1, 5, 6} and res[1]['http_status'] == 200 and res[1]['cert_days'] > 1000 and res[5]['cert_days'] is None,
      f'checks script: bad lines skipped, nothing injected, certificate read ({sorted(res)})')
called = log.read_text()
check('--resolve example.net:443:127.0.0.1 https://example.net/' in called and 'http://plain.example.net/' in called and 'https://www.example.net/shop' in called
      and 'plain.example.net/a' not in called, 'checks script: requests go through Traefik on 127.0.0.1 (odd paths fall back to /)')

# ── cache and rate limit ─────────────────────────────────────────────────────────────────────────────────────────
clock = [1000.0]
w.reset()
d1, ran1 = w.get(fake_execute, clock=lambda: clock[0])
clock[0] += 30
d2, ran2 = w.get(fake_execute, clock=lambda: clock[0])
check(ran1 and not ran2 and d2['cached'] and d2['age'] == 30, 'inventory cached for 60 s')
try: w.get(fake_execute, refresh=True, clock=lambda: clock[0] - 20); check(False, 'refresh rate limited')
except w.RateLimited as exc: check(exc.retry == 5, f'a refresh within 15 s is refused with retry_after ({exc.retry})')
d3, ran3 = w.get(fake_execute, refresh=True, clock=lambda: clock[0])
clock[0] += 61
d4, ran4 = w.get(fake_execute, clock=lambda: clock[0])
check(ran3 and not d3['cached'] and ran4, 'a refresh after 15 s runs; the cache expires after 60 s')

# ── the endpoint ─────────────────────────────────────────────────────────────────────────────────────────────────
import gateway_api.server as srv  # noqa: E402
srv.execute = fake_execute
httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{httpd.server_port}'
AGENT = '11111111-1111-4111-8111-111111111111'
def call(method, path, body=None, token='t' * 40, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)
def audits():
    with sqlite3.connect(os.environ['GATEWAY_DB']) as db: return db.execute('SELECT action,actor,agent_id,detail,task_id FROM audit ORDER BY id').fetchall()
w.reset()
check(call('GET', '/v1/websites', token='x' * 40)[0] == 401, 'unauthenticated: 401')
code, body = call('GET', '/v1/websites')
check(code == 200 and len(body['sites']) == 7 and body['routes_source'] == 'api' and not any(x in json.dumps(body) for x in SECRETS),
      'the platform reads the full inventory with the tool token')
check(audits()[-1][:2] == ('websites_refresh', 'dashboard') and '7 sites' in audits()[-1][3], 'each inventory run is audited (websites_refresh)')
code, body = call('GET', f'/v1/websites?agent_id={AGENT}', headers={'X-HLA-Task': '33333333-3333-4333-8333-333333333333'})
check(code == 403 and body['error'] == 'policy_denied' and audits()[-1][0] == 'agent_websites_denied', 'an agent without levels is refused (and audited)')
call('PUT', '/v1/policy', {'change': {'agents': {AGENT: {'name': 'Web', 'level': 1}}, 'servers': {'traefik': {'level': 0}}}, 'actor': 't'}, token='a' * 40)
check(call('GET', f'/v1/websites?agent_id={AGENT}')[0] == 403, 'level 1 on public-website alone is not enough')
call('PUT', '/v1/policy', {'change': {'servers': {'traefik': {'level': 1}}}, 'actor': 't'}, token='a' * 40)
code, body = call('GET', f'/v1/websites?agent_id={AGENT}&environment=staging', headers={'X-HLA-Task': '33333333-3333-4333-8333-333333333333'})
check(code == 200 and [s['name'] for s in body['sites']] == ['northwind-staging'] and 'routes' not in body['sites'][0] and 'containers_error' in body,
      'level 1 on both: a compact, filtered inventory for the agent')
row = audits()[-1]
check(row[0] == 'agent_websites_read' and row[2] == AGENT and row[4] == '33333333-3333-4333-8333-333333333333', 'agent reads are audited with the agent and task')
code, body = call('GET', f'/v1/websites?agent_id={AGENT}&search=uptime')
check(code == 200 and [s['name'] for s in body['sites']] == ['uptime'], 'search filter')
check(call('GET', '/v1/websites?refresh=1')[1].get('error') == 'refresh_needs_admin_token', 'refresh needs the admin token')
w._cache['at'] -= 100
n = len(CALLS)
code, body = call('GET', '/v1/websites?refresh=1', token='a' * 40)
check(code == 200 and not body['cached'] and len(CALLS) > n and audits()[-1][0] == 'websites_refresh' and audits()[-1][3].startswith('forced: '),
      'admin refresh runs the inventory again (audited as forced)')
code, body = call('GET', '/v1/websites?refresh=1', token='a' * 40)
check(code == 429 and body['error'] == 'refresh_rate_limited' and 1 <= body['retry_after'] <= 15, 'a second refresh within 15 s: 429 with retry_after')
code, body = call('GET', '/v1/websites', token='a' * 40)
check(code == 200 and body['cached'], 'a plain read after it comes from the cache')
httpd.shutdown()

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
