"""Set up/start/stop the isolated learning database. Never touches MySQL."""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.model_settings import _dpapi  # noqa: E402
from app.session import get_session_store  # noqa: E402
from app.tools.dataset_manager import get_dataset_manager  # noqa: E402
from app.tools.file_storage import local_session_dir  # noqa: E402
from app.tools.ingest import validate_and_convert  # noqa: E402

RUNTIME = Path(os.environ["LOCALAPPDATA"]) / "DataPilot/runtime/postgresql-17"
BIN = RUNTIME / "bin"
DATA = ROOT / ".local/postgresql-data"
ADMIN = ROOT / ".local/postgresql-admin.bin"
PORT = 55432


def pg_ctl(action):
    # The long-lived Windows server can inherit pipes and keep communicate() waiting.
    with tempfile.TemporaryFile(mode="w+", encoding="mbcs", errors="replace") as output, \
            tempfile.TemporaryFile(mode="w+", encoding="mbcs", errors="replace") as error:
        result = subprocess.run([str(BIN / "pg_ctl.exe"), "-D", str(DATA), "-l", str(ROOT / ".local/postgresql.log"),
                                 "-w", "-t", "30", action], cwd=ROOT, stdout=output, stderr=error,
                                timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        output.seek(0)
        error.seek(0)
        result.stdout, result.stderr = output.read(), error.read()
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode and action != "status":
        raise RuntimeError(result.stderr.strip() or "PostgreSQL 操作失败，请查看 .local/postgresql.log")
    return result


def start():
    # Windows initdb embeds its binary path in SQL; non-ASCII paths fail under UTF8.
    if not BIN.exists():
        for name in ("bin", "lib", "share"):
            shutil.copytree(ROOT / ".local/runtime/postgresql-17/pgsql" / name, RUNTIME / name)
    if not (DATA / "PG_VERSION").exists():
        if DATA.exists() and any(DATA.iterdir()):
            raise RuntimeError("数据目录非空且不是有效 PostgreSQL 集群，已停止，未覆盖任何文件。")
        if ADMIN.exists():
            password = _dpapi(ADMIN.read_bytes(), decrypt=True).decode()
        else:
            password = secrets.token_urlsafe(36)
            ADMIN.write_bytes(_dpapi(password.encode()))
        with tempfile.NamedTemporaryFile(dir=ROOT / ".local", delete=False) as stream:
            path = Path(stream.name)
            stream.write(password.encode())
        try:
            result = subprocess.run([str(BIN / "initdb.exe"), "-D", str(DATA.relative_to(ROOT)), "-U", "datapilot_admin",
                            "--encoding=UTF8", "--no-locale", "--auth=scram-sha-256", f"--pwfile={path.relative_to(ROOT)}"],
                           cwd=ROOT, capture_output=True)
            print(result.stdout.decode("utf-8", errors="replace"))
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        finally:
            path.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\nshared_buffers = '32MB'\n"
                         "max_connections = 10\nwork_mem = '4MB'\nmax_parallel_workers_per_gather = 0\n")
    if pg_ctl("status").returncode != 0:
        pg_ctl("start")


