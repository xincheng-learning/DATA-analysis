"""Trusted fixture programs exercise the receipt protocol; model code remains Docker-only."""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.tools import python_tool
from test_local_api import local_app  # noqa: F401
from test_product_workflow import product_app  # noqa: F401


def test_exact_currency_can_use_decimal_without_allowing_io_modules():
    assert python_tool.validate_imports('from decimal import Decimal\nresult = Decimal("100.01")') is None
    assert python_tool.validate_imports('import subprocess')


def test_dataframe_receipt_preserves_zero_unknown_and_text_identifier(monkeypatch,capsys):
    monkeypatch.setattr(pd,'read_parquet',lambda path:pd.DataFrame({'order_id':['0001','0002'],'amount':[0.,float('nan')]}))
    exec(python_tool._build_harness("result = dfs['sales']",['/sales.parquet'],table_names=['sales']),{})
    receipt=python_tool._parse_output('fixture',capsys.readouterr().out)
    assert receipt.ok
    assert receipt.columns==['order_id','amount']
    assert receipt.rows==[{'order_id':'0001','amount':0.},{'order_id':'0002','amount':None}]
    assert '0001' in receipt.output


def test_oversized_python_table_is_rejected_without_partial_result(monkeypatch,capsys):
    monkeypatch.setattr(pd,'read_parquet',lambda path:pd.DataFrame({'n':range(5001)}))
    exec(python_tool._build_harness("result = df",['/sales.parquet'],table_names=['sales']),{})
    receipt=python_tool._parse_output('fixture',capsys.readouterr().out)
    assert not receipt.ok and '5000' in receipt.error
    assert not receipt.rows


async def test_invalid_workflow_plan_explains_failure_before_any_tool(product_app,monkeypatch):
    from app.agent import workflow
    calls=[]
    class Invalid:
        def invoke(self,messages,**kwargs):
            calls.append(messages)
            return workflow.WorkflowPlan(requirements=['Python计算','绘图'],steps=[
                dict(id='calc',request='计算',covers=[0],tool='analyze',arguments={'question':'计算','mode':'python'})])
    monkeypatch.setattr(workflow,'get_structured_llm',lambda schema:Invalid())
    monkeypatch.setattr(workflow,'execute_tool',lambda *args:pytest.fail('No product tool may run for an invalid plan'))
    result=workflow.workflow_node({'session_id':'fixture','question':'Python计算并绘图'})
    assert result['error'] and '未执行' in result['answer']
    assert len(calls)==1 and result['checks'][0]['status']=='failed'


def test_python_node_publishes_computed_table_and_verifiable_origin(monkeypatch):
    from app.agent import nodes
    from app.tools import dataset_manager
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='sales',parquet_path=Path('/sales.parquet'))])
    monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:dataset))
    monkeypatch.setattr(python_tool,'execute_python',lambda *args,**kwargs:SimpleNamespace(ok=True,output='actual tool text',error=None,
        columns=['month','amount'],rows=[{'month':'2018-07','amount':0.}]))
    result=nodes.execute_python_node({'session_id':'fixture','python_code':'result = dfs["sales"]'})
    assert result['columns']==['month','amount'] and result['rows']==[{'month':'2018-07','amount':0.}]
    assert result['row_count']==1 and result['checks'][0]['id']=='python_result'
    assert result['checks'][0]['status']=='passed'


