"""Panel persistence and frozen context without a paid model call."""
import json
import pytest
from app import session
from app.schemas import AskRequest
from test_chat_storage import storage_app  # noqa: F401


async def test_nested_investigation_sources_use_exact_paths_for_boards_and_complete_results(storage_app,monkeypatch):
    from app.agent.workflow import Result,_result_source
    from app.tools.query_results import read_result
    from app import superset
    client,_,_=storage_app
    await client.post('/sessions/nested-source/upload',files={'files':('x.csv',b'n\n1\n','text/csv')})
    def leaf(value,**extra):
        return {'step_id':'same','question':'调查','status':'succeeded','sql':'SELECT saved',
            'columns':['n'],'rows':[{'n':value+i} for i in range(61)],'row_count':61,'data_version':'v1',
            'chart_spec':{'data':[{'type':'bar','x':['A'],'y':[value]}]},**extra}
    meta={'workflow':{'steps':[{'id':'second','request':'第二项','status':'succeeded','result':{'investigation':{'steps':[
        leaf(900,superseded=True),leaf(200),leaf(999,status='failed')]}}},
        {'id':'first','request':'第一项','status':'succeeded','result':{'investigation':{'steps':[leaf(100)]}}}]}}
    store=session.get_session_store()._backend
    store.save_message('nested-source','assistant','多项调查',meta)
    mid=store.get_messages('nested-source')[-1]['id']
    board=(await client.post('/boards',json={'title':'精确来源'})).json()
    captures=[]
    monkeypatch.setattr(superset,'create_view',lambda sid,source,request:captures.append(source) or {'id':'a'*32})
    for outer,inner,value in [(0,1,200),(1,0,100)]:
        path=['workflow','steps',outer,'result','investigation','steps',inner]
        body={'session_id':'nested-source','message_id':mid,'source_path':path}
        added=await client.post(f"/boards/{board['id']}/charts",json=body)
        assert added.status_code==200,added.text
        assert added.json()['items'][-1]['spec']['data'][0]['y']==[value]
        result=_result_source(Result(message_id=mid,source_path=path),{'session_id':'nested-source'},[])
        assert read_result('nested-source',result['result_id'],50)['rows'][-1]=={'n':value+60}
        advanced=await client.post('/superset/views',json={**body,'title':'高级','chart_type':'pivot_table','dimensions':['n'],'measure':'n'})
        assert advanced.status_code==200,advanced.text
        assert captures[-1]['rows'][0]=={'n':value}
    assert len(added.json()['items'])==2
    from app.agent.workflow import WorkflowStep,execute_tool,WriteBoard
    from app.context_memory import project_messages
    path=['workflow','steps',1,'result','investigation','steps',0]
    args=WriteBoard(title='聊天调查面板',sources=[Result(message_id=mid,source_path=path)]).model_dump()
    tool=WorkflowStep(id='panel',request='保存具体调查图',covers=[0],tool='board',arguments=args)
    result=execute_tool(tool,{'session_id':'nested-source'},[],'qa')
    saved=(await client.get(f"/boards/{result['board']['id']}")).json()
    assert saved['items'][0]['spec']['data'][0]['y']==[100]
    projected=project_messages(store.get_messages('nested-source'))[-1]['evidence']['result_sources']
    assert next(item for item in projected if item['source_path']==path)['sample_rows'][0]=={'n':100}
    for path in [['workflow','steps',0,'result','investigation','steps',i] for i in [0,2,9]]+[['rows',0]]:
        with pytest.raises(ValueError):
            _result_source(Result(message_id=mid,source_path=path),{'session_id':'nested-source'},[])
    with pytest.raises(ValueError):
        _result_source(Result(message_id=mid,source_path=['workflow','steps',1,'result','investigation','steps',0]),{'session_id':'other-chat'},[])


async def seed_chart(client):
    await client.post('/sessions/board-source/upload',files={'files':('test.csv',b'month,amount\nJan,10\nFeb,20\n','text/csv')})
    store=session.get_session_store()._backend
    store.save_message('board-source','assistant','销售增长',{'chart_spec':{'data':[{'type':'bar','x':['Jan','Feb'],'y':[10,20]}],'layout':{'title':'销售额','yaxis':{'title':'金额（元）'}}},'sql':'SELECT secret','row_count':120,'rows':[{}]*50})
    detail=(await client.get('/sessions/board-source')).json()
    return detail['messages'][-1]['id']