def setup():
    if settings.app_mode != "local" or settings.session_backend != "sqlite" or settings.local_data_dir != ROOT / ".local":
        raise RuntimeError("实验安装仅接受本项目 .local 数据目录和 local/sqlite 模式。")
    current = database.load_database()
    if current and (current.database, current.username, current.port, current.host) != ("datapilot_lab", "datapilot_reader", PORT, "127.0.0.1"):
        raise RuntimeError("已有其他数据库配置，未覆盖；请保留配置并手动选择实验连接。")
    start()
    password = _dpapi(ADMIN.read_bytes(), decrypt=True).decode()
    params = dict(host="127.0.0.1", port=PORT, user="datapilot_admin", password=password, connect_timeout=5)
    readonly_password = secrets.token_urlsafe(36)
    with psycopg.connect(**params, dbname="postgres", autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname='datapilot_lab'").fetchone():
            connection.execute("CREATE DATABASE datapilot_lab")
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='datapilot_reader'").fetchone():
            connection.execute(sql.SQL("CREATE ROLE datapilot_reader LOGIN PASSWORD {}").format(sql.Literal(readonly_password)))
        else:
            config = database.load_database()
            if not config or config.username != "datapilot_reader" or config.database != "datapilot_lab" or config.port != PORT:
                raise RuntimeError("已有实验账号但网页连接不同，已停止，未重置密码或覆盖配置。")
            readonly_password = config.password.get_secret_value()
        connection.execute("GRANT CONNECT ON DATABASE datapilot_lab TO datapilot_reader")
        connection.execute("ALTER ROLE datapilot_reader SET default_transaction_read_only = on")
    records = [("2026-01", "华东", 600), ("2026-01", "华南", 400), ("2026-01", "西部", 200),
               ("2026-02", "华东", 300), ("2026-02", "华南", 360), ("2026-02", "西部", 240)]
    with psycopg.connect(**params, dbname="datapilot_lab") as connection:
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute("CREATE TABLE IF NOT EXISTS sales_actuals (month text, region text, revenue numeric(12,2), PRIMARY KEY(month,region))")
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO sales_actuals VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", records)
        connection.execute("GRANT USAGE ON SCHEMA public TO datapilot_reader")
        connection.execute("GRANT SELECT ON sales_actuals TO datapilot_reader")
    database.save_database(database.DatabaseInput(database="datapilot_lab", username="datapilot_reader", password=SecretStr(readonly_password)))
    with database.connect_readonly() as connection:
        assert connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    try:
        with psycopg.connect(host="127.0.0.1", port=PORT, dbname="datapilot_lab", user="datapilot_reader", password=readonly_password) as connection:
            connection.execute("SET transaction_read_only = off")
            connection.execute("UPDATE sales_actuals SET revenue = revenue WHERE false")
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise AssertionError("Read-only account unexpectedly has UPDATE permission")

    example = ROOT / ".local/examples/sales_targets.xlsx"
    example.parent.mkdir(parents=True, exist_ok=True)
    if not example.exists():
        pd.DataFrame([(m, r, value) for (m, r, _), value in zip(records, [600, 400, 200, 650, 400, 250], strict=True)],
                     columns=["month", "region", "target"]).to_excel(example, index=False)
    sid = "postgres-excel-lab"
    store = get_session_store()
    store.get_or_create(sid, user_id="local-user")
    manager = get_dataset_manager()
    dataset = manager.get_or_create(sid, local_session_dir(sid))
    if "sales_actuals" not in dataset.table_names:
        database.import_snapshots(dataset, manager, [database.TableRef(table_name="sales_actuals")])
    if "sales_targets" not in dataset.table_names:
        manager.add_file(sid, validate_and_convert(example.name, io.BytesIO(example.read_bytes()), dataset.parquet_dir, dataset.table_names))
    query = """SELECT a.month, SUM(a.revenue) AS actual, SUM(t.target) AS target,
        SUM(a.revenue)-SUM(t.target) AS gap, ROUND(100*SUM(a.revenue)/SUM(t.target),2) AS attainment_pct
        FROM sales_actuals a JOIN sales_targets t USING(month,region) GROUP BY a.month ORDER BY a.month"""
    rows = dataset.execute(query).fetchall()
    assert [tuple(float(value) if i else value for i, value in enumerate(row)) for row in rows] == [
        ("2026-01", 1200, 1200, 0, 100), ("2026-02", 900, 1300, -400, 69.23)], rows
    backend = store._backend
    backend.set_title(sid, "实验：PostgreSQL 销售 + Excel 目标", only_if_empty=True)
    if not backend.get_messages(sid):
        backend.save_message(sid, "user", "已准备模拟实验：PostgreSQL 实际销售与 Excel 销售目标，按月份和区域联合分析。", {"type": "upload"})
    print(json.dumps({"session_id": sid, "port": PORT, "readonly_verified": True, "joint_query_verified": True,
                      "rows": [[str(value) for value in row] for row in rows]}, ensure_ascii=False))
    dataset.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "start", "stop", "status"], nargs="?", default="setup")
    action = parser.parse_args().action
    if action == "setup":
        setup()
    elif action == "start":
        start()
    else:
        raise SystemExit(pg_ctl(action).returncode)
