"""Same-plan comparisons over actual versioned data; no model calls."""
import hashlib
from decimal import Decimal

import pytest

from app import session
from app.schemas import AnalysisBrief
from app.answer_acceptance import prepare_brief
from app.tools.dataset_manager import get_dataset_manager
from app.tools.data_workbench import CleaningRequest,preview_cleaning,apply_cleaning,undo_cleaning,workbench_snapshot,describe_workbench
from test_chat_storage import storage_app  # noqa: F401


async def seed(client,content=b'amount,status\n10, completed \n20,completed\n900,cancelled\n'):
    sid='impact'
    assert (await client.post(f'/sessions/{sid}/upload',files={'files':('orders.csv',content,'text/csv')})).status_code==200
    data=get_dataset_manager().get(sid)
    question='仅统计status=completed的商品金额，未知不补零'
    brief=AnalysisBrief(goal=question,parts=[{'requirement':'商品金额','criterion':{'answer_type':'value','result_columns':['amount'],'request_quote':question}}],
        calculation_plan={'source_quotes':[question],'stages':[{'name':'total','source':'orders',
            'select':[{'name':'amount','op':'sum','field':'orders.amount'}],
            'filters':[{'field':'orders.status','op':'eq','value':'completed'}]}]}).model_dump()
    brief=prepare_brief({'question':question},brief,dataset=data)
    store=session.get_session_store()._backend
    store.save_messages(sid,[('user',question,{}),('assistant','原分析任务',{'analysis_brief':brief,'data_version':workbench_snapshot(data)})])
    return data,store.get_messages(sid)[-1]['id']


def preview(data,rules):
    return preview_cleaning(data,CleaningRequest(table='orders',snapshot=workbench_snapshot(data),rules=rules))


async def test_preview_uses_identical_scope_and_keeps_active_data_unchanged(storage_app):
    client,_,_=storage_app
    data,mid=await seed(client)
    before=workbench_snapshot(data);original=hashlib.sha256(data.files[0].parquet_path.read_bytes()).hexdigest()
    report=preview(data,[{'operation':'trim','column':'status'}])['analysis_impact']
    assert report['status']=='verified' and report['phase']=='preview' and report['source_message_id']==mid
    assert report['before']['input_coverage']['input_rows']==1
    assert report['after']['input_coverage']['input_rows']==2
    assert report['changes'][0]['before']==20 and report['changes'][0]['after']==30
    assert Decimal(report['changes'][0]['delta'])==10
    assert report['plan_sha256'] and workbench_snapshot(data)==before
    assert hashlib.sha256(data.files[0].parquet_path.read_bytes()).hexdigest()==original
    assert data.execute("SELECT SUM(amount) FROM orders WHERE status='completed'").fetchone()[0]==20


async def test_missing_values_remain_unknown_and_show_coverage_even_when_total_unchanged(storage_app):
    client,_,_=storage_app
    data,_=await seed(client,b'amount,status\n,completed\n20,completed\n')
    report=preview(data,[{'operation':'fill','column':'amount','value':0,'confirmed':True}])['analysis_impact']
    assert report['status']=='verified' and report['changes']==[]
    assert report['before']['input_coverage']['missing_rows']['orders.amount']==1
    assert report['after']['input_coverage']['missing_rows']['orders.amount']==0
    assert '业务' in report['scope']


async def test_uncomputable_original_is_partial_not_a_fabricated_zero(storage_app):
    client,_,_=storage_app
    data,_=await seed(client,b'amount,status\nUSD 10,completed\nUSD 20,completed\n')
    report=preview(data,[{'operation':'parse_number','column':'amount','currency_token':'USD','failure_policy':'reject'}])['analysis_impact']
    assert report['status']=='partial' and report['before']['status']=='unverified'
    assert report['after']['rows']==[{'amount':30}] and report['changes']==[]


async def test_applied_impact_is_rechecked_from_saved_version_and_undo_restores_original(storage_app):
    client,_,_=storage_app
    data,_=await seed(client)
    initial=workbench_snapshot(data);path=data.files[0].parquet_path;original=path.read_bytes()
    report=preview(data,[{'operation':'trim','column':'status'}])
    apply_cleaning(data,report['preview_id'],get_dataset_manager())
    impact=describe_workbench(data)['tables'][0]['analysis_impact']
    assert impact['phase']=='applied' and impact['status']=='verified'
    assert impact['after_snapshot']==workbench_snapshot(data) and impact['preview_agrees'] is True
    assert impact['changes'][0]['after']==30 and data.files[0].parquet_path!=path
    undo_cleaning(data,'orders',workbench_snapshot(data),get_dataset_manager())
    assert workbench_snapshot(data)==initial and path.read_bytes()==original


