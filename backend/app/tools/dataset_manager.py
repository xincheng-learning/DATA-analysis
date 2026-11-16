"""Per-session dataset manager.

Each session that has uploaded files gets its own DuckDB connection that
queries Parquet files on disk via ``read_parquet()``. This keeps RAM low
(DuckDB streams from disk) while supporting multi-file, multi-table
querying per session.

Key design decisions:
  - DuckDB connections are created lazily on first query after upload.
  - Tables are VIEWs over ``read_parquet('path')`` — no data copied into RAM.
  - Idle sessions (no query in 15 min) have their connection closed to free
    file handles. Re-opened transparently on next access.
  - A global memory budget caps the total estimated data footprint across
    all active sessions. If exceeded, oldest idle sessions are evicted
    first; if still over, new uploads get HTTP 503.
"""
from __future__ import annotations

import logging
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from app.config import settings
from app.tools.ingest import IngestedFile
from app.privacy import is_sensitive_column, model_safe_rows  # re-export existing callers

logger = logging.getLogger(__name__)

# --- Configuration -----------------------------------------------------------

# Global budget for estimated data across all sessions (bytes).
# Conservative for a 512MB deploy: leave ~250MB for app + libraries.
GLOBAL_BUDGET_BYTES = 250 * 1024 * 1024  # 250 MB

# Idle timeout: close DuckDB connections after this many seconds of inactivity.
IDLE_TIMEOUT_SECONDS = 15 * 60  # 15 minutes

# Expansion factor: Parquet on disk → estimated RAM when DuckDB scans it.
# DuckDB streams lazily so this is an overestimate, which is safe.
EXPANSION_FACTOR = 3.0
MAX_QUERY_RESULT_ROWS = 100_000
MAX_QUERY_RESULT_BYTES = 32 * 1024 * 1024


class QueryResultLimitExceeded(ValueError):
    pass


# --- Data classes ------------------------------------------------------------


