"""Typed maintenance operations. Which ones a caller may run is decided by gateway/policy.py."""
import re

# Minimum effective policy level per target. Package scripts run as root on the server.
PACKAGES = {
    'curl': 2, 'jq': 2, 'git': 2,
    'htop': 3, 'nginx': 3,
}
SERVICES = {'nginx': 3}
CONTAINER_LEVEL = 3
COMPOSE_LEVEL = 4
_CONTAINER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
# apt update + install can take longer than a diagnostic; core.execute caps this at 120 s.
TIMEOUT = 120

# HLA_SELF_HEALING_V1: one fixed script per operation. @TARGET@ is substituted only after it
# matched the allowlist above (plain package/unit names), so it never carries shell syntax.
# Lines starting with HLA_ are parsed by outcome(); the rest is ordinary apt/systemd output.
_PREAMBLE = """PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH
apt_get() { sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -q -o DPkg::Lock::Timeout=60 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"; }
"""
_INSTALL = _PREAMBLE + """pkg=@TARGET@
st=$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null)
if [ "$st" = "install ok installed" ]; then echo HLA_STAGE=already_installed
else
 out=$(apt_get install -y --no-install-recommends "$pkg" 2>&1); rc=$?; printf '%s\\n' "$out"; echo HLA_INSTALL_RC=$rc
 if [ $rc -ne 0 ] && printf '%s' "$out" | grep -Eq '404 +Not Found|Unable to locate package|has no installation candidate|Unable to fetch some archives|Failed to fetch'; then
  echo HLA_STAGE=refresh_index
  out=$(apt_get update 2>&1); urc=$?; printf '%s\\n' "$out" | tail -n 5; echo HLA_UPDATE_RC=$urc
  out=$(apt_get install -y --no-install-recommends "$pkg" 2>&1); rc=$?; printf '%s\\n' "$out"; echo HLA_RETRY_RC=$rc
 fi
fi
echo "HLA_VERIFY=$(dpkg-query -W -f='${Status}|${Version}' "$pkg" 2>/dev/null)"
dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx 'install ok installed'
"""
_RESTART = _PREAMBLE + """unit=@TARGET@
sudo -n systemctl restart "$unit"; echo HLA_RESTART_RC=$?
sleep 2
st=$(systemctl is-active "$unit"); echo "HLA_VERIFY=$st"
if [ "$st" != active ]; then sudo -n journalctl -u "$unit" -n 20 --no-pager 2>&1; exit 1; fi
"""
_CONTAINER_RESTART = _PREAMBLE + """c=@TARGET@
sudo -n docker restart "$c" >/dev/null; echo HLA_RESTART_RC=$?
sleep 2
st=$(sudo -n docker inspect -f '{{.State.Status}}' "$c" 2>&1); echo "HLA_VERIFY=$st"
if [ "$st" != running ]; then sudo -n docker logs --tail 20 "$c" 2>&1; exit 1; fi
"""
# Validate first, then pull and start; verified when every service of the project is running.
_COMPOSE_UP = _PREAMBLE + """cd @TARGET@ || exit 1
sudo -n docker compose config -q || { echo HLA_VERIFY=invalid_config; exit 1; }
sudo -n docker compose pull -q 2>&1 | tail -n 5
out=$(sudo -n docker compose up -d 2>&1); urc=$?; printf '%s\n' "$out" | tail -n 10; echo HLA_UP_RC=$urc
sleep 3
bad=$(sudo -n docker compose ps -a --format '{{.Name}} {{.State}}' | grep -v ' running$')
if [ -z "$bad" ]; then echo HLA_VERIFY=running; else echo HLA_VERIFY=not_running; printf '%s\n' "$bad"; exit 1; fi
"""

# First match wins; checked against the whole output so the model gets a reason, not just an exit code.
_ERROR_CLASSES = (
    ('lock', r'Could not get lock|Unable to acquire the dpkg frontend lock|dpkg frontend lock'),
    ('dpkg_interrupted', r'dpkg was interrupted'),
    ('disk_full', r'No space left on device|You don.t have enough free space'),
    ('network', r'Temporary failure resolving|Could not resolve|Connection timed out|Network is unreachable'),
    ('not_found', r'Unable to locate package|has no installation candidate'),
    ('sudo', r'sudo: a password is required|is not in the sudoers'),
)


def resolve(operation, target, level, compose_projects=()):
    """The script for an allowed operation at this effective level, or ValueError."""
    if not isinstance(operation, str) or not isinstance(target, str):
        raise ValueError('invalid_maintenance_parameters')
    if operation == 'install_package' and target in PACKAGES and level >= PACKAGES[target]:
        return _INSTALL.replace('@TARGET@', target)
    if operation == 'restart_service' and target in SERVICES and level >= SERVICES[target]:
        return _RESTART.replace('@TARGET@', target)
    if operation == 'restart_container' and _CONTAINER.match(target) and level >= CONTAINER_LEVEL:
        return _CONTAINER_RESTART.replace('@TARGET@', target)
    if operation == 'compose_up' and target in compose_projects and level >= COMPOSE_LEVEL:
        return _COMPOSE_UP.replace('@TARGET@', target)  # paths were validated by policy.update
    raise ValueError('policy_level_or_target_denied')


def outcome(operation, target, result):
    """Structured result: did the change actually take effect, and if not, why."""
    text = (result.get('stdout') or '') + '\n' + (result.get('stderr') or '')
    marks = dict(re.findall(r'^HLA_([A-Z_]+)=(.*)$', text, re.M))
    summary = {'operation': operation, 'target': target, 'retried_after_index_refresh': marks.get('STAGE') == 'refresh_index'}
    if operation == 'install_package':
        status, _, version = marks.get('VERIFY', '').partition('|')
        summary['verified'] = status == 'install ok installed'
        summary['already_installed'] = marks.get('STAGE') == 'already_installed'
        if summary['verified']: summary['version'] = version
    elif operation == 'restart_service':
        summary['verified'] = marks.get('VERIFY') == 'active'
        summary['service_state'] = marks.get('VERIFY') or None
    else:  # restart_container, compose_up
        summary['verified'] = marks.get('VERIFY') == 'running'
        summary['service_state'] = marks.get('VERIFY') or None
    if result.get('timed_out'):
        summary['error_class'] = 'timeout'
    elif not summary['verified'] and operation != 'install_package':
        summary['error_class'] = 'invalid_config' if marks.get('VERIFY') == 'invalid_config' else 'service_not_active'  # logs in stdout say why
    elif not summary['verified']:
        summary['error_class'] = next((name for name, pattern in _ERROR_CLASSES if re.search(pattern, text)), 'unknown')
    return summary
