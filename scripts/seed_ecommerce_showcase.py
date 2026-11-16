"""Create a separate public-data showcase using fixed SQL, without model calls."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def seed(data_dir: Path, demo_dir: Path, session_id: str):
    from app.config import settings
    from app.session import SessionStore, SQLiteBackend
    from app.tools import dataset_manager
    from app.tools.ingest import validate_and_convert
    from app.tools.data_workbench import workbench_snapshot, _state, _write_state
    from app.tools.relationships import relationship_candidates, RelationshipRequest, confirm_relationship
    from app.tools.viz import ChartSpec, build_plotly_spec
    from app.agent.nodes import execute_sql_node, compose_answer_node

    settings.app_mode = 'local'
    settings.local_data_dir = data_dir.resolve()
    destination = settings.local_data_dir / 'uploads' / session_id
    if destination.exists():
        raise ValueError('演示会话已存在，未覆盖任何数据。请直接打开已有演示，或指定新的 --session-id。')
    if not (demo_dir / 'manifest.json').exists():
        raise ValueError('请先运行 prepare_ecommerce_demo.py 准备公开数据。')
    store = SessionStore(backend=SQLiteBackend(settings.local_data_dir / 'datapilot.sqlite3'))
    if store._backend.get(session_id):
        raise ValueError('同名会话已存在，未覆盖。')
    manager = dataset_manager.DatasetManager()
    dataset_manager._manager = manager
    ds = manager.get_or_create(session_id, destination)
    for file in sorted(demo_dir.glob('*.csv')):
        with file.open('rb') as source:
            manager.add_file(session_id, validate_and_convert(file.name, source, destination))
    version = workbench_snapshot(ds)
    for candidate in relationship_candidates(ds):
        if candidate['source'].startswith('Olist'):
            candidate.pop('source')
            confirm_relationship(ds, RelationshipRequest(**candidate, snapshot=version, confirmed=True))
    state = _state(ds)
    state['commerce_snapshot'] = version
    _write_state(ds, state)
    store.get_or_create(session_id, user_id='local-user')
    store._backend.set_title(session_id, 'Olist 电商演示 · 固定 SQL，无模型')
    store._backend.save_message(session_id, 'assistant',
        '这是独立的公开数据演示会话：5,000 位客户的完整历史，2016—2018 年，币种 BRL。以下固定 SQL、图表和文字由程序执行生成，没有调用模型，不代表真实模型验收。自由提问仍可使用你原来的模型设置。',
        {'data_version': version})
    monthly = """WITH item_totals AS (SELECT order_id,SUM(price) item_amount FROM order_items GROUP BY order_id),
payment_totals AS (SELECT order_id,SUM(payment_value) payment_amount FROM payments GROUP BY order_id)
SELECT strftime(TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP),'%Y-%m') AS month,
SUM(i.item_amount) AS item_amount,SUM(p.payment_amount) AS payment_amount
FROM orders o LEFT JOIN item_totals i USING(order_id) LEFT JOIN payment_totals p USING(order_id)
WHERE o.order_status='delivered' GROUP BY month ORDER BY month"""
    regional = """SELECT strftime(TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP),'%Y-%m') AS month,
c.customer_state AS region,SUM(i.price) AS item_amount
FROM order_items i JOIN orders o USING(order_id) JOIN customers c USING(customer_id)
WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='2018-01-01'
AND o.order_purchase_timestamp<'2018-07-01' AND c.customer_state IN ('SP','RJ','MG')
GROUP BY month,region ORDER BY month,region"""
    heatmap = """SELECT strftime(TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP),'%Y-%m') AS month,
c.customer_state AS region,COUNT(*) AS order_count
FROM orders o JOIN customers c USING(customer_id)
WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='2018-01-01'
AND o.order_purchase_timestamp<'2018-07-01' AND c.customer_state IN ('SP','RJ','MG')
GROUP BY month,region ORDER BY month,region"""
    cases = [
        ('对比各月商品金额与支付额（分别按订单汇总后关联）', monthly,
         dict(chart_type='line',x_column='month',y_column='item_amount',y_columns=['item_amount','payment_amount'],title='商品金额与支付额 · 观察差异',x_label='月份',y_label='金额（BRL）')),
        ('对比2018年上半年三个州的商品金额构成', regional,
         dict(chart_type='bar',x_column='month',y_column='item_amount',series_column='region',bar_mode='stack',title='三州商品金额构成 · 2018上半年',x_label='月份',y_label='商品金额（BRL）')),
        ('用热力图比较三个州每个月的已送达订单数', heatmap,
         dict(chart_type='heatmap',x_column='month',y_column='region',z_column='order_count',title='订单分布 · 月份 × 州',x_label='月份',y_label='州')),
        ('查看123笔订单记录；表格每次展开50行', 'SELECT order_id,order_purchase_timestamp,order_status FROM orders ORDER BY order_purchase_timestamp,order_id LIMIT 123', None),
    ]
    for question, sql, chart in cases:
        started = time.monotonic()
        result = execute_sql_node({'session_id':session_id,'sql':sql,'data_version':version})
        if result.get('error'):
            raise ValueError(result['error'])
        final = {**result,'sql':sql,'question':question,'data_version':version,'route':'viz' if chart else 'sql'}
        if chart:
            final['chart_spec'] = build_plotly_spec(ChartSpec(**chart), result['rows'])
        final.update(compose_answer_node(final))
        final['evidence'] = {'model_calls':0,'max_model_calls':30,'trace':[
            {'node':'execute_sql','label':'固定 SQL 演示（无模型）','status':'succeeded','sql':sql,
             'row_count':result['row_count'],'seconds':round(time.monotonic()-started,3)}]}
        store._backend.save_message(session_id,'user','演示案例：'+question)
        store._backend.save_message(session_id,'assistant',final['answer'],{key:value for key,value in final.items() if key not in {'question','answer'}})
    ds.close()
    return {'session_id':session_id,'tables':len(ds.files),'cases':len(cases),'model_calls':0,'data_version':version}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True, help='Explicit app data directory; creates a new, separate showcase session')
    parser.add_argument('--demo-dir', type=Path, default=ROOT/'.local/ecommerce/demo')
    parser.add_argument('--session-id', default='ecommerce-demo-20261030')
    args = parser.parse_args()
    print(json.dumps(seed(args.data_dir,args.demo_dir,args.session_id),ensure_ascii=False))
