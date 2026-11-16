"""Tests for the file ingestion pipeline.

No LLM calls, no DuckDB, no network — pure file validation and conversion.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from app.tools.ingest import (
    IngestionError,
    IngestedFile,
    MAX_FILE_SIZE_BYTES,
    _derive_table_name,
    validate_and_convert,
)


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "parquet_out"


def _csv_bytes(rows: int = 10, cols: int = 3) -> io.BytesIO:
    """Generate a simple CSV in memory."""
    header = ",".join(f"col_{i}" for i in range(cols))
    body = "\n".join(",".join(str(r * cols + c) for c in range(cols)) for r in range(rows))
    return io.BytesIO(f"{header}\n{body}".encode())


def _excel_bytes(rows: int = 10) -> io.BytesIO:
    """Generate a simple Excel file in memory."""
    df = pd.DataFrame({"x": range(rows), "y": range(rows)})
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return buf


# --- Happy paths ---


def test_csv_ingestion_produces_parquet(tmp_output: Path) -> None:
    result = validate_and_convert("sales.csv", _csv_bytes(), tmp_output)
    assert isinstance(result, IngestedFile)
    assert result.table_name == "sales"
    assert result.parquet_path.exists()
    assert result.parquet_path.suffix == ".parquet"
    assert result.row_count == 10
    assert len(result.columns) == 3
    assert result.size_bytes > 0


def test_excel_ingestion_produces_parquet(tmp_output: Path) -> None:
    result = validate_and_convert("report.xlsx", _excel_bytes(), tmp_output)
    assert result.table_name == "report"
    assert result.row_count == 10
    assert result.parquet_path.exists()


def test_unsupported_decimal_precision_is_rejected_before_parquet_is_saved(tmp_output):
    from decimal import Decimal
    from app.tools.ingest import store_dataframe
    frame=pd.DataFrame({'amount':[Decimal('9007199254740993.01'),Decimal('8.276505322196654E-20')]})
    with pytest.raises(IngestionError,match='小数精度超过38位'):
        store_dataframe('unsupported.csv',frame,tmp_output)
    assert not (tmp_output/'unsupported.parquet').exists()


@pytest.mark.parametrize('ext', ['csv', 'xlsx'])
@pytest.mark.parametrize('column', ['order_no', 'invoice_no', 'order_number', 'batch'])
def test_identifiers_preserve_distinct_values_and_measures_stay_numeric(tmp_output, ext, column):
    frame = pd.DataFrame({column: ['000123', '123', None], 'amount': [10, 20, 30]})
    buf = io.BytesIO()
    if ext == 'csv':
        buf.write(frame.to_csv(index=False).encode())
    else:
        frame.to_excel(buf, index=False, engine='openpyxl')
    buf.seek(0)
    result = validate_and_convert('orders.' + ext, buf, tmp_output)
    restored = pd.read_parquet(result.parquet_path)
    assert restored[column].dropna().tolist() == ['000123', '123']
    assert restored['amount'].sum() == 60
    assert pd.api.types.is_numeric_dtype(restored['amount'])


def test_unnamed_long_integer_column_does_not_round_through_float(tmp_output):
    result = validate_and_convert('orders.csv', io.BytesIO(b'batch,amount\n12345678901234567,10\n,20\n12345678901234568,30\n'), tmp_output)
    restored = pd.read_parquet(result.parquet_path)
    assert restored['batch'].dropna().tolist() == ['12345678901234567', '12345678901234568']


def test_parquet_is_smaller_than_raw_csv(tmp_output: Path) -> None:
    csv_data = _csv_bytes(rows=1000, cols=10)
    raw_size = len(csv_data.getvalue())
    result = validate_and_convert("big.csv", csv_data, tmp_output)
    # Parquet should be smaller (or at worst comparable for tiny numeric data)
    assert result.size_bytes <= raw_size * 1.5  # generous bound


# --- Validation errors ---


def test_rejects_unsupported_extension(tmp_output: Path) -> None:
    with pytest.raises(IngestionError, match="不支持文件类型"):
        validate_and_convert("data.json", io.BytesIO(b"{}"), tmp_output)


def test_rejects_oversized_file(tmp_output: Path) -> None:
    huge = io.BytesIO(b"x" * (MAX_FILE_SIZE_BYTES + 1))
    with pytest.raises(IngestionError, match="超过"):
        validate_and_convert("huge.csv", huge, tmp_output)


def test_rejects_empty_file(tmp_output: Path) -> None:
    with pytest.raises(IngestionError, match="文件为空"):
        validate_and_convert("empty.csv", io.BytesIO(b""), tmp_output)


def test_rejects_unparseable_csv(tmp_output: Path) -> None:
    garbage = io.BytesIO(b"\x00\x01\x02\x03\x04\x05")
    # pandas may or may not raise — but if it "parses" garbage with no
    # real columns, we still reject it
    with pytest.raises(IngestionError):
        validate_and_convert("bad.csv", garbage, tmp_output)


# --- Table name derivation ---


def test_table_name_from_normal_filename() -> None:
    assert _derive_table_name("Sales Data 2024.csv", []) == "sales_data_2024"


def test_table_name_strips_special_chars() -> None:
    assert _derive_table_name("my-file (1).xlsx", []) == "my_file_1"


def test_table_name_handles_collision() -> None:
    assert _derive_table_name("sales.csv", ["sales"]) == "sales_2"
    assert _derive_table_name("sales.csv", ["sales", "sales_2"]) == "sales_3"


def test_table_name_prepends_if_starts_with_number() -> None:
    name = _derive_table_name("2024_data.csv", [])
    assert name[0].isalpha()  # must start with letter for SQL


def test_identifiers_and_na_codes_survive_and_raw_file_is_retained(tmp_output):
    original = b'order_id,sku,amount\n0007,NA,12.50\n0008,X,\n'
    result = validate_and_convert('sales.csv', io.BytesIO(original), tmp_output)
    frame = pd.read_parquet(result.parquet_path)
    assert frame.order_id.tolist() == ['0007', '0008']
    assert frame.sku.tolist() == ['NA', 'X']
    assert frame.amount.iloc[0] == 12.5
    assert pd.isna(frame.amount.iloc[1])
    raw = tmp_output / result.source['raw_file']
    assert raw.read_bytes() == original
    assert result.data_version
