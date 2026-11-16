"""Local chat location and copy-before-switch; credentials stay in the app directory."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings


class StorageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    directory: str = Field(min_length=1, max_length=4096)


class BrowseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    directory: str = Field(default='', max_length=4096)


@lru_cache(maxsize=32)
def _read_location(app_directory: Path) -> Path:
    pointer = app_directory / 'storage-location.json'
    if settings.app_mode != 'local' or not pointer.exists():
        return app_directory
    directory = Path(json.loads(pointer.read_text(encoding='utf-8'))['directory'])
    if (not directory.is_absolute() or directory.resolve() != directory
            or not (directory / 'datapilot.sqlite3').is_file()):
        raise ValueError('已设置的聊天目录不可用，请恢复该文件夹后重启；不会创建空记录覆盖历史。')
    return directory


def chat_directory() -> Path:
    if settings.app_mode != 'local':
        return settings.local_data_dir
    return _read_location(settings.local_data_dir.resolve())


def storage_status(store) -> dict:
    from app.folder_picker import available
    root = chat_directory()
    assets = [path for path in (root / 'uploads').rglob('*') if path.is_file()]
    database = root / 'datapilot.sqlite3'
    return {
        'directory': str(root),
        'browse_available': available(),
        'session_count': store._backend.count_sessions(),
        'file_count': len(assets),
        'size_bytes': sum(path.stat().st_size for path in assets) + (database.stat().st_size if database.exists() else 0),
    }


def _manifest(directory: Path) -> dict:
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.resolve() != path.absolute():
            raise ValueError('聊天关联文件包含链接，不能复制到其他位置。请先将其恢复为普通文件。')
        if path.is_file():
            with path.open('rb') as stream:
                files[path.relative_to(directory).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return files


def _save_location(directory: Path) -> None:
    pointer = settings.local_data_dir / 'storage-location.json'
    if pointer.resolve() != pointer.absolute():
        raise ValueError('存储设置文件不能是链接。')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=pointer.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'directory': str(directory)}, stream, ensure_ascii=False)
        temporary.replace(pointer)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def copy_and_switch(directory: str, store, manager) -> dict:
    """Caller holds the shared data lock and has checked that all tasks are idle."""
    from app.session import SQLiteBackend

    source = chat_directory()
    requested = Path(directory.strip()).expanduser()
    if not requested.is_absolute():
        raise ValueError('请填写完整的绝对文件夹路径。')
    target = Path(os.path.abspath(requested))
    if target.resolve() != target:
        raise ValueError('目标文件夹不能通过链接跳转到其他位置。')
    if target == source:
        return {**storage_status(store), 'previous_directory': str(source)}
    if target.parent == target or source in target.parents or target in source.parents:
        raise ValueError('新旧目录不能相互包含，请选择独立的文件夹。')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('目标文件夹必须为空或尚不存在，不会覆盖已有文件。')
    if not isinstance(store._backend, SQLiteBackend):
        raise ValueError('聊天存储设置只适用于本地SQLite聊天记录。')
    if settings.docker_bind_root:
        raise ValueError('容器运行时请通过数据挂载设置保存位置；此窗口用于直接在本机运行的项目。')

    target.mkdir(parents=True, exist_ok=True)
    # The temporary tree is inside the explicitly chosen empty destination.
    with tempfile.TemporaryDirectory(prefix='.datapilot-copy-', dir=target) as temporary:
        stage = Path(temporary)
        if stage.resolve().parent != target:
            raise ValueError('临时复制目录超出了所选文件夹。')
        uploads = source / 'uploads'
        manifest = _manifest(uploads)
        if uploads.exists():
            if uploads.resolve() != uploads.absolute():
                raise ValueError('上传数据目录不能是链接。')
            shutil.copytree(uploads, stage / 'uploads')
        else:
            (stage / 'uploads').mkdir()
        if _manifest(stage / 'uploads') != manifest:
            raise OSError('关联文件复制校验失败。')
        with closing(sqlite3.connect(store._backend.path)) as original, closing(sqlite3.connect(stage / 'datapilot.sqlite3')) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise OSError('聊天数据库复制校验失败。')
            for table in ('sessions', 'messages'):
                query = f'SELECT COUNT(*) FROM {table}'
                if original.execute(query).fetchone() != copied.execute(query).fetchone():
                    raise OSError('聊天记录数量核对失败。')
        if any(path != stage for path in target.iterdir()):
            raise ValueError('复制期间目标文件夹出现其他文件，请选择空文件夹重试。')
        (stage / 'datapilot.sqlite3').replace(target / 'datapilot.sqlite3')
        (stage / 'uploads').replace(target / 'uploads')

    replacement = SQLiteBackend(target / 'datapilot.sqlite3')
    manager.close_all()
    _save_location(target)
    _read_location.cache_clear()
    store._backend = replacement
    return {**storage_status(store), 'previous_directory': str(source)}
