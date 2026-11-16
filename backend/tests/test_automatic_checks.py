import pytest
import duckdb
from app.tools.viz import ChartSpec, build_plotly_spec
from test_chat_storage import storage_app  # noqa: F401


def test_chart_verification_checks_category_value_pairs_not_just_values():
    from app.checks import verify_chart
    rows=[{'region':'East','amount':10},{'region':'West','amount':20}]
    spec=ChartSpec(chart_type='bar',x_column='region',y_column='amount',title='金额',x_label='地区',y_label='金额')
    chart=build_plotly_spec(spec,rows)
    assert verify_chart(chart,rows,2)['status']=='passed'
    chart['data'][0]['y']=[20,10]
    with pytest.raises(ValueError,match='配对|来源'):
        verify_chart(chart,rows,2)


def test_null_aggregation_is_reported_as_partial_not_as_a_complete_total():
    from app.checks import aggregate_coverage
    con=duckdb.connect(':memory:')
    con.execute('CREATE TABLE sales(region VARCHAR, amount INTEGER)')
    con.execute("INSERT INTO sales VALUES ('East',10),('East',NULL),('West',20)")
    checks=aggregate_coverage(con,'SELECT region, SUM(amount) AS total FROM sales GROUP BY region')
    assert checks[0]['status']=='warning' and checks[0]['missing_rows']==1
    assert '已知' in checks[0]['detail']
    known=aggregate_coverage(con,'SELECT SUM(amount) FROM sales WHERE amount IS NOT NULL')
    assert known[0]['status']=='passed' and known[0]['missing_rows']==0
    assert aggregate_coverage(con,'SELECT COUNT(amount) FROM sales')==[]
    con.close()


def test_join_check_returns_only_actual_probed_coverage():
    from app.tools.join_validation import validate_join_aggregates
    con=duckdb.connect(':memory:')
    con.execute('CREATE TABLE a(id INTEGER,amount INTEGER)');con.execute('INSERT INTO a VALUES (1,10)')
    con.execute('CREATE TABLE b(id INTEGER)');con.execute('INSERT INTO b VALUES (1)')
    checks=validate_join_aggregates(con,'SELECT SUM(a.amount) FROM a JOIN b USING(id)')
    assert checks and checks[0]['joined_rows']==checks[0]['source_rows']==1
    assert validate_join_aggregates(con,'SELECT * FROM a JOIN b USING(id)')==[]
    con.close()


async def test_top_n_uses_full_saved_result_instead_of_only_the_first_fifty_rows(storage_app,monkeypatch):
    import pandas as pd
    from app.agent.nodes import make_chart_node
    from app.tools.query_results import save_result
    from app.tools import viz
    frame=pd.DataFrame({'region':[f'R{i}' for i in range(60)],'amount':range(60)})
    result_id=save_result('chart-full',frame)
    spec=ChartSpec(chart_type='bar',x_column='region',y_column='amount',row_limit=3,title='前三',x_label='地区',y_label='金额')
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:spec)
    state={'session_id':'chart-full','question':'前三名','columns':list(frame.columns),'rows':frame.head(50).to_dict(orient='records'),'row_count':60,'result_id':result_id}
    result=make_chart_node(state)
    assert result['chart_spec']['data'][0]['x']==['R59','R58','R57']
    assert result['checks'][-1]['source_rows']==60


async def test_rejected_query_does_not_keep_checks_from_an_earlier_attempt(storage_app):
    from app.agent.nodes import execute_sql_node
    client,_,_=storage_app
    await client.post('/sessions/rejected/upload',files={'files':('orders.csv',b'amount\n10\n','text/csv')})
    result=execute_sql_node({'session_id':'rejected','sql':'DELETE FROM orders','checks':[{'id':'old','status':'passed'}]})
    assert result['error'] and result.get('checks')==[]
