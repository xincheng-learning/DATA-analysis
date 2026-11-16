"""Folder browsing never relocates history and accepts paths only as data."""
import json
import subprocess
from pathlib import Path

import pytest

from app import main
from test_chat_storage import storage_app  # noqa: F401


async def test_browse_returns_selected_folder_without_saving_or_copying(storage_app, monkeypatch):
    from app import folder_picker
    client, root, target = storage_app
    seen=[]
    monkeypatch.setattr(folder_picker, 'choose_directory', lambda initial: seen.append(initial) or str(target))
    response=await client.post('/settings/storage/browse', json={'directory':str(root)})
    assert response.status_code == 200 and response.json()=={'directory':str(target)}
    assert seen==[root]
    assert not (root/'storage-location.json').exists() and not target.exists()


async def test_cancel_is_a_successful_no_selection_and_foreign_origin_cannot_open_picker(storage_app, monkeypatch):
    from app import folder_picker
    client, root, _ = storage_app
    seen=[]
    monkeypatch.setattr(folder_picker, 'choose_directory', lambda initial: seen.append(initial))
    response=await client.post('/settings/storage/browse', json={'directory':''})
    assert response.status_code==200 and response.json()=={'directory':None}
    assert seen==[root]
    response=await client.post('/settings/storage/browse', json={},headers={'origin':'https://foreign.example'})
    assert response.status_code==403 and seen==[root]


async def test_picker_failure_is_recoverable_and_does_not_change_location(storage_app, monkeypatch):
    from app import folder_picker
    client, root, _ = storage_app
    def fail(initial):
        raise OSError('native dialog unavailable')
    monkeypatch.setattr(folder_picker, 'choose_directory', fail)
    response=await client.post('/settings/storage/browse',json={})
    assert response.status_code==503 and '填写' in response.json()['detail']
    assert (await client.get('/settings/storage')).json()['directory']==str(root)


def test_native_path_is_passed_on_stdin_not_interpolated_into_powershell(monkeypatch, tmp_path):
    from app import folder_picker
    initial=tmp_path/'中文 $() ` 路径'
    initial.mkdir()
    calls=[]
    monkeypatch.setattr(folder_picker, 'available', lambda: True)
    def run(command, **kwargs):
        calls.append((command,kwargs))
        return subprocess.CompletedProcess(command,0,json.dumps({'directory':str(initial)},ensure_ascii=False),'')
    monkeypatch.setattr(folder_picker.subprocess,'run',run)
    assert folder_picker.choose_directory(initial)==str(initial)
    command,options=calls[0]
    assert Path(command[0]).name=='pwsh.exe' and '-STA' in command
    assert str(initial) not in ' '.join(command)
    assert options['input']==str(initial) and options['encoding']=='utf-8'
    assert not folder_picker._picker_lock.locked()


def test_picker_only_allows_one_window_and_releases_lock_on_failure(monkeypatch, tmp_path):
    from app import folder_picker
    monkeypatch.setattr(folder_picker, 'available', lambda: True)
    with folder_picker._picker_lock:
        with pytest.raises(folder_picker.PickerBusy):
            folder_picker.choose_directory(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('launch failed')
    monkeypatch.setattr(folder_picker.subprocess,'run',fail)
    with pytest.raises(OSError):
        folder_picker.choose_directory(tmp_path)
    assert not folder_picker._picker_lock.locked()
