""" render the configd template like OPNsense does and feed the result to the daemon's parser """
import json
import os

import jinja2

from wildcardaliases.config import parse

TEMPLATES = os.path.join(
    os.path.dirname(__file__), '..', 'net', 'wildcard-aliases', 'src', 'opnsense', 'service', 'templates'
)


class Helpers:
    """ the subset of OPNsense's template helpers used by our templates """

    def __init__(self, data):
        self.data = data

    def _get(self, path):
        node = self.data
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def exists(self, path):
        return self._get(path) is not None

    def toList(self, path):  # noqa: N802 (OPNsense naming)
        node = self._get(path)
        if node is None:
            return []
        return node if isinstance(node, list) else [node]


def render(name, data):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES), extensions=['jinja2.ext.do'], trim_blocks=True
    )
    context = dict(data)
    context['helpers'] = Helpers(data)
    return env.get_template('OPNsense/WildcardAliases/%s' % name).render(context)


CONFIG = {
    'OPNsense': {'WildcardAliases': {
        'general': {
            'enabled': '1', 'listen': '127.0.0.1:5354,[::1]:5354', 'upstreams': '127.0.0.1:5353',
            'min_hold': '300', 'grace': '60', 'max_hold': '86400', 'fail_mode': 'closed', 'verbose': '0',
        },
        'aliases': {'alias': [
            {'@uuid': 'a', 'enabled': '1', 'name': 'azure', 'patterns': '*.core.windows.net,login.microsoftonline.com',
             'families': 'IPv4,IPv6', 'max_entries': '4096', 'description': 'Azure "storage"'},
            {'@uuid': 'b', 'enabled': '0', 'name': 'disabled', 'patterns': '*.example.com',
             'families': 'IPv4', 'max_entries': '16', 'description': ''},
            {'@uuid': 'c', 'enabled': '1', 'name': 'v4only', 'patterns': '*.example.org',
             'families': 'IPv4', 'max_entries': '100', 'description': ''},
        ]},
    }}
}


def test_json_template_produces_valid_config():
    cfg = parse(json.loads(render('wildcardaliases.json', CONFIG)))
    assert cfg.listen == (('127.0.0.1', 5354), ('::1', 5354))
    assert cfg.upstreams == (('127.0.0.1', 5353),)
    assert [alias.name for alias in cfg.aliases] == ['azure', 'v4only']
    assert cfg.aliases[0].patterns == ('*.core.windows.net', 'login.microsoftonline.com')
    assert cfg.aliases[1].inet and not cfg.aliases[1].inet6
    assert cfg.fail_closed


def test_json_template_single_alias_and_empty_config():
    single = json.loads(json.dumps(CONFIG))
    aliases = single['OPNsense']['WildcardAliases']['aliases']
    aliases['alias'] = CONFIG['OPNsense']['WildcardAliases']['aliases']['alias'][0]
    assert len(json.loads(render('wildcardaliases.json', single))['aliases']) == 1

    empty = json.loads(render('wildcardaliases.json', {'OPNsense': {}}))
    assert empty['aliases'] == [] and empty['upstreams'] == []


def test_json_template_empty_hold_times_follow_ttl():
    data = json.loads(json.dumps(CONFIG))
    general = data['OPNsense']['WildcardAliases']['general']
    general.update(min_hold='', grace='', max_hold='')
    rendered = json.loads(render('wildcardaliases.json', data))
    assert (rendered['min_hold'], rendered['grace'], rendered['max_hold']) == (None, None, None)
    assert parse(rendered).hold_time(42) == 42
    # set values are passed on
    assert parse(json.loads(render('wildcardaliases.json', CONFIG))).hold_time(10) == 300


def test_rc_conf_template():
    assert 'wildcardaliases_enable="YES"' in render('rc.conf.d', CONFIG)
    assert 'wildcardaliases_enable="NO"' in render('rc.conf.d', {'OPNsense': {}})
