"""Immutable, content-addressed business definitions in local session storage."""
import hashlib
import json

from app.session import SQLiteBackend, get_session_store


PURPOSE = 'metric-contracts:v1'


def load_registry(session_id):
    backend = get_session_store()._backend
    if not isinstance(backend, SQLiteBackend):
        return {'active': {}, 'versions': {}}
    with backend._connect() as con:
        row = con.execute('SELECT document FROM context_summaries WHERE session_id=? AND purpose=?',
                          (session_id, PURPOSE)).fetchone()
    return json.loads(row['document']) if row else {'active': {}, 'versions': {}}


def save_definition(session_id, definition_dict):
    backend = get_session_store()._backend
    if not isinstance(backend, SQLiteBackend):
        raise NotImplementedError('Business definition persistence requires SQLite.')
    content = json.dumps(definition_dict, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    version = hashlib.sha256(content.encode('utf-8')).hexdigest()
    stored = json.loads(content)
    with backend._connect() as con:
        # Lock before reading so concurrent saves cannot replace each other's versions.
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT document FROM context_summaries WHERE session_id=? AND purpose=?',
                          (session_id, PURPOSE)).fetchone()
        registry = json.loads(row['document']) if row else {'active': {}, 'versions': {}}
        registry['versions'].setdefault(version, stored)
        registry['active'][stored['id']] = version
        con.execute('INSERT INTO context_summaries(session_id,purpose,document) VALUES(?,?,?) '
                    'ON CONFLICT(session_id,purpose) DO UPDATE SET document=excluded.document',
                    (session_id, PURPOSE, json.dumps(registry, ensure_ascii=False, sort_keys=True,
                                                   separators=(',', ':'), allow_nan=False)))
    return version
