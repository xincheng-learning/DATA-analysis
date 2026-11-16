from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_local_api import local_app  # noqa: F401


@pytest.fixture
async def product_app(local_app,monkeypatch):
    from app import boards, main, context_memory
    monkeypatch.setattr(boards,'get_session_store',main.get_session_store)
    monkeypatch.setattr(context_memory,'get_session_store',main.get_session_store)
    return local_app


def planned(monkeypatch, steps, requirements):
    from app.agent import workflow
    plan = workflow.WorkflowPlan(requirements=requirements, steps=steps)
    monkeypatch.setattr(workflow, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=lambda messages:plan))
    return workflow


@pytest.mark.parametrize('cancel',[False,True])
async def test_relationship_understanding_task_saves_keys_checks_and_usage_without_blocking_reads(product_app,monkeypatch,cancel):
    import asyncio
    import threading
    from app import main,llm,context_memory
    from app.execution import TaskRegistry
    from app.tools.relationships import RelationshipUnderstanding
    from test_task_api import wait
    client,_,_=product_app
    await client.post('/sessions/semantic/upload',files=[('files',('buyers.csv',b'buyer_code\na\nb\n','text/csv')),
        ('files',('orders.csv',b'customer_ref\na\na\nb\n','text/csv')),('files',('weather.csv',b'humidity\n20\n','text/csv'))])
    plan=RelationshipUnderstanding(tables=[{'table_name':'buyers','purpose':'客户','grain':'一个客户','reason':'编号字段'}],
        candidates=[{'left_table':'buyers','right_table':'orders','left_keys':['buyer_code'],'right_keys':['customer_ref'],'reason':'可能是客户编号'}],
        independent_tables=[{'table_name':'weather','reason':'没有共同业务键'}])
    entered,release=threading.Event(),threading.Event()
    def invoke(messages):entered.set();assert release.wait(5);return plan
    monkeypatch.setattr(llm,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    registry=TaskRegistry();monkeypatch.setattr(main,'tasks',registry)
    try:
        bench=(await client.get('/sessions/semantic/workbench')).json()
        response=await client.post('/sessions/semantic/relationships/understand',json={'snapshot':bench['snapshot']})
        assert response.status_code==200,response.text
        assert await asyncio.to_thread(entered.wait,5),str(registry.read(response.json()['task_id'],'local-user'))
        read=await asyncio.wait_for(client.get('/sessions/semantic/relationships'),.8)
        assert read.status_code==200
        if cancel:
            assert (await client.post(f"/tasks/{response.json()['task_id']}/cancel",json={})).status_code==200
        release.set();task=await wait(client,response.json())
        if cancel:
            assert task['status']=='cancelled'
            assert (await client.get('/sessions/semantic/relationships')).json()['understanding'] is None
            messages=(await client.get('/sessions/semantic')).json()['messages']
            assert messages[-1]['metadata']['evidence']['model_calls']==1
            return
        assert task['status']=='succeeded',task
        receipt=task['result']['workflow']['steps'][0]['result']['relationship_understanding']
        assert receipt['candidates'][0]['report']['actual_join_rows']==3
        assert 'buyer_code' in task['result']['answer'] and 'customer_ref' in task['result']['answer'] and '实际关联 3 行' in task['result']['answer']
        assert task['result']['evidence']['model_calls']==1
        messages=(await client.get('/sessions/semantic')).json()['messages']
        saved=messages[-1]['metadata']['workflow']['steps'][0]['result']['relationship_understanding']
        assert saved==receipt
        projected=context_memory.project_messages(messages)[-1]['evidence']['workflow']['steps'][0]['result']['relationship_understanding']
        assert projected==receipt
    finally:
        release.set();registry.close()


def test_plan_rejects_uncovered_requests_unknown_tools_and_future_dependencies():
    from app.agent.workflow import WorkflowPlan
    for payload in [
        dict(requirements=['统计金额','画图'],steps=[dict(id='sum',request='统计金额',covers=[0],tool='analyze',arguments={'question':'总金额'})]),
        dict(requirements=['修改密钥'],steps=[dict(id='key',request='修改密钥',covers=[0],tool='shell',arguments={})]),
        dict(requirements=['做面板'],steps=[dict(id='board',request='做面板',covers=[0],tool='board',arguments={'title':'面板','step_ids':['future']})]),
    ]:
        with pytest.raises(ValidationError):
            WorkflowPlan.model_validate(payload)


async def test_clean_analyze_chart_and_board_share_current_data_version(product_app, monkeypatch):
    client, _, _ = product_app
    await client.post('/sessions/workflow/upload', files={'files':('sales.csv',b'region,amount\n East ,10\nEast,20\n','text/csv')})
    from test_cleaning_assistant import advice
    advice(monkeypatch)
    steps=[
        dict(id='clean',request='执行地区去空白',covers=[0],tool='cleaning',arguments={'question':'直接清理 region 首尾空白','intent':'apply'}),
        dict(id='chart',request='按地区汇总金额并画图',covers=[1],tool='analyze',arguments={'question':'按region汇总amount','mode':'viz'}),
        dict(id='board',request='保存到面板',covers=[2],tool='board',arguments={'title':'清洗后的金额','step_ids':['chart']}),
        dict(id='export',request='导出结果',covers=[3],tool='export_result',arguments={'step_id':'chart'}),
    ]
    workflow=planned(monkeypatch,steps,['执行清洗','汇总画图','创建面板','导出结果'])
    from app.agent import nodes
    from app.tools import viz
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content='SELECT region,SUM(amount) AS total FROM sales GROUP BY region')))
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:viz.ChartSpec(chart_type='bar',x_column='region',y_column='total',title='地区金额',x_label='地区',y_label='金额'))
    result=workflow.workflow_node({'session_id':'workflow','question':'清洗后画图并做面板'})
    assert result['workflow']['status']=='completed',result
    clean,chart,board,export=result['workflow']['steps']
    assert clean['result']['cleaning_event']['action']=='apply'
    assert chart['result']['rows']==[{'region':'East','total':30.0}]
    assert clean['result']['cleaning_event']['after_snapshot']==chart['result']['data_version']
    from app.boards import get_board
    saved=get_board(board['result']['board']['id'])
    assert len(saved['items'])==1 and saved['items'][0]['placed']
    assert saved['items'][0]['spec']==chart['result']['chart_spec']
    assert export['result']['download']['row_count']==1
    from app.tools.query_results import read_result
    assert read_result('workflow',export['result']['download']['result_id'],0)['rows']==[{'region':'East','total':30.0}]


