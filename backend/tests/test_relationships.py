import pandas as pd
import pytest

from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import store_dataframe
from app.tools.relationships import (RelationshipRequest, relationship_report, confirm_relationship,
                                      current_relationships, relationship_candidates, business_context)
from app.tools.data_workbench import workbench_snapshot, WorkbenchConflict


def test_calendar_boundary_does_not_shrink_full_observation_scope(tmp_path, monkeypatch):
    import json
    from app.tools import relationships
    ds = dataset(tmp_path, {'sample':{'id':['a']}})
    monkeypatch.setattr(relationships,'commerce_facts',lambda ds:{'roles':{},'period':['2018-01-01','2018-08-30']})
    context=json.loads(business_context(ds).split('):\n')[-1])
    assert '不删除' in context['calendar_boundary']['rule']
    assert '明确要求完整自然月' in context['calendar_boundary']['rule']
    ds.close()


def dataset(tmp_path, tables):
    ds = SessionDataset('relations', parquet_dir=tmp_path)
    for name, values in tables.items():
        ds.add_file(store_dataframe(name + '.csv', pd.DataFrame(values), tmp_path))
    return ds


def test_composite_key_exposes_wrong_join_and_correct_join(tmp_path):
    ds = dataset(tmp_path, {'sales': {'month': ['jan', 'jan'], 'region': ['east', 'west'], 'amount': [100, 200]},
                            'targets': {'month': ['jan', 'jan'], 'region': ['east', 'west'], 'target': [90, 180]}})
    body = dict(left_table='sales', right_table='targets', left_keys=['month'], right_keys=['month'],
                snapshot=workbench_snapshot(ds), left_grain='月份+地区', right_grain='月份+地区')
    bad = relationship_report(ds, RelationshipRequest(**body))
    assert bad['cardinality'] == '多对多' and bad['actual_join_rows'] == 4
    assert bad['expected_join_rows'] == 2 and bad['warnings']
    body.update(left_keys=['month', 'region'], right_keys=['month', 'region'])
    good = relationship_report(ds, RelationshipRequest(**body))
    assert good['cardinality'] == '一对一' and good['actual_join_rows'] == 2
    confirm_relationship(ds, RelationshipRequest(**body, confirmed=True))
    assert len(current_relationships(ds)) == 1
    ds.add_file(store_dataframe('new.csv', pd.DataFrame({'id': ['x']}), tmp_path))
    assert current_relationships(ds) == []
    with pytest.raises(WorkbenchConflict):
        confirm_relationship(ds, RelationshipRequest(**body, confirmed=True))
    ds.close()


def test_missing_unmatched_and_row_multiplication_are_measured(tmp_path):
    ds = dataset(tmp_path, {'a': {'order_id': ['a', 'b', None, 'x']},
                            'b': {'order_id': ['a', 'a', 'b', 'y', None]}})
    request = RelationshipRequest(left_table='a', right_table='b', left_keys=['order_id'], right_keys=['order_id'],
                                  snapshot=workbench_snapshot(ds))
    result = relationship_report(ds, request)
    assert result['left_null_rows'] == result['right_null_rows'] == 1
    assert result['left_unmatched_rows'] == result['right_unmatched_rows'] == 1
    assert result['actual_join_rows'] == 3 and result['expected_join_rows'] == 2
    assert result['right_duplicate_keys'] == 1
    assert relationship_candidates(ds)
    with pytest.raises(ValueError, match='粒度'):
        confirm_relationship(ds, request.model_copy(update={'confirmed': True}))
    assert '未经业务验证' in business_context(ds)
    ds.close()


def test_incompatible_join_key_types_get_a_clear_validation_error(tmp_path):
    ds = dataset(tmp_path, {'a': {'key': ['a']}, 'b': {'key': [1]}})
    request = RelationshipRequest(left_table='a', right_table='b', left_keys=['key'], right_keys=['key'], snapshot=workbench_snapshot(ds))
    with pytest.raises(ValueError, match='类型'):
        relationship_report(ds, request)
    ds.close()


def test_model_context_attaches_unmatched_counts_to_their_source_table(tmp_path):
    import json
    ds = dataset(tmp_path, {'items':{'order_id':['a','a']},'orders':{'order_id':['a','b']}})
    req = RelationshipRequest(left_table='items',right_table='orders',left_keys=['order_id'],right_keys=['order_id'],
        left_grain='一条明细',right_grain='一笔订单',confirmed=True,snapshot=workbench_snapshot(ds))
    confirm_relationship(ds,req)
    context = json.loads(business_context(ds).split('):\n')[-1])
    coverage = context['relationships'][0]['coverage']
    assert coverage[0] == {'table':'items','unmatched_rows_from_this_table':0,'compared_with':'orders'}
    assert coverage[1]['table'] == 'orders' and coverage[1]['unmatched_rows_from_this_table'] == 1
    ds.close()


@pytest.mark.parametrize('left,right,status', [([],[],'no_valid_keys'),([None],[None],'no_valid_keys'),(['a'],['b'],'no_matches')])
def test_no_usable_matches_cannot_be_confirmed_as_one_to_one(tmp_path,left,right,status):
    ds=dataset(tmp_path,{'a':{'id':pd.Series(left or ['placeholder'],dtype='string')},'b':{'id':pd.Series(right or ['placeholder'],dtype='string')}})
    for file,values in zip(ds.files,(left,right)):
        if not values:
            pd.DataFrame({'id':pd.Series(dtype='string')}).to_parquet(file.parquet_path,index=False)
            file.row_count=0
    req=RelationshipRequest(left_table='a',right_table='b',left_keys=['id'],right_keys=['id'],snapshot=workbench_snapshot(ds),
        left_grain='一条A记录',right_grain='一条B记录',confirmed=True)
    report=relationship_report(ds,req)
    assert report['match_status']==status and report['cardinality']!='一对一'
    with pytest.raises(ValueError,match='匹配'):confirm_relationship(ds,req)
    ds.close()


