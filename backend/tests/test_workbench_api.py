import pytest

from test_local_api import local_app  # noqa: F401


async def test_structure_browser_reads_every_row_without_models_and_rejects_stale_pages(local_app):
    client,_,_=local_app
    raw=('id,amount\n'+'\n'.join(f'{i:04},{0 if i==0 else i}' for i in range(61))+'\n').encode()
    assert (await client.post('/sessions/structure/upload',files={'files':('rows.csv',raw,'text/csv')})).status_code==200
    snapshot=(await client.get('/sessions/structure/workbench')).json()['snapshot']
    path=f'/sessions/structure/workbench/tables/rows/rows?snapshot={snapshot}'
    first=await client.get(path+'&offset=0');last=await client.get(path+'&offset=50')
    assert first.status_code==last.status_code==200
    assert first.json()['rows'][0]=={'id':'0000','amount':0}
    assert last.json()['rows'][-1]=={'id':'0060','amount':60} and len(last.json()['rows'])==11
    assert (await client.get(path+'&offset=1')).status_code==422
    assert (await client.get(path.replace(snapshot,'stale')+'&offset=0')).status_code==409
    assert (await client.get(path.replace('/tables/rows/','/tables/absent/'))).status_code==422


async def test_structure_pages_and_preview_preserve_decimal_and_large_integer_values(local_app):
    from decimal import Decimal
    import pandas as pd
    from app import main
    from app.tools.ingest import store_dataframe
    client,_,_=local_app
    await client.post('/sessions/exact/upload',files={'files':('base.csv',b'x\n1\n','text/csv')})
    manager=main.get_dataset_manager();dataset=manager.get('exact')
    frame=pd.DataFrame({'position':range(61),'code':[f'{i:03}' for i in range(61)],
        'large_id':pd.array([2**53+i for i in range(60)]+[None],dtype='Int64'),
        'amount':[Decimal('9007199254740993.01')]+[Decimal('0.01')]*59+[None],
        'ordinary':[0]+[1]*60})
    file=store_dataframe('exact.csv',frame,dataset.parquet_dir)
    manager.add_file('exact',file)
    wb=(await client.get('/sessions/exact/workbench')).json()
    table=next(table for table in wb['tables'] if table['table_name']=='exact')
    assert table['dtypes']['amount'].startswith('DECIMAL')
    assert table['preview'][0]['amount']=='9007199254740993.01'
    path=f'/sessions/exact/workbench/tables/exact/rows?snapshot={wb["snapshot"]}'
    first=(await client.get(path+'&offset=0')).json();last=(await client.get(path+'&offset=50')).json()
    assert [row['position'] for row in first['rows']+last['rows']]==list(range(61))
    assert first['rows'][0]=={'position':0,'code':'000','large_id':'9007199254740992','amount':'9007199254740993.01','ordinary':0}
    assert last['rows'][-1]['amount'] is None and last['rows'][-1]['large_id'] is None
    assert last['rows'][0]['amount']=='0.01' and last['rows'][0]['large_id']==str(2**53+50)
    assert (await client.get(path+'&offset=50')).json()==last


