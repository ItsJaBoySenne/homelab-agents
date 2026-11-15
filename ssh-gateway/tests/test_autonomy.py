"""Runs the real maintenance scripts in bash against stand-in sudo/apt-get/dpkg-query/systemctl.

python3 tests/test_autonomy.py   (no network, no root, nothing installed)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import autonomy  # noqa: E402

STUBS = {
    'sudo': '[ "$1" = -n ] && shift; exec "$@"',
    'sleep': 'exit 0',
    'dpkg-query': '''[ -f "$STATE/installed" ] || exit 1
case "$2" in *Version*) printf 'install ok installed|1.7.1-1';; *) printf 'install ok installed';; esac''',
    'apt-get': '''echo "$*" >> "$STATE/apt.log"
case "$*" in *" update"*|update*|*" update") touch "$STATE/updated"; echo "Reading package lists... Done"; exit 0;; esac
case "$SCENARIO" in
 ok) ;;
 stale) [ -f "$STATE/updated" ] || { echo "Err:1 http://deb.debian.org/debian trixie/main amd64 jq amd64 1.7.1-1"; echo "  404  Not Found [IP: 151.101.2.132 80]"; echo "E: Unable to fetch some archives, maybe run apt-get update"; exit 100; };;
 lock) echo "E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 4242 (apt)"; exit 100;;
 missing) echo "E: Unable to locate package jq"; exit 100;;
 nospace) echo "E: You don't have enough free space in /var/cache/apt/archives/."; exit 100;;
esac
touch "$STATE/installed"; echo "Setting up jq (1.7.1-1) ..."''',
    'systemctl': '''case "$1" in
 restart) [ "$SCENARIO" = badconf ] && { echo "Job for nginx.service failed." >&2; exit 1; }; exit 0;;
 is-active) [ "$SCENARIO" = badconf ] && { echo failed; exit 3; }; echo active;;
esac''',
    'journalctl': 'echo "nginx: [emerg] unknown directive \\"servr\\" in /etc/nginx/sites-enabled/default:3"',
    'docker': '''case "$1 $2" in
 "restart "*) exit 0;;
 "inspect "*) [ "$SCENARIO" = badconf ] && echo exited || echo running;;
 "logs "*) echo "web: fatal: missing config";;
 "compose config") [ "$SCENARIO" = badconf ] && { echo "yaml: line 3: did not find expected key" >&2; exit 15; }; exit 0;;
 "compose pull") exit 0;;
 "compose up") echo "Container app-web-1 Started";;
 "compose ps") echo "app-web-1 running"; [ "$SCENARIO" = crashloop ] && echo "app-db-1 restarting"; exit 0;;
esac''',
}

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)


def run(operation, target, scenario, preinstalled=False):
    with tempfile.TemporaryDirectory() as tmp:
        bindir, state = Path(tmp, 'bin'), Path(tmp, 'state')
        bindir.mkdir(); state.mkdir()
        for name, body in STUBS.items():
            path = bindir / name
            path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
        if preinstalled: (state / 'installed').touch()
        script = autonomy.resolve(operation, target, 4, ('/srv/app',))
        # Same script, but resolve commands to the stand-ins first.
        script = script.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin;', f'PATH={bindir}:/usr/bin:/bin;', 1)
        script = script.replace('cd /srv/app ', f'cd {tmp} ', 1)  # compose project dir must exist
        proc = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=30,
                              env={'STATE': str(state), 'SCENARIO': scenario, 'PATH': '/usr/bin:/bin'})
        result = {'stdout': proc.stdout, 'stderr': proc.stderr, 'exit_code': proc.returncode, 'timed_out': False}
        apt_calls = (state / 'apt.log').read_text().splitlines() if (state / 'apt.log').exists() else []
        return result, autonomy.outcome(operation, target, result), apt_calls


r, o, calls = run('install_package', 'jq', 'ok')
check(r['exit_code'] == 0 and o['verified'] and o['version'] == '1.7.1-1' and not o['retried_after_index_refresh'], f'clean install verified {o}')
check(len(calls) == 1 and 'DPkg::Lock::Timeout=60' in calls[0] and calls[0].endswith('install -y --no-install-recommends jq'), f'non-interactive install with lock wait {calls}')

r, o, calls = run('install_package', 'jq', 'stale')
check(r['exit_code'] == 0 and o['verified'] and o['retried_after_index_refresh'], f'stale index: refreshed, retried, verified {o}')
check([c.split()[-1] for c in calls] == ['jq', 'update', 'jq'], f'install, update, install once {calls}')
check('HLA_UPDATE_RC=0' in r['stdout'], 'update exit code reported from apt-get, not tail')

r, o, calls = run('install_package', 'jq', 'ok', preinstalled=True)
check(r['exit_code'] == 0 and o['already_installed'] and o['verified'] and calls == [], f'already installed: no apt call {o}')

r, o, calls = run('install_package', 'jq', 'lock')
check(r['exit_code'] != 0 and not o['verified'] and o['error_class'] == 'lock' and len(calls) == 1, f'lock: no index refresh, classified {o}')

r, o, calls = run('install_package', 'jq', 'missing')
check(not o['verified'] and o['error_class'] == 'not_found' and o['retried_after_index_refresh'] and len(calls) == 3, f'missing after refresh: not_found, one retry {o}')

r, o, _ = run('install_package', 'jq', 'nospace')
check(not o['verified'] and o['error_class'] == 'disk_full', f'disk full classified {o}')

r, o, _ = run('restart_service', 'nginx', 'ok')
check(r['exit_code'] == 0 and o['verified'] and o['service_state'] == 'active', f'restart verified active {o}')

r, o, _ = run('restart_service', 'nginx', 'badconf')
check(r['exit_code'] != 0 and not o['verified'] and o['service_state'] == 'failed' and o['error_class'] == 'service_not_active' and 'unknown directive' in r['stdout'], f'failed restart: state + journal excerpt {o}')

check(autonomy.outcome('install_package', 'jq', {'stdout': '', 'stderr': '', 'timed_out': True})['error_class'] == 'timeout', 'timeout classified')
for bad in (('install_package', 'jq; rm -rf /', 5), ('restart_service', 'ssh', 5), ('remove_package', 'jq', 5),
            ('restart_container', 'web; reboot', 5), ('compose_up', '/etc', 5), ('install_package', 'jq', 1)):
    try: autonomy.resolve(*bad, compose_projects=('/srv/app',)); check(False, f'refused {bad}')
    except ValueError: check(True, f'refused {bad}')
for op, target, need in (('install_package', 'nginx', 3), ('restart_service', 'nginx', 3), ('restart_container', 'web', 3), ('compose_up', '/srv/app', 4)):
    try: autonomy.resolve(op, target, need - 1, ('/srv/app',)); check(False, f'{op} refused below level {need}')
    except ValueError: check(True, f'{op} refused below level {need}')
    check(len(autonomy.resolve(op, target, need, ('/srv/app',))) < 2048, f'{op} allowed at level {need} and fits the command limit')

r, o, _ = run('restart_container', 'web', 'ok')
check(r['exit_code'] == 0 and o['verified'] and o['service_state'] == 'running', f'container restart verified {o}')
r, o, _ = run('restart_container', 'web', 'badconf')
check(r['exit_code'] != 0 and not o['verified'] and o['error_class'] == 'service_not_active' and 'exited' in r['stdout'], f'failed container restart: state + logs {o}')
r, o, _ = run('compose_up', '/srv/app', 'ok')
check(r['exit_code'] == 0 and o['verified'] and 'HLA_UP_RC=0' in r['stdout'], f'compose deploy verified {o}')
r, o, _ = run('compose_up', '/srv/app', 'badconf')
check(r['exit_code'] != 0 and o['error_class'] == 'invalid_config', f'invalid compose config stops before deploy {o}')
r, o, _ = run('compose_up', '/srv/app', 'crashloop')
check(r['exit_code'] != 0 and o['error_class'] == 'service_not_active' and 'app-db-1 restarting' in r['stdout'], f'deploy with a non-running service reported {o}')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
