"""Which addresses the gateway may connect to for owner-defined outside calls (HTTP tools, MCP servers).

One rule set for every caller, so a trick that one check misses cannot slip through another:
  - IPv6 forms that carry an IPv4 address are unwrapped and the IPv4 address is checked: IPv4-mapped (::ffff:a.b.c.d),
    IPv4-compatible (::a.b.c.d), SIIT (::ffff:0:a.b.c.d), NAT64 (64:ff9b::/96 and 64:ff9b:1::/48), 6to4 (2002::/16)
    and Teredo (2001::/32: the server address and the client address, which is the inverted last 32 bits).
  - Always refused: loopback, unspecified, link-local, cloud metadata (169.254.0.0/16, fd00:ec2::/32, 100.100.100.200),
    multicast, broadcast and other reserved ranges, IPv6 site-local (fec0::/10), documentation and benchmark ranges
    (192.0.2/24, 198.51.100/24, 203.0.113/24, 198.18/15, 192.0.0/24, 2001:db8::/32), and the gateway's own addresses.
  - Private ranges (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7) only when the caller allows private addresses.
  - Host names of this stack (the compose services, localhost) are refused before any lookup, even with private allowed.
"""
import ipaddress
import socket
import threading
import time

_V4_REFUSED = [ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '100.100.100.200/32', '192.0.0.0/24', '192.0.2.0/24', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4')]
_V6_REFUSED = [ipaddress.ip_network(n) for n in (
    '::/128', '::1/128', 'fe80::/10', 'fec0::/10', 'ff00::/8', 'fd00:ec2::/32', '2001:db8::/32', '100::/64')]
_PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', 'fc00::/7')]
_COMPAT = ipaddress.ip_network('::/96')
_SIIT = ipaddress.ip_network('::ffff:0:0:0/96')
_NAT64 = ipaddress.ip_network('64:ff9b::/96')
_NAT64_LOCAL = ipaddress.ip_network('64:ff9b:1::/48')
_6TO4 = ipaddress.ip_network('2002::/16')
_TEREDO = ipaddress.ip_network('2001::/32')
# The services of this stack (compose files) and loopback names: never a target of an outside call.
STACK_HOSTS = {'ssh-gateway-api', 'ssh-gateway', 'agent-router', 'queue-worker', 'database', 'backup', 'localhost'}
_own = {'at': 0.0, 'addresses': frozenset()}
_own_lock = threading.Lock()

LOOPBACK_REASON = 'loopback, link-local or metadata address'
PRIVATE_REASON = 'private address'


def _v4(value):
    return ipaddress.IPv4Address(value & 0xFFFFFFFF)


def embedded_ipv4(ip):
    """The IPv4 addresses an IPv6 address carries (empty for plain IPv6 or IPv4 input)."""
    if ip.version != 6:
        return []
    n = int(ip)
    if ip.ipv4_mapped:
        return [ip.ipv4_mapped]
    if ip in _SIIT:
        return [_v4(n)]
    if ip in _COMPAT and n > 1:  # ::a.b.c.d (:: and ::1 are checked as IPv6)
        return [_v4(n)]
    if ip in _NAT64:
        return [_v4(n)]
    if ip in _NAT64_LOCAL:
        # RFC 6052 /48 layout (bits 48-63 and 72-87) and the common /96 layout: both are checked.
        return [_v4(((n >> 64) & 0xFFFF) << 16 | ((n >> 40) & 0xFFFF)), _v4(n)]
    if ip in _6TO4:
        return [_v4(n >> 80)]
    if ip in _TEREDO:
        return [_v4(n >> 64), _v4(~n)]
    return []


def own_addresses():
    """The gateway container's own addresses (best effort, cached for a minute)."""
    with _own_lock:
        if time.time() - _own['at'] < 60:
            return _own['addresses']
        found = set()
        try:
            name = socket.gethostname()
            _, _, addresses = socket.gethostbyname_ex(name)
            found.update(addresses)
            found.update(info[4][0] for info in socket.getaddrinfo(name, None))
        except (OSError, UnicodeError):
            pass
        clean = set()
        for a in found:
            try:
                clean.add(ipaddress.ip_address(a.split('%')[0]))
            except ValueError:
                pass
        _own.update(at=time.time(), addresses=frozenset(clean))
        return _own['addresses']


_METADATA = [ipaddress.ip_network(n) for n in ('169.254.0.0/16', '100.100.100.200/32', 'fd00:ec2::/32')]


def _single(ip, allow_private, allow_loopback):
    if ip.is_loopback and allow_loopback:  # tests only: their fake servers listen on 127.0.0.1
        return None if allow_private else PRIVATE_REASON
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or any(ip in n for n in _METADATA):
        return LOOPBACK_REASON
    if ip.is_multicast or any(ip in n for n in (_V4_REFUSED if ip.version == 4 else _V6_REFUSED)):
        return 'reserved, multicast or documentation address'
    if any(ip in n for n in _PRIVATE):
        return None if allow_private else PRIVATE_REASON
    if ip.is_reserved or not ip.is_global:
        return 'reserved address'
    return None


def address_problem(address, allow_private=False, allow_loopback=False):
    """None when the gateway may connect to this address, else a short reason (never raises for bad input)."""
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0].strip('[]'))
    except ValueError:
        return 'not an IP address'
    inner = embedded_ipv4(ip)
    for candidate in inner or [ip]:
        problem = _single(candidate, allow_private, allow_loopback)
        if problem:
            return problem
    if ip in own_addresses() or any(c in own_addresses() for c in inner):
        return 'the gateway\'s own address'
    return None


def is_private(address, allow_loopback=False):
    """A private (LAN/VPN) address, also inside an IPv6 wrapper: plain http may go there."""
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0].strip('[]'))
    except ValueError:
        return False
    return any(any(c in n for n in _PRIVATE) or (allow_loopback and c.is_loopback) for c in embedded_ipv4(ip) or [ip])


def host_problem(host):
    """A reason when a host name belongs to this stack (checked before resolving), else None."""
    name = str(host or '').strip().rstrip('.').lower()
    if name in STACK_HOSTS or name.endswith('.localhost'):
        return 'a host of this stack or loopback name'
    return None


def resolve(host, port):
    """The addresses of host in getaddrinfo order, without duplicates. Raises OSError/UnicodeError."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def connect_order(addresses):
    """Checked addresses in the order to try: getaddrinfo order, but the first IPv4 address right after the first one."""
    first = addresses[:1]
    v4 = [a for a in addresses[1:] if ':' not in a][:1]
    return first + v4 + [a for a in addresses[1:] if a not in v4]
