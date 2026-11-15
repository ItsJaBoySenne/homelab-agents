"""Inventory of the websites on the website VM, with their public routes and live status (HLA_WEBSITES_V1).

Three fixed, read-only scripts (no agent input in any command):
- public-website: every container (running and stopped) from `docker ps -a` and `docker inspect` with a template that
  prints names, image, state, health, start time, port bindings and labels. Never the environment.
- traefik: the routers, services and middlewares from the Traefik API (TRAEFIK_API_URL, default http://127.0.0.1:8080);
  only when that fails, the file provider's dynamic config files (TRAEFIK_DYNAMIC_DIR, default /opt/traefik/dynamic).
- traefik: one HTTP(S) request per public URL through Traefik itself (curl --resolve <host>:443:127.0.0.1) plus the
  certificate's expiry date. Host names come from the routes, are checked against a host name pattern here and travel
  on the command's stdin (one "scheme host path" line each), where the script checks them again.

The API answers and the config files can hold basicAuth hashes and other middleware settings: they are parsed here and
never leave the gateway. What is kept per route: name, rule, host names and path prefixes, entry points, TLS (and the
cert resolver's name), middleware names and types (never their values), the service name and its backend URLs. Labels
are filtered the same way. Stdlib only: a small YAML-subset parser for the dynamic files, tomllib for TOML ones.
"""
import calendar
import json
import os
import re
import secrets
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

from .core import SERVERS

NAME = 'websites'
WEBSITE_SERVER = 'public-website'
TRAEFIK_SERVER = 'traefik'
NEEDS = (WEBSITE_SERVER, TRAEFIK_SERVER)  # an agent reads the inventory with level >= 1 on both
CACHE_SECONDS = 60
REFRESH_SECONDS = 15
MAX_CHECKS = 50
SLOW_SECONDS = 3.0
CERT_WARN_DAYS = 14
ENVIRONMENTS = ('production', 'staging', 'test')
PROTECTING = {'basicauth', 'digestauth', 'forwardauth', 'ipallowlist', 'ipwhitelist'}
KEEP_LABELS = ('com.docker.compose.project', 'com.docker.compose.service', 'com.docker.compose.project.working_dir',
               'com.docker.compose.project.config_files', 'hla.environment', 'hla.url', 'hla.name')
HOSTNAME = re.compile(r'^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')
URL_PATH = re.compile(r'^/[A-Za-z0-9._~/-]{0,200}$')
_API_URL = re.compile(r'^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$')
_DIR = re.compile(r'^/(?:[A-Za-z0-9._ -]+/)*[A-Za-z0-9._ -]+/?$')
_BACKEND = re.compile(r'^(?:https?|h2c)://\[?([A-Za-z0-9.:-]+?)\]?(?::([0-9]{1,5}))?(?:/.*)?$')

# ── remote scripts ────────────────────────────────────────────────────────────────────────────────────────────────
_PRE = "S=; sudo -n true 2>/dev/null && S='sudo -n'\n"
# Never .format()ed: the Go templates stay literal. Labels are filtered in the gateway; .Config.Env is never asked for.
_INSPECT = ('I {"name":{{json .Name}},"image":{{json .Config.Image}},"state":{{json .State.Status}},'
            '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"started":{{json .State.StartedAt}},'
            '"finished":{{json .State.FinishedAt}},"exit":{{json .State.ExitCode}},"restarts":{{json .RestartCount}},'
            '"ports":{{json .NetworkSettings.Ports}},"bindings":{{json .HostConfig.PortBindings}},"labels":{{json .Config.Labels}}}')
# sudo is probed with the docker command itself, so a sudoers rule for exactly `docker ps` / `docker inspect` is enough.
CONTAINERS_SCRIPT = """command -v docker >/dev/null 2>&1 || { echo 'HLA_ERR docker is not installed'; exit 0; }
DK=docker; docker ps -q >/dev/null 2>&1 || { sudo -n docker ps -q >/dev/null 2>&1 && DK='sudo -n docker'; }
if ! out=$($DK ps -a --no-trunc --format '{{json .}}' 2>&1); then echo "HLA_ERR $(printf '%s' "$out" | head -c 300 | tr '\\n' ' ')"; exit 0; fi
printf '%s\\n' "$out" | sed -n 's/^{/P {/p'
ids=$($DK ps -aq --no-trunc)
[ -n "$ids" ] && $DK inspect --format '""" + _INSPECT + """' $ids 2>&1 | head -c 3000000
echo HLA_DONE
"""
_ROUTES = _PRE + """A={api}; D={dir}; B={boundary}
r=$(curl -fsS --max-time 5 "$A/api/http/routers" 2>&1); c=$?
echo "$B API routers $c"; printf '%s\\n' "$r" | head -c 3000000; echo; echo "$B END"
if [ $c -eq 0 ]; then
 for k in services middlewares; do r=$(curl -fsS --max-time 5 "$A/api/http/$k" 2>&1); echo "$B API $k $?"; printf '%s\\n' "$r" | head -c 3000000; echo; echo "$B END"; done
elif $S test -d "$D"; then
 n=0; set -f; IFS='
'
 for f in $($S find "$D" -maxdepth 2 -type f \\( -name '*.yml' -o -name '*.yaml' -o -name '*.toml' \\) 2>/dev/null | sort | head -100); do
  n=$((n+1)); echo "$B FILE $f"; $S cat -- "$f" 2>&1 | head -c 300000; echo; echo "$B END"
 done
 [ $n -eq 0 ] && echo "$B NOFILES"
else echo "$B NODIR"
fi
echo "$B DONE"
"""
# Hosts arrive on stdin as "scheme host path" lines; each is checked again here before it is used, always quoted.
_CHECKS = """B={boundary}; n=0
while read -r p h u; do
 n=$((n+1))
 case "$p" in https) port=443;; http) port=80;; *) continue;; esac
 case "$h" in ''|*[!a-z0-9.-]*) continue;; esac
 case "$u" in /*) ;; *) u=/;; esac
 case "$u" in *[!A-Za-z0-9._~/-]*) u=/;; esac
 ( r=$(curl -sk -o /dev/null --max-time 8 -w '%{{http_code}} %{{time_total}} %{{ssl_verify_result}}' --resolve "$h:$port:127.0.0.1" "$p://$h$u" 2>/dev/null)
   e=; [ "$p" = https ] && e=$(timeout 6 openssl s_client -servername "$h" -connect 127.0.0.1:443 </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null)
   echo "$B $n ${{r:-000 0 0}} ${{e#notAfter=}}" ) </dev/null &
 [ $((n % 10)) -eq 0 ] && wait
done
wait; echo "$B DONE"
"""


