import dns.message
import dns.name
import dns.rcode
import dns.rrset

from wildcardaliases.extract import extract


def response(qname, *lines, rcode=dns.rcode.NOERROR):
    query = dns.message.make_query(qname, 'A')
    resp = dns.message.make_response(query)
    resp.set_rcode(rcode)
    for line in lines:
        name, ttl, rdclass, rdtype, rdata = line.split(None, 4)
        resp.answer.append(dns.rrset.from_text(name, ttl, rdclass, rdtype, rdata))
    return resp, dns.name.from_text(qname)


def test_direct_a_and_aaaa():
    resp, qname = response(
        'x.example.com',
        'x.example.com. 300 IN A 192.0.2.1',
        'x.example.com. 600 IN AAAA 2001:db8::1',
    )
    learned = extract(resp, qname)
    assert learned.ipv4 == {'192.0.2.1'}
    assert learned.ipv6 == {'2001:db8::1'}
    assert learned.ttl == 600


def test_cname_chain_is_followed():
    resp, qname = response(
        'acct.blob.core.windows.net',
        'acct.blob.core.windows.net. 60 IN CNAME blob.ams07.store.core.windows.net.',
        'blob.ams07.store.core.windows.net. 120 IN CNAME edge.trafficmanager.net.',
        'edge.trafficmanager.net. 30 IN A 20.60.0.1',
    )
    learned = extract(resp, qname)
    assert learned.ipv4 == {'20.60.0.1'}
    assert learned.ttl == 120


def test_unrelated_records_are_ignored():
    resp, qname = response(
        'x.example.com',
        'x.example.com. 300 IN A 192.0.2.1',
        'evil.example.org. 300 IN A 203.0.113.66',
    )
    assert extract(resp, qname).ipv4 == {'192.0.2.1'}


def test_cname_loop_terminates():
    resp, qname = response(
        'a.example.com',
        'a.example.com. 60 IN CNAME b.example.com.',
        'b.example.com. 60 IN CNAME a.example.com.',
    )
    assert not extract(resp, qname)


def test_https_hints():
    resp, qname = response(
        'svc.example.com',
        'svc.example.com. 300 IN HTTPS 1 . alpn=h2 ipv4hint=192.0.2.7 ipv6hint=2001:db8::7',
    )
    learned = extract(resp, qname)
    assert learned.ipv4 == {'192.0.2.7'}
    assert learned.ipv6 == {'2001:db8::7'}


def test_errors_and_empty_answers():
    resp, qname = response('nx.example.com', rcode=dns.rcode.NXDOMAIN)
    assert not extract(resp, qname)
    resp, qname = response('empty.example.com')
    assert not extract(resp, qname)
