"""Trusted fixture code checks preloaded table bindings, without a model call."""
from pathlib import Path

import pandas as pd
import pytest

from app.tools.python_tool import _build_harness


@pytest.mark.parametrize("names", [["customers", "orders"], ["orders", "customers"]])
def test_python_tables_keep_names_when_upload_order_changes(monkeypatch, capsys, names):
    frames = {"customers": pd.DataFrame({"customer_id": ["C1"]}),
              "orders": pd.DataFrame({"amount": [12, 18]})}
    monkeypatch.setattr(pd, "read_parquet", lambda path: frames[Path(path).stem].copy())
    # Only this literal test program is executed here; model code still requires Docker.
    harness = _build_harness("result = dfs['orders']['amount'].sum()", [f"/{n}.parquet" for n in names], table_names=names)
    namespace = {}
    exec(harness, namespace)
    assert capsys.readouterr().out.strip() == "30"
    assert "df" not in namespace
    assert set(namespace["dfs"]) == {"orders", "customers"}


def test_single_table_keeps_df_and_named_binding(monkeypatch, capsys):
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.DataFrame({"amount": [7, 8]}))
    namespace = {}
    exec(_build_harness("result = df['amount'].sum()", ["/export.parquet"], table_names=["sales"]), namespace)
    assert capsys.readouterr().out.strip() == "15"
    assert namespace["df"] is namespace["dfs"]["sales"]
