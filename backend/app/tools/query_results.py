"""Immutable local query results, read in batches of fifty without rerunning SQL."""
from __future__ import annotations

import re
import uuid
import csv
import io

import pyarrow as pa

from app.tools.file_storage import local_session_dir

PAGE_SIZE = 50


def result_path(session_id, result_id):
    if not re.fullmatch(r"[0-9a-f]{32}", result_id):
        raise ValueError("Invalid result id")
    directory = local_session_dir(session_id) / ".results"
    path = directory / f"{result_id}.arrow"
    if directory.resolve() != directory or path.resolve() != path:
        raise ValueError("Result paths must not redirect through links")
    return path


def save_result(session_id, frame):
    result_id = uuid.uuid4().hex
    path = result_path(session_id, result_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.replace_schema_metadata({b"row_count": str(len(frame)).encode()})
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("xb") as stream:
            with pa.ipc.new_file(stream, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")) as writer:
                writer.write_table(table, max_chunksize=PAGE_SIZE)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return result_id


def read_result(session_id, result_id, offset):
    if type(offset) is not int or offset < 0 or offset % PAGE_SIZE:
        raise ValueError("Offset must be a non-negative multiple of fifty")
    path = result_path(session_id, result_id)
    with pa.memory_map(str(path), "r") as source:
        reader = pa.ipc.open_file(source)
        total = int(reader.schema.metadata[b"row_count"])
        rows = []
        if offset < total:
            from app.agent.nodes import _sanitize_records
            rows = _sanitize_records(reader.get_batch(offset // PAGE_SIZE).to_pylist())
        return {"rows": rows, "columns": reader.schema.names, "row_count": total, "offset": offset}


def result_csv(session_id,result_id):
    path=result_path(session_id,result_id)
    read_result(session_id,result_id,0)
    def chunks():
        yield '\ufeff'
        with pa.memory_map(str(path),'r') as source:
            reader=pa.ipc.open_file(source)
            output=io.StringIO(newline='')
            writer=csv.writer(output)
            def safe(value):
                # Spreadsheet formula text is escaped only in the exported copy.
                return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
            writer.writerow([safe(name) for name in reader.schema.names])
            yield output.getvalue()
            for index in range(reader.num_record_batches):
                output.seek(0)
                output.truncate()
                for row in reader.get_batch(index).to_pylist():
                    writer.writerow([safe(row[name]) for name in reader.schema.names])
                yield output.getvalue()
    return chunks()
