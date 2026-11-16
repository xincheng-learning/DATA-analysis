from types import SimpleNamespace
import pytest
from app import context_memory as memory, session
from app.schemas import AskRequest
from app.attachments import prepare_context
from test_chat_storage import storage_app  # noqa: F401


async def test_short_conversation_uses_complete_source_without_a_summary_call(storage_app,monkeypatch):
    client,_,_=storage_app
    await client.post('/sessions/short/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    backend=session.get_session_store()._backend
    backend.save_message('short','user','确认：只统计已支付订单，币种人民币。',{})
    monkeypatch.setattr(memory,'get_chat_llm',lambda:(_ for _ in ()).throw(AssertionError('Short source must not call a model')))
    req=AskRequest(question='比较',conversation={'session_id':'short'});prepare_context(req)
    memory.resolve_conversation(req)
    assert req._attachments['conversation']['mode']=='source'
    assert '已支付订单' in req._attachments['conversation']['summary']
    assert req._attachments['conversation']['through_id']==backend.get_messages('short')[-1]['id']


async def test_successful_summary_chunks_survive_failure_and_are_not_rebilled(storage_app,monkeypatch):
    client,_,_=storage_app
    await client.post('/sessions/chunks/upload',files={'files':('a.csv',b'x\n1\n','text/csv')})
    records=memory.records_for('chunks')
    monkeypatch.setattr(memory,'_chunks',lambda records:iter(['第一块内容','第二块内容']))
    calls=[]
    def invoke(messages):
        text=messages[-1].content;calls.append(text)
        if len(calls)==2:raise RuntimeError('temporary')
        return SimpleNamespace(content='已确认币种及来源摘要',usage_metadata={'input_tokens':10,'output_tokens':10,'total_tokens':20},response_metadata={})
    monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=invoke))
    with pytest.raises(memory.ContextMemoryError):memory.summarize('chunks',records,'export')
    result=memory.summarize('chunks',records,'export')
    assert len(calls)==3
    assert '第二块内容' in calls[-1] and '第一块内容' not in calls[-1]
    assert result['source_count']==len(records)


def test_image_bytes_are_not_mistaken_for_text_context_tokens(monkeypatch):
    from langchain_core.messages import HumanMessage
    from app.execution import invoke_model
    from app.config import settings
    monkeypatch.setattr(settings,'context_memory_tokens',4000)
    seen=[]
    model=SimpleNamespace(invoke=lambda messages:seen.append(messages) or SimpleNamespace(content='识别结果'))
    invoke_model(model,[HumanMessage(content=[{'type':'text','text':'检查截图'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'a1B2c3D4'*50000}}])])
    assert len(seen)==1