@dataclass
class SessionDataset:
    """Tracks uploaded files and the DuckDB connection for one session."""
    session_id: str
    files: list[IngestedFile] = field(default_factory=list)
    parquet_dir: Path | None = None
    # DuckDB connection — created lazily, closed on idle eviction
    _con: duckdb.DuckDBPyConnection | None = field(default=None, repr=False)
    _last_query_at: float = field(default_factory=time.time)
    _schema_cache: str | None = field(default=None, repr=False)
    last_query_checks: list[dict] = field(default_factory=list, repr=False)
    query_lock: Any = field(default_factory=threading.RLock, repr=False)

    @property
    def total_parquet_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def estimated_memory(self) -> float:
        return self.total_parquet_bytes * EXPANSION_FACTOR

    @property
    def is_idle(self) -> bool:
        return (time.time() - self._last_query_at) > IDLE_TIMEOUT_SECONDS

    @property
    def table_names(self) -> list[str]:
        return [f.table_name for f in self.files]

    def add_file(self, ingested: IngestedFile) -> None:
        self.files.append(ingested)
        self._schema_cache = None  # invalidate
        # If connection is already open, register the new table immediately
        if self._con is not None:
            self._register_table(ingested)

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the DuckDB connection with all tables registered."""
        self._last_query_at = time.time()
        if self._con is None:
            self._con = duckdb.connect(":memory:", config={'memory_limit': '256MB', 'max_temp_directory_size': '256MB'})
            for f in self.files:
                self._register_table(f)
        return self._con

    def close(self) -> None:
        """Close the DuckDB connection to free resources."""
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None
        self._schema_cache = None

    def schema_summary(self, sample_values: int = 2) -> str:
        """Multi-table schema summary for the LLM prompt."""
        with self.query_lock:
            return self._schema_summary(sample_values)

    def _schema_summary(self, sample_values: int) -> str:
        if self._schema_cache is not None:
            return self._schema_cache

        if not self.files:
            return "(no data uploaded yet)"

        con = self.get_connection()
        lines = []
        for f in self.files:
            lines.append(f"Table: {f.table_name} ({f.row_count:,} rows)")
            lines.append("Columns:")
            cols = con.execute(f"DESCRIBE {f.table_name}").fetchall()
            null_counts = self._null_counts(con, f)
            for name, dtype, *_ in cols:
                line = f"  - {name}: {dtype}"
                if null_counts.get(name):
                    line += f" (missing: {null_counts[name]})"
                if sample_values > 0 and not is_sensitive_column(name):
                    samples = self._fetch_samples(con, f.table_name, name, sample_values)
                    if samples:
                        formatted = ", ".join(_fmt_sample(v) for v in samples)
                        line += f" (e.g., {formatted})"
                lines.append(line)
            lines.append("")  # blank line between tables

        self._schema_cache = "\n".join(lines).rstrip()
        return self._schema_cache

    def execute(self, sql: str, timeout: float | None = None, *, as_frame: bool = False) -> Any:
        """Execute SQL against this session's DuckDB. Returns the result relation."""
        from app.tools.sql_sanitizer import check_sql_safety

        # Allowlist check: only permit references to this session's registered tables
        safety_error = check_sql_safety(sql, allowed_tables=self.table_names)
        if safety_error:
            raise ValueError(safety_error)

        con = self.get_connection()
        self._last_query_at = time.time()

        timeout_s = timeout if timeout is not None else settings.sql_timeout_seconds
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            from app.tools.join_validation import validate_join_aggregates
            from app.checks import aggregate_coverage
            from app.tools.numeric_precision import validate_numeric_boundaries
            precision = validate_numeric_boundaries(con, sql)
            joins=validate_join_aggregates(con, sql)
            self.last_query_checks=[{'id':'read_only','status':'passed','detail':'仅允许读取当前聊天已注册的表。'},
                {'id':'join_fanout','status':'passed' if joins else 'not_checked',
                 'detail':'已核对列出的单来源聚合在关联后没有重复行；不证明完整业务关联键。' if joins else '此查询没有可独立核对的单来源关联聚合；未据此确认所有关联粒度。','sources':joins},
                *aggregate_coverage(con,sql), *precision]
            result = con.execute(sql)
            if not as_frame:
                return result
            # Bound Arrow batches before pandas copies the complete result into memory.
            reader = result.fetch_record_batch(1024)
            batches, rows, size = [], 0, 0
            try:
                for batch in reader:
                    rows += batch.num_rows
                    size += batch.nbytes
                    if rows > MAX_QUERY_RESULT_ROWS or size > MAX_QUERY_RESULT_BYTES:
                        raise QueryResultLimitExceeded(f'查询结果超过 {MAX_QUERY_RESULT_ROWS:,} 行或 {MAX_QUERY_RESULT_BYTES // 1024 // 1024} MiB 上限，未返回截断结果。请按分析目标先汇总或明确缩小范围，不可仅添加 LIMIT 冒充完整结果。')
                    batches.append(batch)
                table = pa.Table.from_batches(batches, schema=reader.schema)
                from decimal import Decimal
                for field in table.schema:
                    if pa.types.is_decimal(field.type):
                        # Reject decimal-to-float loss before producing numerical evidence.
                        if any(value is not None and Decimal(str(float(value)))!=value
                               for value in table[field.name].to_pylist()):
                            raise ValueError(f'查询结果字段 {field.name} 的精确小数超出当前分析展示的无损范围，已停止返回结果；原始数据保留，可在数据结构中查看精确原值。不能改用浮点转换绕过精度核对。')
                # Keep the numeric types previously produced by DuckDB fetch_df.
                schema = pa.schema([field.with_type(pa.float64()) if pa.types.is_decimal(field.type) else field for field in table.schema])
                return table.cast(schema).to_pandas()
            finally:
                reader.close()
        finally:
            timer.cancel()

    def preview(self) -> list[dict]:
        with self.query_lock:
            con = self.get_connection()
            result = []
            for file in self.files:
                columns = con.execute(f"DESCRIBE {file.table_name}").fetchall()
                counts = self._null_counts(con, file)
                from app.agent.nodes import _sanitize_records
                import pyarrow.parquet as pq
                with pq.ParquetFile(file.parquet_path) as stored:
                    batch=next(stored.iter_batches(batch_size=5),None)
                result.append({"table_name": file.table_name, "filename": file.original_filename,
                               "rows": file.row_count, "columns": file.columns,
                               "dtypes": {col[0]: col[1] for col in columns},
                               "null_counts": counts,
                               "preview": _sanitize_records(batch.to_pylist() if batch is not None else []),
                               "source": file.source})
            return result

    # --- Internals ---

    @staticmethod
    def _null_counts(con, file: IngestedFile) -> dict[str, int]:
        counts = con.execute("SELECT " + ", ".join(
            f'count(*) - count("{name.replace(chr(34), chr(34)*2)}")' for name in file.columns
        ) + f" FROM {file.table_name}").fetchone()
        return dict(zip(file.columns, counts))

    def _register_table(self, f: IngestedFile) -> None:
        """Create a VIEW over the Parquet file so it's queryable by table name."""
        path = f.parquet_path.as_posix().replace("'", "''")
        self._con.execute(
            f"CREATE OR REPLACE VIEW {f.table_name} AS "
            f"SELECT * FROM read_parquet('{path}')"
        )

    @staticmethod
    def _fetch_samples(
        con: duckdb.DuckDBPyConnection, table: str, column: str, n: int
    ) -> list[Any]:
        try:
            rows = con.execute(
                f'SELECT DISTINCT "{column.replace(chr(34), chr(34)*2)}" FROM {table} '
                f'WHERE "{column.replace(chr(34), chr(34)*2)}" IS NOT NULL LIMIT {n}'
            ).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]


# --- Global DatasetManager ---------------------------------------------------


