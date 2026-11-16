"""Real local HTTP and disk copies; no model API calls or real user data."""
import shutil
import threading
from pathlib import Path
import subprocess
import sys

import httpx
import pandas as pd
import pytest

from app import main, session
from app.config import settings
from app.tools import dataset_manager
from app.tools.query_results import save_result


@pytest.fixture
async def storage_app(monkeypatch, tmp_path):
    root = tmp_path / '原目录'
    root.mkdir()
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(settings, 'session_backend', 'sqlite')
    monkeypatch.setattr(settings, 'local_data_dir', root)
    monkeypatch.setattr(settings, 'cors_allowed_origins', 'http://localhost:3000')
    monkeypatch.setattr(main.limiter, 'enabled', False)
    monkeypatch.setattr(session, '_store', None)
    monkeypatch.setattr(dataset_manager, '_manager', None)
    transport = httpx.ASGITransport(app=main.app, client=('127.0.0.1', 12345))
    async with httpx.AsyncClient(transport=transport, base_url='http://localhost:8000') as client:
        yield client, root, tmp_path / '自定义 聊天'
    manager = dataset_manager.get_dataset_manager()
    for sid in list(manager._sessions):
        manager.close_session(sid)


async def seed(client):
    response = await client.post('/sessions/storage-case/upload', files={
        'files': ('sales.csv', b'id,region,amount\na, East ,100\nb,,200\n', 'text/csv')})
    assert response.status_code == 200, response.text


async def test_copy_switch_preserves_chats_cleaning_queries_and_restart(storage_app, monkeypatch):
    client, source, target = storage_app
    await seed(client)
    secrets = source / 'model-settings.bin'
    secrets.write_bytes(b'original-encrypted-config')
    before = (await client.get('/sessions/storage-case/workbench')).json()
    preview = await client.post('/sessions/storage-case/cleaning/preview', json={
        'table': 'sales', 'snapshot': before['snapshot'],
        'rules': [{'operation': 'trim', 'column': 'region'}]})
    applied = await client.post('/sessions/storage-case/cleaning/apply', json={'preview_id': preview.json()['preview_id']})
    assert applied.status_code == 200, applied.text
    result_id = save_result('storage-case', pd.DataFrame({'n': range(120)}))
    metadata = {'sql': 'SELECT * FROM sales', 'result_id': result_id,
                'chart_spec': {'data': [{'type': 'bar', 'x': ['East'], 'y': [100]}]}}
    session.get_session_store()._backend.save_message('storage-case', 'assistant', '原有结论', metadata)
    history = (await client.get('/sessions/storage-case')).json()
    old_dataset = dataset_manager.get_dataset_manager().get('storage-case')
    old_dataset.get_connection()

    status = await client.get('/settings/storage')
    assert status.status_code == 200, status.text
    assert status.json()['session_count'] == 1 and status.json()['size_bytes'] > 0
    response = await client.put('/settings/storage', json={'directory': str(target)})
    assert response.status_code == 200, response.text
    assert response.json()['directory'] == str(target.resolve())
    assert response.json()['previous_directory'] == str(source.resolve())
    assert old_dataset._con is None
    assert source.joinpath('datapilot.sqlite3').is_file()
    assert source.joinpath('uploads/storage-case/sales.parquet').is_file()
    assert secrets.read_bytes() == b'original-encrypted-config'
    assert not target.joinpath('model-settings.bin').exists()
    assert (await client.get('/sessions/storage-case')).json() == history
    page = await client.get(f'/sessions/storage-case/results/{result_id}?offset=50')
    assert page.status_code == 200 and page.json()['rows'][0]['n'] == 50
    assert len(page.json()['rows']) == 50
    new_dataset = dataset_manager.get_dataset_manager().get('storage-case')
    assert new_dataset.files[0].parquet_path.is_relative_to(target)
    assert new_dataset.get_connection().execute('SELECT SUM(amount) FROM sales').fetchone()[0] == 300
    assert new_dataset.get_connection().execute('SELECT region FROM sales ORDER BY id').fetchone()[0] == 'East'

    from app import chat_storage
    dataset_manager.get_dataset_manager().close_all()
    monkeypatch.setattr(session, '_store', None)
    chat_storage._read_location.cache_clear()
    assert (await client.get('/settings/storage')).json()['directory'] == str(target.resolve())
    assert (await client.get('/sessions/storage-case')).json() == history
    restored = (await client.get('/sessions/storage-case/workbench')).json()
    undone = await client.post('/sessions/storage-case/cleaning/undo', json={
        'table': 'sales', 'snapshot': restored['snapshot']})
    assert undone.status_code == 200, undone.text
    assert undone.json()['tables'][0]['preview'][0]['region'] == ' East '
    await client.patch('/sessions/storage-case', json={'title': '新目录里的修改'})
    assert session.SQLiteBackend(source / 'datapilot.sqlite3').get('storage-case').title != '新目录里的修改'


@pytest.mark.parametrize('kind', ['nonempty', 'child', 'parent', 'relative', 'file'])
async def test_invalid_destination_never_overwrites_or_switches(storage_app, kind):
    client, source, target = storage_app
    await seed(client)
    if kind == 'nonempty':
        target.mkdir()
        (target / 'keep.txt').write_text('keep', encoding='utf-8')
    elif kind == 'child':
        target = source / 'child'
    elif kind == 'parent':
        target = source.parent
    elif kind == 'relative':
        target = 'relative-chat-folder'
    elif kind == 'file':
        target.write_text('keep', encoding='utf-8')
    response = await client.put('/settings/storage', json={'directory': str(target)})
    assert response.status_code == 400, response.text
    assert (await client.get('/sessions/storage-case')).status_code == 200
    assert not (source / 'storage-location.json').exists()
    if kind == 'nonempty':
        assert (target / 'keep.txt').read_text(encoding='utf-8') == 'keep'


