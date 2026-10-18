import asyncio
import os
import struct
import sys

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

SCRIPTS = os.path.join(
    os.path.dirname(__file__), '..', 'net', 'wildcard-aliases', 'src',
    'opnsense', 'scripts', 'OPNsense', 'WildcardAliases'
)
sys.path.insert(0, os.path.abspath(SCRIPTS))


class FakeUpstream:
    """ minimal authoritative-ish DNS server answering from a static record list

        records: dict qname -> list of rrset text lines, e.g. "x.example. 300 IN A 192.0.2.1"
        the full list is placed in the answer section (so CNAME chains can be modelled)
    """

    def __init__(self, records=None, rcodes=None, truncate=False):
        self.records = records or {}
        self.rcodes = rcodes or {}
        self.truncate = truncate
        self.queries = []
        self.ids = []
        self.qtypes = []
        # optional hook (query wire, reply wire) -> list of datagrams to send instead of the reply
        self.tamper = None
        self.udp = None
        self.tcp = None
        self.port = None

    def answer(self, wire, tcp):
        query = dns.message.from_wire(wire)
        qname = query.question[0].name.to_text().lower()
        self.queries.append((qname, 'tcp' if tcp else 'udp'))
        self.ids.append(query.id)
        self.qtypes.append((qname, dns.rdatatype.to_text(query.question[0].rdtype)))
        response = dns.message.make_response(query)
        if qname in self.rcodes:
            response.set_rcode(self.rcodes[qname])
        elif self.truncate and not tcp:
            response.flags |= dns.flags.TC
        else:
            for line in self.records.get(qname, []):
                name, ttl, rdclass, rdtype, rdata = line.split(None, 4)
                response.answer.append(dns.rrset.from_text(name, ttl, rdclass, rdtype, rdata))
        return response.to_wire()

    async def start(self):
        loop = asyncio.get_running_loop()
        upstream = self

        class Udp(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                reply = upstream.answer(data, False)
                for datagram in (upstream.tamper(data, reply) if upstream.tamper else [reply]):
                    self.transport.sendto(datagram, addr)

        self.udp, _ = await loop.create_datagram_endpoint(Udp, local_addr=('127.0.0.1', 0))
        self.port = self.udp.get_extra_info('sockname')[1]

        async def tcp_client(reader, writer):
            try:
                while True:
                    length = struct.unpack('!H', await reader.readexactly(2))[0]
                    data = await reader.readexactly(length)
                    out = upstream.answer(data, True)
                    writer.write(struct.pack('!H', len(out)) + out)
                    await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()

        self.tcp = await asyncio.start_server(tcp_client, '127.0.0.1', self.port)

    async def stop(self):
        self.udp.close()
        self.tcp.close()
        await self.tcp.wait_closed()


@pytest.fixture
def fake_upstream_cls():
    return FakeUpstream
