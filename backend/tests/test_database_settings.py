from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from app import database
from app.config import settings


def test_database_password_encrypted_and_bound_to_exact_connection(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    config = database.DatabaseInput(database="lab", username="reader", password=SecretStr("test-only-db-secret"))
    database.save_database(config)
    assert b"test-only-db-secret" not in (tmp_path / "database-settings.bin").read_bytes()
    assert database.load_database().password.get_secret_value() == "test-only-db-secret"
    assert "test-only-db-secret" not in str(database.database_status())
    database.save_database(config.model_copy(update={"password": None}))
    with pytest.raises(ValueError, match="重新填写"):
        database.save_database(config.model_copy(update={"password": None, "port": 5432}))
    assert database.load_database().port == 55432


@pytest.mark.parametrize("change", [{"host": "remote.example"}, {"host": "127.0.0.1.evil"}, {"database": "postgresql://evil"}, {"database": "db host=evil"}, {"port": 0}])
def test_database_rejects_remote_hosts_and_libpq_connection_expansion(change):
    with pytest.raises(ValidationError):
        database.DatabaseInput(**{"database": "lab", "username": "reader", **change})


def test_snapshot_rejects_oversize_without_registering_or_leaving_files(monkeypatch, tmp_path):
    from app.tools.dataset_manager import DatasetManager
    monkeypatch.setattr(settings, "app_mode", "cloud")
    monkeypatch.setattr(database, "load_database", lambda: database.DatabaseInput(database="lab", username="reader"))
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchmany.side_effect = [[("x",), ("y",)], []]
    cursor.description = [SimpleNamespace(name="value")]
    monkeypatch.setattr(database, "connect_readonly", lambda *args: connection)
    monkeypatch.setattr(database, "_tables", lambda connection: [{"schema_name": "public", "table_name": "sales"}])
    monkeypatch.setattr(database, "ROW_LIMIT", 1)
    manager = DatasetManager()
    dataset = manager.get_or_create("oversize", tmp_path)
    with pytest.raises(ValueError, match="未截断"):
        database.import_snapshots(dataset, manager, [database.TableRef(table_name="sales")])
    assert not dataset.files and not list(tmp_path.glob("*.parquet"))
    with pytest.raises(ValueError, match="权限"):
        database.import_snapshots(dataset, manager, [database.TableRef(table_name="private")])


def test_optional_relationship_metadata_retains_composite_order_and_recovers_permission_failure():
    import psycopg
    connection=MagicMock()
    connection.execute.return_value.fetchall.return_value=[('p','pk',['tenant','id'],None,None,[]),
        ('f','fk',['tenant','owner'],'public','owners',['tenant','code'])]
    meta=database.relationship_metadata(connection,'public','events')
    assert meta['primary_key']==['tenant','id']
    assert meta['foreign_keys'][0]['referenced_columns']==['tenant','code']
    connection.execute.side_effect=psycopg.errors.InsufficientPrivilege('no metadata rights')
    assert database.relationship_metadata(connection,'public','events')['status']=='not_visible'
    assert connection.transaction.call_count==2
