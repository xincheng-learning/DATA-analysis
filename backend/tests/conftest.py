"""Pytest fixtures shared across backend tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import app` work when tests are run from anywhere.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tools.sql import SQLTool  # noqa: E402


@pytest.fixture(scope="session")
def sql_tool(tmp_path_factory) -> SQLTool:
    """Small offline data preserves the order/item grain and timestamp checks."""
    csv_path = tmp_path_factory.mktemp("sql-tool") / "orders.csv"
    csv_path.write_text(
        "order_id,order_purchase_timestamp,price,product_category_en\n"
        "order1,2026-01-01 10:00:00,100,books\n"
        "order1,2026-01-01 10:00:00,200,books\n"
        "order2,2026-02-01 10:00:00,50,home\n",
        encoding="utf-8",
    )
    tool = SQLTool(
        csv_path=csv_path,
        column_type_overrides={"order_purchase_timestamp": "TIMESTAMP"},
    )
    yield tool
    tool.con.close()
