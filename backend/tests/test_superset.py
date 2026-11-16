from decimal import Decimal

import pytest
from test_local_api import local_app  # noqa: F401


def test_daily_service_start_only_starts_existing_resources(monkeypatch):
    import runpy
    from types import SimpleNamespace
    from app import superset
    start=runpy.run_path(str(superset.ROOT/'scripts/superset_local.py'))['start']
    names=['datapilot-superset-'+service+'-1' for service in ('database','web','mcp')]
    calls=[]
    health=['starting','healthy']
    def run(args,**kwargs):
        calls.append(args)
        return '\n'.join(names) if 'ps' in args else health.pop(0) if 'inspect' in args and health else 'healthy' if 'inspect' in args else ''
    monkeypatch.setitem(start.__globals__,'run',run)
    monkeypatch.setitem(start.__globals__,'record',lambda *args:None)
    mcp=['starting','healthy']
    monkeypatch.setattr(superset,'mcp_call',lambda *args,**kwargs:{'status':mcp.pop(0)})
    monkeypatch.setattr(start.__globals__['time'],'sleep',lambda seconds:None)
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args):return SimpleNamespace(status_code=200)
    monkeypatch.setattr(start.__globals__['httpx'],'Client',Client)
    start()
    assert [call for call in calls if 'start' in call]==[['docker','start',name] for name in names]
    assert calls[2][1]=='inspect' and calls[3][1]=='inspect' and calls[4]==['docker','start',names[1]]
    assert not mcp
    calls.clear()
    monkeypatch.setitem(start.__globals__,'run',lambda args,**kwargs:calls.append(args) or ('\n'.join(names) if 'ps' in args else 'starting'))
    with pytest.raises(RuntimeError,match='数据库尚未就绪'):
        start()
    assert [call for call in calls if 'start' in call]==[['docker','start',names[0]]]
    names.pop()
    with pytest.raises(RuntimeError,match='不会自动构建或迁移'):
        start()


def test_snapshot_grain_and_numeric_values_are_preserved_without_imputation():
    from app.superset import prepare_rows,verify_rows
    rows=[{'渠道':'搜索','品类':'图书','金额':Decimal('30.00')},{'渠道':'搜索','品类':'服装','金额':None}]
    prepared=prepare_rows(rows,['渠道','品类'],'金额','pivot_table')
    assert prepared==[{'dim_0':'搜索','dim_1':'图书','metric_value':Decimal('30.00')},
                      {'dim_0':'搜索','dim_1':'服装','metric_value':None}]
    assert verify_rows(prepared,list(reversed(prepared)))['status']=='passed'
    with pytest.raises(ValueError,match='不一致'):
        verify_rows(prepared,[{**prepared[0],'metric_value':31},prepared[1]])
    with pytest.raises(ValueError,match='不一致'):
        verify_rows(prepared,[prepared[0]])


def test_hierarchy_and_flow_refuse_nonadditive_rates_and_averages():
    from app.superset import prepare_rows
    for measure in ['aov', 'retention_rate', 'avg_price', '客单价', '转化率']:
        rows=[{'region':'A',measure:0.2}]
        with pytest.raises(ValueError,match='平均值或比率'):
            prepare_rows(rows,['region'],measure,'treemap')
        assert prepare_rows(rows,['region'],measure,'pivot_table')[0]['metric_value']==Decimal('0.2')


async def test_presentation_reads_every_verified_row_and_fails_closed_on_tampering(local_app,monkeypatch):
    client,_,_=local_app
    from app import superset
    rows=[{'dim_0':str(i),'metric_value':Decimal('12.30')} for i in range(201)]
    view={'id':'a'*32,'title':'完整结果','dimensions':['地区'],'measure':'金额','status':'ready',
          'chart_type':'treemap','chart_id':1,'row_count':201,'checksum':superset.fingerprint(rows),'checks':[]}
    monkeypatch.setattr(superset,'get_view',lambda _:view)
    monkeypatch.setattr(superset,'check_chart_configuration',lambda view:None)
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':rows})
    assert len((await client.post(f"/superset/views/{view['id']}/read",json={})).json()['rows'])==50
    full=await client.get(f"/superset/views/{view['id']}/presentation")
    assert full.status_code==200 and len(full.json()['rows'])==201
    assert full.json()['rows'][-1]=={'地区':'200','金额':'12.30'}
    await client.post('/sessions/advanced-readback/upload',files={'files':('x.csv',b'n\n1\n','text/csv')})
    readback=await client.post(f"/superset/views/{view['id']}/read?session_id=advanced-readback",json={})
    assert readback.status_code==200,readback.text
    saved=readback.json()
    assert len(saved['rows'])==50 and saved['row_count']==201
    final=await client.get(f"/sessions/advanced-readback/results/{saved['result_id']}?offset=200")
    assert final.json()['rows']==[{'地区':'200','金额':'12.30'}]
    csv=await client.get(f"/sessions/advanced-readback/results/{saved['result_id']}/export")
    assert csv.status_code==200 and '200,12.30' in csv.text and len(csv.text.splitlines())==202
    rows[-1]['metric_value']=Decimal('13.30')
    broken=await client.get(f"/superset/views/{view['id']}/presentation")
    assert broken.status_code==400 and 'rows' not in broken.json()