async def test_preview_stops_dependent_work_instead_of_claiming_completion(product_app, monkeypatch):
    client, _, _ = product_app
    await client.post('/sessions/preview-flow/upload', files={'files':('sales.csv',b'region\n East \n','text/csv')})
    from test_cleaning_assistant import advice
    advice(monkeypatch)
    workflow=planned(monkeypatch,[
        dict(id='clean',request='先给清洗方案',covers=[0],tool='cleaning',arguments={'question':'给清洗建议','intent':'preview'}),
        dict(id='board',request='再做面板',covers=[1],tool='board',arguments={'title':'不能提前创建'}),
    ],['清洗方案','后续面板'])
    result=workflow.workflow_node({'session_id':'preview-flow','question':'先给方案，再做面板'})
    assert result['workflow']['status']=='waiting'
    assert result['workflow']['steps'][1]['status']=='skipped'
    from app.boards import list_boards
    assert list_boards()==[]


async def test_invalid_tool_arguments_are_rejected_before_any_write(product_app, monkeypatch):
    from app.agent.workflow import WorkflowPlan
    with pytest.raises(ValidationError):
        WorkflowPlan(requirements=['新建面板','执行未知操作'],steps=[
            dict(id='board',request='新建',covers=[0],tool='board',arguments={'title':'不能产生副作用'}),
            dict(id='bad',request='执行',covers=[1],tool='cleaning',arguments={'question':'检查','intent':'shell'}),
        ])