async def test_no_confirmed_plan_does_not_block_cleaning_or_claim_verified(storage_app):
    client,_,_=storage_app
    await client.post('/sessions/impact/upload',files={'files':('orders.csv',b'amount,status\n10, completed \n','text/csv')})
    data=get_dataset_manager().get('impact')
    report=preview(data,[{'operation':'trim','column':'status'}])
    assert report['analysis_impact']['status']=='unverified'
    assert '计划' in report['analysis_impact']['reason']
    apply_cleaning(data,report['preview_id'],get_dataset_manager())
    assert data.execute('SELECT status FROM orders').fetchone()[0]=='completed'


async def test_unexpected_saved_result_never_switches_the_active_version(storage_app,monkeypatch):
    from app import cleaning_impact
    from app.tools.data_workbench import WorkbenchConflict
    client,_,_=storage_app
    data,_=await seed(client)
    initial=workbench_snapshot(data)
    report=preview(data,[{'operation':'trim','column':'status'}])
    evaluate=cleaning_impact._evaluate
    def changed(*args,**kwargs):
        result=evaluate(*args,**kwargs)
        if kwargs.get('replacement'):result['rows'][0]['amount']+=1
        return result
    monkeypatch.setattr(cleaning_impact,'_evaluate',changed)
    with pytest.raises(WorkbenchConflict,match='预览不一致'):
        apply_cleaning(data,report['preview_id'],get_dataset_manager())
    assert workbench_snapshot(data)==initial


async def test_latest_task_without_a_plan_does_not_resurrect_an_older_analysis(storage_app):
    client,_,_=storage_app
    data,_=await seed(client)
    store=session.get_session_store()._backend
    store.save_message('impact','user','改看成本',{})
    store.save_message('impact','assistant','缺成本',{'analysis_brief':{'acceptance':[{'result_columns':['cost']} ]}})
    result=preview(data,[{'operation':'trim','column':'status'}])['analysis_impact']
    assert result['status']=='unverified'


async def test_plan_api_retains_applied_comparison_across_reload_and_undo(storage_app,monkeypatch):
    from types import SimpleNamespace
    from app import cleaning_assistant as cleaning
    client,_,_=storage_app
    data,mid=await seed(client)
    advice=cleaning.CleaningAdvice(summary='清理状态空白',table='orders',rules=[{'operation':'trim','column':'status'}])
    monkeypatch.setattr(cleaning,'get_structured_llm',lambda *a,**kw:SimpleNamespace(invoke=lambda messages:advice))
    plan=cleaning.cleaning_node({'question':'清理状态空白','session_id':'impact'})['cleaning']['plan_id']
    pending=(await client.get(f'/sessions/impact/cleaning/plans/{plan}')).json()
    assert pending['preview']['analysis_impact']['phase']=='preview'
    applied=await client.post(f'/sessions/impact/cleaning/plans/{plan}/apply',json={})
    assert applied.status_code==200,applied.text
    impact=applied.json()['receipt']['analysis_impact']
    assert impact['source_message_id']==mid and impact['phase']=='applied' and impact['changes'][0]['after']==30
    assert (await client.get(f'/sessions/impact/cleaning/plans/{plan}')).json()['receipt']['analysis_impact']==impact
    undone=(await client.post(f'/sessions/impact/cleaning/plans/{plan}/undo',json={})).json()
    assert undone['status']=='undone' and undone['receipt']['analysis_impact']==impact


def test_group_changes_keep_missing_groups_and_unknown_values_distinct():
    from app.cleaning_impact import _changes
    reference={'brief':{'calculation_plan':{'stages':[{'name':'totals','source':'orders','select':[
        {'name':'status','op':'column','field':'orders.status'},
        {'name':'amount','op':'sum','field':'orders.amount'}]}]}}}
    before={'columns':['status','amount'],'numeric_columns':['amount'],'rows':[
        {'status':'old','amount':10},{'status':'unknown','amount':None}]}
    after={'columns':['status','amount'],'numeric_columns':['amount'],'rows':[
        {'status':'new','amount':10},{'status':'unknown','amount':5}]}
    changes=_changes(reference,before,after)
    assert [(c['dimensions']['status'],c['row_status']) for c in changes]==[
        ('old','removed'),('unknown','changed'),('new','added')]
    assert all(c['delta'] is None for c in changes)


async def test_large_result_is_unverified_instead_of_truncated(storage_app):
    from app.cleaning_impact import _evaluate
    client,_,_=storage_app
    data,_=await seed(client,('amount,status\n'+''.join(f'{i},group{i}\n' for i in range(51))).encode())
    reference={'question':'按状态统计金额','confirmed':['按状态统计金额'],'brief':{'calculation_plan':{
        'source_quotes':['按状态统计金额'],'stages':[{'name':'totals','source':'orders',
            'select':[{'name':'status','op':'column','field':'orders.status'},{'name':'amount','op':'sum','field':'orders.amount'}],
            'group_by':['orders.status']}]}}}
    result=_evaluate(data,reference,'orders')
    assert result['status']=='unverified' and result['rows']==[] and '50行' in result['reason']
