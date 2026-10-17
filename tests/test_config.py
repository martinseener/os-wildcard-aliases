import json

import pytest

from wildcardaliases.config import ConfigError, load, parse, parse_address


def base(**extra):
    data = {
        'listen': ['127.0.0.1:5354'],
        'upstreams': ['127.0.0.1:5353'],
        'aliases': [{'name': 'azure', 'patterns': ['*.core.windows.net'], 'inet': True, 'inet6': False}],
    }
    data.update(extra)
    return data


def test_parse_minimal():
    cfg = parse(base())
    assert cfg.listen == (('127.0.0.1', 5354),)
    assert cfg.upstreams == (('127.0.0.1', 5353),)
    assert cfg.aliases[0].name == 'azure' and not cfg.aliases[0].inet6
    assert cfg.fail_closed


@pytest.mark.parametrize('value,expected', [
    ('127.0.0.1', ('127.0.0.1', 53)),
    ('127.0.0.1:5353', ('127.0.0.1', 5353)),
    ('::1', ('::1', 53)),
    ('[::1]:5353', ('::1', 5353)),
    ('[2001:db8::1]', ('2001:db8::1', 53)),
])
def test_parse_address(value, expected):
    assert parse_address(value) == expected


@pytest.mark.parametrize('value', ['', 'dns.google', '127.0.0.1:0', '127.0.0.1:99999', '[::1', '[::1]x'])
def test_parse_address_invalid(value):
    with pytest.raises(ConfigError):
        parse_address(value)


@pytest.mark.parametrize('change', [
    {'upstreams': []},
    {'upstreams': ['127.0.0.1:5354']},  # would loop to ourselves
    {'aliases': [{'name': 'bad-name', 'patterns': ['*.x.com']}]},
    {'aliases': [{'name': 'a', 'patterns': []}]},
    {'aliases': [{'name': 'a', 'patterns': ['x.*.com']}]},
    {'aliases': [{'name': 'a', 'patterns': ['x.com']}, {'name': 'a', 'patterns': ['y.com']}]},
    {'aliases': [{'name': 'a', 'patterns': ['x.com'], 'inet': False, 'inet6': False}]},
    {'fail_mode': 'maybe'},
    {'min_hold': -1},
    {'upstream_timeout': 0},
])
def test_invalid_configs(change):
    with pytest.raises(ConfigError):
        parse(base(**change))


def test_hold_time_defaults_to_ttl():
    cfg = parse(base())
    assert (cfg.min_hold, cfg.grace, cfg.max_hold) == (0, 0, None)
    assert cfg.hold_time(10) == 10
    assert cfg.hold_time(604800) == 604800
    # what the template writes for empty fields
    cfg = parse(base(min_hold=None, grace=None, max_hold=None))
    assert cfg.hold_time(60) == 60


def test_hold_time():
    cfg = parse(base(min_hold=300, grace=60, max_hold=3600))
    assert cfg.hold_time(10) == 300
    assert cfg.hold_time(1000) == 1060
    assert cfg.hold_time(86400) == 3600


def test_load(tmp_path):
    path = tmp_path / 'c.json'
    path.write_text(json.dumps(base()))
    assert load(str(path)).aliases[0].patterns == ('*.core.windows.net',)
    path.write_text('{nope')
    with pytest.raises(ConfigError):
        load(str(path))
