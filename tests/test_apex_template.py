from pathlib import Path
import json
from jinja2 import Environment

TEMPLATE = Path(__file__).resolve().parents[1] / 'net/wildcard-aliases/src/opnsense/service/templates/OPNsense/WildcardAliases/wildcardaliases.json'

class Helpers:
    def toList(self, _):
        return [dict(enabled='1', name='github_wildcard', patterns='*.github.com', families='IPv4,IPv6', max_entries='4096', include_apex='1'), dict(enabled='1', name='other', patterns='*.other.com', families='IPv4', max_entries='4096')]
    def exists(self, _):
        return True

def test_include_apex_is_opt_in():
    env = Environment(extensions=['jinja2.ext.do'])
    rendered = env.from_string(TEMPLATE.read_text()).render(helpers=Helpers(), OPNsense={'WildcardAliases': {'general': {'listen': '127.0.0.1:5354', 'upstreams': '127.0.0.1:5353'}}})
    data = json.loads(rendered)
    assert data['aliases'][0]['patterns'] == ['*.github.com', 'github.com']
    assert data['aliases'][1]['patterns'] == ['*.other.com']
