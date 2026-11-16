from __future__ import annotations

import io
from decimal import Decimal

import pandas as pd
import pytest

from app.config import settings
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import IngestionError, store_dataframe, validate_and_convert


def test_file_and_database_sources_preview_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    directory = tmp_path / "uploads" / "joint"
    manager = DatasetManager()
    manager.get_or_create("joint", directory)
    data = io.BytesIO()
    pd.DataFrame({"month": ["2026-01", "2026-02"], "target": [120, 180]}).to_excel(data, index=False)
    data.seek(0)
    manager.add_file("joint", validate_and_convert("targets.xlsx", data, directory))
    manager.add_file("joint", store_dataframe(
        "actuals.csv", pd.DataFrame({"month": ["2026-01", "2026-02"], "actual": [100, None]}), directory,
        source={"kind": "postgresql", "database": "lab", "schema": "public", "table": "actuals"},
    ))
    dataset = manager.get("joint")
    preview = dataset.preview()
    assert len(preview) == 2
    actual = next(item for item in preview if item["table_name"] == "actuals")
    assert actual["null_counts"]["actual"] == 1
    assert actual["source"]["kind"] == "postgresql"
    assert actual["source"]["imported_at"]
    assert actual["preview"][1]["actual"] is None
    result = dataset.execute("SELECT a.actual, t.target FROM actuals a JOIN targets t USING(month) ORDER BY month").fetchall()
    assert result == [(100.0, 120), (None, 180)]
    dataset.close()
    restored = DatasetManager().get("joint").preview()
    assert {item["filename"] for item in restored} == {"targets.xlsx", "actuals.csv"}
    assert next(item for item in restored if item["table_name"] == "actuals")["source"]["kind"] == "postgresql"


def test_failed_parquet_write_never_leaves_a_broken_restorable_file(monkeypatch, tmp_path):
    def fail(table, destination):
        from pathlib import Path
        Path(destination).write_bytes(b"incomplete")
        raise OSError("disk full")
    monkeypatch.setattr("app.tools.ingest.pq.write_table", fail)
    with pytest.raises(IngestionError):
        store_dataframe("broken.csv", pd.DataFrame({"a": [1]}), tmp_path)
    assert not list(tmp_path.iterdir())


def test_decimal_literal_strings_and_idle_session_import_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_mode", "cloud")
    manager = DatasetManager()
    dataset = manager.get_or_create("idle", tmp_path)
    dataset._last_query_at = 0
    manager.add_file("idle", store_dataframe("money.csv", pd.DataFrame({"amount": [Decimal("1.20"), None],
        "label": ["None", "nan"]}), tmp_path, source={"kind": "postgresql"}))
    assert dataset.execute("SELECT * FROM money").fetchall() == [(Decimal("1.20"), "None"), (None, "nan")]
    dataset.close()
