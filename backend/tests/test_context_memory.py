"""Bounded, persistent model summaries; no paid API requests."""
from types import SimpleNamespace

import pytest

from app import session
from app.config import settings
from test_chat_storage import storage_app  # noqa: F401


def test_memory_keeps_fact_values_without_repeating_sql_and_shared_provenance():
    from app.context_memory import project_messages
    facts=[{'id':f'F{i}','label':'sales','statistic':kind,'value':value,'sql':'SELECT private_source '*500,'scope':'同一范围','data_version':'v1'} for i,kind,value in [(1,'min',10),(2,'max',20)]]
    original={'id':1,'role':'assistant','content':'销售对比','metadata':{'facts':facts,'sql':'SELECT private_source','data_version':'v1'}}
    projected=project_messages([original])[0]
    assert [fact['value'] for fact in projected['evidence']['facts']]==[10,20]
    assert 'private_source' not in str(projected)
    assert projected['evidence']['data_version']=='v1'
    assert original['metadata']['facts'][0]['sql'].startswith('SELECT private_source')


def test_memory_retains_the_denominator_for_zero_rates_and_masks_personal_fields():
    from app.context_memory import project_messages
    original={'id':7,'role':'assistant','content':'复购分析','metadata':{'columns':['cohort','eligible','repeated','rate','email'],'rows':[{'cohort':'June','eligible':2,'repeated':0,'rate':0,'email':'private@example.test'}],'row_count':1}}
    result=project_messages([original])[0]['query_result']
    assert result['rows'][0]['eligible']==2 and result['rows'][0]['rate']==0
    assert result['included_rows']==result['returned_rows']==1
    assert 'private@example.test' not in str(result)


def model(monkeypatch):
    from app import context_memory
    calls=[]
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content='目标：比较销售。确认口径：GMV，币种BRL，订单先聚合后关联。结论待核对，不能把缺失金额填零。',usage_metadata={'input_tokens':100,'output_tokens':20,'total_tokens':120},response_metadata={})
    monkeypatch.setattr(context_memory,'get_chat_llm',lambda:SimpleNamespace(invoke=invoke))
    return calls


