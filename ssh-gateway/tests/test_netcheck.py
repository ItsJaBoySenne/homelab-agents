"""Address rules for outside calls (gateway/netcheck.py), shared by HTTP tools and MCP servers.

python3 tests/test_netcheck.py   (no network)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import ipaddress
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import http_tools, mcp, netcheck  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

ap = netcheck.address_problem

# Embedded IPv4 addresses are unwrapped.
emb = lambda a: [str(x) for x in netcheck.embedded_ipv4(ipaddress.ip_address(a))]
check(emb('::ffff:10.1.2.3') == ['10.1.2.3'], 'IPv4-mapped')
check(emb('::10.1.2.3') == ['10.1.2.3'], 'IPv4-compatible')
check(emb('::ffff:0:10.1.2.3') == ['10.1.2.3'], 'SIIT')
check(emb('64:ff9b::10.1.2.3') == ['10.1.2.3'], 'NAT64 well-known prefix')
check('10.1.2.3' in emb('64:ff9b:1::10.1.2.3') and '10.1.2.3' in emb('64:ff9b:1:a01:2:300::'), 'NAT64 local-use prefix (/96 and /48 layouts)')
check(emb('2002:0a01:0203::1') == ['10.1.2.3'], '6to4')
check(emb('2001:0:4136:e378:8000:63bf:f5fe:fdfc')[1] == '10.1.2.3', 'Teredo: the client address is the inverted last 32 bits')
check(emb('2606:4700::1111') == [] and emb('8.8.8.8') == [], 'plain addresses carry nothing')

# Always refused, also with private addresses allowed.
REFUSED = ['127.0.0.1', '::1', '0.0.0.0', '::', '169.254.169.254', '169.254.1.1', 'fe80::1', 'fd00:ec2::254', '100.100.100.200',
           '224.0.0.1', 'ff02::1', '240.0.0.1', '255.255.255.255', 'fec0::1', '192.0.2.1', '198.51.100.1', '203.0.113.1', '198.18.0.1',
           '198.19.255.1', '192.0.0.8', '2001:db8::1',
           '::ffff:127.0.0.1', '::127.0.0.1', '::ffff:0:127.0.0.1', '64:ff9b::127.0.0.1', '64:ff9b::a9fe:a9fe', '64:ff9b:1::a9fe:a9fe',
           '2002:7f00:0001::1', '2002:a9fe:a9fe::1', '2001:0:4136:e378:8000:63bf:80ff:fffe', '::ffff:169.254.169.254', '::ffff:192.0.2.1']
for addr in REFUSED:
    check(ap(addr, True) is not None, 'refused with allow_private: ' + addr)
# Private ranges only with allow_private.
PRIVATE = ['10.0.0.1', '172.16.5.4', '192.168.1.1', '100.64.0.1', 'fd12::1', 'fc00::1', '::ffff:10.0.0.1', '2002:c0a8:0101::1',
           '64:ff9b::192.168.1.1', '2001:0:4136:e378:8000:63bf:f5fe:fdfc']
for addr in PRIVATE:
    check(ap(addr, False) == netcheck.PRIVATE_REASON and ap(addr, True) is None, 'private only with allow_private: ' + addr)
for addr in ('8.8.8.8', '2606:4700::1111', '64:ff9b::808:808', '2002:0808:0808::1', '93.184.216.34'):
    check(ap(addr, False) is None, 'public: ' + addr)
check(ap('not-an-ip', True) == 'not an IP address', 'garbage is refused')
check(ap('fe80::1%eth0', True) is not None, 'zone ids are stripped and still refused')
check(netcheck.is_private('::ffff:192.168.1.5') and not netcheck.is_private('8.8.8.8'), 'is_private looks inside wrappers')

# The gateway's own addresses.
netcheck._own.update(at=10 ** 12, addresses=frozenset({ipaddress.ip_address('172.20.0.5')}))
check(ap('172.20.0.5', True) == "the gateway's own address" and ap('::ffff:172.20.0.5', True), "the gateway's own address is refused")
check(ap('172.20.0.6', True) is None, 'its neighbours follow the private rule')
netcheck._own.update(at=0.0, addresses=frozenset())

# Host names of this stack, refused before any lookup.
for host in ('ssh-gateway-api', 'agent-router', 'database', 'queue-worker', 'localhost', 'LOCALHOST.', 'x.localhost', 'ssh-gateway', 'backup'):
    check(netcheck.host_problem(host), 'stack host refused: ' + host)
check(netcheck.host_problem('api.example.com') is None, 'other names pass')

# DNS order kept (no sorting), duplicates dropped, IPv4 tried second.
real = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, **k: [(socket.AF_INET6, 1, 6, '', ('2606:4700::2', port, 0, 0)), (socket.AF_INET, 1, 6, '', ('93.1.1.9', port)),
                                              (socket.AF_INET, 1, 6, '', ('93.1.1.1', port)), (socket.AF_INET, 1, 6, '', ('93.1.1.9', port))]
check(netcheck.resolve('x.test', 443) == ['2606:4700::2', '93.1.1.9', '93.1.1.1'], 'resolve keeps DNS order without duplicates')
socket.getaddrinfo = real
check(netcheck.connect_order(['2606:4700::2', '2606:4700::3', '93.1.1.9']) == ['2606:4700::2', '93.1.1.9', '2606:4700::3'],
      'connect order: the first answer, then the first IPv4 one')

# Both callers use the same rules.
for addr in REFUSED + PRIVATE:
    check((http_tools.check_address(addr, False) is None) == (mcp.address_problem(addr, False) is None) is False,
          'HTTP tools and MCP agree: ' + addr)

print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
