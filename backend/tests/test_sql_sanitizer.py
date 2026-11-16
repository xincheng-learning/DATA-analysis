"""Regressions captured from real gold baseline SQL, plus boundary attacks."""
import io

import pytest

from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import validate_and_convert
from app.tools.sql_sanitizer import check_sql_safety


def test_reserved_cte_name_returns_the_parser_diagnostic_without_relaxing_safety():
    error = check_sql_safety('WITH pivot AS (SELECT 1) SELECT * FROM pivot',[])
    assert error and 'pivot' in error.lower() and 'syntax' in error.lower()
    assert check_sql_safety('WITH "pivot" AS (SELECT 1) SELECT * FROM "pivot"',[]) is None


@pytest.mark.parametrize("query", [
    "WITH base AS (SELECT * FROM orders), p AS (SELECT * FROM base) SELECT * FROM p",
    "SELECT EXTRACT(month FROM CAST(order_date AS DATE)) FROM orders",
    "WITH monthly AS (SELECT '2026-01' AS month, 715 AS sales FROM orders) SELECT MAX(CASE WHEN month='2026-01' THEN sales END) * 100.0 / NULLIF(MAX(CASE WHEN month='2026-02' THEN sales END),0) FROM monthly",
    "SELECT 'from not_a_table' AS note FROM orders",
    'WITH "Monthly Sales" AS (SELECT * FROM "orders") SELECT * FROM "Monthly Sales"',
    "SELECT '华东' AS region FROM orders a JOIN orders b USING(order_id)",
])
def test_ordinary_analysis_is_not_rejected_as_table_or_file_access(query):
    assert check_sql_safety(query, ["orders"]) is None


@pytest.mark.parametrize("query", [
    "SELECT * FROM orders; DELETE FROM orders",
    "WITH x AS (SELECT * FROM hidden) SELECT * FROM x",
    'SELECT * FROM "hidden"',
    "SELECT * FROM orders, hidden",
    "SELECT * FROM 'C:/private.csv'",
    "SELECT * FROM read_csv('C:/private.csv')",
    'SELECT * FROM "read_parquet"(\'C:/private.parquet\')',
    "SELECT * FROM query('SELECT * FROM hidden')",
    "SELECT * FROM query_table('hidden')",
    "SELECT * FROM orders /* ok */; COPY orders TO 'C:/out.csv'",
    "SELECT * FROM orders; SELECT * FROM hidden",
    "SELECT '华东' AS region FROM orders a JOIN hidden b USING(order_id)",
])
def test_boundary_cannot_be_relaxed_by_cte_fix(query):
    assert check_sql_safety(query, ["orders"]) is not None


def test_cte_runs_through_real_session_without_mutation(tmp_path):
    dataset = SessionDataset(session_id="safe", parquet_dir=tmp_path)
    dataset.add_file(validate_and_convert("orders.csv", io.BytesIO(b"order_date,amount\n2026-01-01,715\n2026-02-01,590\n"), tmp_path))
    try:
        result = dataset.execute("WITH monthly AS (SELECT EXTRACT(month FROM CAST(order_date AS DATE)) m, amount FROM orders) SELECT sum(amount) FROM monthly").fetchone()
        assert result == (1305,)
        with pytest.raises(ValueError):
            dataset.execute("SELECT * FROM orders; DELETE FROM orders")
        assert dataset.execute("SELECT count(*) FROM orders").fetchone() == (2,)
    finally:
        dataset.close()
