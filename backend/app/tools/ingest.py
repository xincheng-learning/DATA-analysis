"""File ingestion: validate, convert CSV/Excel → Parquet, register with session.

The pipeline:
  1. Validate: file extension, size cap, parseable content
  2. Read into a pandas DataFrame (temporarily in memory — small since cap is 10MB)
  3. Write as Parquet (lossless compression, typically 2-10x smaller)
  4. Return metadata (table name, column schema, row count, Parquet path)

Why Parquet:
  - Smaller on disk → saves Supabase Storage / local space
  - DuckDB queries Parquet directly from disk via read_parquet()
  - No RAM overhead at query time (unlike loading a full CSV into memory)
  - Lossless — identical data, just a different container
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILES_PER_BATCH = 5
MAX_ROWS = 500_000  # reject files with more rows than this
MAX_COLUMNS = 200   # reject files with more columns than this

# Table name sanitization: only allow [a-z0-9_], replace everything else.
_TABLE_NAME_RE = re.compile(r"[^a-z0-9_]")


# --- Exceptions --------------------------------------------------------------


class IngestionError(Exception):
    """Raised when a file fails validation or conversion."""
    pass


# --- Data classes ------------------------------------------------------------


@dataclass
class IngestedFile:
    """Result of successfully ingesting one file."""
    original_filename: str
    table_name: str
    parquet_path: Path
    columns: list[str]
    dtypes: dict[str, str]  # column → Parquet/pandas dtype as string
    row_count: int
    size_bytes: int  # Parquet file size on disk
    source: dict = field(default_factory=dict)
    data_version: str = ""


# --- Public API --------------------------------------------------------------


def validate_and_convert(
    filename: str,
    file_data: BinaryIO,
    output_dir: Path,
    existing_table_names: list[str] | None = None,
) -> IngestedFile:
    """Validate an uploaded file and convert it to Parquet.

    Parameters
    ----------
    filename : str
        Original filename from the upload (e.g., "sales_2024.csv").
    file_data : BinaryIO
        File-like object with the raw bytes.
    output_dir : Path
        Directory where the Parquet file will be written.
    existing_table_names : list[str] | None
        Table names already in this session — used to detect collisions.

    Returns
    -------
    IngestedFile with metadata about the converted file.

    Raises
    ------
    IngestionError on any validation or conversion failure.
    """
    # --- Extension check ---
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestionError(
            f"不支持文件类型“{ext}”，支持的格式：{', '.join(sorted(ALLOWED_EXTENSIONS))}。"
        )

    # --- Size check (read all bytes — we need them for pandas anyway) ---
    raw_bytes = file_data.read()
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(raw_bytes) / (1024 * 1024)
        cap_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise IngestionError(
            f"文件大小为 {size_mb:.1f} MB，超过 {cap_mb:.0f} MB 的上限。"
        )

    if len(raw_bytes) == 0:
        raise IngestionError("文件为空，请上传包含数据的文件。")

    # --- Parse into DataFrame ---
    import io

    try:
        reader = pd.read_csv if ext == '.csv' else pd.read_excel
        options = {'engine': 'openpyxl'} if ext == '.xlsx' else {}
        raw = reader(io.BytesIO(raw_bytes), dtype='string', keep_default_na=False, na_values=[''], **options)
        # Inspect values before inference can erase leading zeros or round long integers.
        identifiers = {name: 'string' for name in raw.columns if _is_identifier(name)
                       or raw[name].str.fullmatch(r'[+-]?(?:0\d+|\d{16,})').any()}
        del raw
        df = reader(io.BytesIO(raw_bytes), dtype=identifiers, keep_default_na=False, na_values=[''], **options)
    except IngestionError:
        raise
    except Exception as e:
        raise IngestionError(f"无法解析文件，请检查文件格式和内容。技术详情：{type(e).__name__}: {e}") from e

    version = uuid.uuid4().hex
    sources = output_dir / "_sources"
    if sources.resolve() != sources.absolute():
        raise IngestionError("原始文件目录不能重定向到其他位置。")
    sources.mkdir(parents=True, exist_ok=True)
    original = sources / (version + ext)
    try:
        original.write_bytes(raw_bytes)
        return store_dataframe(filename, df, output_dir, existing_table_names,
                               source={"kind": "file", "raw_file": original.relative_to(output_dir).as_posix(),
                                       "raw_sha256": hashlib.sha256(raw_bytes).hexdigest()}, data_version=version)
    except Exception:
        original.unlink(missing_ok=True)
        raise


def store_dataframe(filename: str, df: pd.DataFrame, output_dir: Path,
                    existing_table_names: list[str] | None = None, *, source: dict | None = None,
                    data_version: str | None = None) -> IngestedFile:
    """Share lossless snapshot storage between uploads and database imports."""
    if df.empty or len(df.columns) == 0:
        raise IngestionError("文件中没有数据或列，请检查后重新上传。")

    if len(df) == 0:
        raise IngestionError("文件只有列名，没有数据行。")

    if len(df) > MAX_ROWS:
        raise IngestionError(
            f"文件有 {len(df):,} 行，超过 {MAX_ROWS:,} 行的上限。"
        )

    if len(df.columns) > MAX_COLUMNS:
        raise IngestionError(
            f"文件有 {len(df.columns)} 列，超过 {MAX_COLUMNS} 列的上限。"
        )

    # --- Derive table name ---
    table_name = _derive_table_name(filename, existing_table_names or [])

    # --- Write Parquet ---
    df = df.copy()
    df.columns = [str(col) for col in df.columns]
    if df.columns.duplicated().any():
        raise IngestionError("存在重复列名，请先修改列名。")
    # Preserve Decimal and null values; only mixed Excel columns need coercion.
    if not source or source.get("kind") == "file":
        for col in df.columns:
            if df[col].dtype == "object":
                try:
                    pa.array(df[col])
                except (pa.ArrowInvalid, pa.ArrowTypeError):
                    df[col] = df[col].map(lambda value: None if pd.isna(value) else str(value))

    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{table_name}.parquet"

    temporary = None
    data_version = data_version or uuid.uuid4().hex
    try:
        provenance = {"imported_at": datetime.now(timezone.utc).isoformat(), **(source or {"kind": "file"})}
        table = pa.Table.from_pandas(df, preserve_index=False)
        unsupported=[field.name for field in table.schema if pa.types.is_decimal(field.type) and field.type.precision>38]
        if unsupported:
            raise IngestionError('字段 '+ '、'.join(unsupported)+' 的小数精度超过38位，当前查询引擎不支持；请在来源中保留为精确文本或降低所需精度后导入。未保存或替换数据。')
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), b"datapilot": json.dumps({
            "filename": filename, "source": provenance, "data_version": data_version,
        }, ensure_ascii=False).encode("utf-8")})
        with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".tmp", delete=False) as stream:
            temporary = stream.name
        pq.write_table(table, temporary)
        os.replace(temporary, parquet_path)
    except IngestionError:
        raise
    except Exception as e:
        raise IngestionError(f"文件保存失败。技术详情：{type(e).__name__}: {e}") from e
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)

    parquet_size = parquet_path.stat().st_size
    logger.info(
        "Ingested %s → %s (%d rows, %d cols, %.1f KB Parquet)",
        filename, table_name, len(df), len(df.columns), parquet_size / 1024,
    )

    return IngestedFile(
        original_filename=filename,
        table_name=table_name,
        parquet_path=parquet_path,
        columns=list(df.columns),
        dtypes={col: str(dtype) for col, dtype in df.dtypes.items()},
        row_count=len(df),
        size_bytes=parquet_size,
        source=provenance,
        data_version=data_version,
    )


# --- Internals ---------------------------------------------------------------

def _is_identifier(name: str) -> bool:
    label = str(name).strip().lower()
    return label in {"id", "sku", "货号", "订单号", "客户号", "商品号", "邮编"} or label.endswith(
        ("_id", "_code", "_no", "_number", "_zip_code_prefix", "编号", "编码"))


def _derive_table_name(filename: str, existing: list[str]) -> str:
    """Turn a filename into a valid, unique SQL table name.

    Examples:
        "Sales Data 2024.csv"  → "sales_data_2024"
        "my-file (1).xlsx"     → "my_file_1"
        collision with existing → appends _2, _3, etc.
    """
    stem = Path(filename).stem.lower().strip()
    # Replace non-alphanumeric with underscore
    name = _TABLE_NAME_RE.sub("_", stem)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name).strip("_")
    # Ensure it starts with a letter (SQL requirement)
    if not name or not name[0].isalpha():
        name = "t_" + name
    # Handle collisions
    if name not in existing:
        return name
    for i in range(2, 100):
        candidate = f"{name}_{i}"
        if candidate not in existing:
            return candidate
    raise IngestionError(f"“{filename}”的同名文件过多，请修改文件名后重新上传。")
