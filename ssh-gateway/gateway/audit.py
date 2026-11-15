"""The audit log: every command, file write, deploy, Home Assistant call, secret use and approval (HLA_AUDIT_V1).

Rows are written by gateway_api/server.py. Since this version a row also says which agent acted, what it did
(`detail`: the command as the agent sent it, with {{secret:NAME}} references and never a value, a file path, a
diagnostic name, a deploy ref) and the dashboard task it belonged to. Older rows have only the digest.

GET /v1/audit reads it, newest first, filtered and paginated by id. Everything returned passes through
`clean()`: secret values the gateway knows become [secret:NAME] and anything that looks like a credential is
masked, so a token an agent typed into a command never reaches the dashboard.
"""
import re
import time

from . import masking

MAX_LIMIT = 200
DETAIL_CHARS = 4000
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ACTION = re.compile(r'^[a-z_]{1,40}\*?$')
_SERVER = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')  # mcp:<name> services too

# Credential-looking text, masked in everything the read endpoint returns. Conservative: a masked git SHA is
# harmless, a shown token is not.
_MASKS = (
    (re.compile(r'(?i)(authorization\s*[:=]\s*["\']?\s*(?:bearer|basic|token)\s+)[^\s"\'\\]+'), r'\1[redacted]'),
    (re.compile(r'(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}'), r'\1[redacted]'),
    # NAME_PASSWORD=value, password: value, … but not {{secret:NAME}}, [secret:NAME] or an already masked value.
    (re.compile(r'(?i)(?<![\w{\[])((?:[a-z0-9_-]*?)(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|auth)'
                r'[a-z0-9_-]*["\']?\s*[:=]\s*["\']?)(?!\[redacted|\[secret:|\{\{secret:|(?:bearer|basic|token)\s)[^\s"\'&;|]+'), r'\1[redacted]'),
    (re.compile(r'(?i)(--?(?:password|passwd|token|secret|api-key|apikey)(?:=|\s+))(?!\[redacted|\[secret:|\{\{secret:)[^\s"\'&;|]+'), r'\1[redacted]'),
    (re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@'), r'\1[redacted]@'),  # user:password@host
    (re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}'
                r'|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})'), '[redacted]'),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)', re.S), '[redacted private key]'),
)
# Short password options of particular commands (masked only inside that command, up to the next | ; & or line).
# A value is a quoted string or a word; references and already masked values are left alone.
_VALUE = r'''(?!\[redacted|\[secret:|\{\{secret:)(?:"[^"]*"|'[^']*'|[^\s"'&;|]+)'''
_CMD = r'(?:(?<![^\s;&|(/`])(?:%s)(?=\s)[^|;&\n]*)'
_SHORT_P = r'(\s-p(?:\s+(?=[^\s-])|(?=[^\s-])))'   # -pVALUE or -p VALUE, not a bare -p (prompts) before the next option
_COMMAND_MASKS = (
    # mysql -pVALUE, mysql -p VALUE (and mariadb, mysqldump, mysqladmin)
    (re.compile(_CMD % r'mysql|mariadb|mysqldump|mysqladmin|mariadb-dump|mariadb-admin|mysqlimport|mysqlcheck', re.I),
     re.compile(_SHORT_P + _VALUE)),
    # curl -u user:pass, --user user:pass, --user=user:pass, -uuser:pass
    (re.compile(_CMD % r'curl', re.I), re.compile(r'''(\s(?:-u|--user|--proxy-user|-U)(?:=|\s*)["']?[^\s:"']*:)(?!\[redacted)[^\s"']+''')),
    (re.compile(_CMD % r'sshpass', re.I), re.compile(_SHORT_P + _VALUE)),
    (re.compile(_CMD % r'redis-cli', re.I), re.compile(r'(\s(?:-a|--pass)(?:=|\s+))' + _VALUE)),
    (re.compile(_CMD % r'(?:docker|podman|nerdctl)\s+login|helm\s+registry\s+login', re.I), re.compile(r'(\s-p(?:=|\s+))' + _VALUE)),
)
_HTPASSWD = re.compile(r'(?i)(?<![^\s;&|(/`])(htpasswd\s+((?:-[A-Za-z]+\s+)+))([^|;&\n]*)')
_ARG = re.compile(r'''"[^"]*"|'[^']*'|[^\s"'&;|]+''')


