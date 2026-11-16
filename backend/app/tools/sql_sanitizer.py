"""SQL sanitization — allowlist approach.

Only permits SELECT statements that reference registered session tables/views.
Rejects:
  - File paths (replacement scans like SELECT * FROM '/path/file.csv')
  - External functions (read_csv_auto, read_parquet, etc.)
  - DDL/DML (CREATE, DROP, INSERT, UPDATE, DELETE, ALTER)
  - Extension loading (INSTALL, LOAD)
  - ATTACH, COPY, PRAGMA
  - Unknown identifiers that look like file paths

The allowlist is: only the table names registered in the user's session.
"""
from __future__ import annotations

import re

import duckdb

# --- Patterns that are ALWAYS blocked regardless of context ---

# Statements that aren't SELECT/WITH
_BLOCKED_FIRST_TOKEN = re.compile(
    r"^\s*(insert|update|delete|drop|create|alter|attach|detach|copy|install|load|import|export|pragma)\b",
    re.IGNORECASE,
)

# File path patterns (replacement scans): any quoted string containing / or \ or .csv/.parquet etc.
_QUOTED_VALUE = re.compile(r"'(?:(?:'')|[^'])*'|\"(?:(?:\"\")|[^\"])*\"")
_FILE_PATH_PATTERN = re.compile(r"[/\\]|\.(csv|parquet|json|xlsx|xls|txt|tsv|db|sqlite|env|key|pem)$", re.IGNORECASE)

# Dangerous functions — even inside a SELECT these access the filesystem/network
_DANGEROUS_FUNCTIONS = re.compile(
    r"\b("
    r"read_csv_auto|read_csv|read_parquet|read_json|read_json_auto|"
    r"read_text|read_blob|read_ndjson|read_ndjson_auto|"
    r"parquet_scan|csv_scan|json_scan|query|query_table|"
    r"httpfs|http_get|http_post|"
    r"glob|list_files|"
    r"getenv|current_setting|"
    r"attach_database|load_extension|install_extension|"
    r"write_csv|write_parquet|"
    r"copy\s+.+\s+to\b"
    r")\s*\(",
    re.IGNORECASE,
)

# Extension-related keywords
_EXTENSION_PATTERN = re.compile(
    r"\b(install|load)\s+['\"]?\w+['\"]?",
    re.IGNORECASE,
)


def _references_query(sql: str) -> str:
    # DuckDB 1.1.3 name extraction loses USING columns; remove join keys only
    # from this inspection copy. The original query is parsed and executed intact.
    raw = sql.encode("utf-8")
    tokens = duckdb.tokenize(sql)
    spans = []
    for i, (start, kind) in enumerate(tokens[:-1]):
        if kind != duckdb.token_type.keyword or raw[start:start+5].lower() != b"using":
            continue
        opening = tokens[i+1][0]
        if raw[opening:opening+1] != b"(":
            continue
        for closing, _ in tokens[i+2:]:
            if raw[closing:closing+1] == b")":
                spans.append((start, closing+1))
                break
    for start, end in reversed(spans):
        raw = raw[:start] + b"ON TRUE" + raw[end:]
    return raw.decode("utf-8")


def check_sql_safety(sql: str, allowed_tables: list[str] | None = None) -> str | None:
    """Validate SQL against the allowlist. Returns error message or None if safe.

    Parameters
    ----------
    sql : str
        The SQL query to validate.
    allowed_tables : list[str] | None
        Table/view names registered in the user's session. If provided,
        any FROM/JOIN reference not in this list is rejected.
    """
    sql_stripped = sql.strip().rstrip(";").strip()

    if not sql_stripped:
        return "Empty query."

    # 1. Must start with SELECT or WITH
    first_token = sql_stripped.split(None, 1)[0].lower()
    if first_token not in ("select", "with"):
        return f"Only SELECT/WITH queries are allowed; got '{first_token}'."

    # 2. Block explicitly dangerous first tokens (shouldn't pass #1, but defense in depth)
    if _BLOCKED_FIRST_TOKEN.search(sql_stripped):
        return "Statement type not allowed."

    # 3. Block file paths (replacement scans)
    for quoted in _QUOTED_VALUE.finditer(sql_stripped):
        value = quoted.group()[1:-1]
        if _FILE_PATH_PATTERN.search(value):
            return f"File path references are not allowed in queries: '{value[:50]}'"

    # 4. Block dangerous functions
    func_match = _DANGEROUS_FUNCTIONS.search(sql_stripped)
    if func_match:
        return f"Function '{func_match.group(1)}' is not allowed. Use table names instead."

    # 5. Block extension operations
    if _EXTENSION_PATTERN.search(sql_stripped):
        return "Extension operations are not allowed."

    # DuckDB understands CTE scope, EXTRACT(... FROM ...), quoted and comma joins.
    # Use a separate connection with external access disabled: parsing must not read user files.
    try:
        with duckdb.connect(":memory:", config={"enable_external_access": "false", "threads": 1}) as parser:
            statements = parser.extract_statements(sql_stripped)
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                return "Only a single SELECT/WITH statement is allowed."
            if allowed_tables is not None:
                references = parser.get_table_names(_references_query(sql_stripped))
                unknown = sorted(t for t in references if t.casefold() not in {a.casefold() for a in allowed_tables})
                if unknown:
                    return f"Unknown table reference: '{unknown[0]}'. Available tables: {allowed_tables}"
    except duckdb.Error as error:
        diagnostic = str(error).splitlines()[0][:400]
        return "SQL could not be safely parsed; use registered tables and a single SELECT/WITH statement. " + diagnostic

    return None
