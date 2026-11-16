import pandas as pd
import pytest

from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import store_dataframe


@pytest.fixture
def dataset(tmp_path):
    ds = SessionDataset('precision-check', parquet_dir=tmp_path)
    ds.add_file(store_dataframe('money.csv', pd.DataFrame({
        'id': ['a', 'b', 'c'], 'paid': [100.01, 100.0, 100.02],
        'item': [80.0, 80.01, 80.0], 'freight': [20.0, 19.99, 20.0],
    }), tmp_path))
    yield ds
    ds.close()


@pytest.mark.parametrize('query', [
    'SELECT count(*) FILTER(WHERE abs(paid-item-freight) > .01) FROM money',
    'WITH d AS (SELECT paid-item-freight diff FROM money) SELECT sum(CASE WHEN abs(diff)>.01 THEN 1 ELSE 0 END) FROM d',
    'WITH d AS (SELECT paid-item-freight diff FROM money) SELECT count(*) FROM d WHERE diff=0',
    'SELECT count(*) FILTER(WHERE paid-item-freight>0) FROM money',
    "SELECT id, sum(paid-item-freight) diff FROM money GROUP BY id HAVING diff=0",
])
def test_near_float_boundaries_are_rejected_without_returning_wrong_counts(dataset, query):
    with pytest.raises(ValueError, match='浮点.*边界'):
        dataset.execute(query)


def test_confirmed_decimal_currency_and_explicit_rounding_are_accepted(dataset):
    query = '''WITH d AS (SELECT
        CAST(paid AS DECIMAL(18,2))-CAST(item AS DECIMAL(18,2))-
        CAST(freight AS DECIMAL(18,2)) diff FROM money)
        SELECT count(*) FILTER(WHERE abs(diff)>.01),count(*) FILTER(WHERE diff=0) FROM d'''
    assert dataset.execute(query).fetchone() == (1, 1)
    assert dataset.execute('SELECT count(*) FROM money WHERE abs(round(paid-item-freight,2))>.01').fetchone() == (1,)


def test_normal_float_ranges_integer_date_and_zero_are_unchanged(dataset):
    assert dataset.execute('SELECT count(*) FROM money WHERE paid>100.005').fetchone() == (2,)
    assert dataset.execute("SELECT 1 WHERE DATE '2026-01-01'<DATE '2026-02-01'").fetchone() == (1,)
    assert dataset.execute('SELECT count(*) FROM money WHERE 1=1 AND freight>0').fetchone() == (3,)


def test_agent_returns_precision_error_and_no_facts(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.agent.nodes import execute_sql_node
    from app.tools import dataset_manager
    monkeypatch.setattr(dataset_manager, 'get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    result = execute_sql_node({'session_id': dataset.session_id,
        'sql': 'SELECT count(*) FILTER(WHERE abs(paid-item-freight)>.01) n FROM money'})
    assert '浮点' in result['error']
    assert result['rows'] == [] and result['checks'] == []


def test_legacy_csv_executor_uses_the_same_precision_guard(tmp_path):
    from app.tools.sql import SQLTool
    path = tmp_path / 'money.csv'
    path.write_text('paid,item,freight\n100.01,80.0,20.0\n', encoding='utf-8')
    tool = SQLTool(path, table_name='money', column_type_overrides={})
    try:
        result = tool.execute('SELECT count(*) FROM money WHERE abs(paid-item-freight)>.01')
        assert not result.ok and '浮点' in result.error and result.dataframe is None
    finally:
        tool.close()


def test_decimal_sql_result_refuses_loss_before_dataframe_facts_or_pagination(tmp_path,monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.agent.nodes import execute_sql_node
    from app.tools import dataset_manager
    ds=SessionDataset('decimal-result',parquet_dir=tmp_path)
    ds.add_file(store_dataframe('money.csv',pd.DataFrame({'amount':[Decimal('9007199254740993.01'),Decimal('0.01')]}),tmp_path))
    monkeypatch.setattr(dataset_manager,'get_dataset_manager',lambda:SimpleNamespace(get=lambda sid:ds))
    try:
        sql='SELECT sum(amount) amount FROM money'
        assert ds.execute(sql).fetchone()[0]==Decimal('9007199254740993.02')
        with pytest.raises(ValueError,match='精确小数.*无损范围'):
            ds.execute(sql,as_frame=True)
        result=execute_sql_node({'session_id':ds.session_id,'sql':sql})
        assert '精确小数' in result['error'] and result['rows']==[] and result['row_count']==0
        assert not result.get('facts') and not result.get('result_id')
        assert ds.execute('SELECT amount FROM money').fetchall()==[(Decimal('9007199254740993.01'),),(Decimal('0.01'),)]
    finally:ds.close()


def test_decimal_guard_preserves_ordinary_money_and_representable_tiny_values(dataset):
    assert dataset.execute('SELECT CAST(paid AS DECIMAL(18,2)) amount,freight FROM money ORDER BY id',as_frame=True)['amount'].tolist()==[100.01,100.,100.02]
    assert dataset.execute('SELECT CAST(0.00000000000001 AS DECIMAL(18,14)) amount',as_frame=True)['amount'].tolist()==[1e-14]
