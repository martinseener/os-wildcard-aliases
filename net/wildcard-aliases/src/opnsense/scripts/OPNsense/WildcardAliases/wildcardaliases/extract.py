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
from dataclasses import dataclass, field

import dns.rcode
import dns.rdataclass
import dns.rdatatype
from dns.rdtypes.svcbbase import ParamKey

# upper bound for CNAME chains we are willing to follow
MAX_CHAIN = 16


@dataclass
class Learned:
    ipv4: set = field(default_factory=set)
    ipv6: set = field(default_factory=set)
    ttl: int = 0

    def __bool__(self):
        return bool(self.ipv4 or self.ipv6)


def extract(response, qname):
    """ collect the addresses a client may connect to after receiving this response.

        Only records that belong to the answer chain of qname are used (qname itself and
        the CNAME targets reachable from it), unrelated records in the answer section are
        ignored. Besides A/AAAA, the ipv4hint/ipv6hint parameters of HTTPS/SVCB records are
        included, as clients may connect to those directly.

        :param response: dns.message.Message
        :param qname: dns.name.Name as asked by the client
        :return: Learned (falsy when nothing was found)
    """
    result = Learned()
    if response.rcode() != dns.rcode.NOERROR:
        return result

    # index CNAMEs by owner, don't rely on dnspython's lookup index (only filled for some code paths)
    cnames = {}
    for rrset in response.answer:
        if rrset.rdtype == dns.rdatatype.CNAME and rrset.rdclass == dns.rdataclass.IN and len(rrset):
            cnames.setdefault(rrset.name, rrset)

    chain = {qname}
    ttls = []
    current = qname
    for _ in range(MAX_CHAIN):
        cname = cnames.get(current)
        if cname is None:
            break
        ttls.append(cname.ttl)
        current = cname[0].target
        if current in chain:
            # CNAME loop, stop following
            break
        chain.add(current)

    for rrset in response.answer:
        if rrset.name not in chain or rrset.rdclass != dns.rdataclass.IN:
            continue
        found = False
        if rrset.rdtype == dns.rdatatype.A:
            result.ipv4.update(rdata.address for rdata in rrset)
            found = True
        elif rrset.rdtype == dns.rdatatype.AAAA:
            result.ipv6.update(rdata.address for rdata in rrset)
            found = True
        elif rrset.rdtype in (dns.rdatatype.HTTPS, dns.rdatatype.SVCB):
            for rdata in rrset:
                hint4 = rdata.params.get(ParamKey.IPV4HINT)
                hint6 = rdata.params.get(ParamKey.IPV6HINT)
                if hint4 is not None:
                    result.ipv4.update(hint4.addresses)
                    found = True
                if hint6 is not None:
                    result.ipv6.update(hint6.addresses)
                    found = True
        if found:
            ttls.append(rrset.ttl)

    if result:
        # caches in front of us keep the answer as long as its longest lived record, so do we
        result.ttl = max(ttls) if ttls else 0
    return result
