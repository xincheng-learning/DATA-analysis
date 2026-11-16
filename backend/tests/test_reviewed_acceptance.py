"""User-reviewed examples: independent SQLite/hand ledger, then controlled HTTP delivery.

The HTTP model doubles test delivery only; live understanding is scored separately.
"""
import csv
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from test_chat_storage import storage_app  # noqa: F401


GOLD = Path(__file__).resolve().parents[2] / 'evals/reliability_v1'
DATA = GOLD / 'data/variant_1'
JAN = [{'region': '华东', 'amount': 61}, {'region': '华北', 'amount': 32}]
FEB = [{'region': '华东', 'amount': 101}]


def test_reviewed_answers_agree_with_an_independent_engine_and_hand_ledger():
    # Literal answers were shown to the user before this run; never derive them from product code.
    expected = [
        [('华东', 61, 101), ('华北', 32, None)], [(3,)], [(31,)], [(None,)], [(None,)],
        [(5,)], [(50,)], [(93,)], [(2.25,)], [(0,), (None,), (2,), (2,), (5,)], [],
    ]
    with sqlite3.connect(':memory:') as db:
        for name in ['orders', 'lines', 'targets', 'refunds', 'observations']:
            with (DATA / (name + '.csv')).open(encoding='utf-8', newline='') as stream:
                reader = csv.reader(stream)
                columns = next(reader)
                db.execute(f'CREATE TABLE {name} ({",".join(columns)})')
                db.executemany(f'INSERT INTO {name} VALUES ({",".join("?" for _ in columns)})',
                               [tuple(None if v == '' else v for v in row) for row in reader])
        # Different engine and per-order reduction, independent of the product plan compiler.
        db.executescript("""
            CREATE TEMP VIEW jan AS SELECT o.order_id,o.customer_id,o.region,SUM(CAST(l.amount AS REAL)) amount
            FROM orders o JOIN lines l ON o.order_id=l.order_id
            WHERE o.status='completed' AND o.ordered_at>='2026-01-01' AND o.ordered_at<'2026-02-01'
            GROUP BY o.order_id,o.customer_id,o.region;
        """)
        queries = [
            "SELECT j.region,SUM(j.amount),(SELECT CAST(t.target AS REAL) FROM targets t WHERE t.region=j.region AND t.month='2026-01') FROM jan j GROUP BY j.region ORDER BY j.region",
            'SELECT COUNT(*) FROM jan', 'SELECT AVG(amount) FROM jan',
            "SELECT (SELECT target FROM targets WHERE month='2026-01' AND region='华北')",
            "SELECT CASE WHEN CAST(target AS REAL)=0 THEN NULL ELSE 100.0/CAST(target AS REAL) END FROM targets WHERE month='2026-02' AND region='华东'",
            "SELECT SUM(CAST(amount AS REAL)) FROM refunds WHERE status='settled'",
            'SELECT 100.0*SUM(n>=2)/COUNT(*) FROM (SELECT customer_id,COUNT(*) n FROM jan GROUP BY customer_id)',
            'SELECT SUM(amount) FROM jan', 'SELECT AVG(CAST(value AS REAL)) FROM observations',
            'SELECT CAST(value AS REAL) FROM observations ORDER BY CAST(record_id AS INTEGER)',
            "SELECT order_id FROM orders WHERE region='西区' AND status='completed'",
        ]
        frozen = json.loads((GOLD / 'answers.json').read_text(encoding='utf-8'))['answers']
        for index, (query, manual) in enumerate(zip(queries, expected, strict=True), 1):
            assert db.execute(query).fetchall() == manual, f'RG{index:03}: SQLite versus manual ledger'
            answer = frozen[f'RG{index:03}'][0]
            assert [tuple(row[c] for c in answer['columns']) for row in answer['rows']] == manual
        assert 10 + 10 + 41 == 61 and 61 + 32 == 93 and 93 / 3 == 31
        db.execute("UPDATE lines SET amount='999999' WHERE order_id='o4'")
        assert db.execute('SELECT SUM(amount) FROM jan').fetchone() == (93,)
        db.execute("UPDATE lines SET amount=CAST(amount AS REAL)+7 WHERE line_id='l1'")
        assert db.execute('SELECT SUM(amount) FROM jan').fetchone() == (100,)