def _quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def api_url():
    value = os.getenv('TRAEFIK_API_URL', '').strip().rstrip('/') or 'http://127.0.0.1:8080'
    if not _API_URL.match(value):
        raise ValueError('TRAEFIK_API_URL must look like http://127.0.0.1:8080')
    return value


def dynamic_dir():
    value = os.getenv('TRAEFIK_DYNAMIC_DIR', '').strip() or '/opt/traefik/dynamic'
    if not _DIR.match(value) or '..' in value.split('/'):
        raise ValueError('TRAEFIK_DYNAMIC_DIR must be an absolute folder')
    return value.rstrip('/') or '/'


def routes_script(boundary):
    return _ROUTES.format(api=_quote(api_url()), dir=_quote(dynamic_dir()), boundary=boundary)


def checks_script(boundary):
    return _CHECKS.format(boundary=boundary)


# ── a defensive YAML subset (block maps and lists, flow [..] / {..}, quoted and plain scalars) ──────────────────────
class YamlError(ValueError):
    pass


def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            if i == 0 or line[i - 1] in ' \t:[{,-':
                quote = ch
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            return line[:i].rstrip()
    return line.rstrip()


def _split_flow(text):
    parts, depth, quote, cur = [], 0, None, ''
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in '"\'':
            quote = ch
        elif ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
            continue
        cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts]


def _key_split(text):
    """(key, rest) for 'key: rest' / 'key:' outside quotes and brackets, else None."""
    quote, depth = None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'' and i == 0:
            quote = ch
        elif ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        elif ch == ':' and depth == 0 and (i + 1 == len(text) or text[i + 1] in ' \t'):
            key = _scalar(text[:i].strip())
            return str(key), text[i + 1:].strip()
    return None


def _scalar(text, depth=0):
    text = text.strip()
    if depth > 20:
        raise YamlError('nested too deeply')
    if not text or text in ('~', 'null', 'Null', 'NULL'):
        return None
    if text[0] == '"' and text.endswith('"') and len(text) >= 2:
        body = text[1:-1]
        return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), body)
    if text[0] == "'" and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    if text[0] == '[' and text.endswith(']'):
        return [_scalar(p, depth + 1) for p in _split_flow(text[1:-1])]
    if text[0] == '{' and text.endswith('}'):
        out = {}
        for part in _split_flow(text[1:-1]):
            kv = _key_split(part)
            if kv is None:
                key, _, rest = part.partition(':')
                kv = (str(_scalar(key)), rest)
            out[kv[0]] = _scalar(kv[1], depth + 1)
        return out
    if text in ('true', 'True', 'TRUE', 'yes', 'on'):
        return True
    if text in ('false', 'False', 'FALSE', 'no', 'off'):
        return False
    if re.fullmatch(r'-?[0-9]+', text):
        return int(text)
    if text[0] in '&*!|>':  # anchors, aliases, tags and block scalars are not supported: keep them as plain text
        return text
    return text


def parse_yaml(text):
    """Parse the subset of YAML Traefik's dynamic files use. Raises YamlError (with a line number, never content)."""
    lines = []
    for number, raw in enumerate(text.replace('\r\n', '\n').split('\n'), 1):
        if '\t' in raw[:len(raw) - len(raw.lstrip())]:
            raise YamlError(f'tab indentation on line {number}')
        line = _strip_comment(raw)
        if not line.strip() or line.strip() in ('---', '...') or line.lstrip().startswith('%'):
            continue
        lines.append((len(line) - len(line.lstrip(' ')), line.strip(), number))
        if len(lines) > 20000:
            raise YamlError('file too long')
    if not lines:
        return {}
    value, pos = _block(lines, 0, lines[0][0], 0)
    if pos < len(lines):
        raise YamlError(f'unexpected indentation on line {lines[pos][2]}')
    return value


def _skip_block_scalar(lines, pos, indent):
    while pos < len(lines) and lines[pos][0] > indent:
        pos += 1
    return pos


def _block(lines, pos, indent, depth):
    if depth > 40:
        raise YamlError('nested too deeply')
    if lines[pos][1].startswith('- ') or lines[pos][1] == '-':
        return _list(lines, pos, indent, depth)
    return _map(lines, pos, indent, depth)


def _value_after_key(lines, pos, indent, rest, number, depth):
    """The value of a key whose line ends at `rest`; pos points after the key line."""
    if rest in ('|', '|-', '|+', '>', '>-', '>+'):
        return None, _skip_block_scalar(lines, pos, indent)
    if rest:
        return _scalar(rest), pos
    if pos < len(lines) and lines[pos][0] > indent:
        return _block(lines, pos, lines[pos][0], depth + 1)
    if pos < len(lines) and lines[pos][0] == indent and (lines[pos][1].startswith('- ') or lines[pos][1] == '-'):
        return _list(lines, pos, indent, depth + 1)
    return None, pos


