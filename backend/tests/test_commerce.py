import pandas as pd
import pytest

from app.commerce import commerce_facts
from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import store_dataframe


@pytest.fixture
def commerce_dataset(tmp_path):
    frames = {
        'orders': pd.DataFrame({'order_id': ['a', 'b', 'c'], 'customer_id': ['c1', 'c2', 'c3'], 'order_status': ['delivered', 'delivered', 'canceled'], 'order_purchase_timestamp': ['2018-01-01', '2018-02-01', '2018-03-01'], 'order_delivered_customer_date': ['2018-01-04', None, None], 'order_estimated_delivery_date': ['2018-01-03', '2018-02-05', None]}),
        'order_items': pd.DataFrame({'order_id': ['a', 'a', 'a', 'b', 'c'], 'order_item_id': ['1', '2', '3', '1', '1'], 'product_id': ['p'] * 5, 'seller_id': ['s'] * 5, 'price': [20, 30, 50, 60, 999], 'freight_value': [1, 2, 3, 4, 100]}),
        'customers': pd.DataFrame({'customer_id': ['c1', 'c2', 'c3'], 'customer_unique_id': ['same', 'same', 'other']}),
        'payments': pd.DataFrame({'order_id': ['a', 'a', 'b', 'c'], 'payment_value': [40, 66, 64, 1099]}),
    }
    dataset = SessionDataset('commerce-test', parquet_dir=tmp_path)
    for name, frame in frames.items():
        dataset.add_file(store_dataframe(name + '.csv', frame, tmp_path))
    yield dataset
    dataset.close()


def test_multi_item_multi_payment_uses_order_grain_and_real_customer(commerce_dataset):
    result = commerce_facts(commerce_dataset)
    facts = {fact['id']: fact for fact in result['facts']}
    assert facts['item_amount']['value'] == '160'
    assert facts['payment_amount']['value'] == '170'
    assert facts['freight_amount']['value'] == '10'
    assert facts['delivered_orders']['value'] == 2
    assert facts['repeat_customers']['value'] == 1
    assert facts['repeat_customer_rate']['denominator'] == 1
    assert facts['late_rate']['value'] == 1
    assert facts['late_rate']['denominator'] == 1
    assert any('日期' in warning for warning in result['warnings'])
    assert result['currency'] == 'BRL'
    assert result['period'][1].startswith('2018-03')


def test_missing_money_is_unknown_not_zero_and_duplicate_orders_block_facts(commerce_dataset):
    items = pd.read_parquet(next(file.parquet_path for file in commerce_dataset.files if file.table_name == 'order_items'))
    items.loc[0, 'price'] = None
    result = commerce_facts(commerce_dataset, {'order_items': items})
    assert next(f for f in result['facts'] if f['id'] == 'item_amount')['value'] is None
    orders = pd.read_parquet(commerce_dataset.files[0].parquet_path)
    result = commerce_facts(commerce_dataset, {'orders': pd.concat([orders, orders.iloc[:1]])})
    assert result['facts'] == []
    assert any('重复' in warning for warning in result['warnings'])


def test_delivery_deadline_uses_calendar_day_and_preserves_unknown_dates(commerce_dataset):
    orders = pd.read_parquet(commerce_dataset.files[0].parquet_path)
    orders.loc[0, 'order_delivered_customer_date'] = '2018-01-03 18:00:00'
    report = commerce_facts(commerce_dataset, {'orders': orders})
    late = next(f for f in report['facts'] if f['id'] == 'late_rate')
    assert late['value'] == 0
    assert late['denominator'] == 1
    assert '自然日' in late['definition']
    orders.loc[0, 'order_delivered_customer_date'] = '2018-01-04 00:00:00'
    late = next(f for f in commerce_facts(commerce_dataset, {'orders': orders})['facts'] if f['id'] == 'late_rate')
    assert late['value'] == 1


