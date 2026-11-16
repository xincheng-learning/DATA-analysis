from copy import deepcopy

import pytest
from test_chat_storage import storage_app  # noqa: F401
from test_boards import seed_chart


async def test_board_appearance_survives_reload_without_changing_source(storage_app):
    client,_,_=storage_app
    mid=await seed_chart(client)
    board=(await client.post('/boards',json={'title':'交付面板'})).json()
    board=(await client.post(f"/boards/{board['id']}/charts",json={'session_id':'board-source','message_id':mid})).json()
    original=deepcopy(board['items'][0]['spec'])
    board['items'][0]['appearance']={'title':'季度交付图','palette':'ocean','font_size':16,'show_legend':False}
    saved=await client.put(f"/boards/{board['id']}",json=board)
    assert saved.status_code==200
    restored=(await client.get(f"/boards/{board['id']}")).json()
    assert restored['items'][0]['appearance']['title']=='季度交付图'
    assert restored['items'][0]['spec']==original
    assert restored['items'][0]['source_key']==board['items'][0]['source_key']


async def test_advanced_appearance_is_local_and_cannot_rewrite_data(storage_app):
    client,_,_=storage_app
    from app import superset
    original={'id':'a'*32,'title':'来源图','status':'ready','created_at':1,'checks':[],
        'chart_type':'treemap','dimensions':['state'],'measure':'amount','row_count':2,'data_version':'original','checksum':'unchanged'}
    superset._store_view(original)
    response=await client.put('/superset/views/'+'a'*32+'/appearance',json={'title':'新展示标题','font_size':16,'palette':'ocean'})
    assert response.status_code==200
    stored=superset.get_view('a'*32)
    assert stored['appearance']=={'title':'新展示标题','font_size':16,'palette':'ocean'}
    assert {k:v for k,v in stored.items() if k!='appearance'}==original
    assert (await client.get('/superset/views')).json()[0]['appearance']==stored['appearance']
    assert (await client.put('/superset/views/'+'a'*32+'/appearance',json={'data':[1]})).status_code==422
    assert (await client.put('/superset/views/'+'a'*32+'/appearance',json={'font_size':1})).status_code==422
    assert (await client.put('/superset/views/'+'a'*32+'/appearance',json={})).status_code==200
    assert superset.get_view('a'*32)['appearance']=={}
