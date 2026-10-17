"""
    Copyright (c) 2026 Martin Seener <martin@sysorchestra.com>
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
"""
import asyncio
import ipaddress
import json
import logging
import os
import secrets
import socket
import struct
import time

import dns.exception
import dns.flags
import dns.message
import dns.rcode

from .backend import BackendError
from .extract import extract
from .matcher import Matcher, normalize
from .store import Store

log = logging.getLogger('wildcardaliases')

# limit the number of queries processed concurrently, more are refused right away
MAX_INFLIGHT = 512
TCP_IDLE_TIMEOUT = 10
# concurrent TCP clients, and queries per TCP connection before it is closed
MAX_TCP_CLIENTS = 256
MAX_TCP_QUERIES = 100
# write learned addresses to disk this many seconds after learning, so a restart loses little
STATE_DELAY = 1.0


class UpstreamError(Exception):
    pass


def is_public(address):
    """ False for private, loopback, link-local, CGNAT, documentation, multicast, ... addresses """
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _is_reply(data, query_id, question):
    """ does data answer our query: same id, QR set and the same question (name case-insensitive) """
    if len(data) < 12 or data[:2] != query_id:
        return False
    try:
        reply = dns.message.from_wire(data, question_only=True)
    except dns.exception.DNSException:
        return False
    return bool(reply.flags & dns.flags.QR) and reply.question == question


class _UdpQuery(asyncio.DatagramProtocol):
    def __init__(self, wire, question, future):
        self.wire = wire
        self.question = question
        self.future = future
        self.query_id = wire[:2]

    def connection_made(self, transport):
        transport.sendto(self.wire)

    def datagram_received(self, data, addr):
        # ignore stray and forged packets, only accept the reply to exactly our query
        if not self.future.done() and _is_reply(data, self.query_id, self.question):
            self.future.set_result(data)

    def error_received(self, exc):
        if not self.future.done():
            self.future.set_exception(exc)


class Upstream:
    """ forward raw DNS messages to the configured upstream servers, in order """

    def __init__(self, servers, timeout):
        self.servers = servers
        self.timeout = timeout

    async def query(self, wire, question, tcp=False):
        """ forward wire (the client's query), returns the reply with the client's id restored

            :param question: the parsed question section of wire, replies must match it
        """
        client_id, errors = wire[:2], []
        for server in self.servers:
            # our own random id per attempt, the client's id is known to the client (and guessable)
            wire = secrets.token_bytes(2) + wire[2:]
            try:
                if tcp:
                    reply = await self._tcp(wire, question, server)
                else:
                    reply = await self._udp(wire, question, server)
                return client_id + reply[2:]
            except (OSError, asyncio.TimeoutError, UpstreamError) as exc:
                errors.append('%s:%d %s' % (server[0], server[1], exc.__class__.__name__))
        raise UpstreamError(', '.join(errors))

    async def _udp(self, wire, question, server):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpQuery(wire, question, future), remote_addr=server
        )
        try:
            return await asyncio.wait_for(future, self.timeout)
        finally:
            transport.close()

    async def _tcp(self, wire, question, server):
        async def exchange():
            reader, writer = await asyncio.open_connection(server[0], server[1])
            try:
                writer.write(struct.pack('!H', len(wire)) + wire)
                await writer.drain()
                length = struct.unpack('!H', await reader.readexactly(2))[0]
                reply = await reader.readexactly(length)
                if not _is_reply(reply, wire[:2], question):
                    raise UpstreamError('reply does not match the query')
                return reply
            finally:
                writer.close()
        try:
            return await asyncio.wait_for(exchange(), self.timeout)
        except asyncio.IncompleteReadError:
            raise UpstreamError('connection closed')


class _UdpServer(asyncio.DatagramProtocol):
    def __init__(self, server):
        self.server = server
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.server.spawn(self._answer(data, addr))

    async def _answer(self, data, addr):
        response = await self.server.handle(data, tcp=False)
        if response is not None and self.transport is not None:
            self.transport.sendto(response, addr)


