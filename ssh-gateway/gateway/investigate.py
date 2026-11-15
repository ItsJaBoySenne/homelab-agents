"""Read-only diagnostics that take a target (a path or a container), level 1 (HLA_INVESTIGATE_V1).

Built for "why can't I delete this file?" on the file server: ownership along the whole path, ACLs, mounts,
container users and mounts, NFS/SMB shares. Nothing here writes. The target is passed through shlex.quote,
so it is always one shell word. Commands use sudo -n only where the server already grants it (dev, sandbox);
elsewhere they run as svc-hla-agent and permission errors are part of the answer.
"""
import re
import shlex

# Use passwordless sudo when available, otherwise run unprivileged; never prompts.
_PRE = "S=; sudo -n true 2>/dev/null && S='sudo -n'\n"
_PATH = _PRE + """p={target}
echo "== permissions along the path (namei)"; $S namei -l "$p" 2>&1 | head -40
echo "== target (stat)"; $S stat -c '%A %U(%u):%G(%g) %s bytes, modified %y' "$p" 2>&1
echo "== ACLs"; if command -v getfacl >/dev/null; then $S getfacl -p "$p" 2>&1 | head -40; else echo "getfacl not installed (package acl): no ACL information"; fi
echo "== contents (first 60)"; $S ls -la "$p" 2>&1 | head -60
echo "== mount"; findmnt -T "$p" -o TARGET,SOURCE,FSTYPE,OPTIONS 2>&1
echo "== free space"; df -h "$p" 2>&1 | tail -1
echo "== checked as"; id
"""
_CONTAINERS = _PRE + """$S docker ps -a --format '{{.Names}}\\t{{.Image}}\\t{{.Status}}' 2>&1 | head -60
"""
# Never print the container environment: it usually holds credentials.
_CONTAINER = _PRE + """c={target}
echo "== user, image, state"; $S docker inspect -f 'user={{{{if .Config.User}}}}{{{{.Config.User}}}}{{{{else}}}}(image default){{{{end}}}} image={{{{.Config.Image}}}} state={{{{.State.Status}}}}' "$c" 2>&1
echo "== mounts (type source -> destination, writable)"; $S docker inspect -f '{{{{range .Mounts}}}}{{{{.Type}}}} {{{{.Source}}}} -> {{{{.Destination}}}} rw={{{{.RW}}}}{{{{println}}}}{{{{end}}}}' "$c" 2>&1
echo "== processes (host uid view)"; $S docker top "$c" -eo user,uid,pid,args 2>&1 | head -20
"""
_CONTAINER_LOGS = _PRE + """$S docker logs --tail 60 {target} 2>&1 | tail -60
"""
_SHARES = _PRE + """echo "== network mounts"; findmnt -t nfs,nfs4,cifs,smb3 -o TARGET,SOURCE,FSTYPE,OPTIONS 2>&1 || true
echo "== NFS exports"; if [ -r /etc/exports ]; then grep -v '^#' /etc/exports | grep -v '^$' || echo "(none)"; else echo "/etc/exports not readable or absent"; fi
echo "== Samba"; if command -v testparm >/dev/null; then $S testparm -s 2>/dev/null | head -80; else echo "Samba not installed"; fi
"""

TARGETED = {
    'path-permissions': ('path', _PATH),
    'container-detail': ('container', _CONTAINER),
    'container-logs': ('container', _CONTAINER_LOGS),
}
UNTARGETED = {'containers': _CONTAINERS, 'shares': _SHARES}
_CONTAINER_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')


def resolve(name, target):
    """The command for a targeted or extra diagnostic, or ValueError with a short reason."""
    if name in UNTARGETED:
        return UNTARGETED[name]  # not .format()ed: Docker's {{...}} templates stay literal
    if name not in TARGETED:
        raise ValueError('invalid_diagnostic')
    kind, template = TARGETED[name]
    if not isinstance(target, str):
        raise ValueError('target_required')
    if kind == 'path':
        if not target.startswith('/') or len(target) > 255 or '..' in target.split('/') or any(ord(ch) < 32 for ch in target):
            raise ValueError('target must be an absolute path without ..')
    elif not _CONTAINER_NAME.match(target):
        raise ValueError('target must be a container name')
    return template.format(target=shlex.quote(target))
