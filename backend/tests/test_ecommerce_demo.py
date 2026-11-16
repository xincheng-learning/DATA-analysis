import importlib.util
import json
from pathlib import Path

import pandas as pd


def load_preparer():
    path = Path(__file__).parents[2] / 'scripts' / 'prepare_ecommerce_demo.py'
    spec = importlib.util.spec_from_file_location('prepare_ecommerce_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_keeps_customer_history_and_all_related_rows(tmp_path):
    module = load_preparer()
    raw, output = tmp_path / 'raw', tmp_path / 'demo'
    raw.mkdir()
    frames = {
        'customers': pd.DataFrame({'customer_id': ['c1', 'c2', 'c3'], 'customer_unique_id': ['u1', 'u1', 'u2']}),
        'orders': pd.DataFrame({'order_id': ['o1', 'o2', 'o3'], 'customer_id': ['c1', 'c2', 'c3'], 'order_status': ['delivered'] * 3, 'order_purchase_timestamp': ['2018-01-01', '2018-02-01', '2018-03-01']}),
        'order_items': pd.DataFrame({'order_id': ['o1', 'o1', 'o2', 'o3'], 'order_item_id': ['1', '2', '1', '1'], 'product_id': ['p1'] * 4, 'seller_id': ['s1'] * 4, 'price': ['30', '70', '50', '10'], 'freight_value': ['1'] * 4}),
        'payments': pd.DataFrame({'order_id': ['o1', 'o1', 'o2', 'o3'], 'payment_value': ['40', '62', '51', '11']}),
        'reviews': pd.DataFrame({'order_id': ['o1', 'o2', 'o3'], 'review_score': [5, 4, 3]}),
        'products': pd.DataFrame({'product_id': ['p1'], 'product_category_name': ['books']}),
        'sellers': pd.DataFrame({'seller_id': ['s1']}),
        'category_translation': pd.DataFrame({'product_category_name': ['books'], 'product_category_name_english': ['books']}),
    }
    for name, frame in frames.items():
        frame.to_csv(raw / module.SOURCE_FILES[name], index=False)
    manifest = module.prepare_demo(raw, output, customer_limit=2)
    assert manifest['metrics']['delivered_orders'] == 3
    assert manifest['metrics']['item_amount'] == '160'
    assert manifest['metrics']['payment_amount'] == '164'
    assert manifest['metrics']['repeat_customers'] == 1
    assert manifest['currency'] == 'BRL'
    first_hashes = {name: value['sha256'] for name, value in manifest['tables'].items()}
    second = module.prepare_demo(raw, output, customer_limit=2)
    assert first_hashes == {name: value['sha256'] for name, value in second['tables'].items()}
    assert json.loads((output / 'manifest.json').read_text(encoding='utf-8'))['sample'] is True
    one = module.prepare_demo(raw, tmp_path / 'one', customer_limit=1)
    selected = pd.read_csv(tmp_path / 'one' / 'customers.csv')
    orders = pd.read_csv(tmp_path / 'one' / 'orders.csv')
    assert set(orders.customer_id) == set(selected.customer_id)
    assert len(orders) == (2 if selected.customer_unique_id.iloc[0] == 'u1' else 1)
    assert one['selected_customers'] == 1