async def test_board_snapshots_are_staged_deduplicated_and_versioned(storage_app):
    client,_,_=storage_app
    mid=await seed_chart(client)
    board=(await client.post('/boards',json={'title':'经营对比'})).json()
    response=await client.post(f'/boards/{board["id"]}/charts',json={'session_id':'board-source','message_id':mid})
    assert response.status_code==200,response.text
    board=response.json(); item=board['items'][0]
    assert not item['placed'] and item['spec']['data'][0]['y']==[10,20]
    assert 'SQL' not in json.dumps(item) and 'SELECT secret' not in json.dumps(item)
    assert '50' in item['scope']
    again=(await client.post(f'/boards/{board["id"]}/charts',json={'session_id':'board-source','message_id':mid})).json()
    assert len(again['items'])==1
    item['placed']=True
    payload={'version':board['version'],'title':board['title'],'items':[item]}
    saved=await client.put(f'/boards/{board["id"]}',json=payload)
    assert saved.status_code==200,saved.text
    assert (await client.put(f'/boards/{board["id"]}',json=payload)).status_code==409
    await client.delete('/sessions/board-source')
    assert (await client.get(f'/boards/{board["id"]}')).json()['items'][0]['spec']['data'][0]['y']==[10,20]


async def test_panel_context_is_frozen_and_only_includes_canvas(storage_app):
    from app.attachments import prepare_context
    client,_,_=storage_app
    mid=await seed_chart(client)
    board=(await client.post('/boards',json={'title':'面板'})).json()
    board=(await client.post(f'/boards/{board["id"]}/charts',json={'session_id':'board-source','message_id':mid})).json()
    with pytest.raises(ValueError,match='画布'):
        prepare_context(AskRequest(question='总结',board={'id':board['id'],'version':board['version']}))
    board['items'][0]['placed']=True
    board=(await client.put(f'/boards/{board["id"]}',json=board)).json()
    req=AskRequest(question='比较',board={'id':board['id'],'version':board['version']})
    prepare_context(req)
    frozen=json.dumps(req._attachments,ensure_ascii=False)
    board['title']='改名'
    assert (await client.put(f'/boards/{board["id"]}',json=board)).status_code==200
    assert json.dumps(req._attachments,ensure_ascii=False)==frozen
    assert 'SQL' not in frozen and 'SELECT secret' not in frozen


async def test_quote_only_accepts_assistant_in_this_chat(storage_app):
    from app.attachments import prepare_context
    client,_,_=storage_app
    mid=await seed_chart(client)
    req=AskRequest(question='为什么',session_id='board-source',quote={'message_id':mid,'text':'销售增长'})
    prepare_context(req)
    assert req._attachments['quote']['text']=='销售增长'
    with pytest.raises(ValueError):
        prepare_context(AskRequest(question='为什么',quote={'message_id':mid,'text':'销售增长'}))


async def test_boards_follow_chat_storage_copy_and_switch(storage_app):
    client,_,target=storage_app
    board=(await client.post('/boards',json={'title':'随目录保存'})).json()
    response=await client.put('/settings/storage',json={'directory':str(target)})
    assert response.status_code==200,response.text
    assert (await client.get(f'/boards/{board["id"]}')).json()['title']=='随目录保存'
    board['items']=[{'id':'compact-note','kind':'text','text':'短笔记','placed':True,'width':160,'height':80}]
    assert (await client.put(f'/boards/{board["id"]}',json=board)).status_code==200
    assert (await client.delete(f'/boards/{board["id"]}',headers={'content-type':'application/json'})).status_code==200
    assert (await client.get(f'/boards/{board["id"]}')).status_code==404