def _map(lines, pos, indent, depth):
    out = {}
    while pos < len(lines):
        ind, text, number = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f'unexpected indentation on line {number}')
        if text.startswith('- ') or text == '-':
            break  # a list at the same indent belongs to the key above (handled by the caller)
        kv = _key_split(text)
        if kv is None:
            raise YamlError(f'expected "key: value" on line {number}')
        key, rest = kv
        value, pos = _value_after_key(lines, pos + 1, indent, rest, number, depth)
        out[key] = value
    return out, pos


def _list(lines, pos, indent, depth):
    out = []
    while pos < len(lines):
        ind, text, number = lines[pos]
        if ind != indent or not (text.startswith('- ') or text == '-'):
            if ind > indent:
                raise YamlError(f'unexpected indentation on line {number}')
            break
        item = text[1:].lstrip()
        if not item:
            pos += 1
            if pos < len(lines) and lines[pos][0] > indent:
                value, pos = _block(lines, pos, lines[pos][0], depth + 1)
            else:
                value = None
            out.append(value)
            continue
        inner = indent + (len(text) - len(item))
        kv = _key_split(item) if not item.startswith(('[', '{', '"', "'")) else None
        if kv is None:
            out.append(_scalar(item))
            pos += 1
            continue
        # "- key: value" starts a map whose further keys sit at the indent of "key".
        key, rest = kv
        first, pos = _value_after_key(lines, pos + 1, inner, rest, number, depth)
        entry = {key: first}
        if pos < len(lines) and lines[pos][0] == inner and not lines[pos][1].startswith('- '):
            more, pos = _map(lines, pos, inner, depth + 1)
            entry.update(more)
        out.append(entry)
    return out, pos


def parse_config(name, text):
    if name.endswith('.toml'):
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise YamlError('invalid TOML' + (f' near line {exc.lineno}' if getattr(exc, 'lineno', None) else '')) from None
    return parse_yaml(text)


# ── routes ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _ci(mapping, key):
    """Case-insensitive lookup (Traefik keys: entryPoints / entrypoints, loadBalancer / loadbalancer)."""
    if not isinstance(mapping, dict):
        return None
    if key in mapping:
        return mapping[key]
    low = key.lower()
    for k, v in mapping.items():
        if isinstance(k, str) and k.lower() == low:
            return v
    return None


def _strings(value):
    if isinstance(value, str):
        return [v.strip() for v in value.split(',') if v.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]
    return []


def rule_hosts(rule):
    """Host names and path prefixes from a router rule (best effort): ([host...], [path...], has_regexp)."""
    rule = rule if isinstance(rule, str) else ''
    hosts, paths = [], []
    for m in re.finditer(r'\bHost\s*\(([^)]*)\)', rule):
        hosts += [h.lower() for h in re.findall(r'[`"\']([^`"\']+)[`"\']', m.group(1))]
    for m in re.finditer(r'\b(?:PathPrefix|Path)\s*\(([^)]*)\)', rule):
        paths += re.findall(r'[`"\']([^`"\']+)[`"\']', m.group(1))
    regexp = bool(re.search(r'\bHost(?:Regexp|SNI)\b', rule))
    if regexp:
        for m in re.finditer(r'\bHostRegexp\s*\(([^)]*)\)', rule):
            for pattern in re.findall(r'[`"\']([^`"\']+)[`"\']', m.group(1)):
                plain = pattern.replace('\\.', '.').lstrip('^').rstrip('$')
                if HOSTNAME.match(plain.lower()):
                    hosts.append(plain.lower())
    seen = []
    for h in hosts:
        if h not in seen and HOSTNAME.match(h):  # anything else is never used in a URL or a check
            seen.append(h)
    return seen[:20], [p for p in paths if URL_PATH.match(p)][:5], regexp


def _middleware_type(cfg):
    """The middleware's kind ('basicauth', 'ipallowlist', ...) from an API entry or a config block; never its values."""
    if not isinstance(cfg, dict):
        return 'unknown'
    if isinstance(cfg.get('type'), str):
        return cfg['type'].lower()[:40]
    for key in cfg:
        if isinstance(key, str) and key not in ('name', 'provider', 'status', 'usedBy', 'error'):
            return key.lower()[:40]
    return 'unknown'


def _name(value, provider):
    value = str(value or '').strip()
    return value if '@' in value or not provider else value + '@' + provider


def _short(value):
    return str(value).split('@', 1)[0]