async def test_active_tasks_and_data_operations_block_switch(storage_app, monkeypatch):
    client, source, target = storage_app
    await seed(client)
    monkeypatch.setattr(main.tasks, 'has_active_tasks', lambda: True, raising=False)
    assert (await client.put('/settings/storage', json={'directory': str(target)})).status_code == 409
    monkeypatch.setattr(main.tasks, 'has_active_tasks', lambda: False)
    acquired, release = threading.Event(), threading.Event()
    def hold_data_lock():
        with main.data_lock:
            acquired.set()
            release.wait(10)
    worker = threading.Thread(target=hold_data_lock)
    worker.start()
    acquired.wait(2)
    try:
        assert (await client.put('/settings/storage', json={'directory': str(target)})).status_code == 409
    finally:
        release.set()
        worker.join()
    assert not (source / 'storage-location.json').exists()


@pytest.mark.parametrize('failure', ['copy', 'commit', 'permission', 'corrupt-copy'])
async def test_failed_copy_or_setting_commit_keeps_original(storage_app, monkeypatch, failure):
    client, source, target = storage_app
    await seed(client)
    assert (await client.get('/settings/storage')).status_code == 200
    from app import chat_storage
    def fail(*args, **kwargs):
        raise OSError('simulated disk failure')
    if failure == 'copy':
        monkeypatch.setattr(shutil, 'copytree', fail)
    elif failure == 'commit':
        monkeypatch.setattr(chat_storage, '_save_location', fail)
    elif failure == 'permission':
        original_mkdir = type(target).mkdir
        def deny_target(path, *args, **kwargs):
            if path == target:
                raise PermissionError('simulated write denied')
            return original_mkdir(path, *args, **kwargs)
        monkeypatch.setattr(type(target), 'mkdir', deny_target)
    else:
        original_copy = shutil.copytree
        def corrupt_copy(source_dir, destination_dir, *args, **kwargs):
            result = original_copy(source_dir, destination_dir, *args, **kwargs)
            (Path(destination_dir) / 'unexpected.txt').write_text('bad copy', encoding='utf-8')
            return result
        monkeypatch.setattr(shutil, 'copytree', corrupt_copy)
    response = await client.put('/settings/storage', json={'directory': str(target)})
    assert response.status_code == 400, response.text
    assert (await client.get('/settings/storage')).json()['directory'] == str(source.resolve())
    assert (await client.get('/sessions/storage-case')).status_code == 200
    assert not (source / 'storage-location.json').exists()


async def test_same_directory_is_noop_and_missing_saved_directory_never_creates_empty_history(storage_app, monkeypatch):
    client, source, target = storage_app
    await seed(client)
    assert (await client.put('/settings/storage', json={'directory': str(source)})).status_code == 200
    assert not (source / 'storage-location.json').exists()
    assert (await client.put('/settings/storage', json={'directory': str(target)})).status_code == 200
    dataset_manager.get_dataset_manager().close_all()
    target.rename(target.with_name('暂时不可用'))
    from app import chat_storage
    chat_storage._read_location.cache_clear()
    monkeypatch.setattr(session, '_store', None)
    response = await client.get('/settings/storage')
    assert response.status_code == 400 and '不可用' in response.json()['detail']
    assert not target.exists()


async def test_settings_are_local_only_and_reject_foreign_origin(storage_app, monkeypatch):
    client, _, target = storage_app
    assert (await client.put('/settings/storage', json={'directory': str(target)}, headers={
        'origin': 'https://foreign.example'})).status_code == 403
    monkeypatch.setattr(settings, 'app_mode', 'cloud')
    # Auth is covered separately; route-level local restriction is still required.
    request = type('Request', (), {'headers': {'content-type': 'application/json'}})()
    with pytest.raises(main.HTTPException) as exc:
        main.storage_status(request)
    assert exc.value.status_code == 403


async def test_copy_includes_chats_beyond_the_fifty_item_sidebar(storage_app):
    client, source, target = storage_app
    store = session.get_session_store()
    for index in range(61):
        sid = f'older-{index}'
        store.get_or_create(sid)
        store._backend.save_message(sid, 'user', f'历史问题 {index}')
    assert (await client.get('/settings/storage')).json()['session_count'] == 61
    response = await client.put('/settings/storage', json={'directory': str(target)})
    assert response.status_code == 200 and response.json()['session_count'] == 61
    assert (await client.get('/sessions/older-0')).json()['messages'][0]['content'] == '历史问题 0'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows directory junction regression')
@pytest.mark.parametrize('location', ['target', 'source'])
async def test_directory_junction_cannot_redirect_copied_data(storage_app, tmp_path, location):
    client, source, target = storage_app
    await seed(client)
    outside = tmp_path / '链接指向的文件夹'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep', encoding='utf-8')
    link = target if location == 'target' else source / 'uploads/storage-case/redirect'
    script = tmp_path / 'create-test-junction.ps1'
    script.write_text('param([string]$LinkPath,[string]$TargetPath)\nNew-Item -ItemType Junction -Path $LinkPath -Target $TargetPath -ErrorAction Stop | Out-Null\n', encoding='utf-8')
    subprocess.run(['C:/Program Files/PowerShell/7/pwsh.exe', '-NoProfile', '-File', str(script),
                    '-LinkPath', str(link), '-TargetPath', str(outside)], check=True, capture_output=True)
    response = await client.put('/settings/storage', json={'directory': str(target)})
    assert response.status_code == 400 and '链接' in response.json()['detail']
    assert not (source / 'storage-location.json').exists()
    assert (outside / 'keep.txt').read_text(encoding='utf-8') == 'keep'