def test_advanced_shapes_reject_duplicates_unknown_negative_and_cyclic_weights():
    from app.superset import prepare_rows
    duplicate=[{'a':'X','b':'Y','v':1},{'a':'X','b':'Y','v':2}]
    with pytest.raises(ValueError,match='粒度'):
        prepare_rows(duplicate,['a','b'],'v','pivot_table')
    for value in [None,-1,True,float('inf')]:
        with pytest.raises(ValueError):
            prepare_rows([{'a':'X','b':'Y','v':value}],['a','b'],'v','sankey')
    with pytest.raises(ValueError,match='环'):
        prepare_rows([{'a':'X','b':'Y','v':1},{'a':'Y','b':'X','v':2}],['a','b'],'v','sankey')


def test_chart_data_check_compares_duplicates_and_nulls_instead_of_only_totals():
    from app.superset import verify_rows
    rows=[{'dim_0':'A','metric_value':10},{'dim_0':'B','metric_value':20}]
    with pytest.raises(ValueError,match='不一致'):
        verify_rows(rows,[{'dim_0':'A','metric_value':20},{'dim_0':'B','metric_value':10}])
    with pytest.raises(ValueError,match='不一致'):
        verify_rows(rows,[rows[0],rows[0]])


def test_superset_decimal_serialization_and_invalid_values():
    from app.superset import verify_rows
    assert verify_rows([{'dim_0':'A','metric_value':Decimal('12.30')}],
                       [{'dim_0':'A','metric_value':'12.30'}])['status']=='passed'
    for value in ['NaN','Infinity','invalid',True]:
        with pytest.raises(ValueError):
            verify_rows([{'dim_0':'A','metric_value':1}],[{'dim_0':'A','metric_value':value}])


def test_saved_query_has_every_dimension_and_no_pivot_postprocessing():
    from app.superset import chart_configuration
    for kind in ['pivot_table','treemap','sunburst','sankey']:
        form,query=chart_configuration(42,kind,2)
        assert query['queries'][0]['columns']==['dim_0','dim_1']
        assert query['queries'][0]['row_limit']==5001
        assert query['queries'][0]['metrics'][0]['aggregate']=='MAX'
        assert query['queries'][0]['post_processing']==[]
        assert query['form_data']==form


def test_dense_hierarchy_labels_are_readable_without_filtering_source_rows():
    from app.superset import chart_configuration
    form,query=chart_configuration(42,'sunburst',2)
    assert form['show_labels_threshold']==3
    assert form['label_type']=='key' and form['color_by_category'] is True
    assert form['number_format']=='.12~g'
    assert query['queries'][0]['row_limit']==5001 and not query['queries'][0]['filters']
    assert chart_configuration(42,'pivot_table',2)[0]['valueFormat']=='.12~g'


def test_single_chart_dashboard_uses_its_full_width_and_readable_height():
    from app.superset import fit_dashboard
    position={'chart':{'type':'CHART','meta':{'chartId':7,'width':4,'height':50}},
        'column':{'type':'COLUMN','meta':{'width':12}},'DASHBOARD_VERSION_KEY':'v2'}
    fitted=fit_dashboard(position)
    assert fitted['chart']['meta']=={'chartId':7,'width':12,'height':90}
    assert fitted['column']['meta']=={'width':12} and fitted['DASHBOARD_VERSION_KEY']=='v2'


def test_native_money_format_keeps_two_decimals_without_rounding_small_values_to_zero():
    from app.superset import native_number_format
    assert native_number_format('amount_cny',[{'metric_value':10.25},{'metric_value':0}])==',.2f'
    assert native_number_format('amount_cny',[{'metric_value':0.001}])=='.12~g'
    assert native_number_format('p_value',[{'metric_value':8e-20}])=='.12~g'


