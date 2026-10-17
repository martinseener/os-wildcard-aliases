import importlib.util
import json
import os
import subprocess
import sys

from conftest import SCRIPTS

spec = importlib.util.spec_from_file_location('entries', os.path.join(SCRIPTS, 'entries.py'))
entries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entries)


def test_listing_filters_expired_and_reads_both_formats():
    state = {
        'azure': {
            '20.60.0.1': {'expires': 1100, 'names': ['acct.blob.core.windows.net', 7]},
            '20.60.0.2': {'expires': 900, 'names': []},
            '20.60.0.3': 1500,
        },
        'broken': 'garbage',
    }
    assert entries.listing(state, now=1000) == {'azure': [
        {'address': '20.60.0.1', 'expires_in': 100, 'names': ['acct.blob.core.windows.net']},
        {'address': '20.60.0.3', 'expires_in': 500, 'names': []},
    ]}
    assert entries.listing('garbage', now=0) == {}


def test_script_output(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'t': {'192.0.2.1': {'expires': 4102444800, 'names': ['x.example.com']}}}))
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'entries.py'), str(path)],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out)['t'][0]['names'] == ['x.example.com']
    missing = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'entries.py'), str(tmp_path / 'nope')],
                             capture_output=True, text=True, check=True).stdout
    assert json.loads(missing) == {}
