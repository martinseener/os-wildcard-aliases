""" render our templates with the real OPNsense template engine (from an opnsense/core checkout)

    The config.xml comes from tests/fixtures/config.xml, or, when WILDCARDALIASES_CONFIG_XML is set, from the
    file written by the PHP model tests ("make php-test"), so the chain
    PHP model -> config.xml -> configd template -> daemon configuration is covered end to end.
"""
import importlib
import json
import os
import shutil
import sys

import pytest

from wildcardaliases.config import parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CORE = os.environ.get('OPNSENSE_CORE', os.path.join(ROOT, '.cache', 'opnsense-core'))
CORE_MODULES = os.path.join(CORE, 'src', 'opnsense', 'service', 'modules')
PLUGIN_TEMPLATES = os.path.join(ROOT, 'net', 'wildcard-aliases', 'src', 'opnsense', 'service', 'templates')

pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORE_MODULES), reason='no opnsense/core checkout (make php-test fetches one)'
)


@pytest.fixture
def engine(tmp_path):
    """ an isolated copy of core's template modules, seeing only our templates """
    service = tmp_path / 'service'
    shutil.copytree(CORE_MODULES, service / 'modules')
    shutil.copytree(PLUGIN_TEMPLATES, service / 'templates')
    sys.path.insert(0, str(service))
    for name in list(sys.modules):
        if name == 'modules' or name.startswith('modules.'):
            del sys.modules[name]
    try:
        yield importlib.import_module('modules.template'), importlib.import_module('modules.config')
    finally:
        sys.path.remove(str(service))


def generate(engine, tmp_path, config_xml):
    template, config = engine
    out = tmp_path / 'root'
    out.mkdir()
    tpl = template.Template(target_root_directory=str(out))
    tpl.set_config(config.Config(config_xml).get())
    tpl.generate('OPNsense/WildcardAliases')
    return out


def test_core_engine_renders_valid_daemon_config(engine, tmp_path):
    config_xml = os.environ.get('WILDCARDALIASES_CONFIG_XML') or os.path.join(ROOT, 'tests', 'fixtures', 'config.xml')
    out = generate(engine, tmp_path, config_xml)

    rc_conf = (out / 'etc' / 'rc.conf.d' / 'wildcardaliases').read_text()
    assert 'wildcardaliases_enable="YES"' in rc_conf

    data = json.loads((out / 'usr' / 'local' / 'etc' / 'wildcardaliases' / 'wildcardaliases.json').read_text())
    cfg = parse(data)
    assert cfg.upstreams == (('127.0.0.1', 5353),)
    names = [alias.name for alias in cfg.aliases]
    assert 'azure_storage' in names
    if not os.environ.get('WILDCARDALIASES_CONFIG_XML'):
        # fixture specifics: disabled aliases are left out, families are honoured
        assert names == ['azure_storage', 'v4only']
        assert cfg.aliases[1].inet and not cfg.aliases[1].inet6