def _route(name, provider, router, services, middlewares):
    rule = str(_ci(router, 'rule') or '')[:600]
    hosts, paths, regexp = rule_hosts(rule)
    tls_cfg = _ci(router, 'tls')
    resolver = _ci(tls_cfg, 'certResolver') if isinstance(tls_cfg, dict) else None
    entry = _strings(_ci(router, 'entryPoints'))
    secure_ep = any(re.search(r'secure|https|443|tls', e, re.I) for e in entry)
    tls = bool(tls_cfg) or tls_cfg == {} and tls_cfg is not None or secure_ep or not entry
    mws = []
    for mw in _strings(_ci(router, 'middlewares'))[:20]:
        full = _name(mw, provider)
        cfg = middlewares.get(full) or middlewares.get(_short(full))
        kind = _middleware_type(cfg)
        if kind == 'chain' and isinstance(cfg, dict):
            members = _strings(_ci(_ci(cfg, 'chain'), 'middlewares'))
            inner = [_middleware_type(middlewares.get(_name(m, provider)) or middlewares.get(_short(m))) for m in members]
            if any(k in PROTECTING for k in inner):
                kind = 'chain:' + next(k for k in inner if k in PROTECTING)
        mws.append({'name': _short(full), 'type': kind})
    service = _name(_ci(router, 'service'), provider) if _ci(router, 'service') else ''
    svc = services.get(service) or services.get(_short(service)) or {}
    lb = _ci(svc, 'loadBalancer')
    backends = []
    for server in (_ci(lb, 'servers') or []) if isinstance(lb, dict) else []:
        url = _ci(server, 'url') if isinstance(server, dict) else None
        if isinstance(url, str) and _BACKEND.match(url.strip()):
            backends.append(url.strip()[:200])
    protected = any(m['type'].split(':')[-1] in PROTECTING or (m['type'] == 'unknown' and 'auth' in m['name'].lower()) for m in mws)
    return {'name': _short(name), 'provider': provider or '', 'rule': rule, 'hosts': hosts, 'paths': paths, 'host_regexp': regexp,
            'entrypoints': entry, 'tls': bool(tls), 'cert_resolver': str(resolver)[:60] if resolver else None,
            'middlewares': mws, 'protected': protected, 'service': _short(service), 'backends': backends[:10]}


def routes_from_api(routers, services, middlewares):
    """Routes from the Traefik API's /api/http/{routers,services,middlewares} (lists of objects named name@provider)."""
    by_name = lambda items: {i['name']: i for i in items if isinstance(i, dict) and isinstance(i.get('name'), str)} if isinstance(items, list) else {}
    svc, mws = by_name(services), by_name(middlewares)
    out = []
    for r in routers if isinstance(routers, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get('name'), str):
            continue
        provider = r.get('provider') or (r['name'].split('@', 1)[1] if '@' in r['name'] else '')
        if provider == 'internal':
            continue
        out.append(_route(r['name'], provider, r, svc, mws))
    return out


def routes_from_config(configs):
    """Routes from parsed file-provider configs: one dict per file; names are global across the files (provider file)."""
    routers, services, middlewares = {}, {}, {}
    for cfg in configs:
        http = _ci(cfg, 'http')
        if not isinstance(http, dict):
            continue
        for target, key in ((routers, 'routers'), (services, 'services'), (middlewares, 'middlewares')):
            block = _ci(http, key)
            if isinstance(block, dict):
                target.update({str(k): v for k, v in block.items() if isinstance(v, dict)})
    return [_route(name, 'file', r, services, middlewares) for name, r in routers.items()]


def routes_from_labels(containers):
    """Routes declared with traefik.http.* labels (docker provider); attached to their container."""
    out = []
    for c in containers:
        tree = {}
        for key, value in (c.get('traefik_labels') or {}).items():
            parts = key.split('.')
            if len(parts) < 5 or parts[1] != 'http':
                continue
            node = tree.setdefault(parts[2], {}).setdefault(parts[3], {})
            for p in parts[4:-1]:
                node = node.setdefault(p, {}) if isinstance(node.get(p, {}), dict) else {}
            node[parts[-1]] = value
        for name, router in (tree.get('routers') or {}).items():
            if not isinstance(router, dict):
                continue
            middlewares = tree.get('middlewares') or {}
            route = _route(name, 'docker', router, {}, middlewares)
            route['container'] = c['name']
            out.append(route)
    return out


# ── containers ───────────────────────────────────────────────────────────────────────────────────────────────────
def _ports(bindings, live):
    """[{host_ip, host_port, container_port, proto}] from inspect's port maps (configured bindings, then live ones)."""
    seen, out = set(), []
    for source in (live, bindings):
        if not isinstance(source, dict):
            continue
        for cport, binds in source.items():
            for b in binds or []:
                if not isinstance(b, dict) or not str(b.get('HostPort') or '').isdigit():
                    continue
                port, proto = (str(cport).split('/', 1) + ['tcp'])[:2]
                item = {'host_ip': str(b.get('HostIp') or ''), 'host_port': int(b['HostPort']), 'container_port': port, 'proto': proto}
                key = (item['host_ip'], item['host_port'], item['proto'])
                if key not in seen:
                    seen.add(key)
                    out.append(item)
    return sorted(out, key=lambda p: (p['host_port'], p['host_ip']))


