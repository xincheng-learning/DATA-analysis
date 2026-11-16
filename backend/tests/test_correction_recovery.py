from types import SimpleNamespace

import pytest

from app.answer_acceptance import prepare_brief
from app.temporal_scope import verify_temporal_scope
from test_chat_storage import storage_app  # noqa: F401


def test_declared_add_subtract_preserve_nulls_and_only_return_requested_profit(tmp_path,monkeypatch):
    import io
    from app.calculation_contract import CalculationPlan,compile_plan,request_output_columns
    from app.tools import dataset_manager
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import validate_and_convert
    manager=DatasetManager();data=manager.get_or_create('arithmetic',tmp_path)
    manager.add_file(data.session_id,validate_and_convert('sales.csv',io.BytesIO(b'price,freight,cost\n10,1,6\n20,2,12\n'),tmp_path))
    plan=CalculationPlan(source_quotes=['收入加运费再减成本'],stages=[{'name':'summary','source':'sales','select':[
        {'name':'profit','op':'subtract','numerator':'gross','denominator':'cost'},
        {'name':'gross','op':'add','numerator':'price','denominator':'freight'},
        {'name':'price','op':'sum','field':'sales.price'},{'name':'freight','op':'sum','field':'sales.freight'},
        {'name':'cost','op':'sum','field':'sales.cost'}]}])
    try:
        sql,columns=compile_plan(data,plan,'收入加运费再减成本',requested=['profit'])
        assert columns==['profit'] and data.execute(sql,as_frame=True).iloc[0]['profit']==15
        monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:manager)
        plan.source_quotes=['收入加运费再减成本。']
        brief={'calculation_plan':plan.model_dump(),'acceptance':[{'id':'R1','requirement':'利润','result_columns':['profit']}],
            'continuation_sources':[{'text':'收入加运费再减成本','source_question':'收入加运费再减成本。'}]}
        assert request_output_columns({'session_id':data.session_id,'question':'同上继续','analysis_brief':brief})==['profit']
        manager.add_file(data.session_id,validate_and_convert('missing.csv',io.BytesIO(b'revenue,cost\n11,6\n22,\n'),tmp_path))
        nullable=CalculationPlan(source_quotes=['逐行收入减成本'],stages=[{'name':'nullable','source':'missing','select':[
            {'name':'profit','op':'subtract','numerator':'missing.revenue','denominator':'missing.cost'}]}])
        null_sql,_=compile_plan(data,nullable,'逐行收入减成本')
        frame=data.execute(null_sql,as_frame=True)
        assert frame.iloc[0]['profit']==5 and frame['profit'].isna().sum()==1
        plan.source_quotes=['收入加运费再减成本']
        plan.stages[0].select[1].numerator='gross'
        with pytest.raises(ValueError,match='循环'):compile_plan(data,plan,'收入加运费再减成本')
    finally:manager.close_all()


def test_followup_keeps_actual_prior_time_quote_without_repeating_it():
    first='仅看2026年一月completed订单，汇总price，不含freight'
    prior=prepare_brief({'question':first},{'requirements':['金额'],'acceptance':[{'requirement_index':0,'request_quote':first,'answer_type':'value','result_columns':['amount']}]})
    messages=[{'id':1,'role':'user','content':first},{'id':2,'role':'assistant','metadata':{'analysis_brief':prior}}]
    request='改为price加freight，其他不变'
    b={'requirements':['金额'],'revision_quote':request,'acceptance':[{'requirement_index':0,'request_quote':request,'answer_type':'value','result_columns':['amount']}],
       'calculation_plan':{'stages':[{'filters':[{'field':'ordered_at','op':'ge','value':'2026-01-01'}]}]}}
    current=prepare_brief({'question':request},b,messages=messages)
    assert current['continuation_sources'][0]['message_id']==1
    verify_temporal_scope(current,request)
    b['revision_quote']='';b['acceptance'][0]['request_quote']='查询西区订单'
    independent=prepare_brief({'question':'查询西区订单'},b,messages=messages)
    with pytest.raises(ValueError,match='时间筛选'):verify_temporal_scope(independent,'查询西区订单')


def test_cost_upload_uses_historical_user_income_confirmation_not_an_assistant_guess():
    revenue='lines.price加lines.freight是收入，利润只按收入减实际成本'
    question='costs.cogs是每条明细实际成本，继续计算'
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='lines',columns=['price','freight']),SimpleNamespace(table_name='costs',columns=['cogs'])])
    b={'requirements':['利润'],'acceptance':[{'requirement_index':0,'answer_type':'value','metric_ids':['profit'],'result_columns':['profit']}],
       'scope_sources':[{'key':'profit_policy','text':'利润只按收入减实际成本'}],
       'metric_bindings':[{'role':'revenue','columns':['lines.price','lines.freight'],'confirmation':{'key':'revenue','text':revenue}},
                          {'role':'costs','columns':['costs.cogs'],'confirmation':{'key':'costs','text':'costs.cogs是每条明细实际成本'}}]}
    prepared=prepare_brief({'question':question},b,dataset=dataset,messages=[{'id':1,'role':'user','content':revenue}])
    assert prepared['metric_dependencies'][0]['status']=='ready'
    assert prepared['metric_bindings'][0]['confirmation']['message_id']==1
    unconfirmed=prepare_brief({'question':question},b,dataset=dataset,messages=[{'id':1,'role':'assistant','content':revenue}])
    assert unconfirmed['metric_dependencies'][0]['status']=='needs_input'