@pytest.mark.parametrize('size',[50,51,61,5000])
def test_uploaded_python_result_over_fifty_rows_is_saved_and_pageable(monkeypatch,tmp_path,size):
    from app.agent import nodes
    from app.tools import dataset_manager,query_results
    rows=[{'order_id':f'{index:04d}','amount':None if index==size-1 else index} for index in range(size)]
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='sales',parquet_path=tmp_path/'sales.parquet')])
    monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:dataset))
    monkeypatch.setattr(query_results,'local_session_dir',lambda sid:tmp_path/sid)
    monkeypatch.setattr(python_tool,'execute_python',lambda *args,**kwargs:SimpleNamespace(ok=True,output='tool receipt',
        columns=['order_id','amount'],rows=rows,error=None))
    result=nodes.execute_python_node({'session_id':'python-pages','python_code':'result = dfs["sales"]'})
    assert result['row_count']==size and len(result['rows'])==min(size,50) and not result.get('result_error')
    if size==50:
        assert result['rows']==rows
        return
    page=query_results.read_result('python-pages',result['result_id'],50)
    last=query_results.read_result('python-pages',result['result_id'],((size-1)//50)*50)
    assert page['row_count']==size and last['rows'][-1]==rows[-1] and page['rows'][0]['order_id']=='0050'
    exported=''.join(query_results.result_csv('python-pages',result['result_id']))
    assert f'{size-1:04d},' in exported and len(exported.splitlines())==size+1


def test_python_graph_draws_only_the_actual_computed_rows(monkeypatch):
    from app.agent import graph,nodes
    from app.tools import viz
    monkeypatch.setattr(graph,'router_node',lambda state:{'route':'python','python_chart':True})
    monkeypatch.setattr(graph,'write_python_node',lambda state:{'python_code':'trusted fixture'})
    monkeypatch.setattr(graph,'execute_python_node',lambda state:{'python_output':'actual', 'columns':['month','amount'],
        'rows':[{'month':'2018-07','amount':0.}],'row_count':1,'error':''})
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:viz.ChartSpec(chart_type='line',x_column='month',y_column='amount',title='实际结果',x_label='月份',y_label='金额（BRL）'))
    result=graph.build_graph().invoke({'question':'Python并画图','session_id':'fixture'})
    assert result['chart_spec']['data'][0]['y']==[0.]
    assert result['checks'][-1]['id']=='chart_source' and '实际结果' in str(result['chart_spec'])


def test_python_save_failure_keeps_the_preview_but_not_a_stale_complete_result(monkeypatch,tmp_path):
    from app.agent import nodes
    from app import analysis_pipeline
    from app.tools import dataset_manager,query_results
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='sales',parquet_path=tmp_path/'sales.parquet')])
    monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:dataset))
    rows=[{'n':index} for index in range(51)]
    monkeypatch.setattr(python_tool,'execute_python',lambda *args,**kwargs:SimpleNamespace(ok=True,output='receipt',columns=['n'],rows=rows,error=None))
    def fail_save(*args):raise OSError('controlled full-result storage failure')
    monkeypatch.setattr(query_results,'save_result',fail_save)
    published=nodes.execute_python_node({'session_id':'failure','python_code':'result=computed','result_id':'old-id'})
    assert len(published['rows'])==50 and published['result_id'] is None and published['result_error']
    delivered=analysis_pipeline.run_delivery({**published,'question':'完整表','route':'python','python_code':'result=computed',
        'analysis_brief':{'goal':'完整表','requirements':['完整表'],'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['n']}]}})
    assert delivered['analysis_process']['coverage'][0]['status']=='linked'


def test_python_chart_without_a_table_explains_the_missing_output():
    from app.agent.nodes import make_chart_node
    result=make_chart_node({'route':'python','python_output':'only prose','question':'画图'})
    assert result['chart_error'] and not result['chart_spec']


def test_python_receipt_rejects_string_instead_of_column_list():
    result=python_tool._parse_output('fixture',python_tool.RESULT_MARKER+'{"columns":"x","data":[[1]]}')
    assert not result.ok


def test_structured_python_answer_does_not_repeat_a_wide_printout():
    from app.agent.nodes import compose_answer_node
    notes='Fisher双侧检验：样本100；这是观察性关联，不能解释为因果。'
    answer=compose_answer_node({'route':'python','python_output':'WIDE_RAW_TABLE','columns':['group','rate','说明'],
        'rows':[{'group':'A','rate':0.,'说明':notes},{'group':'B','rate':None,'说明':notes}],'row_count':2})['answer']
    assert 'WIDE_RAW_TABLE' not in answer and '2' in answer
    assert notes not in answer and '工具原文' in answer


def test_computed_summary_survives_as_tool_text_without_becoming_a_verified_answer(monkeypatch,capsys):
    from app.agent.nodes import compose_answer_node
    monkeypatch.setattr(pd,'read_parquet',lambda path:pd.DataFrame({'amount':[10,20]}))
    exec(python_tool._build_harness("result = df\nresult.attrs['summary'] = f'实际金额合计{result.amount.sum()}，保留2行结果。'",['/sales.parquet'],table_names=['sales']),{})
    receipt=python_tool._parse_output('fixture',capsys.readouterr().out)
    assert receipt.ok and receipt.summary=='实际金额合计30，保留2行结果。'
    answer=compose_answer_node({'route':'python','python_output':receipt.output,'python_summary':receipt.summary,
        'columns':receipt.columns,'rows':receipt.rows,'row_count':2})['answer']
    assert receipt.summary not in answer and '工具原文' in answer
    assert receipt.summary=='实际金额合计30，保留2行结果。'


async def test_context_failure_returns_a_specific_recoverable_error(local_app,monkeypatch):
    client,_,_=local_app
    from app import main
    from app.context_memory import ContextMemoryError
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    def fail(*args,**kwargs):raise ContextMemoryError('历史整理尚未完成，原聊天与已有摘要保留。')
    monkeypatch.setattr(main,'_analyse',fail)
    response=await client.post('/ask',json={'question':'继续'})
    assert response.status_code==422 and '原聊天' in response.json()['detail']