async def test_preview_versioning_restart_and_undo(local_app):
    client, restart, directory = local_app
    sid = 'workbench-api'
    upload = await client.post(f'/sessions/{sid}/upload', files={
        'files': ('sales.csv', b'id,region,amount\n0007, East ,100\n0008,,\n', 'text/csv')})
    assert upload.status_code == 200
    before = (await client.get(f'/sessions/{sid}/workbench')).json()
    body = {'table': 'sales', 'snapshot': before['snapshot'], 'rules': [{'operation': 'trim', 'column': 'region'}]}
    response = await client.post(f'/sessions/{sid}/cleaning/preview', json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['affected_rows'] == 1
    applied = await client.post(f'/sessions/{sid}/cleaning/apply', json={'preview_id': preview['preview_id']})
    assert applied.status_code == 200, applied.text
    assert applied.json()['snapshot'] != before['snapshot']
    assert (await client.post(f'/sessions/{sid}/cleaning/apply', json={'preview_id': preview['preview_id']})).status_code == 409
    restart()
    recovered = await client.get(f'/sessions/{sid}/workbench')
    assert recovered.json()['tables'][0]['preview'][0]['region'] == 'East'
    assert recovered.json()['tables'][0]['preview'][0]['id'] == '0007'
    assert len(recovered.json()['tables']) == 1
    undone = await client.post(f'/sessions/{sid}/cleaning/undo', json={'table': 'sales', 'snapshot': recovered.json()['snapshot']})
    assert undone.status_code == 200 and undone.json()['snapshot'] == before['snapshot']
    assert (await client.post(f'/sessions/{sid}/cleaning/preview', json={**body, 'rules': [{'operation': 'sql'}]})).status_code == 422
    assert (await client.get('/sessions/missing/workbench')).status_code == 404


async def test_running_task_and_external_origin_cannot_switch_data(local_app, monkeypatch):
    from app import main
    client, _, _ = local_app
    sid = 'busy-workbench'
    await client.post(f'/sessions/{sid}/upload', files={'files': ('a.csv', b'id,x\na, 1 \n', 'text/csv')})
    state = (await client.get(f'/sessions/{sid}/workbench')).json()
    body = {'table': 'a', 'snapshot': state['snapshot'], 'rules': [{'operation': 'trim', 'column': 'x'}]}
    monkeypatch.setattr(main.tasks, 'active_for_session', lambda sid: True)
    assert (await client.post(f'/sessions/{sid}/cleaning/preview', json=body)).status_code == 409
    response = await client.post(f'/sessions/{sid}/cleaning/preview', json=body, headers={'origin': 'https://evil.example'})
    assert response.status_code == 403


def test_query_memory_only_supplies_current_version():
    from app.session import Session, QueryMemory, render_session_context
    session = Session('versioned', 0, 0, recent_queries=[
        QueryMemory('old figure', 'SELECT 1', ['x'], 1, [], data_version='old'),
        QueryMemory('current figure', 'SELECT 2', ['x'], 1, [], data_version='new')])
    assert 'old figure' not in render_session_context(session, data_version='new')
    assert 'current figure' in render_session_context(session, data_version='new')
    assert Session.from_dict(session.to_dict()).recent_queries[1].data_version == 'new'


async def test_relationship_checks_are_session_scoped_and_confirmation_invalidates(local_app):
    client, restart, _ = local_app
    sid = 'relationships-api'
    for name, content in [('orders', b'order_id,amount\na,100\nb,200\n'), ('items', b'order_id,item_id\na,1\na,2\nb,1\n')]:
        await client.post(f'/sessions/{sid}/upload', files={'files': (name+'.csv', content, 'text/csv')})
    snapshot = (await client.get(f'/sessions/{sid}/workbench')).json()['snapshot']
    assert (await client.get(f'/sessions/{sid}/relationships')).json()['candidates']
    body = dict(left_table='orders', right_table='items', left_keys=['order_id'], right_keys=['order_id'],
                snapshot=snapshot, left_grain='订单', right_grain='订单明细')
    report = await client.post(f'/sessions/{sid}/relationships/check', json=body)
    assert report.status_code == 200 and report.json()['actual_join_rows'] == 3
    assert (await client.post(f'/sessions/{sid}/relationships/confirm', json={**body, 'confirmed':True})).status_code == 200
    restart()
    assert len((await client.get(f'/sessions/{sid}/relationships')).json()['confirmed']) == 1
    assert (await client.get('/sessions/not-mine/relationships')).status_code == 404
    await client.post(f'/sessions/{sid}/upload', files={'files': ('other.csv', b'x\n1\n', 'text/csv')})
    assert (await client.get(f'/sessions/{sid}/relationships')).json()['confirmed'] == []


async def test_commerce_profile_requires_explicit_confirmation_and_correct_structure(local_app):
    client, _, _ = local_app
    sid = 'profile-api'
    for name, content in [
        ('orders', b'order_id,customer_id,order_status,order_purchase_timestamp\na,c,delivered,2018-01-01\n'),
        ('customers', b'customer_id,customer_unique_id\nc,u\n'),
        ('items', b'order_id,order_item_id,product_id,seller_id,price,freight_value\na,1,p,s,100,5\n')]:
        await client.post(f'/sessions/{sid}/upload', files={'files': (name+'.csv', content, 'text/csv')})
    before = (await client.get(f'/sessions/{sid}/workbench')).json()
    assert before['commerce']['confirmed'] is False
    body = {'snapshot': before['snapshot'], 'profile':'olist_v1','currency':'BRL','confirmed':True}
    assert (await client.post(f'/sessions/{sid}/commerce/confirm',json={**body,'currency':'CNY'})).status_code == 422
    response = await client.post(f'/sessions/{sid}/commerce/confirm',json=body)
    assert response.status_code == 200 and response.json()['commerce']['confirmed'] is True
    await client.post(f'/sessions/{sid}/upload', files={'files': ('other.csv',b'x\n1\n','text/csv')})
    assert (await client.get(f'/sessions/{sid}/workbench')).json()['commerce']['confirmed'] is False


async def test_temporal_hits_endpoint_rejects_stale_and_unknown_rules(local_app):
    client,restart,_=local_app
    await client.post('/sessions/time-api/upload',files={'files':('orders.csv',b'order_id,region,order_purchase_timestamp,order_approved_at\n0001, East ,2018-05-03,2018-05-04\n0002,West,2018-05-03,2018-05-02\n','text/csv')})
    before=(await client.get('/sessions/time-api/workbench')).json()
    table=before['tables'][0];check=table['temporal_checks'][0]
    body={'table':'orders','snapshot':before['snapshot'],'check_id':check['id']}
    matched=await client.post('/sessions/time-api/workbench/temporal-rows',json=body)
    assert matched.status_code==200,matched.text
    value=matched.json();assert value['rows'][0]['order_id']=='0002' and value['rows'][0]['active_row']==2
    assert table['temporal_summary']['affected_rows']==1
    assert (await client.post('/sessions/time-api/workbench/temporal-rows',json={**body,'check_id':'SELECT *'})).status_code==422
    assert (await client.post('/sessions/missing/workbench/temporal-rows',json=body)).status_code==404
    preview=(await client.post('/sessions/time-api/cleaning/preview',json={'table':'orders','snapshot':before['snapshot'],'rules':[{'operation':'trim','column':'region'}]})).json()
    await client.post('/sessions/time-api/cleaning/apply',json={'preview_id':preview['preview_id']})
    assert (await client.post('/sessions/time-api/workbench/temporal-rows',json=body)).status_code==409
    restart()
    old=(await client.get('/sessions/time-api/results/'+value['result_id']+'?offset=0')).json()
    assert old['rows'][0]['order_id']=='0002'
    export=await client.get('/sessions/time-api/results/'+value['result_id']+'/export')
    assert export.status_code==200 and '0002' in export.text


async def test_cleaning_four_operations_query_and_undo_form_one_versioned_journey(local_app,monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from app import main
    from app.agent import nodes,router
    client,restart,root=local_app
    sid='cleaning-journey'
    raw=b'order_id,region,amount,note\n0001, East ,10.00,kept\n0002,West,bad,kept\n0002,West,bad,kept\n0003,,,missing-token\n0004,East,0,kept\n'
    assert (await client.post('/sessions/'+sid+'/upload',files={'files':('sales.csv',raw,'text/csv')})).status_code==200
    from app.tools.dataset_manager import get_dataset_manager
    ds=get_dataset_manager().get(sid);original=ds.files[0].parquet_path
    digest=hashlib.sha256(original.read_bytes()).hexdigest()
    raw_path=original.parent/ds.files[0].source['raw_file'];raw_digest=hashlib.sha256(raw_path.read_bytes()).hexdigest()
    initial=(await client.get('/sessions/'+sid+'/workbench')).json();versions=[initial['snapshot']]
    rules=[{'operation':'trim','column':'region'},
        {'operation':'null_tokens','column':'note','tokens':['missing-token']},
        {'operation':'parse_number','column':'amount','failure_policy':'null'},
        {'operation':'deduplicate','keys':['order_id'],'keep':'first','confirmed':True}]
    expected=[1,1,4,1]
    for rule,affected in zip(rules,expected):
        before=(await client.get('/sessions/'+sid+'/workbench')).json()
        body={'table':'sales','snapshot':before['snapshot'],'rules':[rule]}
        if rule['operation']=='parse_number':
            failed=await client.post('/sessions/'+sid+'/cleaning/preview',json={**body,'rules':[{**rule,'failure_policy':'reject'}]})
            assert failed.status_code==422 and '2' in failed.text
            assert (await client.get('/sessions/'+sid+'/workbench')).json()['snapshot']==before['snapshot']
        preview=(await client.post('/sessions/'+sid+'/cleaning/preview',json=body)).json()
        assert preview['affected_rows']==affected
        assert (await client.get('/sessions/'+sid+'/workbench')).json()['snapshot']==before['snapshot']
        applied=await client.post('/sessions/'+sid+'/cleaning/apply',json={'preview_id':preview['preview_id']})
        assert applied.status_code==200,applied.text
        versions.append(applied.json()['snapshot'])
        assert versions[-1]!=versions[-2]
        assert hashlib.sha256(original.read_bytes()).hexdigest()==digest
    restart()
    recovered=(await client.get('/sessions/'+sid+'/workbench')).json()
    assert recovered['snapshot']==versions[-1] and recovered['tables'][0]['rows']==4
    assert recovered['tables'][0]['null_counts']['amount']==2
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    decision=router.RouteDecision(route='sql',reason='核对新版本',analysis_brief={'goal':'统计','requirements':['核对清洗后数据'],'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['records','known_rows','known_amount']}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content='SELECT COUNT(*) AS records,COUNT(amount) AS known_rows,SUM(amount) AS known_amount FROM sales')))
    result=await client.post('/ask',json={'session_id':sid,'question':'核对清洗后数据'})
    assert result.status_code==200,result.text
    assert result.json()['rows']==[{'records':4,'known_rows':2,'known_amount':10.0}]
    assert result.json()['data_version']==versions[-1]
    for prior in reversed(versions[:-1]):
        before=(await client.get('/sessions/'+sid+'/workbench')).json()
        undone=await client.post('/sessions/'+sid+'/cleaning/undo',json={'table':'sales','snapshot':before['snapshot']})
        assert undone.status_code==200 and undone.json()['snapshot']==prior
    restart()
    restored=(await client.get('/sessions/'+sid+'/workbench')).json()
    assert restored['snapshot']==versions[0] and restored['tables'][0]['rows']==5
    assert restored['tables'][0]['preview'][0]['region']==' East '
    history=(await client.get('/sessions/'+sid)).json()['messages'][-1]
    assert history['metadata']['data_version']==versions[-1] and history['metadata']['rows'][0]['records']==4
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest()==raw_digest
