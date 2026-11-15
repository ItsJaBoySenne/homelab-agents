"""File read/write for free-shell agents (HLA_FILES_V1): level 6 or unhinged mode only.

The path is passed through shlex.quote, so it is always one shell word. Written content never appears in the
command: it is sent on the SSH channel's stdin (core.execute(stdin=...)) and `cat` writes it. Runs as
svc-hla-agent, no sudo. Output starts with a HLA_ header line that parse_write / parse_read turn into fields.
"""
import hashlib
import shlex

MAX_WRITE = 256 * 1024   # bytes, UTF-8 encoded
MAX_READ = 12000         # bytes per read
TIMEOUT = 60

_WRITE = """p={path}
d=$(dirname -- "$p") && mkdir -p -- "$d" || exit 1
cat {op} "$p" || exit 1
printf 'HLA_BYTES=%s\\n' "$(wc -c < "$p" | tr -d ' ')"
"""
_READ = """p={path}
if [ ! -e "$p" ]; then printf 'no such file: %s\\n' "$p" >&2; exit 2; fi
if [ ! -f "$p" ]; then printf 'not a regular file: %s\\n' "$p" >&2; exit 2; fi
if [ ! -r "$p" ]; then printf 'permission denied: %s\\n' "$p" >&2; exit 1; fi
s=$(wc -c < "$p" | tr -d ' ') || exit 1
printf 'HLA_SIZE=%s\\n' "$s"
tail -c +{start} "$p" | head -c {length}
"""


def valid_path(path):
    if not isinstance(path, str) or not path.startswith('/'): raise ValueError('path must be an absolute path')
    if len(path) > 255: raise ValueError('path longer than 255 characters')
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path): raise ValueError('path contains control characters')
    return path


def request(mode, body):
    """Validate a write_file/read_file request: (path, extra) or ValueError with a reason safe to show.
    write_file -> (path, (data: bytes, append: bool)); read_file -> (path, (offset, length))."""
    path = valid_path(body.get('path'))
    if mode == 'write_file':
        content, append = body.get('content'), body.get('append', False)
        if not isinstance(content, str): raise ValueError('content must be a string')
        try: data = content.encode('utf-8')
        except UnicodeEncodeError: raise ValueError('content is not valid UTF-8 text') from None
        if len(data) > MAX_WRITE: raise ValueError(f'content larger than {MAX_WRITE} bytes')
        if append is None: append = False
        if not isinstance(append, bool): raise ValueError('append must be true or false')
        return path, (data, append)
    offset, length = body.get('offset', 0), body.get('length', MAX_READ)
    offset = 0 if offset is None else offset
    length = MAX_READ if length is None else length
    if type(offset) is not int or offset < 0: raise ValueError('offset must be an integer >= 0')
    if type(length) is not int or not 1 <= length <= MAX_READ: raise ValueError(f'length must be an integer from 1 to {MAX_READ}')
    return path, (offset, length)


def sha256(data=b''):
    return hashlib.sha256(data).hexdigest()


def write_command(path, append=False):
    return _WRITE.format(path=shlex.quote(path), op='>>' if append else '>')


def read_command(path, offset=0, length=MAX_READ):
    return _READ.format(path=shlex.quote(path), start=int(offset) + 1, length=int(length))


def _header(stdout, name):
    first, sep, rest = stdout.partition('\n')
    if sep and first.startswith(name + '=') and first[len(name) + 1:].isdigit():
        return int(first[len(name) + 1:]), rest
    return None, stdout


def parse_write(stdout):
    """File size after writing, or None if the script did not get that far."""
    return _header(stdout, 'HLA_BYTES')[0]


def parse_read(stdout):
    """(total size or None, content)."""
    size, rest = _header(stdout, 'HLA_SIZE')
    return size, rest if size is not None else ''
