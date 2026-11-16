"""Regression cases from the six-turn chart review; no paid model calls."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas import AnalysisBrief
from app.answer_acceptance import prepare_brief, assess_coverage
from app import context_memory as memory, session
from app.config import settings
from test_chat_storage import storage_app  # noqa: F401


@pytest.mark.parametrize('name,columns',[
    ('chart-route-daily-2324.json',['sale_date','channel','daily_amount']),
    ('chart-route-heatmap-2335.json',['sale_date','channel','sales_amount'])])
def test_original_model_routes_no_longer_discard_valid_table_for_missing_chart_binding(name,columns):
    from app.agent.router import RouteDecision
    decision=RouteDecision.model_validate_json((Path(__file__).with_name('fixtures')/name).read_text(encoding='utf-8'))
    assert decision.route=='viz'
    chart=next(s for s in decision.analysis_brief.acceptance if s.answer_type=='chart')
    assert chart.result_columns==columns and chart.target_status=='explicit'
    constraint=next(s for s in decision.analysis_brief.acceptance if s.answer_type=='constraint')
    assert '10月7日直播' in constraint.request_quote


def daily_plan():
    return {'source_quotes':['按日渠道销售额'], 'stages':[
        {'name':'daily','source':'orders','select':[
            {'name':'sale_date','op':'column','field':'orders.sale_date'},
            {'name':'channel','op':'column','field':'orders.channel'},
            {'name':'amount','op':'sum','field':'orders.amount'}],
         'group_by':['orders.sale_date','orders.channel']}]}


def test_missing_chart_columns_reuse_only_the_unique_matching_table_target():
    raw={'goal':'按日渠道销售额并画图','calculation_plan':daily_plan(),'parts':[
        {'requirement':'按日渠道销售额','criterion':{'answer_type':'table','result_columns':['sale_date','channel','amount']}},
        {'requirement':'画图','criterion':{'answer_type':'chart'}}]}
    before=deepcopy(raw)
    brief=AnalysisBrief.model_validate(raw)
    assert brief.acceptance[1].result_columns==['sale_date','channel','amount']
    assert raw==before
    raw['parts'].insert(1,{'requirement':'另一张表','criterion':{'answer_type':'table','result_columns':['other']}})
    ambiguous=AnalysisBrief.model_validate(raw)
    assert ambiguous.acceptance[-1].result_columns==[]
    prepared=prepare_brief({'question':raw['goal']},ambiguous.model_dump())
    assert assess_coverage(prepared,{})[-1]['status']=='unanswered'


def test_chart_target_is_not_guessed_from_plan_alone_or_overwrites_explicit_target():
    raw={'goal':'画图','calculation_plan':daily_plan(),'parts':[
        {'requirement':'画图','criterion':{'answer_type':'chart'}}]}
    assert AnalysisBrief.model_validate(raw).acceptance[0].result_columns==[]
    raw['parts'][0]['criterion']['result_columns']=['different_amount']
    assert AnalysisBrief.model_validate(raw).acceptance[0].result_columns==['different_amount']


def multigrain_brief():
    plan=daily_plan()
    plan['stages'].append({'name':'totals','source':'daily','select':[
        {'name':'channel','op':'column','field':'daily.channel'},
        {'name':'amount','op':'sum','field':'daily.amount'}],
        'group_by':['daily.channel']})
    return prepare_brief({'question':'按日渠道销售额并说明渠道合计'},AnalysisBrief(goal='多粒度',calculation_plan=plan,parts=[
        {'requirement':'日渠道图','criterion':{'answer_type':'chart','result_columns':['sale_date','channel','amount']}},
        {'requirement':'渠道合计','criterion':{'answer_type':'value','result_columns':['channel','amount']}}]).model_dump())


def test_multigrain_plan_splits_requirements_with_their_own_plan_prefix():
    from app.agent.workflow import calculation_workflow
    brief=multigrain_brief();before=deepcopy(brief)
    plan=calculation_workflow(brief)
    assert [s.arguments['mode'] for s in plan.steps]==['viz','sql']
    assert [s.covers for s in plan.steps]==[[0],[1]]
    assert [len(s.arguments['calculation_plan']['stages']) for s in plan.steps]==[1,2]
    assert plan.requirement_ids==['R1','R2'] and brief==before
    brief['acceptance'][1]['result_columns']=['unavailable']
    assert calculation_workflow(brief) is None


def test_same_result_table_and_chart_stay_one_task():
    from app.agent.workflow import calculation_workflow
    brief=multigrain_brief();brief['calculation_plan']['stages']=brief['calculation_plan']['stages'][:1]
    brief['acceptance'][1]['result_columns']=['sale_date','channel','amount']
    assert calculation_workflow(brief) is None


def test_router_uses_split_without_spending_a_plan_repair_call(monkeypatch):
    from app.agent import router
    brief=multigrain_brief()
    decision=router.RouteDecision(route='viz',reason='作图',analysis_brief=brief)
    calls=[]
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:(calls.append(messages) or decision)))
    validated=[]
    monkeypatch.setattr('app.calculation_contract.request_output_columns',lambda state:validated.append(state))
    result=router.router_node({'question':'按日渠道销售额并说明渠道合计','schema':'orders(sale_date,channel,amount)'})
    assert len(validated)==1
    assert result['route']=='workflow' and len(calls)==1
    assert len(result['analysis_brief']['workflow_plan']['steps'])==2


async def test_failed_rolling_summary_continues_with_bounded_raw_user_scope(storage_app,monkeypatch):
    client,_,_=storage_app
    await client.post('/sessions/summary-recovery/upload',files={'files':('orders.csv',b'amount\n10\n','text/csv')})
    store=session.get_session_store()._backend
    store.save_message('summary-recovery','user','收入不含运费，取消单不计。',{})
    store.save_message('summary-recovery','assistant','冗长旧报告。'*4000,{})
    store.save_message('summary-recovery','user','改看十月，其他口径不变。',{})
    original=store.get_messages('summary-recovery')
    monkeypatch.setattr(settings,'context_memory_tokens',4000)
    calls=[]
    monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=lambda messages:(calls.append(messages) or SimpleNamespace(content=''))))
    history,status=memory.history_context('summary-recovery')
    assert status['degraded'] and status['used_tokens']<status['limit_tokens']
    assert '收入不含运费，取消单不计。' in history and '改看十月' in history
    assert '未核验' in history and '冗长旧报告。'*100 not in history
    assert memory._document('summary-recovery','rolling') is None
    before=len(calls)
    memory.history_context('summary-recovery')
    store.save_message('summary-recovery','user','接着画图',{})
    assert memory.history_context('summary-recovery')[1]['degraded'] and len(calls)==before
    assert store.get_messages('summary-recovery')[:len(original)]==original
    from app import main
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    def continue_analysis(question,**kwargs):
        assert '收入不含运费，取消单不计。' in kwargs['conversation_context']
        return {'route':'chat','answer':'继续处理本轮请求。'},'summary-recovery'
    monkeypatch.setattr(main,'run_agent',continue_analysis)
    response=await client.post('/ask',json={'session_id':'summary-recovery','question':'按刚才口径继续'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['context_status']['degraded'] and '会话记忆提示' in result['answer']
    assert len(calls)==before


async def test_truncated_summary_is_not_saved_even_if_nonempty(storage_app,monkeypatch):
    client,_,_=storage_app
    await client.post('/sessions/truncated-summary/upload',files={'files':('orders.csv',b'amount\n10\n','text/csv')})
    monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content='只写了一半',response_metadata={'finish_reason':'length'})))
    with pytest.raises(memory.ContextMemoryError):
        memory.summarize('truncated-summary',memory.records_for('truncated-summary'),'rolling')
    assert memory._document('truncated-summary','rolling') is None


@pytest.mark.parametrize('kind',['cancel','budget'])
async def test_history_recovery_does_not_swallow_stop_or_budget(storage_app,monkeypatch,kind):
    from app.execution import AnalysisCancelled,ModelBudgetExceeded
    client,_,_=storage_app
    await client.post('/sessions/stop-memory/upload',files={'files':('orders.csv',b'amount\n10\n','text/csv')})
    session.get_session_store()._backend.save_message('stop-memory','assistant','旧报告。'*5000,{})
    monkeypatch.setattr(settings,'context_memory_tokens',4000)
    error=AnalysisCancelled if kind=='cancel' else ModelBudgetExceeded
    def fail(messages):raise error('停止')
    monkeypatch.setattr(memory,'get_chat_llm',lambda:SimpleNamespace(invoke=fail))
    with pytest.raises(error):memory.history_context('stop-memory')
    assert memory._document('stop-memory','rolling:failure') is None


async def test_split_workflow_keeps_real_daily_chart_totals_filters_and_calculation_checks(storage_app,monkeypatch):
    from app import main,analysis_report
    from app.agent import router,nodes,graph,workflow
    from app.tools import viz,dataset_manager
    from app.calculation_contract import verify_calculation,CalculationMismatch
    client,_,_=storage_app
    sid='multigrain-http'
    csv=b'sale_date,channel,amount,status\n2026-10-08,A,10,completed\n2026-10-08,B,5,completed\n2026-10-09,A,20,completed\n2026-10-09,B,15,completed\n2026-10-09,A,1000,cancelled\n2026-10-01,A,2000,completed\n'
    assert (await client.post(f'/sessions/{sid}/upload',files={'files':('orders.csv',csv,'text/csv')})).status_code==200
    brief=multigrain_brief()
    question='按日渠道销售额，只计completed，日期从2026-10-08至2026-10-14，画日渠道图并给渠道合计。'
    brief['calculation_plan']['source_quotes']=[question]
    brief['calculation_plan']['stages'][0]['filters']=[
        {'field':'orders.status','op':'eq','value':'completed'},
        {'field':'orders.sale_date','op':'ge','value':'2026-10-08','cast':'date'},
        {'field':'orders.sale_date','op':'lt','value':'2026-10-15','cast':'date'}]
    decision=router.RouteDecision(route='viz',reason='同一问题包含两个结果粒度',analysis_brief=brief)
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(graph,'_graph',None)
    def no_replan(*args,**kwargs):pytest.fail('The declared stages must not incur another planner call')
    monkeypatch.setattr(workflow,'get_structured_llm',no_replan)
    def no_report(*args,**kwargs):raise ValueError('Controlled run: keep the evidence summary')
    monkeypatch.setattr(analysis_report,'get_structured_llm',no_report)
    sqls=iter([
        "SELECT sale_date,channel,SUM(amount) amount FROM orders WHERE status='completed' AND sale_date>='2026-10-08' AND sale_date<'2026-10-15' GROUP BY sale_date,channel",
        "SELECT channel,SUM(amount) amount FROM orders WHERE status='completed' AND sale_date>='2026-10-08' AND sale_date<'2026-10-15' GROUP BY channel"])
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=next(sqls))))
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:viz.ChartSpec(chart_type='bar',x_column='sale_date',y_column='amount',series_column='channel',bar_mode='stack',title='日渠道销售额',x_label='日期',y_label='销售额（元）'))
    response=await client.post('/ask',json={'session_id':sid,'question':question})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['workflow']['status']=='completed',result['workflow']['steps']
    steps=result['workflow']['steps'];daily,totals=[s['result'] for s in steps]
    assert sorted(daily['rows'],key=lambda r:(r['sale_date'],r['channel']))==[
        {'sale_date':'2026-10-08','channel':'A','amount':10},
        {'sale_date':'2026-10-08','channel':'B','amount':5},
        {'sale_date':'2026-10-09','channel':'A','amount':20},
        {'sale_date':'2026-10-09','channel':'B','amount':15}]
    assert sorted(totals['rows'],key=lambda r:r['channel'])==[{'channel':'A','amount':30},{'channel':'B','amount':20}]
    assert {t['name']:sum(t['y']) for t in daily['chart_spec']['data']}=={'A':30,'B':20}
    for index,part in enumerate([daily,totals],1):
        check=next(c for c in part['checks'] if c['id']=='compiled_execution')
        assert check['status']=='passed' and check['requirement_ids']==[f'R{index}']
        assert not any(c['id']=='calculation_contract' and c['status']=='passed' for c in part['checks'])
        exported=await client.get(f"/sessions/{sid}/results/{part['result_id']}/export")
        assert exported.status_code==200
    wrong=deepcopy(daily['rows']);wrong[0]['amount']+=1
    with pytest.raises(CalculationMismatch):
        verify_calculation(dataset_manager.get_dataset_manager().get(sid),daily['analysis_brief'],question,daily['columns'],wrong,len(wrong))
    saved=(await client.get(f'/sessions/{sid}')).json()['messages'][-1]['metadata']
    assert len(saved['workflow']['steps'])==2
