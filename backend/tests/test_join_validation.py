import pandas as pd
import pytest

from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import store_dataframe


@pytest.fixture
def joined_data(tmp_path):
    dataset = SessionDataset('join-check', parquet_dir=tmp_path)
    frames = {
        'orders': pd.DataFrame({'order_id': ['a', 'b'], 'amount': [100, 200]}),
        'items': pd.DataFrame({'order_id': ['a', 'a', 'a', 'b'], 'item_id': [1, 2, 3, 1], 'price': [20, 30, 50, 200]}),
        'payments': pd.DataFrame({'order_id': ['a', 'a', 'b'], 'payment': [40, 60, 200]}),
    }
    for name, frame in frames.items():
        dataset.add_file(store_dataframe(name + '.csv', frame, tmp_path))
    yield dataset
    dataset.close()


@pytest.mark.parametrize('sql', [
    'SELECT SUM(o.amount) FROM orders o JOIN items i USING(order_id)',
    'SELECT SUM(amount) FROM orders JOIN items USING(order_id)',
    'SELECT AVG(o.amount) FROM orders o JOIN items i ON o.order_id=i.order_id',
    'WITH t AS (SELECT o.order_id, SUM(o.amount) amount FROM orders o JOIN items i USING(order_id) GROUP BY o.order_id) SELECT SUM(amount) FROM t',
    'SELECT SUM(i.price) FROM items i JOIN payments p USING(order_id)',
    'WITH joined AS (SELECT o.amount AS total FROM orders o JOIN items i USING(order_id)) SELECT SUM(total) FROM joined',
    'SELECT SUM(j.total) FROM (SELECT o.amount AS total FROM orders o JOIN items i USING(order_id)) j',
])
def test_repeated_source_aggregates_are_rejected(joined_data, sql):
    with pytest.raises(ValueError, match='重复'):
        joined_data.execute(sql)


@pytest.mark.parametrize('sql,expected', [
    ('SELECT SUM(i.price) FROM orders o JOIN items i USING(order_id)', 300),
    ('WITH t AS (SELECT order_id, SUM(price) amount FROM items GROUP BY order_id) SELECT SUM(t.amount) FROM orders o JOIN t USING(order_id)', 300),
    ("SELECT SUM(o.amount) FROM orders o JOIN items i USING(order_id) WHERE o.order_id='b'", 200),
    ("SELECT SUM(o.amount) FILTER(WHERE o.order_id='b') FROM orders o JOIN items i USING(order_id)", 200),
    ('SELECT COUNT(DISTINCT o.order_id) FROM orders o JOIN items i USING(order_id)', 2),
    ('SELECT SUM(i.price) FROM items i JOIN items j ON i.order_id=j.order_id AND i.item_id=j.item_id', 300),
    ('SELECT SUM(o.amount) FROM orders o LEFT JOIN items i ON o.order_id=i.order_id AND i.item_id=99', 300),
    ('SELECT SUM(i.price*o.amount) FROM orders o JOIN items i USING(order_id)', 50000),
])
def test_valid_grains_filters_and_explicit_cross_source_calculations_still_work(joined_data, sql, expected):
    assert joined_data.execute(sql).fetchone()[0] == expected


def test_agent_discards_duplicated_results_and_keeps_error_for_its_retry(joined_data, monkeypatch):
    from types import SimpleNamespace

    from app.agent.nodes import execute_sql_node
    from app.tools import dataset_manager
    monkeypatch.setattr(dataset_manager, 'get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: joined_data))
    result = execute_sql_node({'session_id': 'join-check', 'sql': 'SELECT SUM(o.amount) total FROM orders o JOIN items i USING(order_id)'})
    assert '重复' in result['error']
    assert result['rows'] == [] and result['row_count'] == 0
