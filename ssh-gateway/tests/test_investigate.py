"""Targeted read-only diagnostics, run for real in bash on a local folder (no SSH, nothing written).

python3 tests/test_investigate.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import investigate  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

def run(script):
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=30)

# The classic "can't delete": the file is writable, but its directory is not (deletion needs the directory).
root = Path(tempfile.mkdtemp())
shared = root / 'srv files' / 'shared'
shared.mkdir(parents=True)
(shared / 'report.txt').write_text('x')
os.chmod(shared, 0o555)
try:
    out = run(investigate.resolve('path-permissions', str(shared / 'report.txt'))).stdout
    check('== permissions along the path (namei)' in out and 'dr-xr-xr-x' in out and 'report.txt' in out, 'namei shows each level, incl. the read-only parent')
    check('== mount' in out and 'TARGET' in out and '== checked as' in out and 'uid=' in out, 'mount and identity reported')
    check('getfacl not installed' in out or '# file:' in out, 'ACL section present (or says why not)')
    out = run(investigate.resolve('path-permissions', str(shared))).stdout
    check('report.txt' in out.split('== contents')[1], 'directory listing included')
finally:
    os.chmod(shared, 0o755)

marker = root / 'pwned'
script = investigate.resolve('path-permissions', f'/tmp/x; touch {marker}; echo')
run(script)
check(not marker.exists(), 'shell metacharacters in the path are not executed')
for name, target, reason in (('path-permissions', 'srv/files', 'relative path'), ('path-permissions', '/srv/../etc/shadow', '.. in path'),
                             ('path-permissions', '/srv/a\nrm -rf /', 'control character'), ('path-permissions', None, 'missing target'),
                             ('container-detail', 'web; reboot', 'bad container name'), ('container-logs', '-v', 'option as container'),
                             ('rm-everything', '/', 'unknown diagnostic')):
    try: investigate.resolve(name, target); check(False, f'refused: {reason}')
    except ValueError: check(True, f'refused: {reason}')
for name in ('containers', 'shares'):
    proc = run(investigate.resolve(name, None))
    check(proc.returncode in (0, 1) and 'syntax error' not in proc.stderr, f'{name} runs without shell errors')
detail = investigate.resolve('container-detail', 'filebrowser')
check("'filebrowser'" not in detail and 'c=filebrowser' in detail and '{{.Config.User}}' in detail and 'Env' not in detail,
      'container detail: name quoted, Docker template intact, environment never printed')
check(all(len(investigate.resolve(n, '/srv/' + 'a' * 240 if k == 'path' else 'a' * 120)) < 2048 for n, (k, _) in investigate.TARGETED.items()),
      'all scripts fit the 2048-character limit')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
