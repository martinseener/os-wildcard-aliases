import asyncio
import json
import socket
import struct

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

from wildcardaliases.backend import BackendError, MemoryBackend
from wildcardaliases.config import parse
from wildcardaliases import server as server_module
from wildcardaliases.server import WildcardAliasesServer

RECORDS = {
    'acct.blob.core.windows.net.': [
        'acct.blob.core.windows.net. 60 IN CNAME blob.ams07.store.core.windows.net.',
        'blob.ams07.store.core.windows.net. 120 IN A 20.60.0.1',
        'blob.ams07.store.core.windows.net. 120 IN AAAA 2603:1020::1',
    ],
    'www.example.org.': ['www.example.org. 300 IN A 93.184.215.14'],
}


class SlowBackend(MemoryBackend):
    """ slow pf updates, to prove the answer is only sent after the table was updated """

    async def add(self, table, addresses):
        await asyncio.sleep(0.05)
        await super().add(table, addresses)


class FailingBackend(MemoryBackend):
    async def add(self, table, addresses):
        raise BackendError('pfctl exploded')


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def udp_query(port, name, rdtype='A'):
    loop = asyncio.get_running_loop()
    query = dns.message.make_query(name, rdtype)
    future = loop.create_future()

    class Proto(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            transport.sendto(query.to_wire())

        def datagram_received(self, data, addr):
            future.set_result(data)

    transport, _ = await loop.create_datagram_endpoint(Proto, remote_addr=('127.0.0.1', port))
    try:
        return dns.message.from_wire(await asyncio.wait_for(future, 3))
    finally:
        transport.close()


async def tcp_query(port, name):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    wire = dns.message.make_query(name, 'A').to_wire()
    writer.write(struct.pack('!H', len(wire)) + wire)
    await writer.drain()
    length = struct.unpack('!H', await reader.readexactly(2))[0]
    data = await reader.readexactly(length)
    writer.close()
    return dns.message.from_wire(data)


def make_config(upstream_port, tmp_path, **extra):
    data = {
        'listen': ['127.0.0.1:0'],
        'upstreams': ['127.0.0.1:%d' % upstream_port],
        'upstream_timeout': 0.5,
        'aliases': [
            {'name': 'azure', 'patterns': ['*.core.windows.net'], 'inet': True, 'inet6': True},
            {'name': 'azure4', 'patterns': ['*.blob.core.windows.net'], 'inet': True, 'inet6': False},
        ],
        'state_file': str(tmp_path / 'state.json'),
        'status_file': str(tmp_path / 'status.json'),
        'cleanup_interval': 3600,
    }
    data.update(extra)
    return parse(data)


@pytest.fixture
async def env(fake_upstream_cls, tmp_path):
    started = []

    async def factory(backend=None, records=RECORDS, clock=None, **extra):
        upstream = fake_upstream_cls(records)
        await upstream.start()
        server = WildcardAliasesServer(make_config(upstream.port, tmp_path, **extra),
                                       backend or MemoryBackend(), clock=clock or Clock())
        await server.start()
        started.append((upstream, server))
        return upstream, server, server.listen_ports()[0][1]

    yield factory
    for upstream, server in started:
        await server.stop()
        await upstream.stop()


async def test_learns_before_answering(env):
    backend = SlowBackend()
    _, server, port = await env(backend=backend)
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    # at the moment the client has the answer, pf already knows the addresses
    assert backend.tables['azure'] == {'20.60.0.1', '2603:1020::1'}
    assert backend.tables['azure4'] == {'20.60.0.1'}
    assert any(rrset.rdtype == dns.rdatatype.A for rrset in resp.answer)
    assert server.stats['learned'] == 3


async def test_non_matching_is_passed_through(env):
    upstream, server, port = await env()
    resp = await udp_query(port, 'www.example.org')
    assert resp.answer[0][0].address == '93.184.215.14'
    assert server.backend.tables.get('azure', set()) == set()
    assert server.stats['matched'] == 0
    assert upstream.queries == [('www.example.org.', 'udp')]


async def test_tcp_is_forwarded_over_tcp(env):
    upstream, server, port = await env()
    resp = await tcp_query(port, 'acct.blob.core.windows.net')
    assert resp.rcode() == dns.rcode.NOERROR
    assert upstream.queries == [('acct.blob.core.windows.net.', 'tcp')]
    assert '20.60.0.1' in server.backend.tables['azure']


async def test_repeated_queries_only_touch_pf_once(env):
    _, server, port = await env()
    await udp_query(port, 'acct.blob.core.windows.net')
    await udp_query(port, 'acct.blob.core.windows.net')
    adds = [call for call in server.backend.calls if call[0] == 'add']
    assert len(adds) == 2  # one per table, not per query


async def test_expiry_removes_entries(env):
    clock = Clock()
    _, server, port = await env(clock=clock, min_hold=100, grace=0)
    await udp_query(port, 'acct.blob.core.windows.net')
    # longest TTL in the chain is 120s, which beats min_hold
    clock.now += 119
    await server.expire()
    assert server.backend.tables['azure'] == {'20.60.0.1', '2603:1020::1'}
    clock.now += 2
    await server.expire()
    assert server.backend.tables['azure'] == set()
    assert ('delete', 'azure4', ['20.60.0.1']) in server.backend.calls


async def test_backend_failure_fail_closed(env):
    _, server, port = await env(backend=FailingBackend())
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    assert resp.rcode() == dns.rcode.SERVFAIL
    # nothing remembered, the next query retries
    assert server.store.get('azure').entries == {}
    assert server.stats['backend_errors'] >= 1


async def test_backend_failure_fail_open(env):
    _, server, port = await env(backend=FailingBackend(), fail_mode='open')
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    assert resp.rcode() == dns.rcode.NOERROR


async def test_upstream_down_servfail(fake_upstream_cls, tmp_path):
    cfg = make_config(1, tmp_path)  # nothing listens on port 1
    server = WildcardAliasesServer(cfg, MemoryBackend())
    await server.start()
    try:
        resp = await udp_query(server.listen_ports()[0][1], 'www.example.org')
        assert resp.rcode() == dns.rcode.SERVFAIL
        assert server.stats['upstream_errors'] == 1
    finally:
        await server.stop()


async def test_truncated_answer_is_passed_to_client(fake_upstream_cls, tmp_path):
    upstream = fake_upstream_cls(RECORDS, truncate=True)
    await upstream.start()
    server = WildcardAliasesServer(make_config(upstream.port, tmp_path), MemoryBackend())
    await server.start()
    try:
        resp = await udp_query(server.listen_ports()[0][1], 'acct.blob.core.windows.net')
        assert resp.flags & dns.flags.TC
        assert server.backend.tables.get('azure', set()) == set()
    finally:
        await server.stop()
        await upstream.stop()


async def test_garbage_is_ignored(env):
    _, server, port = await env()
    assert await server.handle(b'\x00\x01garbage') is None
    response = dns.message.make_response(dns.message.make_query('x.example.org', 'A'))
    assert await server.handle(response.to_wire()) is None  # QR set, not a query


async def test_restart_restores_state_and_reload_flushes_removed(env, tmp_path):
    clock = Clock()
    upstream, server, port = await env(clock=clock)
    await udp_query(port, 'acct.blob.core.windows.net')
    server.write_state()
    state = json.loads((tmp_path / 'state.json').read_text())
    assert '20.60.0.1' in state['azure']

    # a fresh daemon replaces the pf tables with the saved, unexpired state
    backend = MemoryBackend()
    backend.tables['azure'] = {'198.51.100.99'}  # stale entry from an older run
    server2 = WildcardAliasesServer(server.config, backend, clock=clock)
    await server2.start(state)
    try:
        assert backend.tables['azure'] == {'20.60.0.1', '2603:1020::1'}

        # removing an alias from the configuration flushes its table
        cfg = make_config(upstream.port, tmp_path, aliases=[
            {'name': 'azure', 'patterns': ['*.core.windows.net']},
        ])
        await server2.reload(cfg)
        assert backend.tables['azure4'] == set()
        assert 'azure4' not in server2.store.tables
    finally:
        await server2.stop()


async def test_start_flushes_tables_unconfigured_while_stopped(fake_upstream_cls, tmp_path):
    cfg = make_config(1, tmp_path, aliases=[{'name': 'azure', 'patterns': ['*.core.windows.net']}])
    backend = MemoryBackend()
    backend.tables['old_obj'] = {'192.0.2.1'}
    server = WildcardAliasesServer(cfg, backend, clock=Clock())
    await server.start({'old_obj': {'192.0.2.1': 99999}, 'azure': {}})
    try:
        assert backend.tables['old_obj'] == set()
    finally:
        await server.stop()


async def test_status_file(env, tmp_path):
    _, server, port = await env()
    await udp_query(port, 'acct.blob.core.windows.net')
    server.write_status()
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['tables']['azure']['entries'] == 2
    assert status['tables']['azure']['patterns'] == ['*.core.windows.net']
    assert status['stats']['queries'] == 1


# -- hardening (0.3) ----------------------------------------------------------------------


async def test_upstream_gets_own_random_id_client_gets_its_own_back(env):
    upstream, _, port = await env()
    query = dns.message.make_query('www.example.org', 'A')
    query.id = 4242
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    class Proto(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            transport.sendto(query.to_wire())

        def datagram_received(self, data, addr):
            future.set_result(data)

    transport, _ = await loop.create_datagram_endpoint(Proto, remote_addr=('127.0.0.1', port))
    try:
        reply = dns.message.from_wire(await asyncio.wait_for(future, 3))
    finally:
        transport.close()
    assert reply.id == 4242
    assert upstream.ids[-1] != 4242


def _forge(query_wire, name, address, same_id=True):
    """ a reply an attacker would race in: same or wrong id, possibly for another question """
    query = dns.message.from_wire(query_wire)
    forged = dns.message.make_response(dns.message.make_query(name, 'A'))
    forged.id = query.id if same_id else (query.id + 1) % 65536
    forged.answer.append(dns.rrset.from_text(name, 60, 'IN', 'A', address))
    return forged.to_wire()


async def test_forged_udp_replies_are_ignored(env):
    upstream, server, port = await env()
    # first a reply with a wrong id, then one with the right id for a different question,
    # then the genuine reply: only the last one may be accepted
    upstream.tamper = lambda query, reply: [
        _forge(query, 'acct.blob.core.windows.net.', '198.51.100.66', same_id=False),
        _forge(query, 'other.blob.core.windows.net.', '198.51.100.77'),
        reply,
    ]
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    addresses = {rdata.address for rrset in resp.answer if rrset.rdtype == dns.rdatatype.A for rdata in rrset}
    assert addresses == {'20.60.0.1'}
    assert server.backend.tables['azure4'] == {'20.60.0.1'}


async def test_only_forged_replies_lead_to_servfail(env):
    upstream, server, port = await env()
    upstream.tamper = lambda query, reply: [_forge(query, 'other.blob.core.windows.net.', '198.51.100.77')]
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    assert resp.rcode() == dns.rcode.SERVFAIL
    assert server.backend.tables.get('azure4', set()) == set()


async def test_unexpected_error_answers_servfail(env, monkeypatch):
    _, server, port = await env()

    def broken(response, qname):
        raise RuntimeError('bug')
    monkeypatch.setattr(server_module, 'extract', broken)
    resp = await udp_query(port, 'acct.blob.core.windows.net')
    assert resp.rcode() == dns.rcode.SERVFAIL
    # and the daemon keeps working for other names
    assert (await udp_query(port, 'www.example.org')).rcode() == dns.rcode.NOERROR


async def test_state_is_written_soon_after_learning(env, monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, 'STATE_DELAY', 0.05)
    _, server, port = await env()
    await udp_query(port, 'acct.blob.core.windows.net')
    await asyncio.sleep(0.2)
    state = json.loads((tmp_path / 'state.json').read_text())
    assert set(state['azure4']) == {'20.60.0.1'}
    # learned addresses are not world readable
    assert (tmp_path / 'state.json').stat().st_mode & 0o777 == 0o600


async def test_listen_failure_on_reload_is_retried(env, tmp_path):
    upstream, server, port = await env(cleanup_interval=1)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(('127.0.0.1', 0))
    busy = blocker.getsockname()[1]
    await server.reload(make_config(upstream.port, tmp_path, listen=['127.0.0.1:%d' % busy], cleanup_interval=1))
    assert not server._listeners_ok and server.listen_ports() == []
    blocker.close()
    await asyncio.sleep(1.5)
    assert server._listeners_ok
    assert (await udp_query(busy, 'www.example.org')).rcode() == dns.rcode.NOERROR