def test_overview_keeps_independent_tables_and_does_not_normalize_keys(tmp_path):
    from app.tools.relationships import relationship_overview
    ds=dataset(tmp_path,{'customers':{'id':['001',' A','x']},'noise':{'temperature':[12,13]},'other':{'id':['1','a','X']}})
    view=relationship_overview(ds)
    assert {t['table_name'] for t in view['tables']}=={'customers','noise','other'}
    assert view['reports'][0]['report']['actual_join_rows']==0
    assert all(not item['grain'] for item in view['tables'])
    ds.close()


def test_ai_semantics_checks_alias_keys_keeps_independent_tables_and_caches_by_snapshot(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app import llm
    from app.tools.relationships import RelationshipUnderstanding,understand_relationships,relationship_overview
    ds=dataset(tmp_path,{'buyers':{'buyer_code':['001','002']},'purchases':{'customer_ref':['001','001','002']},'weather':{'humidity':[20,30]}})
    plan=RelationshipUnderstanding(tables=[{'table_name':'buyers','purpose':'客户','grain':'一个客户','reason':'buyer_code字段线索'}],
        candidates=[{'left_table':'buyers','right_table':'purchases','left_keys':['buyer_code'],'right_keys':['customer_ref'],'reason':'客户编号可能对应购买记录'},
            {'left_table':'buyers','right_table':'purchases','left_keys':['invented'],'right_keys':['customer_ref'],'reason':'错误字段'}],
        independent_tables=[{'table_name':'weather','reason':'当前字段仅有湿度，未发现可用业务键'}],questions=['两个客户编码是否来自同一编号体系？'])
    calls=[]
    def invoke(messages):calls.append(messages);return plan
    monkeypatch.setattr(llm,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    before=workbench_snapshot(ds)
    result=understand_relationships(ds,before)
    assert result['candidates'][0]['report']['cardinality']=='一对多'
    assert result['candidates'][1]['error'] and result['independent_tables'][0]['table_name']=='weather'
    assert not current_relationships(ds) and '"001"' not in calls[0][-1].content
    assert understand_relationships(ds,before)==result and len(calls)==1
    ds.add_file(store_dataframe('extra.csv',pd.DataFrame({'value':[1]}),tmp_path))
    assert relationship_overview(ds)['understanding'] is None
    monkeypatch.setattr(llm,'get_structured_llm',lambda schema:(_ for _ in ()).throw(llm.LLMConfigurationError('not configured')))
    with pytest.raises(ValueError,match='基础概览'):understand_relationships(ds,workbench_snapshot(ds))
    assert len(relationship_overview(ds)['tables'])==4
    ds.close()


def test_candidate_limit_reports_unchecked_scope_instead_of_claiming_no_relationship(tmp_path):
    from app.tools.relationships import relationship_overview
    ds=dataset(tmp_path,{f't{i}':{'id':[1,2]} for i in range(10)})
    view=relationship_overview(ds)
    assert len(view['tables'])==10 and len(view['candidates'])==30
    assert view['coverage']=={'candidate_limit':30,'checked_candidates':10,'truncated':True,'unchecked_candidates':20}
    ds.close()


def test_empty_ai_candidates_are_valid_for_independent_tables_and_database_snapshots_share_checks(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app import llm
    from app.tools.relationships import RelationshipUnderstanding,understand_relationships,relationship_overview
    ds=dataset(tmp_path,{'a':{'parent_id':['a','b']},'b':{'parent_id':['a','a','b']},'notes':{'text':['unrelated']}})
    before=relationship_overview(ds)['reports'][0]['report']
    ds.files[1].source.update(kind='postgresql',database='lab',schema='public',table='b')
    after=relationship_overview(ds)['reports'][0]['report']
    assert before['actual_join_rows']==after['actual_join_rows']==3 and after['cardinality']=='一对多'
    plan=RelationshipUnderstanding(tables=[{'table_name':'notes','purpose':'独立笔记','reason':'只有文本字段'}],
        candidates=[],independent_tables=[{'table_name':'notes','reason':'当前没有可用的关联键'}])
    monkeypatch.setattr(llm,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:plan))
    assert understand_relationships(ds,workbench_snapshot(ds))['candidates']==[]
    ds.close()


def test_foreign_key_declarations_never_cross_connection_identities(tmp_path):
    ds=dataset(tmp_path,{'lines':{'catalog_ref':['a']},'same':{'catalog_key':['a']},'other_host':{'catalog_key':['a']},'other_port':{'catalog_key':['a']},'legacy':{'catalog_key':['a']}})
    source={'kind':'postgresql','host':'127.0.0.1','port':55432,'database':'shop','schema':'public'}
    ds.files[0].source={**source,'table':'lines','relationship_metadata':{'foreign_keys':[{'columns':['catalog_ref'],'referenced_schema':'public','referenced_table':'catalog','referenced_columns':['catalog_key']}]}}
    for file in ds.files[1:]:file.source={**source,'table':'catalog'}
    ds.files[2].source['host']='localhost'
    ds.files[3].source['port']=55433
    ds.files[4].source.pop('host')
    foreign=[item for item in relationship_candidates(ds) if '外键声明' in item['source']]
    assert [(item['left_table'],item['right_table']) for item in foreign]==[('lines','same')]
    ds.close()