@pytest.mark.parametrize('kind',['board','charts','conversation'])
async def test_panel_task_calls_model_with_actual_values_and_saves_context_without_sql(storage_app,monkeypatch,kind):
    import asyncio
    from types import SimpleNamespace
    from app import main,attachments,context_memory
    from app.execution import TaskRegistry
    client,_,_=storage_app
    mid=await seed_chart(client)
    board=(await client.post('/boards',json={'title':'已知结果'})).json()
    board=(await client.post(f'/boards/{board["id"]}/charts',json={'session_id':'board-source','message_id':mid})).json()
    board['items'][0]['placed']=True
    board=(await client.put(f'/boards/{board["id"]}',json=board)).json()
    seen=[]
    def invoke(messages):
        seen.extend(messages)
        return SimpleNamespace(content='销售增长：二月20，高于一月10。',usage_metadata={'input_tokens':100,'output_tokens':20,'total_tokens':120},response_metadata={})
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(attachments,'get_chat_llm',lambda:SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(context_memory,'get_chat_llm',lambda:SimpleNamespace(invoke=invoke))
    from app.agent import router
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:router.RouteDecision(route='chat',reason='只读附件摘要', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    registry=TaskRegistry();monkeypatch.setattr(main,'tasks',registry)
    try:
        reference={kind:{'id':board['id'],'version':board['version']}} if kind=='board' else {kind:{'session_id':'board-source',**({'message_ids':[mid]} if kind=='charts' else {})}}
        response=await client.post('/tasks',json={'question':'总结附件','mode':'investigate',**({'session_id':'board-source'} if kind=='charts' else {}),**reference})
        assert response.status_code==200,response.text
        for _ in range(100):
            task=(await client.get(f'/tasks/{response.json()["task_id"]}')).json()
            if task['status'] in {'succeeded','failed'}:break
            await asyncio.sleep(.02)
        assert task['status']=='succeeded',task
        result=task['result']
        assert result['sql'] is None and result['data_sources']==[]
        expected=2 if kind in {'board','charts'} else 1
        assert task['model_calls']==expected and result['evidence']['usage']['total_tokens']==120
        assert ('销售增长' if kind=='conversation' else '10, 20') in seen[-1].content and 'SELECT secret' not in seen[-1].content
        history=(await client.get(f'/sessions/{task["session_id"]}')).json()['messages']
        assert kind in history[-2]['metadata']['context_snapshot']
        assert history[-1]['metadata']['context_snapshot']==result['context_snapshot']
    finally:registry.close()


async def test_chat_and_chart_references_reject_stale_ids_wrong_chats_and_noncharts_before_model(storage_app,monkeypatch):
    from app import main
    client,_,_=storage_app
    mid=await seed_chart(client)
    store=session.get_session_store()._backend
    store.save_message('board-source','assistant','普通解释',{'chart_spec':None})
    plain=store.get_messages('board-source')[-1]['id']
    def no_model():raise AssertionError('Invalid attachments must not reach the model')
    monkeypatch.setattr(main,'get_chat_llm',no_model)
    cases=[{'charts':{'session_id':'board-source','message_ids':[mid,mid]}},
           {'charts':{'session_id':'board-source','message_ids':[plain]}},
           {'charts':{'session_id':'different-chat','message_ids':[mid]}},
           {'conversation':{'session_id':'board-source','message_ids':[999999]}},
           {'conversation':{'session_id':'board-source','message_ids':[mid]*6}}]
    for fields in cases:
        response=await client.post('/tasks',json={'question':'比较','session_id':'board-source',**fields})
        assert response.status_code in (400,422),response.text


async def test_selected_history_and_charts_are_frozen_without_borrowing_old_sql(storage_app):
    from app.attachments import prepare_context
    client,_,_=storage_app
    mid=await seed_chart(client)
    session.get_session_store().get_or_create('other-source')
    session.get_session_store()._backend.save_message('other-source','assistant','销售增长',{'sql':'SELECT secret'})
    req=AskRequest(question='比较附件',session_id='board-source',conversation={'session_id':'other-source'},charts={'session_id':'board-source','message_ids':[mid]})
    prepare_context(req)
    assert req._attachments['charts']['items'][0]['spec']['data'][0]['y']==[10,20]
    assert req._conversation_source['records'][0]['content']=='销售增长'
    before=json.dumps(req._attachments,ensure_ascii=False)
    with session.get_session_store()._backend._connect() as con:
        con.execute('UPDATE messages SET content=? WHERE id=?',('之后修改的历史',mid))
    assert json.dumps(req._attachments,ensure_ascii=False)==before
    assert 'SELECT secret' not in before


async def test_oversized_history_is_frozen_whole_for_chunked_summarization(storage_app):
    from app.attachments import prepare_context
    client,_,_=storage_app
    await seed_chart(client)
    store=session.get_session_store()._backend
    store.save_message('board-source','assistant','x'*61000,{})
    req=AskRequest(question='总结',conversation={'session_id':'board-source'})
    prepare_context(req)
    assert req._conversation_source['records'][-1]['content']=='x'*61000
    assert 'messages' not in req._attachments['conversation']