@pytest.mark.parametrize('structured',[False,True])
async def test_chat_confirmation_continues_remaining_work_without_replanning(product_app,monkeypatch,structured):
    client,_,_=product_app
    await client.post('/sessions/resume-work/upload',files={'files':('sales.csv',b'region\n East \n','text/csv')})
    from test_cleaning_assistant import advice
    advice(monkeypatch)
    workflow=planned(monkeypatch,[
        dict(id='clean',request='清洗预览',covers=[0],tool='cleaning',arguments={'question':'给出region去空白方案','intent':'preview'}),
        dict(id='board',request='创建分析笔记面板',covers=[1],tool='board',arguments={'title':'确认后继续'}),
    ],['先给清洗方案','确认后建面板'])
    pending=workflow.workflow_node({'session_id':'resume-work','question':'先给方案，同意后继续建面板'})
    from app import main,boards
    from app.schemas import AskRequest
    main.get_session_store()._backend.save_message('resume-work','assistant',pending['answer'],pending)
    monkeypatch.setattr(main,'run_agent',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('不能重新规划或重复清洗')))
    if structured:
        from app.execution import TaskRegistry
        from app.schemas import AskResponse, CleaningReply
        from app.cleaning_assistant import chat_confirmation_target
        from test_task_api import wait
        message=main.get_session_store()._backend.get_messages('resume-work')[-1]
        reply=CleaningReply(message_id=message['id'],plan_id=pending['cleaning']['plan_id'])
        data=main.get_dataset_manager().get('resume-work')
        messages=main.get_session_store()._backend.get_messages('resume-work')
        for wrong in [reply.model_copy(update={'message_id':message['id']+1}),reply.model_copy(update={'plan_id':'f'*32})]:
            assert '未执行' in chat_confirmation_target('确认清洗并继续原任务',data,messages,wrong)['answer']
        monkeypatch.setattr(main,'tasks',TaskRegistry())
        monkeypatch.setattr(main,'get_chat_llm',lambda:object())
        response=await client.post('/tasks',json={'session_id':'resume-work','question':'确认清洗并继续原任务','cleaning_reply':reply.model_dump()})
        assert response.status_code==200,response.text
        task=await wait(client,response.json())
        assert task['status']=='succeeded',task
        result=AskResponse.model_validate(task['result'])
        assert main.get_session_store()._backend.get_messages('resume-work')[-2]['metadata']['cleaning_reply']==reply.model_dump()
        duplicate=await client.post('/tasks',json={'session_id':'resume-work','question':'确认清洗并继续原任务','cleaning_reply':reply.model_dump()})
        repeated=await wait(client,duplicate.json())
        assert repeated['status']=='succeeded',repeated
        assert '未执行清洗或后续步骤' in repeated['result']['answer']
    else:
        result=main._analyse(AskRequest(session_id='resume-work',question='那你直接帮我执行清洗操作吧'),'local-user')
    assert result.workflow['status']=='completed'
    assert len(boards.list_boards())==1
    assert all(step['status']=='succeeded' for step in result.workflow['steps'])


async def test_cancel_after_cleaning_preserves_receipt_and_skips_board(product_app,monkeypatch):
    client,_,_=product_app
    await client.post('/sessions/stop-work/upload',files={'files':('sales.csv',b'region,amount\n East ,10\n','text/csv')})
    from test_cleaning_assistant import advice
    advice(monkeypatch)
    workflow=planned(monkeypatch,[
        dict(id='clean',request='执行清洗',covers=[0],tool='cleaning',arguments={'question':'清理region空白','intent':'apply'}),
        dict(id='query',request='查询金额',covers=[1],tool='analyze',arguments={'question':'查询总金额'}),
        dict(id='board',request='创建面板',covers=[2],tool='board',arguments={'title':'不能创建'}),
    ],['执行清洗','统计金额','创建面板'])
    from app import execution,main,boards
    from app.agent import nodes,router
    from app.schemas import AskRequest
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:router.RouteDecision(route='workflow',reason='执行多项任务',analysis_brief={'goal':'执行多项任务','requirements':['执行清洗','统计金额','创建面板'],
        'acceptance':[{'requirement_index':i,'answer_type':'explanation'} for i in range(3)]})))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:(_ for _ in ()).throw(execution.AnalysisCancelled('停止'))))
    task=execution.AnalysisTask(owner='local-user',session_id='stop-work')
    token=execution._current.set(task)
    try:
        with pytest.raises(execution.AnalysisCancelled):
            main._analyse(AskRequest(question='直接清洗region再统计金额和建面板',session_id='stop-work'),'local-user')
    finally:
        execution._current.reset(token)
    assert task.result['workflow']['status']=='stopped'
    history=main.get_session_store()._backend.get_messages('stop-work')
    saved=history[-1]['metadata']['workflow']['steps']
    assert saved[0]['result']['cleaning_event']['action']=='apply'
    assert saved[0]['result']['cleaning_event']['approval_text']=='直接清洗region再统计金额和建面板'
    assert saved[1]['status']=='cancelled' and saved[2]['status']=='skipped'
    assert boards.list_boards()==[]


