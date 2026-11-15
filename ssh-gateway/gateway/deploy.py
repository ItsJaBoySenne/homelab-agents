"""Git/Compose deployments with staging and automatic rollback (HLA_DEPLOY_V1).

An app is a Git checkout with a compose file in a registered folder on its server. The checkout pulls with
that server's own read-only deploy key, so no credential passes through the gateway or the agent.

1. Staging (optional): on the sandbox, clone/fetch the repo into /srv/staging/<app>, resolve the ref to an
   exact commit, `docker compose up -d --build`, and verify (all containers running, health URL answers).
2. Target: refuse when the checkout has local changes, record the current commit, check out the commit that
   passed staging, rebuild and verify the same way. If that fails, check out the previous commit again,
   rebuild and verify: the outcome says whether the rollback worked.

Values are substituted with shlex.quote after validation; scripts are POSIX sh and fit the 2048-character cap.
They run without a terminal, so git/ssh fail instead of prompting; on the target only git and docker need sudo.
"""
import json
import re
import shlex
import threading
import time

STAGING_ROOT = '/srv/staging'
TIMEOUT = 300  # per phase; core.execute caps a single script at 300 s
_REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$')
SHA = re.compile(r'^[0-9a-f]{40}$')

# Shared helpers: up() rebuilds and starts; ok() waits for all containers running, then the health URL (if any).
_COMMON = r"""S=;sudo -n true 2>/dev/null&&S='sudo -n'
f(){ echo HLA_VERIFY=$1;exit 1;}
up(){ o=$($S docker compose up -d --build 2>&1);r=$?;printf '%s\n' "$o"|tail -n 15;return $r;}
ok(){ sleep 3;n=$($S docker compose ps -q|wc -l);b=$($S docker compose ps -a --format '{{.Name}} {{.State}}'|grep -v ' running$')
[ "$n" -gt 0 ]&&[ -z "$b" ]||{ printf '%s\n' "$b";return 1;};[ -z "$H" ]&&return 0
i=0;while [ $i -lt 10 ];do curl -fsS -o /dev/null --max-time 5 "$H"&&return 0;i=$((i+1));sleep 3;done;echo "health check failed: $H";return 2;}
res(){ case $1 in 0)echo HLA_VERIFY=running;;2)echo HLA_VERIFY=unhealthy;;3)echo HLA_VERIFY=up_failed;;*)echo HLA_VERIFY=not_running;;esac;}
"""
_STAGING = _COMMON + r"""H=@HEALTH@;D=@DIR@;K=$HOME/.ssh/deploy-@NAME@
GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes";[ -f "$K" ]&&GIT_SSH_COMMAND="$GIT_SSH_COMMAND -o IdentitiesOnly=yes -i $K"
export GIT_SSH_COMMAND GIT_TERMINAL_PROMPT=0
[ -d "$D/.git" ]||{ $S mkdir -p "$D"&&$S chown "$(id -u):$(id -g)" "$D"&&git clone -q @REPO@ "$D";}||f fetch_failed
cd "$D"&&git fetch -q --prune --tags origin||f fetch_failed
R=@REF@;c=$(git rev-parse -q --verify "origin/$R^{commit}"||git rev-parse -q --verify "$R^{commit}")||f unknown_ref
echo HLA_SHA=$c
git checkout -q --detach "$c"||f checkout_failed
[ -f .env ]||[ ! -f .env.example ]||cp .env.example .env
$S docker compose config -q||f invalid_config
if up;then ok;v=$?;else v=3;fi;res $v;[ $v = 0 ]
"""
_TARGET = _COMMON + r"""H=@HEALTH@;D=@DIR@;c=@SHA@;R=@REF@
G="$S git -c safe.directory=$D -C $D"
cd "$D"&&[ -d .git ]||f not_a_git_checkout
[ -z "$($G status --porcelain --untracked-files=no)" ]||{ $G status --short --untracked-files=no|head -n 20;f dirty_worktree;}
p=$($G rev-parse HEAD)||f not_a_git_checkout;echo HLA_FROM=$p
$G fetch -q --prune --tags origin||f fetch_failed
[ -n "$c" ]||c=$($G rev-parse -q --verify "origin/$R^{commit}"||$G rev-parse -q --verify "$R^{commit}")||f unknown_ref
$G cat-file -e "$c^{commit}"||f unknown_ref
echo HLA_SHA=$c
$G checkout -q --detach "$c"||f checkout_failed
$S docker compose config -q||{ $G checkout -q --detach "$p";f invalid_config;}
if up;then ok;v=$?;else v=3;fi;res $v;[ $v = 0 ]&&exit 0
echo "== rolling back to $p"
$G checkout -q --detach "$p"&&up&&ok&&echo HLA_ROLLBACK=ok||echo HLA_ROLLBACK=failed
exit 1
"""


def valid_ref(ref):
    return isinstance(ref, str) and bool(_REF.match(ref)) and '..' not in ref and not ref.endswith(('.lock', '/'))


