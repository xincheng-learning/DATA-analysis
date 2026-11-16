"""Image trust boundary and the real multimodal task path, without paid calls."""
import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from app import main, attachments, session
from app.execution import TaskRegistry
from test_chat_storage import storage_app  # noqa: F401


def png():
    data = io.BytesIO()
    Image.new('RGB', (80, 40), 'white').save(data, format='PNG')
    return data.getvalue()


async def test_image_upload_is_validated_idempotent_and_survives_restart(storage_app, monkeypatch):
    from app.tools import dataset_manager
    client, root, _ = storage_app
    raw = png()
    response = await client.post('/sessions/pictures/upload', files=[
        ('files', ('same.png', raw, 'image/png')),
        ('files', ('broken.png', b'not an image', 'image/png')),
        ('files', ('sales.csv', b'id,amount\n1,20\n', 'text/csv')),
    ])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'partial'
    assert [item['upload_index'] for item in result['uploaded']] == [0, 2]
    assert result['errors'][0]['upload_index'] == 1
    image = result['uploaded'][0]['image']
    assert image['width'] == 80 and image['height'] == 40
    again = (await client.post('/sessions/pictures/upload', files={'files': ('same.png', raw, 'image/png')})).json()
    assert again['uploaded'][0]['image']['id'] == image['id']
    monkeypatch.setattr(session, '_store', None)
    monkeypatch.setattr(dataset_manager, '_manager', None)
    fetched = await client.get(f'/sessions/pictures/images/{image["id"]}')
    assert fetched.status_code == 200 and fetched.headers['content-type'] == 'image/png'
    assert Image.open(io.BytesIO(fetched.content)).size == (80, 40)
    assert (await client.get('/sessions/pictures/tables')).json()['tables'][0]['table_name'] == 'sales'
    await client.post('/sessions/other/upload', files={'files': ('x.csv', b'x\n1\n', 'text/csv')})
    assert (await client.get(f'/sessions/other/images/{image["id"]}')).status_code == 404
    assert (await client.post('/tasks', json={'question':'看图','session_id':'other','images':[image]})).status_code in (400, 422)
    assert (await client.post('/tasks', json={'question':'看图','session_id':'pictures','images':[{'id':'../sales.parquet','filename':'bad'}]})).status_code == 422


async def test_real_image_blocks_reach_model_and_history_keeps_only_references(storage_app, monkeypatch):
    client, _, _ = storage_app
    result = (await client.post('/sessions/pictures/upload', files={'files': ('sql.png', png(), 'image/png')})).json()
    image = result['uploaded'][0]['image']
    await client.post('/sessions/pictures/upload', files={'files': ('sales.csv', b'id,amount\n1,20\n', 'text/csv')})
    seen = []
    def invoke(messages):
        seen.extend(messages)
        return SimpleNamespace(answer='截图语法问题：缺少 GROUP BY。建议补上；未执行 SQL。',recommended_sql=None,clarification=None,usage_metadata={'input_tokens':100,'output_tokens':20,'total_tokens':120}, response_metadata={})
    monkeypatch.setattr(main, 'get_chat_llm', lambda: object())
    monkeypatch.setattr(attachments, 'get_chat_llm', lambda: SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(attachments, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=invoke))
    registry = TaskRegistry()
    monkeypatch.setattr(main, 'tasks', registry)
    try:
        response = await client.post('/tasks', json={'session_id':'pictures','question':'SQL 错在哪？','mode':'investigate','images':[image]})
        assert response.status_code == 200, response.text
        for _ in range(100):
            task = (await client.get('/tasks/' + response.json()['task_id'])).json()
            if task['status'] in {'succeeded','failed'}:
                break
            await asyncio.sleep(.02)
        assert task['status'] == 'succeeded', task
        assert task['model_calls'] == 1 and task['result']['sql'] is None
        assert isinstance(seen[-1].content, list)
        blocks = seen[-1].content
        assert 'sales' in blocks[0]['text'] and 'amount' in blocks[0]['text']
        assert '不能' in seen[0].content and '执行' in seen[0].content
        encoded = blocks[1]['image_url']['url'].split(',', 1)[1]
        assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (80, 40)
        assert blocks[1]['image_url']['detail'] == 'high'
        history = (await client.get('/sessions/pictures')).json()['messages']
        assert history[-2]['metadata']['context_snapshot']['images'][0]['id'] == image['id']
        assert 'base64' not in json.dumps(history)
    finally:
        registry.close()


async def test_fake_oversize_and_animated_images_rejected_before_model(storage_app):
    client, _, _ = storage_app
    wide = io.BytesIO()
    Image.new('RGB', (8193, 1)).save(wide, format='PNG')
    animated = io.BytesIO()
    Image.new('RGB',(10,10),'red').save(animated,format='PNG',save_all=True,append_images=[Image.new('RGB',(10,10),'blue')],duration=100,loop=0)
    for filename, raw in [('fake.png',b'<svg onload="bad()"/>'),('large.png',wide.getvalue()),('huge.jpg',b'x'*(10*1024*1024+1)),('animated.png',animated.getvalue())]:
        response = await client.post('/sessions/reject/upload', files={'files':(filename,raw,'image/png')})
        assert response.status_code == 200
        assert response.json()['status'] == 'failed'
        assert not response.json()['uploaded']


async def test_images_follow_storage_copy_and_session_deletion(storage_app):
    client, root, target = storage_app
    result = (await client.post('/sessions/pictures/upload', files={'files': ('sql.png', png(), 'image/png')})).json()
    image = result['uploaded'][0]['image']
    path = f'/sessions/pictures/images/{image["id"]}'
    before = (await client.get(path)).content
    response = await client.put('/settings/storage',json={'directory':str(target)})
    assert response.status_code == 200, response.text
    assert (await client.get(path)).content == before
    assert (root/'uploads'/'pictures'/'_images'/image['id']).is_file()
    assert (await client.delete('/sessions/pictures')).status_code == 200
    assert not (target/'uploads'/'pictures').exists()
    assert (await client.get(path)).status_code == 404