def test_fixed_facts_and_business_context_share_versioned_definitions(commerce_dataset):
    from app.metrics import DEFINITION_VERSION, reference_catalog
    from app.tools.relationships import business_context
    catalog = {item['id']: item for item in reference_catalog()}
    for fact in commerce_facts(commerce_dataset)['facts']:
        assert fact['definition'] == catalog[fact['id']]['definition']
        assert fact['definition_version'] == DEFINITION_VERSION
        assert fact['grain'] == catalog[fact['id']]['grain']
    assert DEFINITION_VERSION in business_context(commerce_dataset)


def catalog_brief():
    return {'acceptance':[{'id':'R1','requirement_index':0,'requirement':'商品金额','answer_type':'value','metric_ids':['item_amount'],'result_columns':['amount']}],
        'calculation_plan':{'source_quotes':['统计已送达商品金额'],'stages':[{
            'name':'summary','source':'orders','joins':[{'table':'order_items','left_keys':['order_id'],'right_keys':['order_id']}],
            'filters':[{'field':'orders.order_status','op':'eq','value':'delivered'}],
            'select':[{'name':'amount','op':'sum','field':'order_items.price'}]}]}}


def verify_catalog_result(dataset, brief):
    from app.calculation_contract import CalculationPlan, compile_plan, verify_calculation
    query,columns=compile_plan(dataset,CalculationPlan.model_validate(brief['calculation_plan']),'统计已送达商品金额')
    frame=dataset.execute(query,as_frame=True)
    rows=[{name:None if pd.isna(value) else value for name,value in row.items()} for row in frame.to_dict('records')]
    return verify_calculation(dataset,brief,'统计已送达商品金额',columns,rows,len(frame))


@pytest.mark.parametrize('mistake',['freight','status','join'])
def test_model_plan_and_code_agreeing_on_wrong_business_definition_is_rejected(commerce_dataset,monkeypatch,mistake):
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:True)
    brief=catalog_brief();stage=brief['calculation_plan']['stages'][0]
    if mistake=='freight':stage['select'][0]['field']='order_items.freight_value'
    elif mistake=='status':stage['filters']=[]
    else:stage['joins'][0]['right_keys']=['seller_id']
    with pytest.raises(ValueError,match='指标口径'):
        verify_catalog_result(commerce_dataset,brief)


def test_supported_definition_returns_scoped_receipt_and_only_one_key_probe(commerce_dataset,monkeypatch):
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:True)
    from app.calculation_contract import CalculationPlan,compile_plan,verify_calculation
    brief=catalog_brief();query,columns=compile_plan(commerce_dataset,CalculationPlan.model_validate(brief['calculation_plan']),'统计已送达商品金额')
    frame=commerce_dataset.execute(query,as_frame=True)
    calls=[];execute=commerce_dataset.execute
    def record(sql,**kwargs):calls.append(sql);return execute(sql,**kwargs)
    monkeypatch.setattr(commerce_dataset,'execute',record)
    checks=verify_calculation(commerce_dataset,brief,'统计已送达商品金额',columns,frame.to_dict('records'),len(frame))
    check=next(c for c in checks if c['id']=='metric_definition')
    assert check['status']=='passed' and check['metric_ids']==['item_amount']
    assert check['definition_version'] and '用户意图' in check['scope']
    assert len(calls)==2  # One existing plan replay plus one business-key probe.


@pytest.mark.parametrize('identifier,role,field,op,expected',[
    ('freight_amount','order_items','freight_value','sum',10),
    ('payment_amount','payments','payment_value','sum',170),
    ('delivered_orders','orders','order_id','count_distinct',2),
])
def test_other_supported_atomic_metrics(commerce_dataset,monkeypatch,identifier,role,field,op,expected):
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:True)
    brief=catalog_brief();brief['acceptance'][0]['metric_ids']=[identifier]
    stage=brief['calculation_plan']['stages'][0]
    stage['select'][0].update(field=role+'.'+field,op=op)
    stage['joins']=[] if role=='orders' else [{'table':role,'left_keys':['order_id'],'right_keys':['order_id']}]
    from app.calculation_contract import CalculationPlan,compile_plan
    query,_=compile_plan(commerce_dataset,CalculationPlan.model_validate(brief['calculation_plan']),'统计已送达商品金额')
    assert commerce_dataset.execute(query,as_frame=True).iloc[0,0]==expected
    assert next(c for c in verify_catalog_result(commerce_dataset,brief) if c['id']=='metric_definition')['status']=='passed'


