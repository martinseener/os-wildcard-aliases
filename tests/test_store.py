from wildcardaliases.store import Store, TableState


def test_learn_new_and_refresh():
    t = TableState('t', 10)
    new, evicted = t.learn(['192.0.2.1', '192.0.2.2'], expires=100, now=0)
    assert new == ['192.0.2.1', '192.0.2.2'] and evicted == []
    # known address: nothing to add, expiry extended but never shortened
    new, _ = t.learn(['192.0.2.1'], expires=200, now=1)
    assert new == [] and t.entries['192.0.2.1'] == 200
    t.learn(['192.0.2.1'], expires=150, now=2)
    assert t.entries['192.0.2.1'] == 200
    assert t.learned_total == 2


def test_expire():
    t = TableState('t', 10)
    t.learn(['192.0.2.1'], expires=100, now=0)
    t.learn(['192.0.2.2'], expires=300, now=0)
    assert t.expire(99) == []
    assert t.expire(100) == ['192.0.2.1']
    assert list(t.entries) == ['192.0.2.2']


def test_eviction_prefers_oldest_and_keeps_fresh():
    t = TableState('t', 3)
    t.learn(['192.0.2.1'], expires=100, now=0)
    t.learn(['192.0.2.2'], expires=200, now=0)
    t.learn(['192.0.2.3'], expires=300, now=0)
    new, evicted = t.learn(['192.0.2.4'], expires=400, now=1)
    assert new == ['192.0.2.4']
    assert evicted == ['192.0.2.1']
    assert set(t.entries) == {'192.0.2.2', '192.0.2.3', '192.0.2.4'}
    assert t.evicted_total == 1


def test_batch_larger_than_table():
    t = TableState('t', 2)
    new, evicted = t.learn(['192.0.2.1', '192.0.2.2', '192.0.2.3'], expires=100, now=0)
    # never report addresses for deletion that were never installed
    assert evicted == []
    assert len(new) == 2 and len(t.entries) == 2
    assert set(new) == set(t.entries)


def test_store_reconfigure_and_state_roundtrip():
    s = Store()
    assert s.reconfigure({'a': 10, 'b': 10}) == []
    s.get('a').learn(['192.0.2.1'], expires=100, now=0)
    s.get('b').learn(['192.0.2.2'], expires=50, now=0)
    state = s.to_state()

    s2 = Store()
    s2.reconfigure({'a': 10, 'c': 10})
    s2.load_state(state, now=60)
    assert s2.get('a').entries == {'192.0.2.1': 100}
    assert s2.get('c').entries == {}
    assert s2.reconfigure({'c': 5}) == ['a']


def test_load_state_respects_lowered_limit_and_garbage():
    s = Store()
    s.reconfigure({'a': 1})
    s.load_state({'a': {'192.0.2.1': 100, '192.0.2.2': 200, 'x': 'bad'}}, now=0)
    assert s.get('a').entries == {'192.0.2.2': 200}
    s.load_state('garbage', now=0)


def test_names_are_remembered_capped_and_sanitized():
    t = TableState('t', 10)
    for idx in range(7):
        t.learn(['192.0.2.1'], expires=100, now=0, name='host%d.example.com' % idx)
    t.learn(['192.0.2.1'], expires=100, now=0, name='host3.example.com')
    # the 5 most recent, a repeated name moves to the end
    assert t.names['192.0.2.1'] == [
        'host2.example.com', 'host4.example.com', 'host5.example.com', 'host6.example.com', 'host3.example.com'
    ]
    t.learn(['192.0.2.2'], expires=100, now=0, name='<img src=x onerror=alert(1)>.example.com')
    t.learn(['192.0.2.2'], expires=100, now=0, name='a\\032b.example.com')
    assert t.names['192.0.2.2'] == []
    assert t.listing(now=40) == [
        {'address': '192.0.2.1', 'expires_in': 60, 'names': t.names['192.0.2.1']},
        {'address': '192.0.2.2', 'expires_in': 60, 'names': []},
    ]
    t.expire(now=200)
    assert t.names == {}


def test_state_roundtrip_keeps_names_and_reads_old_format():
    s = Store()
    s.reconfigure({'a': 10})
    s.get('a').learn(['192.0.2.1'], expires=100, now=0, name='x.example.com')
    s2 = Store()
    s2.reconfigure({'a': 10})
    s2.load_state(s.to_state(), now=0)
    assert s2.get('a').names == {'192.0.2.1': ['x.example.com']}
    # state written by 1.0 and earlier: address -> expiry only; garbage names are dropped
    s3 = Store()
    s3.reconfigure({'a': 10})
    s3.load_state({'a': {'192.0.2.1': 100, '192.0.2.2': {'expires': 100, 'names': ['ok.example.com', '<x>', 3]},
                         '192.0.2.3': {'expires': True}}}, now=0)
    assert s3.get('a').entries == {'192.0.2.1': 100, '192.0.2.2': 100}
    assert s3.get('a').names == {'192.0.2.1': [], '192.0.2.2': ['ok.example.com']}
