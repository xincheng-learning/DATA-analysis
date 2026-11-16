"""Offline persistence and concurrency checks for versioned business definitions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app import analysis_contract_store as contracts
from app.session import InMemoryBackend, Session, SQLiteBackend


@pytest.fixture
def backend(tmp_path, monkeypatch):
    backend = SQLiteBackend(tmp_path / 'contracts.sqlite3')
    backend.save(Session('one', 1, 1))
    backend.save(Session('two', 1, 1))
    monkeypatch.setattr(contracts, 'get_session_store', lambda: SimpleNamespace(_backend=backend))
    return backend


def definition(identifier='revenue', field='orders.amount'):
    return {'id': identifier, 'label': '净收入', 'calculation': {'field': field, 'op': 'sum'}}


def test_missing_registry_is_empty_and_session_isolated(backend):
    assert contracts.load_registry('one') == {'active': {}, 'versions': {}}
    version = contracts.save_definition('one', definition())
    assert contracts.load_registry('one')['active'] == {'revenue': version}
    assert contracts.load_registry('two') == {'active': {}, 'versions': {}}
    assert contracts.load_registry('missing') == {'active': {}, 'versions': {}}


def test_full_canonical_content_is_the_version_and_key_order_is_idempotent(backend):
    original = definition()
    canonical = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    expected = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    assert contracts.save_definition('one', original) == expected
    reordered = {'calculation': {'op': 'sum', 'field': 'orders.amount'}, 'label': '净收入', 'id': 'revenue'}
    assert contracts.save_definition('one', reordered) == expected
    assert contracts.load_registry('one') == {'active': {'revenue': expected}, 'versions': {expected: original}}


def test_revision_retains_old_content_without_mutating_inputs(backend):
    old = definition()
    snapshot = deepcopy(old)
    first = contracts.save_definition('one', old)
    new = definition(field='orders.net_amount')
    second = contracts.save_definition('one', new)
    assert old == snapshot
    assert first != second
    registry = contracts.load_registry('one')
    assert registry == {'active': {'revenue': second}, 'versions': {first: old, second: new}}
    new['calculation']['field'] = 'changed_after_save'
    registry['versions'][first]['calculation']['field'] = 'changed_after_load'
    assert contracts.load_registry('one')['versions'] == {first: snapshot, second: definition(field='orders.net_amount')}


def test_restart_reads_versions_from_sqlite_and_does_not_touch_other_purposes(backend, monkeypatch):
    with backend._connect() as con:
        con.execute('INSERT INTO context_summaries VALUES(?,?,?)', ('one', 'unrelated', '{"keep":true}'))
    version = contracts.save_definition('one', definition())
    reopened = SQLiteBackend(backend.path)
    monkeypatch.setattr(contracts, 'get_session_store', lambda: SimpleNamespace(_backend=reopened))
    assert contracts.load_registry('one')['versions'][version] == definition()
    with reopened._connect() as con:
        assert con.execute('SELECT document FROM context_summaries WHERE purpose=?', ('unrelated',)).fetchone()[0] == '{"keep":true}'
        assert con.execute('SELECT purpose FROM context_summaries WHERE purpose<>?', ('unrelated',)).fetchone()[0] == 'metric-contracts:v1'


def test_concurrent_updates_preserve_every_metric_and_version(backend):
    definitions = [definition(f'metric_{i}') for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(lambda item: contracts.save_definition('one', item), definitions))
    registry = contracts.load_registry('one')
    assert registry['active'] == {item['id']: version for item, version in zip(definitions, versions)}
    assert registry['versions'] == dict(zip(versions, definitions))


def test_concurrent_revisions_of_same_metric_retain_all_versions(backend):
    definitions = [definition(field=f'orders.amount_{i}') for i in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(lambda item: contracts.save_definition('one', item), definitions))
    registry = contracts.load_registry('one')
    assert registry['versions'] == dict(zip(versions, definitions))
    assert registry['active']['revenue'] in versions


def test_non_sqlite_is_read_empty_and_write_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(contracts, 'get_session_store', lambda: SimpleNamespace(_backend=InMemoryBackend()))
    assert contracts.load_registry('one') == {'active': {}, 'versions': {}}
    with pytest.raises(NotImplementedError, match='SQLite'):
        contracts.save_definition('one', definition())