def _htpasswd(m):
    """htpasswd -b [file] user PASSWORD: the password is the argument after the file (none with -n) and the user."""
    flags = m.group(2).replace('-', '').replace(' ', '')
    args = list(_ARG.finditer(m.group(3)))
    at = 1 if 'n' in flags else 2   # positionals before the password
    if 'b' not in flags or len(args) <= at or re.match(r'\[redacted|\[secret:|\{\{secret:', args[at].group(0)):
        return m.group(0)
    rest = m.group(3)
    return m.group(1) + rest[:args[at].start()] + '[redacted]' + rest[args[at].end():]


def _mask_commands(text):
    for command, option in _COMMAND_MASKS:
        text = command.sub(lambda m, option=option: option.sub(r'\1[redacted]', m.group(0)), text)
    return _HTPASSWD.sub(_htpasswd, text)


# Long random-looking strings (mixed letters and digits, 32+ characters) that are not plain hex digests.
_RANDOM =re.compile(r'(?<![A-Za-z0-9_\-+/])[A-Za-z0-9_\-+/]{32,}={0,2}(?![A-Za-z0-9_\-+/])')


def migrate(db):
    """Additive: who acted, what, and for which dashboard task (NULL on older rows)."""
    columns = {r[1] for r in db.execute('PRAGMA table_info(audit)')}
    for name in ('agent_id', 'detail', 'task_id'):
        if name not in columns:
            db.execute(f'ALTER TABLE audit ADD COLUMN {name} TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS audit_digest_idx ON audit(digest)')
    db.execute('CREATE INDEX IF NOT EXISTS audit_agent_idx ON audit(agent_id, id)')


def task_id(value):
    """A dashboard task id from the X-HLA-Task header, or None (anything else is ignored)."""
    value = (value or '').strip().lower()
    return value if _UUID.match(value) else None


def agent_id(value):
    value = str(value or '').strip().lower()
    return value if _UUID.match(value) else None


def mask(text, known=None):
    """Text safe to show: known secret values become [secret:NAME], credential-looking parts [redacted]."""
    if not isinstance(text, str) or not text:
        return text
    text = masking.mask_text(text, masking.prepare([(value, '[secret:' + name + ']') for value, name in known or ()]))
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    text = _mask_commands(text)

    def random(m):
        s = m.group(0)
        if re.fullmatch(r'[0-9a-f]+', s):
            return s  # hex digests and commit ids
        return '/'.join('[redacted]' if len(part) >= 32 and sum(bool(re.search(c, part)) for c in ('[a-z]', '[A-Z]', '[0-9]')) >= 3
                        else part for part in s.split('/'))
    return _RANDOM.sub(random, text)


def _since(value, default=None):
    if value in (None, ''):
        return default
    value = int(value)
    if not 0 <= value <= 10 ** 11:
        raise ValueError('timestamps are unix seconds')
    return value