def _fill(script, **values):
    for key, value in values.items():
        script = script.replace('@' + key.upper() + '@', shlex.quote(value or ''))
    return script


def staging_script(app, ref):
    return _fill(_STAGING, health=app['health_url'], dir=STAGING_ROOT + '/' + app['name'], repo=app['repo'], ref=ref).replace(
        '@NAME@', app['name'])  # app names are [a-z0-9-] (policy.update), so safe unquoted in the key path


def target_script(app, ref, sha=None):
    return _fill(_TARGET, health=app['health_url'], dir=app['path'], sha=sha, ref=ref)


def _phase(result):
    text = (result.get('stdout') or '') + '\n' + (result.get('stderr') or '')
    marks = dict(re.findall(r'^HLA_([A-Z]+)=(\S*)$', text, re.M))
    state = 'timeout' if result.get('timed_out') else marks.get('VERIFY') or 'unknown'
    sha = marks.get('SHA') if SHA.match(marks.get('SHA') or '') else None
    return {'verified': result.get('exit_code') == 0 and state == 'running', 'state': state, 'sha': sha,
            'from': marks.get('FROM') if SHA.match(marks.get('FROM') or '') else None,
            'rollback': marks.get('ROLLBACK'), 'log': text.strip()[-1500:]}


def run(execute, app, ref, *, sha=None, staging=True):
    """Stage (if enabled) then deploy one commit. execute(server, script, timeout=, max_output=) is core.execute."""
    result = {'app': app['name'], 'server': app['server'], 'ref': ref, 'staging': None, 'target': None, 'sha': sha, 'from': None}
    if staging and app['staging']:
        from .core import SANDBOX
        if not SANDBOX:
            raise RuntimeError('staging needs a sandbox server in the inventory')
        st = _phase(execute(SANDBOX, staging_script(app, ref), timeout=TIMEOUT, max_output=8000))
        result['staging'] = st
        if not st['verified']:
            return {**result, 'status': 'staging_failed', 'verified': False, 'error_class': 'staging_' + st['state']}
        sha = st['sha']  # the target gets exactly the commit that passed staging
    t = _phase(execute(app['server'], target_script(app, ref, sha), timeout=TIMEOUT, max_output=8000))
    status = ('deployed' if t['verified'] else 'rolled_back' if t['rollback'] == 'ok'
              else 'rollback_failed' if t['rollback'] == 'failed' else 'failed')  # failed: stopped before anything changed
    return {**result, 'target': t, 'sha': t['sha'] or sha, 'from': t['from'], 'status': status,
            'verified': t['verified'], 'error_class': None if t['verified'] else t['state']}


# One deployment per app at a time (the gateway is one process with request threads).
_lock = threading.Lock()
_running = set()


def claim(name):
    with _lock:
        if name in _running:
            return False
        _running.add(name)
        return True


def release(name):
    with _lock:
        _running.discard(name)


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS deployments (id INTEGER PRIMARY KEY, app TEXT NOT NULL, server TEXT NOT NULL,
        ref TEXT, sha TEXT, from_sha TEXT, status TEXT NOT NULL, requested_by TEXT, started INTEGER, finished INTEGER, result TEXT)''')


def mark_interrupted(db):
    """At gateway startup only: a deployment cut off by a restart can never finish; say so."""
    db.execute("UPDATE deployments SET status='interrupted',finished=? WHERE status='running'", (int(time.time()),))


def start(db, app, ref, requested_by):
    cur = db.execute('INSERT INTO deployments(app,server,ref,status,requested_by,started) VALUES (?,?,?,?,?,?)',
                     (app['name'], app['server'], ref, 'running', requested_by, int(time.time())))
    return cur.lastrowid


def finish(db, ident, result):
    db.execute('UPDATE deployments SET status=?,sha=?,from_sha=?,finished=?,result=? WHERE id=?',
               (result['status'], result.get('sha'), result.get('from'), int(time.time()), json.dumps(result)[:20000], ident))


def known_good(db, app, sha):
    """A commit this app ran successfully before (deployed, or the commit a deployment replaced): safe to roll back to."""
    return db.execute("""SELECT 1 FROM deployments WHERE app=? AND ((status='deployed' AND sha=?) OR (from_sha=? AND status<>'running'))
        LIMIT 1""", (app, sha, sha)).fetchone() is not None


def history(db, app=None, limit=50):
    rows = db.execute('''SELECT id,app,server,ref,sha,from_sha,status,requested_by,started,finished,result FROM deployments
        WHERE ? IS NULL OR app=? ORDER BY id DESC LIMIT ?''', (app, app, limit)).fetchall()
    keys = ('id', 'app', 'server', 'ref', 'sha', 'from_sha', 'status', 'requested_by', 'started', 'finished', 'result')
    out = []
    for row in rows:
        item = dict(zip(keys, row))
        item['result'] = json.loads(item['result']) if item['result'] else None
        out.append(item)
    return out