async def test_controlled_month_revision_keeps_table_chart_csv_and_history_consistent(storage_app, monkeypatch):
    from app import main, session
    from app.agent import graph, nodes, router
    from app.tools import dataset_manager, viz
    client, _, _ = storage_app
    sid = 'reviewed-delivery'
    for name in ['orders', 'lines', 'targets']:
        response = await client.post(f'/sessions/{sid}/upload', files={'files': (name + '.csv', (DATA / (name + '.csv')).read_bytes(), 'text/csv')})
        assert response.status_code == 200, response.text
    questions = ['只看2026年一月已完成订单，按地区汇总商品金额，并画柱状图。', '改成2026年二月，其他条件不变。']
    decisions, sqls = [], []
    for index, (start, end) in enumerate([('2026-01-01', '2026-02-01'), ('2026-02-01', '2026-03-01')]):
        decisions.append(router.RouteDecision(route='viz', reason='受控计划，只验交付链路', analysis_brief={
            'goal': questions[index], 'revision_quote': questions[index] if index else '',
            'parts': [{'requirement': '按地区商品金额与图表', 'criterion': {'answer_type': 'chart', 'result_columns': ['region', 'amount'], 'request_quote': questions[index]}}],
            'scope_sources': [{'key': 'period', 'text': questions[index]}],
            'calculation_plan': {'source_quotes': [questions[index]], 'stages': [{
                'name': 'regional', 'source': 'orders', 'joins': [{'table': 'lines', 'left_keys': ['order_id'], 'right_keys': ['order_id']}],
                'filters': [{'field': 'orders.status', 'op': 'eq', 'value': 'completed'}, {'field': 'orders.ordered_at', 'op': 'ge', 'value': start}, {'field': 'orders.ordered_at', 'op': 'lt', 'value': end}],
                'select': [{'name': 'region', 'op': 'column', 'field': 'orders.region'}, {'name': 'amount', 'op': 'sum', 'field': 'lines.amount'}],
                'group_by': ['orders.region'], 'order_by': [{'field': 'region'}],
            }]}}))
        sqls.append(f"SELECT o.region,SUM(l.amount) amount FROM orders o JOIN lines l ON o.order_id=l.order_id WHERE o.status='completed' AND o.ordered_at>='{start}' AND o.ordered_at<'{end}' GROUP BY o.region ORDER BY o.region")
    calls = {'router': 0, 'sql': 0}
    def route(messages):
        at = calls['router']; calls['router'] += 1
        return decisions[at]
    def sql(messages):
        at = calls['sql']; calls['sql'] += 1
        return SimpleNamespace(content=sqls[at])
    monkeypatch.setattr(main, 'get_chat_llm', lambda: object())
    monkeypatch.setattr(router, '_get_router_llm', lambda: SimpleNamespace(invoke=route))
    monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=sql))
    monkeypatch.setattr(viz, 'choose_chart', lambda **kwargs: viz.ChartSpec(chart_type='bar', x_column='region', y_column='amount', title='地区商品金额', x_label='地区', y_label='商品金额（原始单位）'))
    monkeypatch.setattr(graph, '_graph', None)
    results = []
    for question, expected in zip(questions, [JAN, FEB], strict=True):
        response = await client.post('/ask', json={'session_id': sid, 'question': question})
        assert response.status_code == 200, response.text
        result = response.json(); results.append(result)
        assert not result.get('error') and not result.get('chart_error'), result
        assert result['rows'] == expected
        trace = result['chart_spec']['data'][0]
        assert dict(zip(trace['x'], trace['y'], strict=True)) == {r['region']: r['amount'] for r in expected}
        exported = await client.get(f"/sessions/{sid}/results/{result['result_id']}/export")
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
        assert [{'region': r['region'], 'amount': float(r['amount'])} for r in rows] == expected
    assert calls == {'router': 2, 'sql': 0}
    history = (await client.get(f'/sessions/{sid}')).json()
    analyses = [m for m in history['messages'] if m['role'] == 'assistant' and m['metadata'].get('analysis_scope')]
    assert [m['metadata']['rows'] for m in analyses] == [JAN, FEB]
    assert [m['metadata']['analysis_scope_state']['status'] for m in analyses] == ['historical_scope', 'current']
    dataset_manager.get_dataset_manager().get(sid).close()
    monkeypatch.setattr(session, '_store', None); monkeypatch.setattr(dataset_manager, '_manager', None)
    assert (await client.get(f'/sessions/{sid}')).json() == history