def query(db, params, known=None):
    """{rows, next_before_id[, facets]} for GET /v1/audit. params: {agent, server, action, q, since, until, limit,
    before_id, facets}. Raises ValueError for bad parameters."""
    where, args = [], []
    agent = params.get('agent') or ''
    if agent:
        if agent == 'none':
            where.append("agent_id IS NULL AND (actor IS NULL OR actor NOT LIKE 'agent:%')")
        elif _UUID.match(agent):
            where.append("(agent_id=? OR (agent_id IS NULL AND actor=?))"); args += [agent, 'agent:' + agent]
        else:
            raise ValueError('agent must be an agent id or none')
    server = params.get('server') or ''
    if server:
        if not _SERVER.match(server):
            raise ValueError('invalid server')
        where.append('server=?'); args.append(server)
    action = params.get('action') or ''
    if action:
        if not _ACTION.match(action):
            raise ValueError('invalid action')
        if action.endswith('*'):
            where.append('action LIKE ?'); args.append(action[:-1].replace('_', r'\_') + '%')
            where[-1] += " ESCAPE '\\'"
        else:
            where.append('action=?'); args.append(action)
    text = (params.get('q') or '').strip()
    if text:
        if len(text) > 200:
            raise ValueError('search text is at most 200 characters')
        like = '%' + text.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
        where.append("(detail LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' OR server LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\')")
        args += [like] * 4
    since, until = _since(params.get('since')), _since(params.get('until'))
    if since is not None:
        where.append('timestamp>=?'); args.append(since)
    if until is not None:
        where.append('timestamp<?'); args.append(until)
    before = params.get('before_id')
    if before not in (None, ''):
        before = int(before)
        where.append('id<?'); args.append(before)
    limit = int(params.get('limit') or 50)
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f'limit must be 1..{MAX_LIMIT}')
    sql = ('SELECT id,timestamp,server,action,digest,exit_code,timed_out,truncated,actor,agent_id,detail,task_id,'
           # Who approved an approved command: the dashboard grant for the same digest just before it.
           "(SELECT g.actor FROM audit g WHERE g.digest=audit.digest AND g.action='chat_approval' AND g.id<audit.id "
           'ORDER BY g.id DESC LIMIT 1) FROM audit' + (' WHERE ' + ' AND '.join(where) if where else '')
           + ' ORDER BY id DESC LIMIT ?')
    rows = db.execute(sql, (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for (ident, ts, srv, act, dig, code, timed_out, truncated, actor, agent_col, detail, task, granted) in rows:
        agent_value = agent_col or (actor[6:] if isinstance(actor, str) and actor.startswith('agent:') else None)
        out.append({
            'id': ident, 'timestamp': ts, 'server': srv, 'action': act, 'digest': (dig or '')[:16],
            'exit_code': code, 'timed_out': bool(timed_out), 'truncated': bool(truncated),
            'actor': mask(actor, known) if actor and not str(actor).startswith('agent:') else None,
            'agent_id': agent_id(agent_value),
            'detail': mask((detail or '')[:DETAIL_CHARS], known) or None,
            'task_id': task_id(task),
            'approved_by': mask(granted, known) if act == 'agent_approved_command' else None,
            'outcome': outcome(act, code, timed_out),
        })
    result = {'rows': out, 'next_before_id': out[-1]['id'] if more and out else None}
    if params.get('facets'):
        result['facets'] = facets(db)
    return result


def outcome(action, code, timed_out):
    if action == 'agent_denied':
        return 'denied'
    if action in ('chat_approval',) or action.startswith('agent_secret_') and code is None:
        return 'recorded'
    if action.startswith('deploy_'):
        return 'ok' if action == 'deploy_deployed' else 'failed'
    if timed_out:
        return 'timed_out'
    if code is None:
        return 'error'
    return 'ok' if code == 0 else 'failed'


def facets(db, days=180):
    """Values for the dashboard's filters, from the last `days` days."""
    since = int(time.time()) - days * 86400
    servers = [r[0] for r in db.execute('SELECT DISTINCT server FROM audit WHERE timestamp>=? AND server IS NOT NULL ORDER BY server', (since,))]
    actions = [r[0] for r in db.execute('SELECT DISTINCT action FROM audit WHERE timestamp>=? AND action IS NOT NULL ORDER BY action', (since,))]
    agents = set()
    for agent_col, actor in db.execute("SELECT DISTINCT agent_id, actor FROM audit WHERE timestamp>=? AND (agent_id IS NOT NULL OR actor LIKE 'agent:%')", (since,)):
        value = agent_id(agent_col or (actor or '')[6:])
        if value:
            agents.add(value)
    return {'servers': servers, 'actions': actions, 'agents': sorted(agents)}


def known_secrets(agent_secrets, extra_values=()):
    """(value, name) pairs to replace: the generated secrets (cached by agent_secrets) and the gateway's
    own tokens. Never raises: an unreachable secret store still leaves the pattern masking."""
    values = {}
    try:
        values.update(agent_secrets.load() if agent_secrets.configured() else {})
    except Exception:
        pass
    for name, value in extra_values:
        if value:
            values[name] = value
    return sorted(((v, n) for n, v in values.items() if isinstance(v, str) and len(v) >= agent_secrets.MIN_REDACT),
                  key=lambda p: -len(p[0]))