async def test_chart_retry_reuses_saved_result_without_querying_or_replanning(storage_app,monkeypatch):
    from app import main,session
    from app.execution import TaskRegistry
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.query_results import save_result
    from app.tools import viz
    from test_task_api import wait
    import pandas as pd
    client,_,_=storage_app;sid='retry-chart'
    await client.post(f'/sessions/{sid}/upload',files={'files':('sales.csv',b'region,amount\nEast,33\n','text/csv')})
    data=get_dataset_manager().get(sid);rid=save_result(sid,pd.DataFrame([{'region':'East','amount':33}]))
    meta={'route':'viz','sql':'SELECT region,amount FROM sales','rows':[{'region':'East','amount':33}],'columns':['region','amount'],
        'row_count':1,'result_id':rid,'data_version':workbench_snapshot(data),'chart_error':'绘图服务中断'}
    store=session.get_session_store()._backend;store.save_message(sid,'assistant','数据已算出，图未完成',meta);mid=store.get_messages(sid)[-1]['id']
    monkeypatch.setattr(main,'tasks',TaskRegistry())
    monkeypatch.setattr('app.agent.nodes.execute_sql_node',lambda *_:pytest.fail('must not rerun query'))
    monkeypatch.setattr(viz,'choose_chart',lambda **_:viz.ChartSpec(chart_type='bar',x_column='region',y_column='amount',title='金额',x_label='地区',y_label='金额'))
    task=await client.post(f'/sessions/{sid}/messages/{mid}/continue',json={});assert task.status_code==200,task.text
    result=await wait(client,task.json());assert result['status']=='succeeded',result
    final=result['result'];assert final['rows']==meta['rows'] and final['result_id']==rid
    assert final['chart_spec']['data'][0]['y']==[33] and not final.get('chart_error')
    assert (await client.post(f'/sessions/{sid}/messages/{mid}/continue',json={})).status_code==409
    store.save_message(sid,'assistant','计算核对失败',{**meta,'validation_failure':'不应交付'})
    invalid=store.get_messages(sid)[-1]['id']
    assert (await client.post(f'/sessions/{sid}/messages/{invalid}/continue',json={})).status_code==409
    store.save_message(sid,'assistant','仍需绘图',meta);mid=store.get_messages(sid)[-1]['id']
    await client.post(f'/sessions/{sid}/upload',files={'files':('other.csv',b'x\n1\n','text/csv')})
    assert (await client.post(f'/sessions/{sid}/messages/{mid}/continue',json={})).status_code==409


@pytest.mark.parametrize('stopped',[False,True])
async def test_ordered_workflow_continues_without_repeating_completed_board_write(storage_app,monkeypatch,stopped):
    from app import main,session,boards
    from app.agent import workflow
    from app.execution import TaskRegistry
    from test_task_api import wait
    client,_,_=storage_app;sid='resume-ordered'
    await client.post(f'/sessions/{sid}/upload',files={'files':('sales.csv',b'amount\n33\n','text/csv')})
    plan=workflow.WorkflowPlan(requirements=['先保存笔记','再分析','最后保存'],steps=[
        {'id':'first','request':'保存笔记','tool':'board','covers':[0],'arguments':{'title':'已完成笔记','note':'只应写一次'}},
        {'id':'query','request':'分析','tool':'analyze','covers':[1],'arguments':{'question':'算金额'}},
        {'id':'last','request':'保存完成笔记','tool':'board','covers':[2],'depends_on':['query'],'arguments':{'title':'完成笔记','note':'后续步骤'}}])
    original=workflow.execute_tool;calls=[];failed=True
    def execute(step,state,completed,run_id):
        calls.append(step.id)
        if step.tool=='analyze':return {'error':'受控失败'} if failed else {'answer':'金额33'}
        return original(step,state,completed,run_id)
    monkeypatch.setattr(workflow,'execute_tool',execute)
    saved=workflow._run_plan({'session_id':sid,'question':'原任务'},plan)
    assert calls==['first','query'] and len(boards.list_boards())==1
    if stopped:saved['workflow']['status']='stopped';saved['workflow']['steps'][1]['status']='cancelled'
    store=session.get_session_store()._backend;store.save_message(sid,'assistant',saved['answer'],saved);mid=store.get_messages(sid)[-1]['id']
    failed=False;monkeypatch.setattr(main,'tasks',TaskRegistry())
    task=await client.post(f'/sessions/{sid}/messages/{mid}/continue',json={});assert task.status_code==200,task.text
    result=await wait(client,task.json());assert result['status']=='succeeded',result
    assert result['result']['workflow']['status']=='completed'
    assert calls==['first','query','query','last'] and len(boards.list_boards())==2
    saved['workflow']['steps'][0]['status']='failed'
    store.save_message(sid,'assistant','写入状态不明确',saved);unsafe=store.get_messages(sid)[-1]['id']
    assert (await client.post(f'/sessions/{sid}/messages/{unsafe}/continue',json={})).status_code==409
    assert len(boards.list_boards())==2
