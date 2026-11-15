"""Deploy scripts run for real under sh against a local git origin, with fake docker/sudo/curl/sleep on PATH.

python3 tests/test_deploy.py   (needs git; nothing outside a temp folder is touched)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import deploy  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

T = Path(tempfile.mkdtemp())
SHIM, HOME = T / 'bin', T / 'home'
SHIM.mkdir(); HOME.mkdir()
shims = {
    # Not passwordless here, so the scripts run without sudo, like on a server without it.
    'sudo': 'exit 1',
    'sleep': 'exit 0',
    # Healthy when the checked-out version contains a HEALTHY file.
    'curl': '[ -f "$PWD/HEALTHY" ]',
    # docker compose, driven by compose.yaml in the current folder: "invalid" fails config, "crash" exits.
    'docker': '''[ "$1" = compose ] || exit 2; shift
case "$1" in
 config) ! grep -q invalid compose.yaml;;
 up) echo "built $(head -n 1 compose.yaml)"; if grep -q crash compose.yaml; then echo exited > .state; else echo running > .state; fi;;
 ps) if [ "$2" = -q ]; then echo c0ffee; else echo "web $(cat .state 2>/dev/null || echo created)"; fi;;
esac''',
}
for name, body in shims.items():
    (SHIM / name).write_text('#!/bin/sh\n' + body + '\n'); (SHIM / name).chmod(0o755)
ENV = {**os.environ, 'PATH': f'{SHIM}:{os.environ["PATH"]}', 'HOME': str(HOME),
       'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}

def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()

# Origin history: A (good) -> B (good, healthy) on main; tags crash (C), invalid (D), sick (E: runs, no HEALTHY).
work = T / 'work'; work.mkdir()
git('init', '-q', '-b', 'main', cwd=work)
def commit(compose, healthy=False, tag=None, branch_from=None):
    if branch_from: git('checkout', '-q', '--detach', branch_from, cwd=work)
    (work / 'compose.yaml').write_text(compose + '\n')
    (work / 'HEALTHY').unlink(missing_ok=True)
    if healthy: (work / 'HEALTHY').write_text('ok')
    git('add', '-A', cwd=work); git('commit', '-q', '-m', compose, cwd=work)
    if tag: git('tag', tag, cwd=work)
    return git('rev-parse', 'HEAD', cwd=work)
A = commit('version A')
B = commit('version B', healthy=True)
C = commit('version C crash', tag='crash', branch_from=B)
D = commit('version D invalid', tag='invalid', branch_from=B)
E = commit('version E sick', tag='sick', branch_from=B)
git('checkout', '-q', 'main', cwd=work)
origin = T / 'origin.git'
git('clone', '-q', '--bare', str(work), str(origin), cwd=T)
target = T / 'srv' / 'site'
git('clone', '-q', str(origin), str(target), cwd=T)
git('checkout', '-q', '--detach', A, cwd=target)

deploy.STAGING_ROOT = str(T / 'staging')
APP = {'name': 'site', 'server': 'public-website', 'path': str(target), 'repo': str(origin), 'health_url': '', 'staging': True}
RAN = []

def execute(server, script, timeout=30, max_output=12000):
    RAN.append(server)
    check(len(script) <= 2048, f'{server} script fits the 2048-character cap ({len(script)})')
    p = subprocess.run(['sh', '-c', script], cwd=T, env=ENV, capture_output=True, text=True, timeout=60)
    return {'exit_code': p.returncode, 'timed_out': False, 'stdout': p.stdout, 'stderr': p.stderr, 'truncated': False}

head = lambda: git('rev-parse', 'HEAD', cwd=target)

r = deploy.run(execute, APP, 'main')
check(RAN == ['sandbox', 'public-website'] and r['staging']['verified'] and r['staging']['sha'] == B, 'staging clones, resolves main to B and verifies')
check(r['status'] == 'deployed' and r['verified'] and r['sha'] == B and r['from'] == A and head() == B, f'target moved A -> B and verified: {r["status"]}')
check((T / 'staging' / 'site' / '.git').is_dir(), 'staging checkout in the staging root')

RAN.clear()
r = deploy.run(execute, APP, 'crash')
check(r['status'] == 'staging_failed' and r['error_class'] == 'staging_not_running' and RAN == ['sandbox'] and head() == B,
      'a commit that fails staging never reaches the target')

no_staging = {**APP, 'staging': False}
r = deploy.run(execute, no_staging, 'crash')
check(r['status'] == 'rolled_back' and r['error_class'] == 'not_running' and r['sha'] == C and head() == B
      and (target / '.state').read_text().strip() == 'running', 'failing deploy rolls back to B and B runs again')

r = deploy.run(execute, no_staging, 'invalid')
check(r['status'] == 'failed' and r['error_class'] == 'invalid_config' and head() == B, 'invalid compose file: checkout undone, nothing restarted')

r = deploy.run(execute, {**no_staging, 'health_url': 'http://127.0.0.1:8080/health'}, 'sick')
check(r['status'] == 'rolled_back' and r['error_class'] == 'unhealthy' and 'health check failed' in r['target']['log'] and head() == B,
      'unhealthy deploy (health URL) rolls back')
r = deploy.run(execute, {**APP, 'health_url': 'http://127.0.0.1:8080/health'}, 'sick')
check(r['status'] == 'staging_failed' and r['error_class'] == 'staging_unhealthy', 'health URL also checked in staging')

r = deploy.run(execute, no_staging, 'no-such-branch')
check(r['status'] == 'failed' and r['error_class'] == 'unknown_ref' and head() == B, 'unknown ref refused')

(target / 'compose.yaml').write_text('locally edited\n')
r = deploy.run(execute, no_staging, 'main')
check(r['status'] == 'failed' and r['error_class'] == 'dirty_worktree' and 'compose.yaml' in r['target']['log'], 'local changes on the server block the deploy')
git('checkout', '-q', '--', 'compose.yaml', cwd=target)

r = deploy.run(execute, no_staging, A, sha=A)
check(r['status'] == 'deployed' and head() == A and r['from'] == B, 'roll back to an exact commit')

marker = T / 'pwned'
check(not deploy.valid_ref(f'main;touch {marker}') and not deploy.valid_ref('../x') and not deploy.valid_ref('-c') and deploy.valid_ref('release/1.2'),
      'ref validation')
script = deploy.target_script({**no_staging, 'path': str(target)}, f'x$(touch {marker})')
execute('public-website', script)
check(not marker.exists(), 'shell syntax in a ref is never executed (quoted)')
# The limits enforced by policy.update: path 120, repo 200, health URL 120, ref 100 characters.
long_app = {'name': 'a' * 40, 'server': 'docker', 'path': '/srv/' + 'p' * 115, 'repo': 'git@github.com:' + 'o' * 181 + '.git',
            'health_url': 'http://127.0.0.1:65535/' + 'h' * 97, 'staging': True}
check(len(deploy.staging_script(long_app, 'r' * 100)) <= 2048 and len(deploy.target_script(long_app, 'r' * 100, 'f' * 40)) <= 2048,
      'longest allowed values still fit 2048 characters')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
