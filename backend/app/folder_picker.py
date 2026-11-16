"""Explicit local folder browsing; selecting a folder never changes stored data."""
import json
import subprocess
import sys
import threading
from pathlib import Path

from app.config import settings

POWERSHELL = Path('C:/Program Files/PowerShell/7/pwsh.exe')
_picker_lock = threading.Lock()


class PickerBusy(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == 'win32' and not settings.docker_bind_root and POWERSHELL.is_file()


def choose_directory(initial: Path) -> str | None:
    if not available():
        raise OSError('Native folder browsing is unavailable')
    if not _picker_lock.acquire(blocking=False):
        raise PickerBusy('已有文件夹选择窗口，请先完成选择或关闭该窗口。')
    try:
        script = Path(__file__).with_suffix('.ps1').read_text(encoding='utf-8')
        result = subprocess.run(
            [str(POWERSHELL), '-NoLogo', '-NoProfile', '-STA', '-Command', script],
            input=str(initial), capture_output=True, text=True, encoding='utf-8', check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return json.loads(result.stdout)['directory']
    finally:
        _picker_lock.release()
