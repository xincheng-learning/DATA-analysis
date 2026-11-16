"""Local persistence uses real SQLite and survives fresh backend instances."""
from __future__ import annotations

from app.session import QueryMemory, SessionStore, SQLiteBackend


def test_local_history_and_memory_survive_restart(tmp_path):
    path = tmp_path / "datapilot.sqlite3"
    backend = SQLiteBackend(path)
    store = SessionStore(backend=backend)
    session = store.get_or_create(None)
    store.record_query(session.session_id, QueryMemory(
        question="销售额", sql="SELECT 300 AS revenue", columns=["revenue"],
        row_count=1, sample_rows=[{"revenue": 300}],
    ))
    backend.save_message(session.session_id, "user", "销售额是多少？")
    backend.save_message(session.session_id, "assistant", "300", {"route": "sql"})
    backend.set_title(session.session_id, "我的实验")
    store.get_or_create(session.session_id)

    restarted = SQLiteBackend(path)
    restored = restarted.get(session.session_id)
    assert restored.recent_queries[0].sample_rows == [{"revenue": 300}]
    assert restarted.list_sessions()[0]["title"] == "我的实验"
    assert restarted.get_messages(session.session_id)[1]["metadata"] == {"route": "sql"}
    assert restarted.get_messages(session.session_id)[0]["content"] == "销售额是多少？"


def test_local_delete_removes_only_selected_session_and_messages(tmp_path):
    backend = SQLiteBackend(tmp_path / "datapilot.sqlite3")
    store = SessionStore(backend=backend)
    first = store.get_or_create(None)
    second = store.get_or_create(None)
    backend.save_message(first.session_id, "user", "first")
    backend.save_message(second.session_id, "user", "keep")
    backend.delete(first.session_id)
    restarted = SQLiteBackend(backend.path)
    assert restarted.get(first.session_id) is None
    assert restarted.get_messages(first.session_id) == []
    assert restarted.get_messages(second.session_id)[0]["content"] == "keep"


def test_message_transaction_rolls_back_on_invalid_session(tmp_path):
    import sqlite3

    import pytest

    backend = SQLiteBackend(tmp_path / "datapilot.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        backend.save_message("missing", "user", "must not become orphaned")
    assert backend.list_sessions() == []