def test_readback_rejects_changed_form_even_when_mcp_returns_old_rows(monkeypatch):
    import json
    from copy import deepcopy
    from app import superset
    view={'dataset_id':42,'chart_id':7,'chart_type':'treemap','dimensions':['region']}
    form,query=superset.chart_configuration(42,'treemap',1)
    chart={'params':json.dumps(form),'query_context':json.dumps(query)}
    class API:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def call(self,*args):return {'result':chart}
    monkeypatch.setattr(superset,'SupersetAPI',API)
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':[{'dim_0':'A','metric_value':10}]})
    assert superset.chart_rows(view)[0]['metric_value']==10
    changed=deepcopy(form);changed['number_format']='.15~g'
    chart['params']=json.dumps(changed)
    assert superset.chart_rows(view)[0]['metric_value']==10
    changed['adhoc_filters']=[{'subject':'dim_0','operator':'==','comparator':'A','clause':'WHERE','expressionType':'SIMPLE'}]
    chart['params']=json.dumps(changed)
    with pytest.raises(ValueError,match='配置'):
        superset.chart_rows(view)
    chart['params']=json.dumps(form)
    query['queries'][0]['row_limit']=1
    chart['query_context']=json.dumps(query)
    with pytest.raises(ValueError,match='配置'):
        superset.chart_rows(view)


def test_pivot_keeps_unknown_groups_with_explicit_result_count_without_filling_amount():
    from app.superset import chart_configuration
    form,query=chart_configuration(42,'pivot_table',2,retain_unknown=True)
    assert [metric['aggregate'] for metric in form['metrics']]==['MAX','COUNT']
    assert form['metrics'][1]['label']=='结果记录数'
    assert query['queries'][0]['metrics']==form['metrics']


def test_readback_checks_auxiliary_count_and_preserves_unknown(monkeypatch):
    from app import superset
    expected=[{'dim_0':'A','metric_value':None}]
    view={'id':'a'*32,'title':'未知金额','dimensions':['地区'],'measure':'金额','status':'ready',
          'chart_id':1,'row_count':1,'checksum':superset.fingerprint(expected),'checks':[],'retain_unknown':True}
    monkeypatch.setattr(superset,'get_view',lambda _:view)
    monkeypatch.setattr(superset,'check_chart_configuration',lambda view:None)
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':[{**expected[0],'结果记录数':1}]})
    assert superset.read_view(view['id'])['rows']==[{'地区':'A','金额':None}]
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':[{**expected[0],'结果记录数':2}]})
    with pytest.raises(ValueError,match='记录数'):
        superset.read_view(view['id'])


async def test_advanced_view_api_is_local_only_and_tokens_are_never_in_status(local_app,monkeypatch):
    client,_,_=local_app
    from app import boards,main,superset
    monkeypatch.setattr(boards,'get_session_store',main.get_session_store)
    from types import SimpleNamespace
    class OfflineClient:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,*args): return SimpleNamespace(status_code=503)
    monkeypatch.setattr(superset.httpx,'Client',OfflineClient)
    state=(await client.get('/superset/status')).json()
    assert set(state)=={'status','message','updated_at'}
    assert (await client.get('/superset/views')).json()==[]
    assert (await client.get('/superset/views/missing/token')).status_code==405
    assert (await client.post('/superset/views/missing/token',json={})).status_code==400
    assert (await client.post('/superset/login',headers={'origin':'https://untrusted.invalid'},json={})).status_code==403
    assert (await client.post('/superset/service/delete',json={})).status_code==422
    monkeypatch.setattr(main.settings,'app_mode','cloud')
    from app.superset_routes import local_only
    from starlette.requests import Request
    from starlette.responses import Response
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        local_only(Request({'type':'http','method':'GET','headers':[]}),Response())
    assert error.value.status_code==403


def test_readback_rejects_changed_defaults_even_when_totals_match(monkeypatch):
    from app import superset
    rows=[{'dim_0':'A','metric_value':10},{'dim_0':'B','metric_value':20}]
    view={'id':'a'*32,'title':'已保存','dimensions':['地区'],'measure':'金额','status':'ready',
          'chart_id':1,'row_count':2,'checksum':superset.fingerprint(rows),'checks':[]}
    monkeypatch.setattr(superset,'get_view',lambda _:view)
    monkeypatch.setattr(superset,'check_chart_configuration',lambda view:None)
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':rows})
    assert superset.read_view(view['id'])['rows'][0]=={'地区':'A','金额':'10'}
    changed=[{**rows[0],'metric_value':20},{**rows[1],'metric_value':10}]
    monkeypatch.setattr(superset,'mcp_call',lambda *args:{'data':changed})
    with pytest.raises(ValueError,match='不一致'):
        superset.read_view(view['id'])