def parse_containers(stdout):
    """(containers, error) from the containers script's output."""
    text = stdout or ''
    err = re.search(r'^HLA_ERR (.*)$', text, re.M)
    if err:
        return [], 'Docker could not be read on public-website: ' + err.group(1).strip()[:300]
    ps, found = {}, {}
    for line in text.splitlines():
        if line.startswith('P {'):
            try:
                row = json.loads(line[2:])
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('Names'):
                ps[str(row['Names']).split(',')[0].lstrip('/')] = row
        elif line.startswith('I {'):
            try:
                row = json.loads(line[2:])
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('name'):
                found[str(row['name']).lstrip('/')] = row
    if 'HLA_DONE' not in text and not found and not ps:
        return [], 'No answer from Docker on public-website (output cut off or the command failed)'
    out = []
    for name in sorted(set(found) | set(ps)):
        i, p = found.get(name, {}), ps.get(name, {})
        labels = i.get('labels') if isinstance(i.get('labels'), dict) else {}
        if not labels and isinstance(p.get('Labels'), str):
            labels = dict(kv.split('=', 1) for kv in p['Labels'].split(',') if '=' in kv)
        kept = {k: str(v)[:300] for k, v in labels.items() if k in KEEP_LABELS}
        # traefik.* labels: everything but middleware settings (basicAuth users and the like stay here).
        traefik = {k: str(v)[:300] for k, v in labels.items() if isinstance(k, str) and k.startswith('traefik.')
                   and not re.match(r'^traefik\.(http|tcp|udp)\.middlewares\.[^.]+\.[^.]+\.', k)}
        traefik.update({'.'.join(k.split('.')[:5]): '' for k in labels if isinstance(k, str)
                        and re.match(r'^traefik\.http\.middlewares\.[^.]+\.[^.]+\.', k)})  # the kind, without its values
        state = str(i.get('state') or p.get('State') or 'unknown').lower()
        health = i.get('health') if isinstance(i.get('health'), str) else None
        if not health and isinstance(p.get('Status'), str):
            m = re.search(r'\((healthy|unhealthy|health: starting)\)', p['Status'])
            health = ('starting' if 'starting' in m.group(1) else m.group(1)) if m else None
        out.append({'name': name[:128], 'image': str(i.get('image') or p.get('Image') or '')[:200], 'state': state,
                    'health': health, 'status': str(p.get('Status') or '')[:120],
                    'started': i.get('started') if isinstance(i.get('started'), str) and not i['started'].startswith('0001') else None,
                    'exit_code': i.get('exit') if isinstance(i.get('exit'), int) else None,
                    'restarts': i.get('restarts') if isinstance(i.get('restarts'), int) else None,
                    'ports': _ports(i.get('bindings'), i.get('ports')), 'labels': kept, 'traefik_labels': traefik,
                    'project': kept.get('com.docker.compose.project'), 'service': kept.get('com.docker.compose.service')})
    return out, None


# ── remote output parsing ────────────────────────────────────────────────────────────────────────────────────────
def _sections(stdout, boundary):
    """[(header words, body)] between '<boundary> ...' and '<boundary> END' lines."""
    out, cur, body = [], None, []
    for line in (stdout or '').split('\n'):
        if line.startswith(boundary + ' '):
            words = line[len(boundary) + 1:].split(' ')
            if words[0] == 'END':
                if cur is not None:
                    out.append((cur, '\n'.join(body)))
                cur, body = None, []
            elif words[0] in ('API', 'FILE'):
                cur, body = words, []
            else:
                out.append((words, ''))
        elif cur is not None:
            body.append(line)
    if cur is not None:  # cut off: keep what came
        out.append((cur + ['TRUNCATED'], '\n'.join(body)))
    return out


def read_routes(stdout, boundary):
    """{source, routes, error, files, file_errors} from the traefik routes script (raw texts are dropped here)."""
    api, files, errors, notes = {}, [], [], []
    api_error = None
    for words, body in _sections(stdout, boundary):
        if words[0] == 'API' and len(words) >= 3:
            if words[2] == '0':
                try:
                    api[words[1]] = json.loads(body)
                except ValueError:
                    api_error = 'the Traefik API answered something that is not JSON'
            elif words[1] == 'routers':
                first = (body.strip().splitlines() or [''])[0]
                api_error = 'Traefik API not reachable' + (' (' + first[:160] + ')' if first else ' (curl exit ' + words[2] + ')')
        elif words[0] == 'FILE' and len(words) >= 2:
            path = ' '.join(w for w in words[1:] if w != 'TRUNCATED')[:300]
            if body.startswith('cat: ') or 'Permission denied' in body[:200]:
                errors.append({'file': path, 'error': 'not readable'})
                continue
            try:
                files.append((path, parse_config(path, body)))
            except (YamlError, RecursionError, ValueError, TypeError) as exc:
                errors.append({'file': path, 'error': str(exc)[:120] if isinstance(exc, YamlError) else 'could not parse'})
        elif words[0] in ('NODIR', 'NOFILES'):
            notes.append(words[0])
    if 'routers' in api and isinstance(api['routers'], list):
        return {'source': 'api', 'routes': routes_from_api(api['routers'], api.get('services') or [], api.get('middlewares') or []),
                'error': None, 'files': [], 'file_errors': []}
    folder = dynamic_dir()
    if files:
        return {'source': 'files', 'routes': routes_from_config([cfg for _, cfg in files]), 'files': [p for p, _ in files],
                'file_errors': errors, 'error': (api_error or 'Traefik API not used') + '; routes read from ' + folder}
    why = (' and ' + folder + (' does not exist or is not readable' if 'NODIR' in notes else ' has no readable .yml/.toml files'
                               if not errors else ' could not be parsed'))
    return {'source': 'none', 'routes': [], 'files': [], 'file_errors': errors, 'error': (api_error or 'Traefik API not reachable') + why}


