"""File storage via Supabase Storage.

Uploaded Parquet files are persisted in a Supabase Storage bucket so they
survive server restarts. On session load, if the local file is missing,
it's downloaded from Supabase Storage and the DuckDB connection is rebuilt.

Bucket structure:
  uploads/{session_id}/{table_name}.parquet
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from app.config import settings
from app.chat_storage import chat_directory

logger = logging.getLogger(__name__)

BUCKET_NAME = "uploads"


def local_session_dir(session_id: str) -> Path:
    """Keep all local file operations inside the configured upload directory."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
        raise ValueError("Invalid session id")
    root = (chat_directory() / "uploads").resolve()
    target = root / session_id
    if target.resolve() != target:
        raise ValueError("Local session directories must not redirect through links")
    return target


def _get_storage():
    """Get the Supabase storage client."""
    if settings.app_mode == "local":
        raise RuntimeError("Cloud storage is disabled in local mode")
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)
    return client.storage


def ensure_bucket_exists() -> None:
    """Create the uploads bucket if it doesn't exist."""
    if settings.app_mode == "local":
        (chat_directory() / "uploads").mkdir(parents=True, exist_ok=True)
        return
    storage = _get_storage()
    try:
        storage.get_bucket(BUCKET_NAME)
    except Exception:
        try:
            storage.create_bucket(BUCKET_NAME, options={"public": False})
            logger.info("Created Supabase storage bucket: %s", BUCKET_NAME)
        except Exception as e:
            # Bucket might already exist (race condition or permission)
            logger.debug("Bucket creation skipped: %s", e)


def upload_parquet(session_id: str, table_name: str, local_path: Path) -> str:
    """Upload a Parquet file to Supabase Storage.

    Returns the storage path (e.g., 'uploads/abc123/sales.parquet').
    """
    if settings.app_mode == "local":
        if local_path.resolve().parent != local_session_dir(session_id):
            raise ValueError("Upload is outside the local session directory")
        return f"{session_id}/{local_path.name}"
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"

    with open(local_path, "rb") as f:
        storage.from_(BUCKET_NAME).upload(
            remote_path,
            f,
            file_options={"content-type": "application/octet-stream", "upsert": "true"},
        )

    logger.info("Uploaded %s to storage: %s/%s", local_path.name, BUCKET_NAME, remote_path)
    return remote_path


def download_parquet(session_id: str, table_name: str, local_dir: Path) -> Path:
    """Download a Parquet file from Supabase Storage to a local path.

    Returns the local path where the file was saved.
    """
    if settings.app_mode == "local":
        path = local_session_dir(session_id) / f"{table_name}.parquet"
        if path.resolve().parent != local_session_dir(session_id):
            raise ValueError("Invalid local table path")
        if not path.is_file():
            raise FileNotFoundError(path.name)
        return path
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"
    local_path = local_dir / f"{table_name}.parquet"

    local_dir.mkdir(parents=True, exist_ok=True)

    data = storage.from_(BUCKET_NAME).download(remote_path)
    local_path.write_bytes(data)

    logger.info("Downloaded %s/%s to %s", BUCKET_NAME, remote_path, local_path)
    return local_path


def file_exists_in_storage(session_id: str, table_name: str) -> bool:
    """Check if a Parquet file exists in Supabase Storage."""
    if settings.app_mode == "local":
        path = local_session_dir(session_id) / f"{table_name}.parquet"
        return path.resolve().parent == local_session_dir(session_id) and path.is_file()
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"
    try:
        # List files in the session folder and check
        files = storage.from_(BUCKET_NAME).list(session_id)
        return any(f["name"] == f"{table_name}.parquet" for f in files)
    except Exception:
        return False


def delete_session_files(session_id: str) -> None:
    """Delete all files for a session from Supabase Storage."""
    if settings.app_mode == "local":
        directory = local_session_dir(session_id)
        if directory.exists():
            shutil.rmtree(directory)
        return
    storage = _get_storage()
    try:
        files = storage.from_(BUCKET_NAME).list(session_id)
        if files:
            paths = [f"{session_id}/{f['name']}" for f in files]
            storage.from_(BUCKET_NAME).remove(paths)
            logger.info("Deleted %d files for session %s", len(paths), session_id)
    except Exception as e:
        logger.warning("Failed to delete storage files for session %s: %s", session_id, e)