async def test_advanced_tool_receipt_survives_workflow_contract_and_memory(local_app,monkeypatch):
    client,_,_=local_app
    await client.post('/sessions/advanced-flow/upload',files={'files':('sales.csv',b'region,amount\nA,10\n','text/csv')})
    from app import boards,main,superset,context_memory
    from app.agent import workflow
    monkeypatch.setattr(boards,'get_session_store',main.get_session_store)
    monkeypatch.setattr(superset,'create_view',lambda *args:{'id':'a'*32,'title':'地区树图','row_count':1,'checks':[{'status':'passed'}]})
    monkeypatch.setattr(superset,'read_view',lambda view_id,sid:{'answer':'回读 '+view_id})
    plan=workflow.WorkflowPlan(requirements=['创建树图'],steps=[{'id':'advanced','request':'创建树图','covers':[0],
        'tool':'superset_create','arguments':{'message_id':1,'title':'地区树图','chart_type':'treemap','dimensions':['region'],'measure':'amount'}},
        {'id':'read','request':'回读本轮新建视图','covers':[0],'tool':'superset_read','arguments':{'step_id':'advanced'}}])
    monkeypatch.setattr(workflow,'_result_source',lambda *args:{'result_id':'b'*32})
    final=workflow._run_plan({'session_id':'advanced-flow','question':'创建树图'},plan)
    step=final['workflow']['steps'][0]
    assert step['status']=='succeeded' and step['result']['advanced_view']['id']=='a'*32
    assert final['workflow']['steps'][1]['result']['answer']=='回读 '+'a'*32
    memory=context_memory.project_messages([{'id':1,'role':'assistant','content':final['answer'],'metadata':final}])
    assert memory[0]['evidence']['workflow']['steps'][0]['result']['advanced_view']['id']=='a'*32


async def test_optional_service_offline_explains_where_to_start_and_records_failure(local_app,monkeypatch):
    client,_,_=local_app
    await client.post('/sessions/offline-advanced/upload',files={'files':('sales.csv',b'region,amount\nA,10\n','text/csv')})
    from app import boards,main,superset
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.query_results import save_result
    import pandas as pd
    monkeypatch.setattr(boards,'get_session_store',main.get_session_store)
    def unavailable(*args):
        raise superset.psycopg.OperationalError('private connection details')
    monkeypatch.setattr(superset,'publish_rows',unavailable)
    source={'sql':'SELECT region,amount FROM sales','data_version':workbench_snapshot(main.get_dataset_manager().get('offline-advanced')),
            'result_id':save_result('offline-advanced',pd.DataFrame([{'region':'A','amount':10}]))}
    with pytest.raises(ValueError,match='高级可视化.*启动服务'):
        superset.create_view('offline-advanced',source,superset.ViewRequest(title='地区',chart_type='treemap',dimensions=['region'],measure='amount'))
    assert superset.list_views()[0]['status']=='failed'


async def test_verified_python_table_is_a_source_without_pretending_it_is_sql(local_app,monkeypatch):
    client,_,_=local_app
    await client.post('/sessions/python-advanced/upload',files={'files':('sales.csv',b'region,amount\nA,10\n','text/csv')})
    from app import boards,main,superset
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.query_results import save_result
    import pandas as pd
    monkeypatch.setattr(boards,'get_session_store',main.get_session_store)
    source={'python_code':'result = computed_table','checks':[{'id':'python_result','status':'passed'}],
        'data_version':workbench_snapshot(main.get_dataset_manager().get('python-advanced')),
        'result_id':save_result('python-advanced',pd.DataFrame([{'region':'A','amount':10}]))}
    seen=[]
    def publish(view_id,rows):
        seen.extend(rows)
        raise RuntimeError('Stop before external service; source was accepted.')
    monkeypatch.setattr(superset,'publish_rows',publish)
    request=superset.ViewRequest(title='Python地区',chart_type='treemap',dimensions=['region'],measure='amount')
    with pytest.raises(RuntimeError,match='source was accepted'):
        superset.create_view('python-advanced',source,request)
    assert seen==[{'dim_0':'A','metric_value':10}]
    view=superset.get_view(superset.list_views()[0]['id'])
    assert not view['source_sql'] and view['source_python']==source['python_code']
    with pytest.raises(ValueError,match='成功完整查询'):
        superset.create_view('python-advanced',{**source,'checks':[]},request)
