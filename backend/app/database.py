"""Local PostgreSQL read-only snapshots; credentials never enter agent state."""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.config import settings
from app.model_settings import SecretStorageError, _dpapi, settings_lock
from app.tools.ingest import store_dataframe

ROW_LIMIT = 50_000


class DatabaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(default="127.0.0.1", pattern=r"^(127\.0\.0\.1|localhost|::1)$")
    port: int = Field(default=55432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    username: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    password: SecretStr | None = None


class TableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str = Field(default="public", min_length=1, max_length=63)
    table_name: str = Field(min_length=1, max_length=63)


class SnapshotInput(BaseModel):
    tables: list[TableRef] = Field(min_length=1, max_length=5)


def _path():
    name = "database-settings.container.bin" if settings.local_secret_key_file else "database-settings.bin"
    path = settings.local_data_dir.resolve() / name
    if path.resolve() != path:
        raise SecretStorageError("数据库配置路径异常，请移除链接后重试。")
    return path


def load_database() -> DatabaseInput | None:
    with settings_lock:
        path = _path()
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 65536:
                raise ValueError("oversized")
            return DatabaseInput.model_validate_json(_dpapi(path.read_bytes(), decrypt=True))
        except Exception:
            raise SecretStorageError("无法读取加密数据库设置，请重新配置。") from None


def save_database(config: DatabaseInput):
    with settings_lock:
        if config.password is None or not config.password.get_secret_value():
            previous = load_database()
            if not previous or config.model_dump(exclude={"password"}) != previous.model_dump(exclude={"password"}):
                raise ValueError("首次配置或更换连接地址、数据库、用户时，请重新填写密码。")
            config = previous
        payload = {**config.model_dump(exclude={"password"}), "password": config.password.get_secret_value()}
        encrypted = _dpapi(json.dumps(payload).encode())
        path = _path()
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            raise SecretStorageError("数据库设置未保存，原配置已保留，请检查磁盘权限。") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def database_status():
    config = load_database()
    return {**(config.model_dump(exclude={"password"}) if config else {}), "configured": config is not None,
            "encrypted": config is not None}


def connect_readonly(config: DatabaseInput | None = None):
    config = config or load_database()
    if config is None:
        raise ValueError("请先保存 PostgreSQL 连接设置。")
    connection = psycopg.connect(host=config.host, port=config.port, dbname=config.database,
                                user=config.username, password=config.password.get_secret_value(),
                                connect_timeout=5, application_name="datapilot-snapshot",
                                options="-c statement_timeout=10000 -c lock_timeout=2000")
    connection.read_only = True
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return connection


def _tables(connection):
    return [{"schema_name": row[0], "table_name": row[1]} for row in connection.execute("""
        SELECT n.nspname, c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r','p','v','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp%%'
          AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')
        ORDER BY 1,2
    """).fetchall()]


def list_database_tables():
    with connect_readonly() as connection:
        return _tables(connection)


def relationship_metadata(connection,schema,table):
    try:
        with connection.transaction():
            rows=connection.execute('''SELECT c.contype,c.conname,
                ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
                    JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.pos),
                rn.nspname,rt.relname,
                ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,pos)
                    JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.pos)
                FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                LEFT JOIN pg_class rt ON rt.oid=c.confrelid LEFT JOIN pg_namespace rn ON rn.oid=rt.relnamespace
                WHERE n.nspname=%s AND t.relname=%s AND c.contype IN ('p','f') ORDER BY c.conname''',(schema,table)).fetchall()
    except psycopg.Error:
        return {'status':'not_visible','primary_key':[],'foreign_keys':[]}
    return {'status':'declared' if rows else 'not_declared_or_not_visible',
        'primary_key':next((row[2] for row in rows if row[0]=='p'),[]),
        'foreign_keys':[{'name':row[1],'columns':row[2],'referenced_schema':row[3],
            'referenced_table':row[4],'referenced_columns':row[5]} for row in rows if row[0]=='f']}


def import_snapshots(dataset, manager, refs: list[TableRef]):
    config = load_database()
    created = []
    try:
        with connect_readonly(config) as connection:
            allowed = {(item["schema_name"], item["table_name"]) for item in _tables(connection)}
            selected = [(ref.schema_name, ref.table_name) for ref in refs]
            if len(set(selected)) != len(selected) or any(ref not in allowed for ref in selected):
                raise ValueError("所选表重复、不存在或没有读取权限，请重新检查连接。")
            names = list(dataset.table_names)
            for schema, table in selected:
                with connection.cursor(name="datapilot_import") as cursor:
                    cursor.execute(sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                    rows, data_bytes = [], 0
                    while batch := cursor.fetchmany(500):
                        rows.extend(batch)
                        data_bytes += sum(len(str(value).encode("utf-8")) for row in batch for value in row if value is not None)
                        if len(rows) > ROW_LIMIT or data_bytes > 10 * 1024 * 1024:
                            raise ValueError(f"单表超过 {ROW_LIMIT:,} 行或 10 MB 内容上限，请建立较小的视图；未截断导入。")
                    columns = [col.name for col in cursor.description]
                file = store_dataframe(f"{table}.csv", pd.DataFrame(rows, columns=columns), dataset.parquet_dir,
                                       names, source={"kind": "postgresql", "host": config.host, "port": config.port,
                                       "database": config.database, "schema": schema, "table": table,
                                       "relationship_metadata":relationship_metadata(connection,schema,table)})
                created.append(file)
                names.append(file.table_name)
        # Keep the whole import batch atomic if the session budget is insufficient.
        from app.tools.dataset_manager import EXPANSION_FACTOR, MemoryBudgetExceeded
        if manager.total_estimated_memory + sum(file.size_bytes * EXPANSION_FACTOR for file in created) > manager._global_budget:
            raise MemoryBudgetExceeded("数据超过会话内存预算，请减少导入表。")
        for file in created:
            manager.add_file(dataset.session_id, file)
        return [{"filename": file.original_filename, "table_name": file.table_name, "rows": file.row_count,
                 "columns": file.columns, "source": file.source} for file in created]
    except Exception:
        for file in created:
            if file not in dataset.files:
                file.parquet_path.unlink(missing_ok=True)
        raise