def test_verified_definition_reaches_report_evidence_and_requirement_coverage(commerce_dataset,monkeypatch):
    from types import SimpleNamespace
    from app.agent.nodes import execute_sql_node
    from app.analysis_pipeline import run_delivery
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:True)
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:commerce_dataset))
    state={'route':'sql','session_id':commerce_dataset.session_id,'question':'统计已送达商品金额','analysis_brief':catalog_brief(),
        'sql':"SELECT SUM(i.price) AS amount FROM orders o JOIN order_items i USING(order_id) WHERE o.order_status='delivered'"}
    delivered=run_delivery({**state,**execute_sql_node(state)})
    receipt=next(c for c in delivered['analysis_report']['evidence'] if c['kind']=='execution_receipt')
    assert receipt['verified_metric_ids']==['item_amount']
    assert delivered['analysis_report']['coverage'][0]['status']=='answered'


@pytest.mark.parametrize('case',['unconfirmed','multistage','missing','duplicate','dimension','different_date_basis'])
def test_unverified_cases_keep_results_without_certifying_the_metric(commerce_dataset,monkeypatch,case):
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:case!='unconfirmed')
    brief=catalog_brief()
    if case=='multistage':
        brief['calculation_plan']['stages'].append({'name':'output','source':'summary','select':[{'name':'amount','op':'max','field':'summary.amount'}]})
    if case=='different_date_basis':
        brief['calculation_plan']['stages'][0]['filters'].append({'field':'orders.order_delivered_customer_date','op':'ge','value':'2018-01-01'})
        brief['scope_sources']=[{'key':'period','text':'已确认按实际送达日期筛选2018-01-01及以后'}]
    if case=='dimension':
        commerce_dataset.add_file(store_dataframe('products.csv',pd.DataFrame({'product_id':['p'],'product_category_name':['category']}),commerce_dataset.parquet_dir))
        stage=brief['calculation_plan']['stages'][0];stage['source']='order_items'
        stage['joins']=[{'table':'orders','left_keys':['order_id'],'right_keys':['order_id']},
            {'table':'products','left_keys':['order_items.product_id'],'right_keys':['product_id']}]
    if case in {'missing','duplicate'}:
        file=next(file for file in commerce_dataset.files if file.table_name=='order_items')
        data=pd.read_parquet(file.parquet_path)
        if case=='missing':data.loc[0,'price']=None
        else:data=pd.concat([data,data.iloc[:1]])
        data.to_parquet(file.parquet_path,index=False)
    checks=verify_catalog_result(commerce_dataset,brief)
    check=next(c for c in checks if c['id']=='metric_definition')
    assert check['status']=='not_checked' and check['detail']


@pytest.mark.parametrize('engine',['sql','python'])
def test_business_definition_failure_keeps_its_type_and_stops_identical_retries(commerce_dataset,monkeypatch,engine):
    from types import SimpleNamespace
    from app.agent import nodes
    from app.agent.graph import decide_after_validator
    from app.tools.python_tool import PythonResult
    monkeypatch.setattr('app.tools.relationships.commerce_confirmed',lambda ds:True)
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:commerce_dataset))
    brief=catalog_brief();brief['calculation_plan']['stages'][0]['select'][0]['field']='order_items.freight_value'
    state={'session_id':commerce_dataset.session_id,'question':'统计已送达商品金额','analysis_brief':brief,
        'sql':"SELECT SUM(i.freight_value) AS amount FROM orders o JOIN order_items i USING(order_id) WHERE o.order_status='delivered'",
        'python_code':'result = wrong_but_matches_plan'}
    monkeypatch.setattr('app.tools.python_tool.execute_python',lambda *args,**kwargs:PythonResult(code='fixture',columns=['amount'],rows=[{'amount':10.0}]))
    result=(nodes.execute_sql_node if engine=='sql' else nodes.execute_python_node)(state)
    assert result['error'] and not result['rows']
    assert result['checks'][0]['id']=='metric_definition'
    assert (decide_after_validator if engine=='sql' else nodes.decide_after_python)(result)=='compose_answer'
