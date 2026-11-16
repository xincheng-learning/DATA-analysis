import pytest
from test_local_api import local_app, upload  # noqa: F401


async def test_folder_crud_preserves_chat_and_files_across_restart(local_app):
    client, restart, root = local_app
    await upload(client, 'folder-a')
    await upload(client, 'folder-b')
    folder = (await client.post('/chat-folders', json={'title': ' 电商分析 '})).json()
    assert folder['title'] == '电商分析'
    moved = await client.post('/chat-folders/move', json={'session_ids': ['folder-a'], 'folder_id': folder['id']})
    assert moved.status_code == 200
    restart()
    chats = {item['session_id']: item for item in (await client.get('/sessions')).json()}
    assert chats['folder-a']['folder_id'] == folder['id']
    assert chats['folder-b']['folder_id'] is None
    assert (await client.patch(f"/chat-folders/{folder['id']}", json={'title': '月度复盘'})).status_code == 200
    assert (await client.get('/chat-folders')).json()[0]['title'] == '月度复盘'
    assert (await client.delete(f"/chat-folders/{folder['id']}")).json()['chats_preserved']
    restart()
    assert all(item['folder_id'] is None for item in (await client.get('/sessions')).json())
    assert (root / 'uploads/folder-a/sales.parquet').exists()
    assert len((await client.get('/sessions/folder-a')).json()['messages']) == 1
    assert (await client.get('/chat-folders')).json() == []


async def test_move_is_atomic_and_does_not_create_missing_chats(local_app):
    client, _, _ = local_app
    await upload(client, 'keep')
    folder = (await client.post('/chat-folders', json={'title': 'A'})).json()
    for body in [
        {'session_ids': ['keep', 'missing'], 'folder_id': folder['id']},
        {'session_ids': ['keep'], 'folder_id': 'missing'},
    ]:
        assert (await client.post('/chat-folders/move', json=body)).status_code == 404
        assert (await client.get('/sessions')).json()[0]['folder_id'] is None
    for title in ['', '   ', 'x' * 61]:
        assert (await client.post('/chat-folders', json={'title': title})).status_code == 422
    assert (await client.post('/chat-folders/move', json={'session_ids': []})).status_code == 422


def test_sidebar_lists_older_chats_and_deletion_cascades_membership(tmp_path):
    from app.session import SQLiteBackend, Session
    store = SQLiteBackend(tmp_path / 'chats.sqlite3')
    for i in range(61):
        store.save(Session(str(i), i, i))
    folder = store.create_folder('历史')
    store.move_sessions(['0', '60'], folder['id'])
    assert len(store.list_sessions()) == 61
    store.move_sessions(['0'], None)
    assert next(item for item in store.list_sessions() if item['id'] == '0')['folder_id'] is None
    store.delete('60')
    with store._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM chat_folder_memberships').fetchone()[0] == 0
