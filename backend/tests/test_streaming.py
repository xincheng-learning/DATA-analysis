import threading

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.runnables import Runnable

from app import execution


class TwoChunkModel(Runnable):
    def __init__(self, first, release):
        self.first, self.release = first, release
        self.closed = False

    def invoke(self, input, config=None, **kwargs):
        raise AssertionError('A streaming reply must not invoke a buffered response.')

    def stream(self, input, config=None, **kwargs):
        try:
            yield AIMessageChunk(content='第一段中文')
            self.first.set()
            assert self.release.wait(3)
            yield AIMessageChunk(content='，第二段。', usage_metadata={'input_tokens': 10, 'output_tokens': 8, 'total_tokens': 18})
        finally:
            self.closed = True


@pytest.mark.parametrize('stop', [False, True])
def test_real_model_chunks_arrive_before_completion_and_stop_keeps_first(stop, monkeypatch):
    from app import model_settings
    monkeypatch.setattr(model_settings, 'get_model_status', lambda: {})
    first, release = threading.Event(), threading.Event()
    model = TwoChunkModel(first, release)
    registry = execution.TaskRegistry()
    try:
        task = registry.submit('owner', 'session', lambda: execution.invoke_model(model, [], stream_text=True))
        assert first.wait(3)
        snapshot = registry.read(task['task_id'], 'owner')
        assert snapshot['status'] == 'running'
        assert snapshot['draft_answer'] == '第一段中文'
        assert snapshot['result'] is None and snapshot['model_calls'] == 1
        if stop:
            registry.cancel(task['task_id'], 'owner')
        release.set()
        registry.close()
        final = registry.read(task['task_id'], 'owner')
        assert model.closed
        if stop:
            assert final['status'] == 'cancelled' and final['draft_answer'] == '第一段中文'
        else:
            assert final['draft_answer'] == '第一段中文，第二段。'
            assert final['usage']['total_tokens'] == 18
        assert final['model_calls'] == 1
        with pytest.raises(KeyError):
            registry.read(task['task_id'], 'stranger')
    finally:
        release.set()
        registry.close()


def test_code_generation_does_not_publish_internal_model_chunks(monkeypatch):
    from types import SimpleNamespace
    task = execution.AnalysisTask('owner', 'session')
    token = execution._current.set(task)
    try:
        execution.invoke_model(SimpleNamespace(invoke=lambda _: SimpleNamespace(content='SELECT secret_column')), [])
        assert task.public()['draft_answer'] == ''
    finally:
        execution._current.reset(token)


def test_openai_compatible_wire_stream_preserves_usage_and_excludes_hidden_reasoning(monkeypatch):
    import json
    import httpx
    from app.llm import build_compatible_client
    from app import model_settings
    monkeypatch.setattr(model_settings,'get_model_status',lambda:{})
    seen=[]
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for delta in [{'reasoning_content':'不应公开的隐藏推理'},{'content':'第一段'},{'content':'第二段'}]:
                yield ('data: '+json.dumps({'id':'test','object':'chat.completion.chunk','model':'deepseek-flash',
                    'choices':[{'index':0,'delta':delta,'finish_reason':None}]},ensure_ascii=False)+'\n\n').encode()
            yield ('data: '+json.dumps({'id':'test','object':'chat.completion.chunk','model':'deepseek-flash','choices':[],
                'usage':{'prompt_tokens':10,'completion_tokens':8,'total_tokens':18}})+'\n\n').encode()
            yield b'data: [DONE]\n\n'
    def handle(request):
        seen.append(json.loads(request.content));return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
    task=execution.AnalysisTask('owner','session');token=execution._current.set(task)
    try:
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            model=build_compatible_client('https://api.deepseek.com','controlled-key','deepseek-flash',http_client=client)
            result=execution.invoke_model(model,[HumanMessage(content='测试')],stream_text=True)
        assert result.content=='第一段第二段' and task.draft_answer=='第一段第二段'
        assert task.public()['usage']['total_tokens']==18
        assert len(seen)==1 and seen[0]['stream'] and seen[0]['stream_options']['include_usage']
        assert '隐藏推理' not in task.draft_answer
    finally:
        execution._current.reset(token)


