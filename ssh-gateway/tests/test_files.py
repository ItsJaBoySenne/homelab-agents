"""File write/read scripts run for real under sh on a temp folder, content fed on stdin as over SSH (no SSH).

python3 tests/test_files.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import files  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

def run(script, data=None):
    proc = subprocess.run(['sh', '-c', script], input=data if data is not None else b'', capture_output=True, timeout=30)
    return proc.returncode, proc.stdout.decode(errors='replace'), proc.stderr.decode(errors='replace')

def write(path, text, append=False):
    path, (data, append) = files.request('write_file', {'path': str(path), 'content': text, 'append': append})
    return run(files.write_command(path, append), data)

def read(path, **kw):
    path, (offset, length) = files.request('read_file', {'path': str(path), **kw})
    code, out, err = run(files.read_command(path, offset, length))
    size, content = files.parse_read(out)
    return code, size, content, err

T = Path(tempfile.mkdtemp())
target = T / 'site' / 'deep' / 'nested' / 'index.html'
html = '<!doctype html>\n<title>Homelab Agents</title>\n<p>hello</p>\n'
code, out, err = write(target, html)
check(code == 0 and target.read_text() == html and files.parse_write(out) == len(html.encode()), 'new file with nested directories, size reported')
code, out, _ = write(target, 'short\n')
check(code == 0 and target.read_text() == 'short\n' and files.parse_write(out) == 6, 'overwrite replaces the file')
code, out, _ = write(target, 'more\n', append=True)
check(code == 0 and target.read_text() == 'short\nmore\n' and files.parse_write(out) == 11, 'append adds, size is the whole file')

tricky = 'it\'s "quoted" $(touch {m}) `touch {m}` $HOME \\n\\\\ \n\nEOF\n\'EOF\'\n€ ünïcödé 日本語 🙂\r\nno trailing newline'.format(m=T / 'pwned1')
write(T / 'tricky.txt', tricky)
check((T / 'tricky.txt').read_bytes() == tricky.encode() and not (T / 'pwned1').exists(), 'quotes, $(...), backticks, newlines, unicode survive byte-exact')

m = T / 'pwned2'
weird = T / f'dir with spaces; touch {m} &' / f"a'b $(touch {m}) `touch {m}`.txt"
code, out, _ = write(weird, 'ok')
check(code == 0 and weird.read_text() == 'ok' and not (T / 'pwned2').exists(), 'path with spaces and shell metacharacters quoted, nothing executed')
check("'" in files.write_command(str(weird)) and files.read_command(str(weird)).startswith('p=\''), 'path is one shell-quoted word')

body = 'é' + 'abcdefghij' * 30
(T / 'r.txt').write_text(body)
size = len(body.encode())
code, s, content, _ = read(T / 'r.txt')
check(code == 0 and s == size and content == body, 'read whole file (default offset/length)')
code, s, content, _ = read(T / 'r.txt', offset=2, length=10)
check(s == size and content == 'abcdefghij', 'offset is in bytes, length limits the bytes returned')
N = files.MAX_READ
(T / 'big.txt').write_bytes(b'x' * (N + 500))
code, s, content, _ = read(T / 'big.txt')
check(s == N + 500 and len(content) == N and N < s, 'large file: first 12000 bytes')
code, s, content, _ = read(T / 'big.txt', offset=N)
check(len(content) == 500 and N + len(content) >= s, 'second page reaches the end (eof)')
code, s, content, _ = read(T / 'big.txt', offset=10 ** 9)
check(code == 0 and content == '' and s == N + 500, 'offset past the end: empty content, size still reported')
(T / 'empty').write_bytes(b'')
check(read(T / 'empty')[:3] == (0, 0, ''), 'empty file')

code, s, content, err = read(T / 'missing.txt')
check(code != 0 and s is None and 'no such file' in err, 'missing file: non-zero exit and error')
code, s, content, err = read(T)
check(code != 0 and 'not a regular file' in err, 'directory: not a regular file')
if os.geteuid() != 0:
    (T / 'secret').write_text('x'); os.chmod(T / 'secret', 0)
    check(read(T / 'secret')[0] != 0, 'unreadable file: non-zero exit')
    ro = T / 'ro'; ro.mkdir(); os.chmod(ro, 0o555)
    code, out, err = write(ro / 'f.txt', 'x')
    check(code != 0 and files.parse_write(out) is None and err, 'write into read-only directory fails with stderr')
    os.chmod(ro, 0o755)

big = ('0123456789abcdef' * 64 + '\n') * 195  # ~200 KiB
code, out, _ = write(T / 'big' / 'page.html', big)
check(code == 0 and files.parse_write(out) == len(big.encode()) == (T / 'big' / 'page.html').stat().st_size
      and files.sha256(big.encode()) == files.sha256((T / 'big' / 'page.html').read_bytes()), '200 KiB write: exact size and sha256')

for mode, body, reason in (('write_file', {'path': 'rel/x', 'content': ''}, 'relative path'),
                           ('write_file', {'path': '/a\nb', 'content': ''}, 'control character'),
                           ('write_file', {'path': '/a\x00b', 'content': ''}, 'NUL'),
                           ('write_file', {'path': '/' + 'a' * 255, 'content': ''}, 'path over 255 chars'),
                           ('write_file', {'path': '/x', 'content': 5}, 'content not a string'),
                           ('write_file', {'path': '/x'}, 'content missing'),
                           ('write_file', {'path': '/x', 'content': 'a' * (files.MAX_WRITE + 1)}, 'content over 256 KiB'),
                           ('write_file', {'path': '/x', 'content': '\ud800'}, 'lone surrogate'),
                           ('write_file', {'path': '/x', 'content': '', 'append': 'yes'}, 'append not a bool'),
                           ('read_file', {'path': '/x', 'offset': -1}, 'negative offset'),
                           ('read_file', {'path': '/x', 'length': 0}, 'length 0'),
                           ('read_file', {'path': '/x', 'length': files.MAX_READ + 1}, 'length over 12000'),
                           ('read_file', {'path': '/x', 'offset': '3'}, 'offset as string'),
                           ('read_file', {'path': '/x', 'length': True}, 'length as bool')):
    try: files.request(mode, body); check(False, f'refused: {reason}')
    except ValueError: check(True, f'refused: {reason}')
check(files.request('write_file', {'path': '/x', 'content': '€' * (files.MAX_WRITE // 3)})[0] == '/x', 'limit counts UTF-8 bytes (just under the limit is fine)')
check(all(len(c) < 2048 for c in (files.write_command('/' + 'a' * 254, True), files.read_command('/' + "'" * 254, 10 ** 12, N))),
      'scripts fit the 2048-character command limit')

print('\n' + ('ALL PASSED' if not failures else f'{len(failures)} FAILED'))
sys.exit(1 if failures else 0)
