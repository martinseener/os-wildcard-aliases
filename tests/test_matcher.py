import dns.name
import pytest

from wildcardaliases.matcher import Matcher, normalize


def make():
    return Matcher([
        ('azure', ['*.core.windows.net', 'login.microsoftonline.com']),
        ('blob', ['*.blob.core.windows.net']),
    ])


def test_wildcard_matches_subdomains():
    m = make()
    assert m.match('myacct.blob.core.windows.net') == {'azure', 'blob'}
    assert m.match('a.b.c.core.windows.net') == {'azure'}
    assert m.match('queue.core.windows.net') == {'azure'}


def test_wildcard_does_not_match_apex_or_lookalikes():
    m = make()
    assert m.match('core.windows.net') == set()
    assert m.match('evilcore.windows.net') == set()
    assert m.match('core.windows.net.evil.com') == set()
    assert m.match('blob.core.windows.net') == {'azure'}


def test_exact_match_only():
    m = make()
    assert m.match('login.microsoftonline.com') == {'azure'}
    assert m.match('x.login.microsoftonline.com') == set()


def test_case_trailing_dot_and_name_aliases():
    m = make()
    assert m.match('MyAcct.Blob.Core.Windows.NET.') == {'azure', 'blob'}
    assert m.match(dns.name.from_text('x.core.windows.net')) == {'azure'}


def test_idn_patterns_are_punycoded():
    m = Matcher([('idn', ['*.bücher.example'])])
    assert m.match('shop.xn--bcher-kva.example') == {'idn'}


@pytest.mark.parametrize('pattern', ['', '*', '*.', 'a.*.example.com', '**.example.com', '*example.com', '.'])
def test_invalid_patterns(pattern):
    with pytest.raises(ValueError):
        Matcher.parse(pattern)


def test_empty_matcher_is_falsy():
    assert not Matcher()
    assert make()


def test_normalize():
    assert normalize('A.Example.COM.') == 'a.example.com'
