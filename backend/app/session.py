"""In-session memory for follow-up questions.

Backends
--------
- ``InMemoryBackend``   process-local dict, dies on restart. Used by tests.
- ``SQLiteBackend``     single-user local database, survives restart.
- ``SupabaseBackend``   Postgres via Supabase REST API. Persistent across
                        restarts, days, and multiple workers.

The choice is driven by ``settings.session_backend`` (memory / sqlite / supabase).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


# --- Data models ------------------------------------------------------------


@dataclass
class QueryMemory:
    """A single successful query, retained for follow-up context."""
    question: str
    sql: str
    columns: list[str]
    row_count: int
    sample_rows: list[dict[str, Any]]
    data_version: str | None = None


@dataclass
class Session:
    """One user's conversational state."""
    session_id: str
    created_at: float
    last_accessed_at: float
    recent_queries: list[QueryMemory] = field(default_factory=list)
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            session_id=data["session_id"],
            created_at=float(data["created_at"]),
            last_accessed_at=float(data["last_accessed_at"]),
            recent_queries=[
                QueryMemory(
                    question=q["question"],
                    sql=q["sql"],
                    columns=list(q["columns"]),
                    row_count=int(q["row_count"]),
                    sample_rows=list(q["sample_rows"]),
                    data_version=q.get("data_version"),
                )
                for q in data.get("recent_queries", [])
            ],
            title=data.get("title"),
        )


# --- Backend protocol -------------------------------------------------------


class SessionBackend(Protocol):
    def get(self, session_id: str) -> Session | None: ...
    def save(self, session: Session) -> None: ...
    def delete(self, session_id: str) -> None: ...


# --- In-memory backend ------------------------------------------------------


class InMemoryBackend:
    """Process-local store with lazy TTL eviction. Thread-safe."""

    def __init__(self, ttl_seconds: float = 30 * 60) -> None:
        self._sessions: dict[str, Session] = {}
        self._expires_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            self._evict_expired(time.time())
            return self._sessions.get(session_id)

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            if self._ttl > 0:
                self._expires_at[session.session_id] = time.time() + self._ttl

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._expires_at.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired(time.time())
            return len(self._sessions)

    def _evict_expired(self, now: float) -> None:
        if not self._ttl:
            return
        expired = [sid for sid, exp in self._expires_at.items() if exp <= now]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._expires_at.pop(sid, None)


# --- Local SQLite backend ---------------------------------------------------