def _cert_days(text, now):
    text = ' '.join((text or '').split())
    if not text:
        return None
    try:
        return int((calendar.timegm(time.strptime(text.replace(' GMT', ''), '%b %d %H:%M:%S %Y')) - now) // 86400)
    except ValueError:
        return None


def read_checks(stdout, boundary, wanted, now):
    """{index: {http_status, seconds, verify, cert_days}} from the checks script."""
    out = {}
    for line in (stdout or '').splitlines():
        if not line.startswith(boundary + ' '):
            continue
        words = line[len(boundary) + 1:].split()
        if len(words) < 4 or not words[0].isdigit():
            continue
        n = int(words[0])
        if n not in wanted:
            continue
        try:
            code, seconds, verify = int(words[1]), round(float(words[2]), 3), int(words[3])
        except ValueError:
            continue
        out[n] = {'http_status': code, 'seconds': seconds, 'verify': verify, 'cert_days': _cert_days(' '.join(words[4:]), now)}
    return out


# ── environment and status ───────────────────────────────────────────────────────────────────────────────────────
def guess_environment(*names):
    text = ' '.join(str(n or '') for n in names).lower()
    if 'staging' in text or re.search(r'(^|[^a-z])stage([^a-z]|$)', text):
        return 'staging'
    if re.search(r'(^|[^a-z])(alpha|beta|test|testing|preview|dev|development|acc|acceptance|demo|sandbox)([^a-z0-9]|[0-9]|$)', text):
        return 'test'
    return 'production'


def label_environment(value):
    v = str(value or '').strip().lower()
    return {'production': 'production', 'prod': 'production', 'live': 'production', 'staging': 'staging', 'stage': 'staging',
            'test': 'test', 'testing': 'test', 'dev': 'test', 'development': 'test', 'preview': 'test', 'alpha': 'test',
            'beta': 'test'}.get(v)


RANK = {'down': 4, 'degraded': 3, 'up': 2, 'protected': 1, 'unknown': 0}


def url_status(check, protected, tls):
    """(status, reason) of one URL from its check."""
    if not check:
        return 'unknown', 'not checked'
    code, seconds = check['http_status'], check['seconds']
    if code == 0:
        return 'down', 'no answer (timeout or connection refused)'
    if code >= 500:
        return 'down', f'HTTP {code}'
    found = []
    if code in (401, 403) and protected:
        status, reason = 'protected', ('login required' if code == 401 else 'access restricted') + f' (HTTP {code})'
    elif code >= 400:
        status, reason = 'degraded', f'HTTP {code}'
    else:
        status, reason = 'up', f'HTTP {code}'
    days = check.get('cert_days')
    if tls and days is not None and days < 0:
        return 'down', 'certificate expired'
    if tls and days is not None and days < CERT_WARN_DAYS:
        found.append(f'certificate expires in {days} day' + ('' if days == 1 else 's'))
    if tls and check.get('verify') not in (0, None):
        found.append(f'certificate not trusted (verify code {check["verify"]})')
    if seconds > SLOW_SECONDS:
        found.append(f'slow ({seconds:.1f} s)')
    if found and status in ('up', 'protected'):
        return 'degraded', '; '.join(found)
    return status, '; '.join([reason] + found)


def container_status(c, others_running):
    state, health = c['state'], c.get('health')
    if state == 'running':
        if health == 'unhealthy':
            return 'degraded', f"container {c['name']} is unhealthy"
        if health == 'starting':
            return 'degraded', f"container {c['name']} is starting"
        return 'up', ''
    if state == 'exited' and c.get('exit_code') == 0 and others_running:
        return 'up', ''  # a one-shot helper (migrations, init) next to running containers
    what = {'restarting': 'keeps restarting', 'paused': 'is paused', 'created': 'was never started', 'dead': 'is dead'}.get(
        state, 'exited' + (f" (code {c['exit_code']})" if c.get('exit_code') is not None else ''))
    return ('degraded' if others_running else 'down'), f"container {c['name']} {what}"


def _url(route, host, path):
    return ('https' if route['tls'] else 'http') + '://' + host + (path if path and path != '/' else '/')


# ── inventory ────────────────────────────────────────────────────────────────────────────────────────────────────
def _site_id(value):
    return re.sub(r'[^A-Za-z0-9_.:@-]', '-', str(value))[:120] or 'site'


def _local_backend(url, hosts):
    m = _BACKEND.match(url or '')
    if not m:
        return None
    host = m.group(1).lower()
    port = int(m.group(2)) if m.group(2) else (443 if url.startswith('https') else 80)
    return port if host in hosts else None


def build(containers, routes_info, website_ip):
    """Sites from containers and routes (before the HTTP checks)."""
    hosts = {website_ip, WEBSITE_SERVER, 'public-website.lan', 'public-website.local'}
    hosts |= {h.strip().lower() for h in os.getenv('WEBSITE_BACKEND_HOSTS', '').split(',') if h.strip()}
    sites, by_container, port_owner = {}, {}, {}
    for c in containers:
        key = c['labels'].get('hla.name') or c.get('project') or c['name']
        sid = _site_id(key)
        site = sites.setdefault(sid, {'id': sid, 'name': str(key)[:120], 'project': c.get('project'), 'containers': [], 'routes': [],
                                      'working_dir': c['labels'].get('com.docker.compose.project.working_dir')})
        site['containers'].append(c)
        by_container[c['name']] = sid
        for p in c['ports']:
            if p['proto'] == 'tcp' and p['host_ip'] in ('', '0.0.0.0', '::', website_ip):
                port_owner.setdefault(p['host_port'], sid)
    label_routes = routes_from_labels(containers)
    routes = list(routes_info['routes'])
    api_docker = {r['name'] for r in routes if r['provider'] == 'docker'}
    if routes_info['source'] != 'api':
        routes += [r for r in label_routes if r['name'] not in api_docker]
    label_owner = {r['name']: r['container'] for r in label_routes}
    other = 0
    for r in routes:
        sid = None
        if r.get('container'):
            sid = by_container.get(r['container'])
        elif r['provider'] == 'docker' and r['name'] in label_owner:
            sid = by_container.get(label_owner[r['name']])
        local = [p for p in (_local_backend(b, hosts) for b in r['backends']) if p]
        if not sid:
            sid = next((port_owner[p] for p in local if p in port_owner), None)
        if not sid and local:
            sid = _site_id('route:' + (r['service'] or r['name']))
            sites.setdefault(sid, {'id': sid, 'name': r['hosts'][0] if r['hosts'] else r['name'], 'project': None, 'containers': [],
                                   'routes': [], 'working_dir': None, 'unmatched': True})
        if not sid:
            other += 1
            continue
        sites[sid]['routes'].append(r)
    for site in sites.values():
        labels = [c['labels'] for c in site['containers']]
        explicit = next((label_environment(l.get('hla.environment')) for l in labels if label_environment(l.get('hla.environment'))), None)
        names = [site.get('project'), site['name']] + [c['name'] for c in site['containers']] + [h for r in site['routes'] for h in r['hosts']]
        site['environment'] = explicit or guess_environment(*names)
        site['environment_source'] = 'label' if explicit else 'guess'
        site['label_url'] = next((l.get('hla.url') for l in labels if str(l.get('hla.url') or '').startswith(('http://', 'https://'))), None)
        lan = []
        for c in site['containers']:
            for p in c['ports']:
                if p['proto'] == 'tcp' and p['host_ip'] in ('', '0.0.0.0', '::', website_ip):
                    u = f"http://{website_ip}:{p['host_port']}"
                    if u not in lan:
                        lan.append(u)
        site['lan_urls'] = lan
    return sites, other


def plan_checks(sites):
    """[(scheme, host, path)] to check, deduplicated, at most MAX_CHECKS; and whether some were left out."""
    plan = []
    for site in sites.values():
        for r in site['routes']:
            for host in r['hosts']:
                for path in (r['paths'] or ['/'])[:1]:
                    item = ('https' if r['tls'] else 'http', host, path if URL_PATH.match(path) else '/')
                    if HOSTNAME.match(host) and item not in plan:
                        plan.append(item)
    return plan[:MAX_CHECKS], len(plan) > MAX_CHECKS


def finish(sites, results, checks_error):
    """Per-URL and per-site status from the containers and the HTTP checks."""
    out = []
    for site in sites.values():
        urls, parts = [], []
        for r in site['routes']:
            for host in r['hosts'] or []:
                path = (r['paths'] or ['/'])[0]
                key = ('https' if r['tls'] else 'http', host, path if URL_PATH.match(path) else '/')
                check = results.get(key)
                st, reason = url_status(check, r['protected'], r['tls'])
                if not check:
                    reason = checks_error or ('not checked: not a plain host name' if not HOSTNAME.match(host) else 'not checked')
                urls.append({'url': _url(r, host, path), 'router': r['name'], 'entrypoints': r['entrypoints'], 'tls': r['tls'],
                             'protected': r['protected'], 'http_status': check['http_status'] if check else None,
                             'seconds': check['seconds'] if check else None, 'cert_days': check.get('cert_days') if check else None,
                             'status': st, 'reason': reason, 'error': None if check else reason})
                parts.append((st, host + ': ' + reason))
            if not r['hosts']:
                urls.append({'url': None, 'router': r['name'], 'entrypoints': r['entrypoints'], 'tls': r['tls'], 'protected': r['protected'],
                             'http_status': None, 'seconds': None, 'cert_days': None, 'status': 'unknown',
                             'reason': 'rule has no plain host name' + (' (HostRegexp)' if r['host_regexp'] else ''), 'error': 'no_host'})
        running = [c for c in site['containers'] if c['state'] == 'running']
        problems = []
        for c in site['containers']:
            st, reason = container_status(c, any(x is not c for x in running))
            if st != 'up':
                problems.append((st, reason))
        checked = [p for p in parts if p[0] != 'unknown']
        if checked:
            # The public URLs decide; one URL up beats another that only asks for a login.
            worst = max(RANK[p[0]] for p in checked)
            top = 'up' if worst <= RANK['up'] and any(p[0] == 'up' for p in checked) else max((p[0] for p in checked), key=RANK.get)
            reasons = [p[1] for p in checked if p[0] == top]
        elif site['containers']:
            top = 'up' if running else 'down'
            reasons = (['running; no public URL (LAN only)'] if not urls else
                       ['running; ' + (checks_error or 'the public URL was not checked')]) if running else []
        else:
            top, reasons = 'unknown', [checks_error or 'not checked']
        for st, reason in problems:
            if RANK[st] > RANK[top]:
                top, reasons = st, []
            if st == top or RANK[st] >= RANK['degraded']:
                reasons.append(reason)
        if site.get('unmatched'):
            reasons.append('no container on public-website publishes the port this route points at')
        containers = [{'name': c['name'], 'image': c['image'], 'state': c['state'], 'health': c['health'], 'status': c['status'],
                       'started': c['started'], 'exit_code': c['exit_code'], 'restarts': c['restarts'], 'service': c.get('service'),
                       'ports': [f"{p['host_ip'] or '0.0.0.0'}:{p['host_port']}->{p['container_port']}/{p['proto']}" for p in c['ports']]}
                      for c in site['containers']]
        routes = [{k: r[k] for k in ('name', 'provider', 'rule', 'hosts', 'entrypoints', 'tls', 'cert_resolver', 'middlewares', 'protected',
                                     'service', 'backends')} for r in site['routes']]
        out.append({'id': site['id'], 'name': site['name'], 'project': site.get('project'), 'working_dir': site.get('working_dir'),
                    'environment': site['environment'], 'environment_source': site['environment_source'],
                    'urls': urls, 'lan_urls': site['lan_urls'], 'label_url': site.get('label_url'), 'containers': containers, 'routes': routes,
                    'unmatched': bool(site.get('unmatched')), 'public': bool(urls),
                    'status': top, 'status_reason': '; '.join(dict.fromkeys(reasons))[:400]})
    order = {'production': 0, 'staging': 1, 'test': 2}
    return sorted(out, key=lambda s: (order.get(s['environment'], 3), s['name']))


def _run(execute, server, script, **kw):
    try:
        return execute(server, script, **kw), None
    except Exception as exc:
        detail = str(exc)[:160] if isinstance(exc, (RuntimeError, ValueError)) else ''
        return None, f'could not reach {server}: {type(exc).__name__}' + (f' ({detail})' if detail else '')


def inventory(execute, now=None):
    """The whole inventory: two SSH calls in parallel (containers, routes), then one for the checks."""
    now = time.time() if now is None else now
    boundary = 'HLA_' + secrets.token_hex(8)
    website_ip = SERVERS[WEBSITE_SERVER][0]
    try:
        route_cmd = routes_script(boundary)
    except ValueError as exc:
        route_cmd, route_cfg_error = None, str(exc)
    else:
        route_cfg_error = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        fc = pool.submit(_run, execute, WEBSITE_SERVER, CONTAINERS_SCRIPT, timeout=60, max_output=4_000_000)
        fr = pool.submit(_run, execute, TRAEFIK_SERVER, route_cmd, timeout=60, max_output=4_000_000) if route_cmd else None
        cres, cerr = fc.result()
        rres, rerr = fr.result() if fr else (None, route_cfg_error)
    containers, containers_error = [], cerr
    if cres is not None:
        if cres.get('timed_out'):
            containers_error = 'Docker on public-website did not answer in time'
        else:
            containers, containers_error = parse_containers(cres.get('stdout'))
            if containers_error and cres.get('stderr'):
                containers_error += ' ' + cres['stderr'].strip()[:160]
    if rres is not None and not rres.get('timed_out'):
        routes_info = read_routes(rres.get('stdout'), boundary)
    else:
        routes_info = {'source': 'none', 'routes': [], 'files': [], 'file_errors': [],
                       'error': rerr or 'Traefik host did not answer in time'}
    if routes_info['source'] == 'none' and any(k.startswith('traefik.http.routers.') for c in containers for k in c.get('traefik_labels') or {}):
        routes_info = {**routes_info, 'source': 'labels'}
    sites, other = build(containers, routes_info, website_ip)
    plan, cut = plan_checks(sites)
    results, checks_error = {}, None
    if plan:
        stdin = ''.join(f'{s} {h} {p}\n' for s, h, p in plan).encode()
        cres2, err = _run(execute, TRAEFIK_SERVER, checks_script(boundary), timeout=150, max_output=200_000, stdin=stdin)
        if err:
            checks_error = 'HTTP checks failed: ' + err
        elif cres2.get('timed_out'):
            checks_error = 'HTTP checks did not finish in time'
        found = read_checks((cres2 or {}).get('stdout'), boundary, set(range(1, len(plan) + 1)), now)
        results = {plan[n - 1]: v for n, v in found.items()}
        if not found and not checks_error:
            checks_error = 'HTTP checks returned nothing (is curl installed on the traefik host?)'
    return {'sites': finish(sites, results, checks_error), 'routes_source': routes_info['source'], 'routes_error': routes_info['error'],
            'routes_files': routes_info['files'], 'routes_file_errors': routes_info['file_errors'], 'containers_error': containers_error,
            'checks_error': checks_error, 'checks_truncated': cut, 'other_routes': other, 'checked_at': int(now),
            'traefik_api': api_url() if not route_cfg_error else None, 'dynamic_dir': dynamic_dir() if not route_cfg_error else None}


def summary(data):
    counts = {}
    for s in data.get('sites') or []:
        counts[s['status']] = counts.get(s['status'], 0) + 1
    return (f"{len(data.get('sites') or [])} sites (" + ', '.join(f'{v} {k}' for k, v in sorted(counts.items())) + ')'
            + f"; routes from {data.get('routes_source')}" + ('; containers not read' if data.get('containers_error') else ''))[:300]


# ── cache ────────────────────────────────────────────────────────────────────────────────────────────────────────
class RateLimited(Exception):
    def __init__(self, retry):
        super().__init__('refresh_rate_limited')
        self.retry = retry


_lock = threading.Lock()
_cache = {'at': 0.0, 'data': None}


def reset():
    _cache.update(at=0.0, data=None)


def get(execute, refresh=False, clock=time.monotonic):
    """(data, ran): the cached inventory (60 s), or a fresh one; a forced refresh at most once per 15 s."""
    with _lock:
        age = clock() - _cache['at']
        if refresh and _cache['data'] is not None and age < REFRESH_SECONDS:
            raise RateLimited(max(1, int(REFRESH_SECONDS - age + 0.999)))
        if not refresh and _cache['data'] is not None and age < CACHE_SECONDS:
            return {**_cache['data'], 'cached': True, 'age': int(age)}, False
        data = inventory(execute)
        _cache.update(at=clock(), data=data)
        return {**data, 'cached': False, 'age': 0}, True


def for_agent(data, environment=None, search=None):
    """A compact copy for the model: no route internals beyond names and middleware kinds."""
    out = []
    for s in data.get('sites') or []:
        if environment and environment != 'all' and s['environment'] != environment:
            continue
        text = ' '.join([s['name'], s.get('project') or ''] + [u['url'] or '' for u in s['urls']] + [c['name'] for c in s['containers']]).lower()
        if search and search.lower() not in text:
            continue
        out.append({'name': s['name'], 'environment': s['environment'], 'environment_source': s['environment_source'],
                    'status': s['status'], 'reason': s['status_reason'],
                    'urls': [{k: u[k] for k in ('url', 'status', 'http_status', 'seconds', 'cert_days', 'protected')} for u in s['urls']],
                    'lan_urls': s['lan_urls'],
                    'containers': [{k: c[k] for k in ('name', 'image', 'state', 'health', 'status')} for c in s['containers']]})
    return out