def test_report_only_streams_closed_findings_with_valid_evidence(monkeypatch):
    from app import analysis_report, model_settings
    from app.insights import result_facts
    import pandas as pd
    first, release=threading.Event(), threading.Event()
    frame=pd.DataFrame([{'month':'June','revenue':10},{'month':'July','revenue':20}])
    result={'status':'completed','steps':[{'question':'两期比较','status':'succeeded','columns':list(frame),
        'rows':frame.to_dict('records'),'row_count':2,'sql':'SELECT month,revenue','facts':result_facts(frame,'SELECT month,revenue','v1'),'data_version':'v1'}]}
    class Plans(Runnable):
        def invoke(self,input,config=None,**kwargs):raise AssertionError('must stream')
        def stream(self,input,config=None,**kwargs):
            yield analysis_report.ReportPlan(findings=[{'text':'已获得可比结果','evidence_ids':['S1.R1']},
                {'text':'由于秘密原因增长999倍','evidence_ids':['invented']}])
            first.set();assert release.wait(3)
            yield analysis_report.ReportPlan(findings=[{'text':'已获得可比结果','evidence_ids':['S1.R1']},
                {'text':'完成对照','evidence_ids':['S1.R2']}])
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:Plans())
    monkeypatch.setattr(model_settings,'get_model_status',lambda:{})
    registry=execution.TaskRegistry()
    try:
        task=registry.submit('owner','case',lambda:analysis_report.compose_report('两期比较',result))
        assert first.wait(3)
        draft=registry.read(task['task_id'],'owner')['draft_answer']
        assert '已获得可比结果' in draft and 'S1.R1' in draft
        assert '秘密' not in draft and '999' not in draft and 'invented' not in draft
    finally:
        release.set();registry.close()


@pytest.mark.parametrize('split',range(1,len('<｜｜DSML｜｜')+1))
def test_unexecuted_tool_protocol_never_reaches_streamed_or_saved_answer(split,monkeypatch):
    from app import model_settings
    monkeypatch.setattr(model_settings,'get_model_status',lambda:{})
    marker='<｜｜DSML｜｜'
    raw=marker+' calls> <｜｜DSML｜｜ invoke name="query_sql">SELECT private_data FROM sales'
    snapshots=[]
    task=execution.AnalysisTask('qa','qa');token=execution._current.set(task)
    class ProtocolModel(Runnable):
        def invoke(self,input,config=None,**kwargs):raise AssertionError('must stream once')
        def stream(self,input,config=None,**kwargs):
            yield AIMessageChunk(content='已有结果保留。')
            yield AIMessageChunk(content=raw[:split])
            snapshots.append(task.draft_answer)
            yield AIMessageChunk(content=raw[split:],usage_metadata={'input_tokens':10,'output_tokens':20,'total_tokens':30})
    try:
        with pytest.raises(execution.UnexpectedModelReply,match='未执行的工具指令'):
            execution.invoke_model(ProtocolModel(),[],stream_text=True)
        assert task.draft_answer=='已有结果保留。'
        assert all(s=='已有结果保留。' for s in snapshots)
        assert task.model_calls==1 and task.public()['usage']['total_tokens']==30
        assert not task.result and 'SELECT' not in task.draft_answer
    finally:execution._current.reset(token)


def test_buffered_real_chat_protocol_is_rejected_without_execution_or_extra_request():
    from types import SimpleNamespace
    calls=[]
    raw='\n\n<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="query_sql">SELECT month,status FROM sales;'
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=raw)
    with pytest.raises(execution.UnexpectedModelReply,match='未执行的工具指令'):
        execution.invoke_model(SimpleNamespace(invoke=invoke),[],stream_text=True)
    assert len(calls)==1


@pytest.mark.parametrize('chunk',[AIMessageChunk(content='<｜｜DSML'),
    AIMessageChunk(content='',tool_call_chunks=[{'name':'query_sql','args':'{}','id':'unexecuted','index':0}])])
def test_partial_protocol_and_structured_tool_calls_are_not_plain_chat(chunk,monkeypatch):
    from app import model_settings
    monkeypatch.setattr(model_settings,'get_model_status',lambda:{})
    class Model(Runnable):
        def invoke(self,input,config=None,**kwargs):raise AssertionError('stream once')
        def stream(self,input,config=None,**kwargs):yield chunk
    task=execution.AnalysisTask('qa','qa');token=execution._current.set(task)
    try:
        with pytest.raises(execution.UnexpectedModelReply):execution.invoke_model(Model(),[],stream_text=True)
        assert task.draft_answer=='' and task.model_calls==1
    finally:execution._current.reset(token)
