"""Cross-check saved Agent outputs against independent CSV/Decimal calculations; no model calls."""
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
RUN = ROOT / 'evals/runs/olist-teacher-20261111'
OUT = ROOT / 'output/olist-business-2026-11-11'


def load(name):
    return json.loads((RUN / (name + '.json')).read_text(encoding='utf-8'))['final']


def rows(result):
    if result.get('result_id'):
        paths = list((RUN / 'app-data').rglob(result['result_id'] + '.arrow'))
        assert len(paths) == 1
        with pa.memory_map(str(paths[0]), 'r') as source:
            data = pa.ipc.open_file(source).read_all().to_pylist()
    else:
        data = result['rows']
    assert len(data) == result['row_count']
    return data


def close(actual, expected):
    assert abs(Decimal(str(actual)) - Decimal(str(expected))) < Decimal('.000001'), (actual, expected)


def verify():
    base = json.loads((OUT / 'independent-baseline.json').read_text(encoding='utf-8'))
    steps = {s['id']: s['result'] for s in load('06-business-structure')['workflow']['steps']}
    # The Agent explicitly groups missing translations with unknown categories;
    # the original baseline retains untranslated Portuguese identifiers separately.
    def table(name):
        with (ROOT / '.local/ecommerce/demo' / (name + '.csv')).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    delivered_ids = {r['order_id'] for r in table('orders') if r['order_status'] == 'delivered'}
    products = {r['product_id']: r['product_category_name'] for r in table('products')}
    translation = {r['product_category_name']: r['product_category_name_english'] for r in table('category_translation')}
    categories = defaultdict(lambda: {'amount': Decimal(0), 'lines': 0, 'orders': set()})
    for r in table('order_items'):
        if r['order_id'] not in delivered_ids:
            continue
        category = translation.get(products.get(r['product_id']), 'Unknown')
        group = categories[category]
        group['amount'] += Decimal(r['price']); group['lines'] += 1; group['orders'].add(r['order_id'])
    category_rows = [r for r in rows(steps['cat_stats']) if r['row_type'] == 'category']
    assert len(category_rows) == len(categories) == 65
    for r in category_rows:
        reference = categories[r['product_category_en']]
        close(r['item_amount'], reference['amount'])
        assert (r['item_lines'], r['distinct_orders']) == (reference['lines'], len(reference['orders']))
    chart_rows = rows(steps['cat_chart'])
    assert len(chart_rows) == 11
    assert {r['product_category_en'] for r in chart_rows[:-1]} == {r['name'] for r in base['categories'][:10]}
    for r in chart_rows:
        expected = categories[r['product_category_en']]['amount'] if r['product_category_en'] in categories else sum(Decimal(v['amount']) for v in base['categories'][10:])
        close(r['item_amount_brl'], expected)
    close(sum(Decimal(str(r['item_amount_brl'])) for r in chart_rows), base['total_item_amount'])
    buyers = [r for r in rows(steps['state_stats']) if r['dimension'] == 'buyer_state']
    assert {r['state'] for r in buyers} == set(base['regions'])
    for r in buyers:
        reference = base['regions'][r['state']]
        close(r['item_amount'], reference['item_amount'])
        assert r['distinct_orders'] == reference['orders']
    sellers = {r['name']: r for r in base['sellers']}
    seller_rows = rows(load('06b-sellers-corrected'))
    assert len(seller_rows) == len(sellers) == 1201
    for r in seller_rows:
        reference = sellers[r['seller_id']]
        close(r['item_amount_brl'], reference['amount'])
        assert (r['item_row_count'], r['distinct_orders']) == (reference['lines'], reference['orders'])
    payment = {r['name']: r for r in base['payment_types']}
    payment_rows = rows(steps['pay_stats'])
    assert {r['payment_type'] for r in payment_rows} == set(payment)
    for r in payment_rows:
        reference = payment[r['payment_type']]
        close(r['payment_amount_brl'], reference['amount'])
        assert (r['payment_records'], r['distinct_orders']) == (reference['records'], reference['orders'])
    # Recompute every order balance in exact decimal arithmetic from original files.
    delivered = {r['order_id'] for r in table('orders') if r['order_status'] == 'delivered'}
    balances = defaultdict(Decimal)
    for r in table('payments'):
        if r['order_id'] in delivered:
            balances[r['order_id']] += Decimal(r['payment_value'])
    for r in table('order_items'):
        if r['order_id'] in delivered:
            balances[r['order_id']] -= Decimal(r['price']) + Decimal(r['freight_value'])
    recon = rows(load('06c-money-precision'))[0]
    assert recon['order_count'] == len(delivered) == 5008
    assert recon['orders_abs_diff_gt_0_01'] == sum(abs(v) > Decimal('.01') for v in balances.values()) == 13
    for field, expected in [('orders_diff_zero', sum(v == 0 for v in balances.values())),
        ('orders_diff_positive', sum(v > 0 for v in balances.values())),
        ('orders_diff_negative', sum(v < 0 for v in balances.values())), ('orders_diff_unknown', 0)]:
        assert recon[field] == expected
    close(recon['total_difference'], sum(balances.values()))
    close(recon['total_item_amount'], base['total_item_amount'])
    close(recon['total_payment_amount'], base['total_payment_amount'])
    python = load('06d-python-money')
    python_recon = rows(python)[0]
    for key, expected in recon.items():
        close(python_recon[key], expected)
    assert all(python_recon[key] == 5008 for key in ('orders_payment_known', 'orders_item_known', 'orders_freight_known'))
    assert '13' in python['python_summary'] and '146.04' in python['python_summary']
    assert python['python_summary'] in python['answer']
    from app.config import settings
    from app.tools.dataset_manager import DatasetManager
    settings.local_data_dir = RUN / 'app-data'
    settings.app_mode = 'local'
    dataset = DatasetManager().get('olist-teacher-20261111')
    try:
        dataset.execute(steps['recon']['sql'])
    except ValueError as error:
        assert '浮点' in str(error) and '边界' in str(error)
        original_blocked = str(error)
    else:
        raise AssertionError('Original incorrect SQL must be rejected by the precision guard')
    actual = dataset.execute(load('06c-money-precision')['sql']).fetchone()
    assert actual[5:9] == (13, 4980, 18, 10)
    dataset.close()
    state_change = defaultdict(lambda: [Decimal(0), Decimal(0)])
    customers = {r['customer_id']: r for r in table('customers')}
    orders = {r['order_id']: r for r in table('orders') if r['order_id'] in delivered and r['order_purchase_timestamp'][:7] in {'2018-06', '2018-07'}}
    for r in table('order_items'):
        order = orders.get(r['order_id'])
        if order:
            state_change[customers[order['customer_id']]['customer_state']][order['order_purchase_timestamp'][:7] == '2018-07'] += Decimal(r['price'])
    top = sorted(state_change, key=lambda s: (-abs(state_change[s][1] - state_change[s][0]), s))[:5]
    changes = rows(load('04b-sales-chart-completed'))
    assert [r['buyer_state'] for r in changes[:5]] == top
    for r in changes:
        pair = state_change[r['buyer_state']] if r['row_type'] == 'state' else [sum(v[i] for s, v in state_change.items() if s not in top) for i in (0, 1)]
        close(r['june_item_amount_brl'], pair[0]); close(r['july_item_amount_brl'], pair[1]); close(r['change_brl'], pair[1] - pair[0])
    close(sum(Decimal(str(r['change_brl'])) for r in changes), '8107.21')
    delivery = load('09-delivery-artifacts')['workflow']
    assert delivery['status'] == 'completed' and len(delivery['steps']) == 4
    assert all(s['status'] == 'succeeded' for s in delivery['steps'])
    result = {'passed': True, 'verified': {'categories': len(category_rows), 'category_chart': 11,
        'buyer_states': len(buyers), 'sellers': len(seller_rows), 'payment_methods': len(payment_rows),
        'order_balances': 5008, 'python_decimal_reconciliation': 5008, 'sales_change_chart': 6, 'delivery_tool_steps': 4},
        'original_wrong_count': steps['recon']['rows'][0]['orders_abs_diff_gt_0_01'],
        'corrected_count': recon['orders_abs_diff_gt_0_01'], 'original_sql_now_rejected': original_blocked,
        'money_precision': 'Confirmed BRL scale 2; CAST inputs before SUM/subtraction; CSV Decimal independently checked.',
        'category_policy': '65 translated categories: missing translations and missing source categories explicitly grouped as Unknown. Original baseline retains 66 raw identities; 21.52 BRL untranslated category is included in Unknown, not lost.',
        'correction': 'Preserved first attempt. Corrected model SQL passed after teacher instruction; not a first-pass success.'}
    (OUT / 'verified-business.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    verify()