async def test_selected_board_can_be_updated_from_chat_without_changing_the_attachment_snapshot(product_app,monkeypatch):
    client,_,_=product_app
    await client.post('/sessions/board-tools/upload',files={'files':('sales.csv',b'region\nEast\n','text/csv')})
    from app import attachments,boards,main
    from app.agent import router
    from app.schemas import AskRequest
    board=boards.write_tool_board('','用户选定面板',[],'原笔记')
    workflow=planned(monkeypatch,[dict(id='board',request='添加笔记',covers=[0],tool='board',arguments={
        'board_id':board['id'],'version':board['version'],'note':'已按当前要求添加的新笔记'})],['给选定面板添加笔记'])
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:router.RouteDecision(route='workflow',reason='更新选定面板', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    req=AskRequest(question='在这个面板上加一条笔记',session_id='board-tools',board={'id':board['id'],'version':board['version']})
    attachments.prepare_context(req)
    result=main._analyse(req,'local-user')
    assert result.workflow['status']=='completed'
    saved=boards.get_board(board['id'])
    assert saved['version']==board['version']+1 and len(saved['items'])==2
    assert result.context_snapshot['board']['version']==board['version']
    assert len(result.context_snapshot['board']['items'])==1


async def test_export_historical_workflow_substep_uses_saved_complete_rows(product_app,monkeypatch):
    client,_,_=product_app
    await client.post('/sessions/old-result/upload',files={'files':('sales.csv',b'amount\n30\n','text/csv')})
    from app import main
    backend=main.get_session_store()._backend
    backend.save_message('old-result','assistant','历史统计',{'workflow':{'steps':[
        {'id':'sum','status':'succeeded','result':{'columns':['amount'],'rows':[{'amount':30}],'row_count':1}},
    ]}})
    mid=backend.get_messages('old-result')[-1]['id']
    workflow=planned(monkeypatch,[dict(id='export',request='导出历史结果',covers=[0],tool='export_result',
        arguments={'message_id':mid,'source_step_id':'sum'})],['导出历史结果'])
    result=workflow.workflow_node({'session_id':'old-result','question':'导出刚才结果，不重算'})
    assert result['workflow']['status']=='completed'
    download=result['workflow']['steps'][0]['result']['download']
    from app.tools.query_results import read_result
    assert read_result('old-result',download['result_id'],0)['rows']==[{'amount':30}]


def test_workflow_reordering_preserves_requirement_ids_and_active_child_scope(monkeypatch):
    from app.agent import workflow
    from app.config import settings
    from app import boards,superset
    from app.tools import dataset_manager
    monkeypatch.setattr(settings,'app_mode','local')
    monkeypatch.setattr(boards,'list_boards',lambda:[])
    monkeypatch.setattr(superset,'list_views',lambda:[])
    plan=workflow.WorkflowPlan(requirements=['第二项','第一项'],requirement_ids=['R2','R1'],steps=[
        dict(id='b',request='第二项',covers=[0],tool='analyze',arguments={'question':'第二项'}),
        dict(id='a',request='第一项',covers=[1],tool='analyze',arguments={'question':'第一项'})])
    monkeypatch.setattr(workflow,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:plan))
    captured=[]
    monkeypatch.setattr(workflow,'_run_plan',lambda state,planned:captured.append(planned) or {})
    state={'session_id':'reorder','question':'两项分析','analysis_brief':{'acceptance':[
        {'id':'R1','requirement':'第一项','answer_type':'value'},
        {'id':'R2','requirement':'第二项','answer_type':'value'}]}}
    workflow.workflow_node(state)
    assert [s.covers for s in captured[0].steps]==[[1],[0]]
    assert captured[0].requirements==['第一项','第二项']
    monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:None))
    monkeypatch.setattr(workflow,'_analyze',lambda args,state,history:state['analysis_brief'])
    child=workflow.execute_tool(captured[0].steps[0],state,[],'qa')
    assert child['active_requirement_ids']==['R2']