class SQLiteBackend:
    """Single-user local history; each operation closes its own connection."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    recent_queries TEXT NOT NULL,
                    title TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS analysis_boards (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS advanced_views (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_summaries (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL,
                    document TEXT NOT NULL,
                    PRIMARY KEY(session_id,purpose)
                );
                CREATE TABLE IF NOT EXISTS chat_folders (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_folder_memberships (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    folder_id TEXT NOT NULL REFERENCES chat_folders(id) ON DELETE CASCADE
                );
            """)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def get(self, session_id: str) -> Session | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return Session.from_dict({
            "session_id": row["id"], "created_at": row["created_at"],
            "last_accessed_at": row["last_accessed_at"], "title": row["title"],
            "recent_queries": json.loads(row["recent_queries"]),
        })

    def save(self, session: Session, user_id: str | None = None) -> None:
        title = session.title or (session.recent_queries[0].question[:100] if session.recent_queries else None)
        with self._connect() as con:
            con.execute("""
                INSERT INTO sessions VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_accessed_at = excluded.last_accessed_at,
                    recent_queries = excluded.recent_queries,
                    title = COALESCE(sessions.title, excluded.title)
            """, (session.session_id, session.created_at, session.last_accessed_at,
                  json.dumps([asdict(q) for q in session.recent_queries], ensure_ascii=False), title))

    def delete(self, session_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def set_title(self, session_id: str, title: str, only_if_empty: bool = False) -> None:
        with self._connect() as con:
            con.execute("""
                UPDATE sessions SET title = ?
                WHERE id = ? AND (? = 0 OR title IS NULL)
            """, (title[:100], session_id, only_if_empty))

    def save_message(self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        self.save_messages(session_id, [(role, content, metadata)])

    def save_messages(self, session_id: str, messages) -> None:
        """Commit a whole conversation round or leave all its messages unwritten."""
        with self._connect() as con:
            con.executemany("""
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, [(session_id, role, content, json.dumps(metadata or {}, ensure_ascii=False),
                   datetime.now(timezone.utc).isoformat()) for role, content, metadata in messages])

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("""
                SELECT id, role, content, metadata, created_at FROM messages
                WHERE session_id = ? ORDER BY id
            """, (session_id,)).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def count_sessions(self) -> int:
        with self._connect() as con:
            return con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("""SELECT sessions.*, m.folder_id FROM sessions
                LEFT JOIN chat_folder_memberships m ON m.session_id = sessions.id
                ORDER BY last_accessed_at DESC""").fetchall()
        return [{"id": row["id"], "title": row["title"],
                 "folder_id": row["folder_id"],
                 "created_at": datetime.fromtimestamp(row["created_at"], tz=timezone.utc).isoformat(),
                 "last_accessed_at": datetime.fromtimestamp(row["last_accessed_at"], tz=timezone.utc).isoformat()}
                for row in rows]

    def list_folders(self) -> list[dict[str, str]]:
        with self._connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM chat_folders ORDER BY rowid")]

    def create_folder(self, title: str) -> dict[str, str]:
        folder = {"id": uuid.uuid4().hex, "title": title}
        with self._connect() as con:
            con.execute("INSERT INTO chat_folders VALUES (:id, :title)", folder)
        return folder

    def rename_folder(self, folder_id: str, title: str) -> None:
        with self._connect() as con:
            if not con.execute("UPDATE chat_folders SET title = ? WHERE id = ?", (title, folder_id)).rowcount:
                raise ValueError("文件夹不存在。")

    def delete_folder(self, folder_id: str) -> None:
        with self._connect() as con:
            if not con.execute("DELETE FROM chat_folders WHERE id = ?", (folder_id,)).rowcount:
                raise ValueError("文件夹不存在。")

    def move_sessions(self, session_ids: list[str], folder_id: str | None) -> None:
        with self._connect() as con:
            if folder_id is not None and not con.execute("SELECT 1 FROM chat_folders WHERE id = ?", (folder_id,)).fetchone():
                raise ValueError("目标文件夹不存在。")
            for sid in session_ids:
                if not con.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone():
                    raise ValueError("部分聊天已不存在，请刷新后重试；本次没有移动。")
                con.execute("DELETE FROM chat_folder_memberships WHERE session_id = ?", (sid,))
                if folder_id is not None:
                    con.execute("INSERT INTO chat_folder_memberships VALUES (?, ?)", (sid, folder_id))


# --- Supabase backend -------------------------------------------------------


class SupabaseBackend:
    """Postgres via Supabase REST API. Sessions persist indefinitely."""

    def __init__(self, url: str, key: str) -> None:
        from supabase import create_client
        self._client = create_client(url, key)
        logger.info("SupabaseBackend connected to %s", url)

    def get(self, session_id: str) -> Session | None:
        # Fetch session row
        res = (
            self._client.table("sessions")
            .select("*")
            .eq("id", session_id)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]

        # Fetch query memories ordered by position
        mem_res = (
            self._client.table("query_memories")
            .select("*")
            .eq("session_id", session_id)
            .order("position")
            .execute()
        )
        queries = [
            QueryMemory(
                question=m["question"],
                sql=m["sql"],
                columns=m["columns"] if isinstance(m["columns"], list) else json.loads(m["columns"]),
                row_count=m["row_count"],
                sample_rows=m["sample_rows"] if isinstance(m["sample_rows"], list) else json.loads(m["sample_rows"]),
            )
            for m in (mem_res.data or [])
        ]


        from dateutil.parser import isoparse
        created = isoparse(row["created_at"]).timestamp()
        accessed = isoparse(row["last_accessed_at"]).timestamp()

        return Session(
            session_id=row["id"],
            created_at=created,
            last_accessed_at=accessed,
            recent_queries=queries,
        )

    def save(self, session: Session, user_id: str | None = None) -> None:
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()

        # Upsert session row — always update title from first query if available
        title = session.recent_queries[0].question[:100] if session.recent_queries else None

        row = {
            "id": session.session_id,
            "created_at": datetime.fromtimestamp(session.created_at, tz=timezone.utc).isoformat(),
            "last_accessed_at": now_iso,
        }
        if user_id:
            row["user_id"] = user_id
        if title:
            row["title"] = title

        self._client.table("sessions").upsert(row).execute()

        # Replace query memories: delete old, insert new
        self._client.table("query_memories").delete().eq(
            "session_id", session.session_id
        ).execute()

        if session.recent_queries:
            rows = [
                {
                    "session_id": session.session_id,
                    "position": i,
                    "question": q.question,
                    "sql": q.sql,
                    "columns": q.columns,
                    "row_count": q.row_count,
                    "sample_rows": q.sample_rows,
                }
                for i, q in enumerate(session.recent_queries)
            ]
            self._client.table("query_memories").insert(rows).execute()

    def delete(self, session_id: str) -> None:
        # CASCADE handles query_memories and messages
        self._client.table("sessions").delete().eq("id", session_id).execute()

    # --- Message history (for frontend sidebar / chat UI) ---

    def save_message(
        self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        self._client.table("messages").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }).execute()

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        res = (
            self._client.table("messages")
            .select("role, content, metadata, created_at")
            .eq("session_id", session_id)
            .order("created_at")
            .execute()
        )
        return res.data or []

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            self._client.table("sessions")
            .select("id, title, created_at, last_accessed_at")
            .order("last_accessed_at", desc=True)
            .limit(50)
        )
        if user_id:
            query = query.eq("user_id", user_id)
        res = query.execute()
        return res.data or []


# --- Public store -----------------------------------------------------------


class SessionStore:
    """Stable public API on top of any SessionBackend."""

    def __init__(
        self,
        max_queries_per_session: int = 3,
        ttl_seconds: float | None = None,
        backend: SessionBackend | None = None,
    ) -> None:
        self.max_queries = max_queries_per_session
        self.ttl_seconds = float(
            ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds
        )
        self._backend: SessionBackend = (
            backend if backend is not None else _build_default_backend()
        )

    def get_or_create(self, session_id: str | None, user_id: str | None = None) -> Session:
        if session_id:
            existing = self._backend.get(session_id)
            if existing is not None:
                existing.last_accessed_at = time.time()
                self._backend.save(existing)
                return existing

        new_id = session_id or uuid.uuid4().hex
        now = time.time()
        s = Session(session_id=new_id, created_at=now, last_accessed_at=now)
        # Store user_id for ownership filtering
        if user_id and isinstance(self._backend, SupabaseBackend):
            self._backend.save(s, user_id=user_id)
        else:
            self._backend.save(s)
        return s

    def record_query(self, session_id: str, memory: QueryMemory) -> None:
        s = self._backend.get(session_id)
        if s is None:
            now = time.time()
            s = Session(session_id=session_id, created_at=now, last_accessed_at=now)
        s.recent_queries.append(memory)
        if len(s.recent_queries) > self.max_queries:
            s.recent_queries = s.recent_queries[-self.max_queries:]
        s.last_accessed_at = time.time()
        self._backend.save(s)

    def get(self, session_id: str) -> Session | None:
        return self._backend.get(session_id)

    def __len__(self) -> int:
        if isinstance(self._backend, InMemoryBackend):
            return len(self._backend)
        raise NotImplementedError("len() only supported on in-memory backend")


def _build_default_backend() -> SessionBackend:
    choice = (settings.session_backend or "memory").lower()
    if choice == "sqlite":
        if settings.app_mode != "local":
            raise ValueError("SESSION_BACKEND=sqlite requires APP_MODE=local (single-user only).")
        from app.chat_storage import chat_directory
        return SQLiteBackend(chat_directory() / "datapilot.sqlite3")
    if settings.app_mode == "local":
        raise ValueError("APP_MODE=local requires SESSION_BACKEND=sqlite for local persistence.")
    if choice == "supabase":
        url = settings.supabase_url
        key = settings.supabase_key
        if not url or not key:
            logger.warning(
                "SESSION_BACKEND=supabase but SUPABASE_URL or SUPABASE_KEY "
                "is missing. Falling back to in-memory."
            )
            return InMemoryBackend()
        return SupabaseBackend(url=url, key=key)
    return InMemoryBackend()


# --- Module-level singleton + context renderer ------------------------------

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def render_session_context(session: Session, data_version: str | None = None) -> str:
    from app.privacy import model_safe_value
    queries = [q for q in session.recent_queries if data_version is None or q.data_version == data_version]
    if not queries:
        return ""
    lines = ["PREVIOUS QUERIES IN THIS SESSION (most recent last; historical examples only, not current evidence; recompute changed scope):"]
    for i, q in enumerate(queries, 1):
        safe = model_safe_value({'question':q.question,'sql':q.sql,'sample_rows':q.sample_rows})
        lines.append(
            f"  {i}. Q: {safe['question']}\n"
            f"     SQL: {safe['sql']}\n"
            f"     Returned {q.row_count} rows with columns {q.columns}"
        )
    return "\n".join(lines)