class DatasetManager:
    """Manages per-session datasets with a global memory budget.

    Thread-safe. All public methods acquire the lock.
    """

    def __init__(self, global_budget: int = GLOBAL_BUDGET_BYTES) -> None:
        self._sessions: dict[str, SessionDataset] = {}
        self._lock = threading.Lock()
        self._global_budget = global_budget

    def get(self, session_id: str) -> SessionDataset | None:
        with self._lock:
            if session_id not in self._sessions and settings.app_mode == "local":
                import pyarrow.parquet as pq

                from app.tools.file_storage import local_session_dir

                directory = local_session_dir(session_id)
                files = []
                for path in sorted(directory.glob("*.parquet")):
                    if path.resolve().parent != directory or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path.stem):
                        raise ValueError("Invalid stored table path")
                    metadata = pq.read_metadata(path)
                    schema = pq.read_schema(path)
                    provenance = json.loads((schema.metadata or {}).get(b"datapilot", b"{}"))
                    files.append(IngestedFile(
                        original_filename=provenance.get("filename", path.name), table_name=path.stem,
                        parquet_path=path, columns=schema.names,
                        dtypes={field.name: str(field.type) for field in schema},
                        row_count=metadata.num_rows, size_bytes=path.stat().st_size,
                        source=provenance.get("source", {"kind": "file"}),
                        data_version=provenance.get("data_version", ""),
                    ))
                if files:
                    dataset = SessionDataset(session_id=session_id, parquet_dir=directory, files=files)
                    from app.tools.data_workbench import restore_workbench
                    restore_workbench(dataset)
                    current = sum(item.estimated_memory for item in self._sessions.values())
                    if current + dataset.estimated_memory > self._global_budget:
                        raise MemoryBudgetExceeded("本地数据超过可用内存预算，请减少上传的数据量。")
                    self._sessions[session_id] = dataset
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, parquet_dir: Path) -> SessionDataset:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionDataset(
                    session_id=session_id,
                    parquet_dir=parquet_dir,
                )
            return self._sessions[session_id]

    def add_file(self, session_id: str, ingested: IngestedFile) -> None:
        """Register an ingested file with the session. Raises if over budget."""
        with self._lock:
            self._evict_idle_locked(exclude=session_id)

            ds = self._sessions.get(session_id)
            if ds is None:
                raise ValueError(f"Session {session_id} not found in DatasetManager")

            # Check global budget
            current_total = sum(d.estimated_memory for d in self._sessions.values())
            new_estimated = ingested.size_bytes * EXPANSION_FACTOR
            if current_total + new_estimated > self._global_budget:
                # Try evicting idle sessions to make room
                self._evict_idle_locked(exclude=session_id)
                current_total = sum(d.estimated_memory for d in self._sessions.values())
                if current_total + new_estimated > self._global_budget:
                    raise MemoryBudgetExceeded(
                        f"数据已达到内存预算上限（{self._global_budget / 1024 / 1024:.0f} MB），"
                        "请稍后重试或使用更小的文件。"
                    )

            ds.add_file(ingested)

    def replace_file(self, session_id, ingested, persist):
        """Commit the active pointer only after the replacement fits the existing budget."""
        with self._lock:
            dataset = self._sessions[session_id]
            index = next(i for i, file in enumerate(dataset.files) if file.table_name == ingested.table_name)
            total = sum(item.estimated_memory for item in self._sessions.values())
            total += (ingested.size_bytes - dataset.files[index].size_bytes) * EXPANSION_FACTOR
            if total > self._global_budget:
                raise MemoryBudgetExceeded('清洗结果超过可用内存预算，当前版本未改变。')
            persist()
            dataset.close()
            dataset.files[index] = ingested

    def close_session(self, session_id: str) -> None:
        """Close and remove a session's dataset entirely."""
        with self._lock:
            ds = self._sessions.pop(session_id, None)
            if ds:
                ds.close()

    def close_all(self) -> None:
        """Release old paths before switching the local chat directory."""
        with self._lock:
            for dataset in self._sessions.values():
                dataset.close()
            self._sessions.clear()

    @property
    def total_estimated_memory(self) -> float:
        with self._lock:
            return sum(d.estimated_memory for d in self._sessions.values())

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _evict_idle_locked(self, exclude: str | None = None) -> None:
        """Close connections for idle sessions. Caller must hold the lock."""
        idle = [sid for sid, ds in self._sessions.items() if sid != exclude and ds.is_idle]
        for sid in idle:
            self._sessions[sid].close()
            del self._sessions[sid]
            logger.info("Evicted idle session dataset: %s", sid)


class MemoryBudgetExceeded(Exception):
    """Raised when adding a file would exceed the global memory budget."""
    pass


# --- Module-level singleton --------------------------------------------------

_manager: DatasetManager | None = None


def get_dataset_manager() -> DatasetManager:
    global _manager
    if _manager is None:
        _manager = DatasetManager()
    return _manager


# --- Helper for sample formatting (shared with sql.py) -----------------------

def _fmt_sample(value: Any) -> str:
    if isinstance(value, str):
        if len(value) > 24:
            return f"'{value[:21]}...'"
        return f"'{value}'"
    return str(value)
