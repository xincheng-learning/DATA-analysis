"""Prepare existing local data for Docker Desktop without exposing credentials."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".local"
PRIVATE = DATA / "docker-secrets"
RECORDS = DATA / "docker-desktop-setup"
ENV_FILE = DATA / "docker.env"
DOCKER = [shutil.which("docker") or "docker", "--context", "desktop-linux"]
COMPOSE = [*DOCKER, "compose", "--env-file", str(ENV_FILE), "-f", str(ROOT / "compose.yaml")]
sys.path.insert(0, str(ROOT / "backend"))

from app import database, model_settings  # noqa: E402
from app.config import settings  # noqa: E402


def run(command, *, label, data=None, env=None):
    result = subprocess.run(command, cwd=ROOT, input=data, env=env, capture_output=True)
    if result.returncode:
        raise RuntimeError(label)
    return result.stdout


def psql(statement):
    return run([
        *COMPOSE, "exec", "-T", "database", "psql", "-U", "datapilot_admin", "-p", "55432",
        "-d", "datapilot_lab", "-v", "ON_ERROR_STOP=1", "-At",
    ], label="容器数据库操作失败，未输出包含凭据的命令。", data=(statement + "\n").encode()).decode().strip()


def prepare():
    if sys.platform != "win32" or settings.app_mode != "local" or settings.local_data_dir != DATA:
        raise RuntimeError("请在当前 Windows 用户和本项目的本地配置下运行。")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    RECORDS.mkdir(parents=True, exist_ok=True)
    account = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
    run(["icacls", str(PRIVATE), "/inheritance:r", "/grant:r", account + ":(OI)(CI)F",
         "*S-1-5-18:(OI)(CI)F"], label="无法保护容器密钥目录，已停止。")
    key_path = PRIVATE / "storage.key"
    if not key_path.exists():
        if any(DATA.glob("*-settings.container.bin")):
            raise RuntimeError("容器配置已存在但加密密钥缺失，未创建新密钥覆盖。")
        with key_path.open("xb") as stream:
            stream.write(Fernet.generate_key())
    Fernet(key_path.read_bytes())
    password_path = PRIVATE / "postgres-password"
    if not password_path.exists():
        volume = subprocess.run([*DOCKER, "volume", "inspect", "datapilot_postgres_data"], capture_output=True)
        if volume.returncode == 0:
            raise RuntimeError("已有数据库卷但密码文件缺失，未重置密码。")
        with password_path.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(36))

    saved_model = model_settings.load_saved_settings()
    saved_database = database.load_database()
    for filename in ("model-settings.bin", "database-settings.bin", "postgresql-admin.bin"):
        source, destination = DATA / filename, RECORDS / ("original-" + filename)
        if source.exists() and not destination.exists():
            shutil.copy2(source, destination)
    sqlite_path = DATA / "datapilot.sqlite3"
    backup = RECORDS / "before-docker.sqlite3"
    if sqlite_path.exists() and not backup.exists():
        with sqlite3.connect(sqlite_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as target:
                source.backup(target)

    settings.local_secret_key_file = key_path
    try:
        if saved_model and not model_settings._config_path().exists():
            model_settings.save_model_settings(saved_model)
        if saved_database and not database._path().exists():
            database.save_database(saved_database)
        if saved_model:
            assert model_settings.load_saved_settings().api_key is not None
        if saved_database:
            assert database.load_database().database == saved_database.database
    finally:
        settings.local_secret_key_file = None

    (DATA / "container-tmp").mkdir(exist_ok=True)
    name = "datapilot-mount-check-" + uuid.uuid4().hex
    run([*DOCKER, "create", "--name", name, "--mount", f"type=bind,source={DATA},target=/app/.local,readonly",
         "python:3.10-slim", "true"], label="Docker Desktop 无法挂载本项目目录。")
    try:
        mounts = json.loads(run([*DOCKER, "inspect", name], label="无法核对项目挂载。"))[0]["Mounts"]
        host_root = next(mount["Source"] for mount in mounts if mount["Destination"] == "/app/.local").replace("\\", "/")
        run([*DOCKER, "run", "--rm", "--volume", "/var/run/docker.sock:/var/run/docker.sock",
             "datapilot-backend:local", "docker", "run", "--rm", "--mount",
             f"type=bind,source={host_root},target=/verify,readonly", "python:3.10-slim", "python", "-c",
             "from pathlib import Path; assert Path('/verify/container-tmp').is_dir()"],
            label="容器中的隔离环境无法读取项目挂载，未继续。")
        ENV_FILE.write_text("DOCKER_BIND_ROOT=" + host_root + "\n", encoding="utf-8")
    finally:
        run([*DOCKER, "rm", name], label="临时挂载检查容器清理失败。")
    print(json.dumps({"prepared": True, "model_preserved": bool(saved_model),
                      "database_preserved": bool(saved_database)}, ensure_ascii=False))


def import_database():
    import psycopg
    from psycopg import sql

    marker = RECORDS / "database-import.json"
    if marker.exists():
        expected = json.loads(marker.read_text(encoding="utf-8"))["rows"]
        assert all(int(psql(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table)).as_string())) == count
                   for table, count in expected.items())
        print(json.dumps({"database_already_imported": True, "rows": expected}, ensure_ascii=False))
        return
    config = database.load_database()
    if config is None:
        print('{"database_configured": false}')
        return
    if (config.host, config.port, config.database, config.username) != (
        "127.0.0.1", 55432, "datapilot_lab", "datapilot_reader",
    ):
        raise RuntimeError("现有数据库不是本项目的独立实验库，未自动迁移其他数据库。")
    if int(psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'")):
        raise RuntimeError("容器数据库已有数据且无导入记录，未覆盖。")
    runtime = Path(os.environ["LOCALAPPDATA"]) / "DataPilot/runtime/postgresql-17/bin"
    pg_ctl = runtime / "pg_ctl.exe"
    was_running = pg_ctl.exists() and subprocess.run(
        [str(pg_ctl), "-D", str(DATA / "postgresql-data"), "status"], capture_output=True,
    ).returncode == 0
    run([sys.executable, "-X", "utf8", "scripts/local_postgres.py", "start"], label="原实验数据库启动失败。")
    try:
        admin_password = model_settings._dpapi((DATA / "postgresql-admin.bin").read_bytes(), decrypt=True).decode()
        with psycopg.connect(host=config.host, port=config.port, dbname=config.database,
                             user="datapilot_admin", password=admin_password) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
            expected = {table: connection.execute(sql.SQL("SELECT count(*) FROM public.{}").format(
                sql.Identifier(table))).fetchone()[0] for table in tables}
        dump = run([str(runtime / "pg_dump.exe"), "-h", config.host, "-p", str(config.port), "-U", "datapilot_admin",
                    "-d", config.database, "--format=custom", "--no-owner", "--no-privileges"],
                   label="原数据库导出失败。", env={**os.environ, "PGPASSWORD": admin_password})
    finally:
        if not was_running:
            run([sys.executable, "-X", "utf8", "scripts/local_postgres.py", "stop"], label="临时启动的原实验数据库未能停止。")
    (RECORDS / "before-docker-postgres.dump").write_bytes(dump)
    role_exists = psql(sql.SQL("SELECT count(*) FROM pg_roles WHERE rolname={}").format(
        sql.Literal(config.username)).as_string())
    if role_exists == "0":
        psql(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {};").format(
            sql.Identifier(config.username), sql.Literal(config.password.get_secret_value())).as_string())
    run([*COMPOSE, "exec", "-T", "database", "pg_restore", "-U", "datapilot_admin", "-p", "55432",
         "-d", "datapilot_lab", "--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges"],
        label="数据库导入失败；数据导入事务已回滚。", data=dump)
    psql(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT CONNECT ON DATABASE datapilot_lab TO {}; "
                 "GRANT USAGE ON SCHEMA public TO {}; GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}; "
                 "ALTER ROLE {} SET default_transaction_read_only=on;").format(
        *[sql.Identifier(config.username)] * 4).as_string())
    actual = {table: int(psql(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table)).as_string()))
              for table in tables}
    assert actual == expected
    assert all(psql(sql.SQL("SELECT has_table_privilege({}, {}, 'UPDATE')").format(
        sql.Literal(config.username), sql.Literal("public." + table)).as_string()) == "f" for table in tables)
    marker.write_text(json.dumps({"rows": actual}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"database_imported": True, "rows": actual, "reader_cannot_update": True}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "import-db"])
    action = parser.parse_args().action
    try:
        (prepare if action == "prepare" else import_database)()
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("配置未完成：" + message, file=sys.stderr)
        raise SystemExit(1) from None