async def test_history_compacts_old_turns_keeps_recent_and_reuses_cache(storage_app,monkeypatch):
    from app import context_memory as memory
    client,_,_=storage_app
    await client.post('/sessions/memory-test/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    store=session.get_session_store()._backend
    monkeypatch.setattr(settings,'context_memory_tokens',4000)
    calls=model(monkeypatch)
    for index in range(24):store.save_message('memory-test','user' if index%2==0 else 'assistant',f'消息{index}，'+('销售口径与待验证问题。'*50),{})
    original=store.get_messages('memory-test')
    history,status=memory.history_context('memory-test')
    assert status['compressed'] and status['used_tokens']<status['trigger_tokens']
    assert '消息23' in history and '消息0，' not in history
    assert 'GMV' in history and 'BRL' in history
    count=len(calls)
    assert memory.history_context('memory-test')[0]==history and len(calls)==count
    store.save_message('memory-test','user','最近新要求：不要把未送达订单混入已送达口径',{})
    assert '最近新要求' in memory.history_context('memory-test')[0] and len(calls)==count
    assert store.get_messages('memory-test')[:len(original)]==original
    assert memory.context_status('memory-test')['compressed']


async def test_export_summarizes_every_part_and_freezes_source_revision(storage_app,monkeypatch):
    from app import context_memory as memory
    from app.schemas import AskRequest
    from app.attachments import prepare_context
    client,_,_=storage_app
    await client.post('/sessions/export-test/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    store=session.get_session_store()._backend
    store.save_message('export-test','assistant','开始标记'+('完整内容。'*9000)+'末尾标记',{'sql':'SELECT secret','chart_spec':{'data':[{'type':'bar','x':['峰值月份'],'y':[37999]}]}})
    calls=model(monkeypatch)
    req=AskRequest(question='总结',conversation={'session_id':'export-test'})
    prepare_context(req)
    store.save_message('export-test','user','接受任务后才出现的内容',{})
    memory.resolve_conversation(req)
    result=req._attachments['conversation']
    assert 'summary' in result and 'messages' not in result
    assert result['message_count']==2
    seen=''.join(call[-1].content for call in calls)
    assert '开始标记' in seen and '末尾标记' in seen
    assert '37999' in seen,'charts with no written conclusion still contribute their actual values'
    assert '接受任务后才出现' not in seen and 'SELECT secret' not in seen
    assert all(memory.token_count(call[-1].content)<settings.context_memory_tokens-1000 for call in calls)
    before=len(calls)
    same=AskRequest(question='再总结',conversation={'session_id':'export-test'})
    prepare_context(same);memory.resolve_conversation(same)
    assert len(calls)>before,'new source messages must invalidate the old export cache'
    before=len(calls)
    again=AskRequest(question='比较',conversation={'session_id':'export-test'})
    prepare_context(again);memory.resolve_conversation(again)
    assert len(calls)==before,'unchanged source must not incur another summary call'


async def test_summary_failure_or_cancellation_keeps_last_successful_memory(storage_app,monkeypatch):
    from app import context_memory as memory
    from app.execution import AnalysisCancelled
    client,_,_=storage_app
    await client.post('/sessions/retry-memory/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    store=session.get_session_store()._backend
    calls=model(monkeypatch)
    records=memory.records_for('retry-memory')
    first=memory.summarize('retry-memory',records,'export')
    store.save_message('retry-memory','user','追加要求',{})
    for error in (RuntimeError('provider failed'),AnalysisCancelled('停止')):
        def fail(messages):raise error
        monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=fail))
        with pytest.raises((memory.ContextMemoryError,AnalysisCancelled)):
            memory.summarize('retry-memory',memory.records_for('retry-memory'),'export')
        assert memory.summarize('retry-memory',records,'export')==first
    assert calls


async def test_business_summary_can_use_the_reserved_quarter_without_increasing_total_context(storage_app,monkeypatch):
    from app import context_memory as memory
    client,_,_=storage_app
    await client.post('/sessions/business-summary/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    monkeypatch.setattr(settings,'context_memory_tokens',16000)
    summary='已核对的事实。'*450
    assert 2000<memory.token_count(summary)<4000
    monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=summary)))
    result=memory.summarize('business-summary',memory.records_for('business-summary'),'rolling')
    assert result['summary']==summary and settings.context_memory_tokens==16000
    assert memory.context_status('business-summary')['used_tokens']<12800


async def test_context_meter_is_read_only_and_isolated_between_chats(storage_app,monkeypatch):
    from app import context_memory as memory
    client,_,_=storage_app
    calls=model(monkeypatch)
    await client.post('/sessions/one/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    assert (await client.get('/sessions/one/context')).json()['limit_tokens']==settings.context_memory_tokens
    assert (await client.get('/sessions/missing/context')).status_code==404
    assert (await client.get('/context')).json()['used_tokens']==0
    assert not calls


async def test_full_prompt_limit_stops_before_a_paid_call(storage_app,monkeypatch):
    from app import context_memory as memory
    from app.execution import invoke_model
    from langchain_core.messages import HumanMessage
    calls=model(monkeypatch)
    monkeypatch.setattr(settings,'context_memory_tokens',4000)
    with pytest.raises(memory.ContextMemoryError,match='未发出'):
        invoke_model(memory.get_chat_llm(),[HumanMessage(content='业务口径 '*10000)])
    assert not calls


async def test_summaries_follow_storage_switch_and_are_deleted_with_their_chat(storage_app,monkeypatch):
    from app import context_memory as memory
    client,_,target=storage_app
    await client.post('/sessions/move-memory/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    model(monkeypatch)
    records=memory.records_for('move-memory')
    summary=memory.summarize('move-memory',records,'export')
    assert (await client.put('/settings/storage',json={'directory':str(target)})).status_code==200
    assert memory.summarize('move-memory',records,'export')==summary
    assert (await client.delete('/sessions/move-memory')).status_code==200
    with session.get_session_store()._backend._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM context_summaries').fetchone()[0]==0