class WildcardAliasesServer:

    def __init__(self, config, backend, clock=time.time):
        self.backend = backend
        self.clock = clock
        self.store = Store()
        self.config = None
        self.matcher = Matcher()
        self.upstream = None
        self.aliases = {}
        self.stats = {
            'queries': 0, 'matched': 0, 'learned': 0, 'upstream_errors': 0,
            'backend_errors': 0, 'servfail_sent': 0, 'private_ignored': 0, 'refused_overload': 0,
            'started': clock(),
        }
        self._servers = []
        self._tasks = set()
        self._inflight = asyncio.Semaphore(MAX_INFLIGHT)
        self._tcp_clients = 0
        self._lock = asyncio.Lock()
        self._housekeeping = None
        self._state_pending = None
        self._listeners_ok = True
        self.apply_config(config)

    # -- configuration -------------------------------------------------------------------

    def apply_config(self, config):
        """ (re)load configuration, returns the names of tables that are no longer configured """
        self.config = config
        self.aliases = {alias.name: alias for alias in config.aliases}
        self.matcher = Matcher((alias.name, alias.patterns) for alias in config.aliases)
        self.private_matcher = Matcher([('private', config.private_domains)])
        self.upstream = Upstream(config.upstreams, config.upstream_timeout)
        log.setLevel(logging.DEBUG if config.verbose else logging.INFO)
        return self.store.reconfigure({alias.name: alias.max_entries for alias in config.aliases})

    async def reload(self, config):
        async with self._lock:
            listen_changed = config.listen != self.config.listen
            removed = self.apply_config(config)
            for name in removed:
                # an unconfigured alias must not keep allowing traffic
                await self._safe(self.backend.replace(name, []))
                log.info('table %s no longer configured, flushed', name)
            for name, table in self.store.tables.items():
                await self._safe(self.backend.replace(name, sorted(table.entries)))
        if listen_changed:
            await self.restart_listeners()
        log.info('configuration reloaded (%d aliases)', len(self.aliases))

    # -- lifecycle -----------------------------------------------------------------------

    async def start(self, state=None):
        now = self.clock()
        if state:
            self.store.load_state(state, now)
            # aliases removed or disabled while we were not running must not keep allowing traffic
            if isinstance(state, dict):
                for name in state:
                    if name not in self.store.tables and isinstance(name, str):
                        await self._safe(self.backend.replace(name, []))
                        log.info('table %s no longer configured, flushed', name)
        # make the pf tables match our view, dropping whatever was left from a previous run
        for name, table in self.store.tables.items():
            await self._safe(self.backend.replace(name, sorted(table.entries)))
        await self.start_listeners()
        self._housekeeping = asyncio.ensure_future(self._housekeeping_loop())

    async def start_listeners(self):
        loop = asyncio.get_running_loop()
        for host, port in self.config.listen:
            family = socket.AF_INET6 if ':' in host else socket.AF_INET
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpServer(self), local_addr=(host, port), family=family
            )
            self._servers.append(transport)
            # bind tcp to the same port, which matters when the OS picked one (port 0)
            port = transport.get_extra_info('sockname')[1]
            tcp = await asyncio.start_server(self._tcp_client, host, port, family=family)
            self._servers.append(tcp)
            log.info('listening on %s port %d (udp/tcp)', host, port)

    async def restart_listeners(self):
        """ rebind to the configured addresses; on failure keep retrying from housekeeping """
        await self.stop_listeners()
        try:
            await self.start_listeners()
            if not self._listeners_ok:
                log.info('listeners restored')
            self._listeners_ok = True
        except OSError as exc:
            # don't keep half of the addresses: either all or none, retried every cleanup interval
            await self.stop_listeners()
            if self._listeners_ok:
                log.error('unable to listen on %s: %s, DNS is NOT served until this is fixed (retrying)',
                          ', '.join('%s:%d' % item for item in self.config.listen), exc)
            self._listeners_ok = False

    def listen_ports(self):
        """ actual bound (host, port) tuples, useful when listening on port 0 """
        result = []
        for server in self._servers:
            # not isinstance(DatagramTransport): before Python 3.12 the UDP transport isn't one
            if not isinstance(server, asyncio.AbstractServer):
                result.append(server.get_extra_info('sockname')[:2])
        return result

    async def stop_listeners(self):
        for server in self._servers:
            server.close()
            if isinstance(server, asyncio.AbstractServer):
                await server.wait_closed()
        self._servers = []

    async def stop(self):
        if self._housekeeping is not None:
            self._housekeeping.cancel()
            try:
                await self._housekeeping
            except asyncio.CancelledError:
                pass
        await self.stop_listeners()
        for task in list(self._tasks):
            task.cancel()
        if self._state_pending is not None:
            self._state_pending.cancel()
        self.write_state()
        self.write_status()

    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- query handling ------------------------------------------------------------------

    async def _tcp_client(self, reader, writer):
        if self._tcp_clients >= MAX_TCP_CLIENTS:
            # protect the file descriptors UDP needs as well
            writer.close()
            return
        self._tcp_clients += 1
        try:
            for _ in range(MAX_TCP_QUERIES):
                header = await asyncio.wait_for(reader.readexactly(2), TCP_IDLE_TIMEOUT)
                length = struct.unpack('!H', header)[0]
                data = await asyncio.wait_for(reader.readexactly(length), TCP_IDLE_TIMEOUT)
                response = await self.handle(data, tcp=True)
                if response is None:
                    break
                writer.write(struct.pack('!H', len(response)) + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            self._tcp_clients -= 1
            writer.close()

    @staticmethod
    def _refused(wire):
        try:
            response = dns.message.make_response(dns.message.from_wire(wire, question_only=True))
        except Exception:
            return None
        response.set_rcode(dns.rcode.REFUSED)
        return response.to_wire()

    @staticmethod
    def _servfail(query):
        response = dns.message.make_response(query)
        response.set_rcode(dns.rcode.SERVFAIL)
        return response.to_wire()

    async def handle(self, wire, tcp=False):
        """ process one query, returns the response wire format (or None to stay silent) """
        if self._inflight.locked():
            # overloaded (or the upstream hangs): answer at once, so clients try elsewhere, instead
            # of queueing without limit
            self.stats['refused_overload'] += 1
            return self._refused(wire)
        async with self._inflight:
            self.stats['queries'] += 1
            try:
                query = dns.message.from_wire(wire)
            except Exception:
                # dnspython raises DNSException for most garbage, but not for everything
                return None
            if query.flags & dns.flags.QR or len(query.question) != 1:
                return None
            try:
                return await self._resolve(wire, query, tcp)
            except Exception:
                # a bug must not leave the client waiting for its timeout
                log.exception('error handling %s', normalize(query.question[0].name))
                self.stats['servfail_sent'] += 1
                return self._servfail(query)

    async def _resolve(self, wire, query, tcp):
        qname = query.question[0].name
        tables = self.matcher.match(qname)

        try:
            response_wire = await self.upstream.query(wire, query.question, tcp=tcp)
        except UpstreamError as exc:
            self.stats['upstream_errors'] += 1
            self.stats['servfail_sent'] += 1
            log.warning('upstream failure for %s: %s', normalize(qname), exc)
            return self._servfail(query)

        if not tables:
            return response_wire

        self.stats['matched'] += 1
        try:
            response = dns.message.from_wire(response_wire)
        except dns.exception.DNSException:
            log.warning('unparsable upstream response for %s', normalize(qname))
            return self._servfail(query) if self.config.fail_closed else response_wire

        learned = self._filter_private(extract(response, qname), qname)
        if learned and not await self.learn(tables, learned, normalize(qname)):
            if self.config.fail_closed:
                self.stats['servfail_sent'] += 1
                return self._servfail(query)
        return response_wire

    def _filter_private(self, learned, qname):
        """ drop private and reserved addresses unless the configuration allows them for qname """
        mode = self.config.private_addresses
        if not learned or mode == 'allow' or (mode == 'domains' and self.private_matcher.match(qname)):
            return learned
        dropped = set()
        for family in (learned.ipv4, learned.ipv6):
            private = {address for address in family if not is_public(address)}
            family -= private
            dropped |= private
        if dropped:
            self.stats['private_ignored'] += len(dropped)
            log.info('ignored private address(es) %s for %s', ', '.join(sorted(dropped)), normalize(qname))
        return learned

    async def learn(self, tables, learned, qname=''):
        """ add learned addresses to the matching tables, returns False when pf could not be updated """
        # serialize with expiry/reload, otherwise a pending pf delete could race a fresh add
        async with self._lock:
            return await self._learn(tables, learned, qname)

    async def _learn(self, tables, learned, qname):
        now = self.clock()
        expires = now + self.config.hold_time(learned.ttl)
        success = True
        for name in sorted(tables):
            alias = self.aliases.get(name)
            if alias is None:
                continue
            addresses = set()
            if alias.inet:
                addresses.update(learned.ipv4)
            if alias.inet6:
                addresses.update(learned.ipv6)
            if not addresses:
                continue
            table = self.store.get(name)
            new, evicted = table.learn(sorted(addresses), expires, now, name=qname)
            try:
                if new:
                    await self.backend.add(name, new)
                    self.stats['learned'] += len(new)
                    self.schedule_state()
                    log.debug('%s: learned %s from %s', name, ', '.join(new), qname)
                if evicted:
                    await self.backend.delete(name, evicted)
                    log.warning('%s: table full (%d), evicted %d entries', name, alias.max_entries, len(evicted))
            except BackendError as exc:
                # forget what we could not install, so the next query retries
                table.forget(new)
                self.stats['backend_errors'] += 1
                log.error('%s: %s', name, exc)
                success = False
        return success

    # -- housekeeping --------------------------------------------------------------------

    async def _safe(self, coro):
        try:
            await coro
            return True
        except BackendError as exc:
            self.stats['backend_errors'] += 1
            log.error('%s', exc)
            return False

    async def expire(self):
        now = self.clock()
        for name, table in self.store.tables.items():
            expired = table.expire(now)
            if expired:
                if await self._safe(self.backend.delete(name, expired)):
                    log.debug('%s: expired %d entries', name, len(expired))
                else:
                    # keep them, so we try to remove them again next round
                    for address in expired:
                        table.entries[address] = now

    def schedule_state(self):
        """ write the state soon, batching the writes of a burst of learned addresses """
        if self._state_pending is None:
            def flush():
                self._state_pending = None
                self.write_state()
            self._state_pending = asyncio.get_running_loop().call_later(STATE_DELAY, flush)

    async def _housekeeping_loop(self):
        last_state = self.clock()
        while True:
            await asyncio.sleep(self.config.cleanup_interval)
            try:
                if not self._listeners_ok:
                    await self.restart_listeners()
                async with self._lock:
                    await self.expire()
                self.write_status()
                if self.clock() - last_state >= self.config.state_interval:
                    self.write_state()
                    last_state = self.clock()
            except Exception:
                log.exception('housekeeping failed')

    def status(self):
        now = self.clock()
        return {
            'pid': os.getpid(),
            'time': now,
            'stats': dict(self.stats),
            'tables': {
                name: dict(table.status(now), patterns=list(self.aliases[name].patterns))
                for name, table in self.store.tables.items() if name in self.aliases
            },
        }

    @staticmethod
    def _write_json(path, data, mode=0o644):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, mode=0o755, exist_ok=True)
        tmp = '%s.tmp' % path
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        # the file may exist from an earlier version with wider permissions
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as fhandle:
            json.dump(data, fhandle)
        os.replace(tmp, path)

    def write_status(self):
        try:
            self._write_json(self.config.status_file, self.status())
        except OSError as exc:
            log.warning('unable to write status: %s', exc)

    def write_state(self):
        try:
            # the learned addresses are root's business only
            self._write_json(self.config.state_file, self.store.to_state(), mode=0o600)
        except OSError as exc:
            log.warning('unable to write state: %s', exc)
