"""Prepare a reproducible customer-complete Olist subset, without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

SOURCE_URL = 'https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce'
SOURCE_FILES = {
    'orders': 'olist_orders_dataset.csv',
    'customers': 'olist_customers_dataset.csv',
    'order_items': 'olist_order_items_dataset.csv',
    'payments': 'olist_order_payments_dataset.csv',
    'reviews': 'olist_order_reviews_dataset.csv',
    'products': 'olist_products_dataset.csv',
    'sellers': 'olist_sellers_dataset.csv',
    'category_translation': 'product_category_name_translation.csv',
}


def download_source(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive = raw_dir / 'olist-download.zip'
    request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': 'DataPilot/non-commercial-demo'})
    with urllib.request.urlopen(request, timeout=180) as response, archive.open('wb') as target:
        shutil.copyfileobj(response, target)
    # Only explicitly named source files are extracted; archive paths are never used as destinations.
    with zipfile.ZipFile(archive) as bundle:
        for filename in SOURCE_FILES.values():
            with bundle.open(filename) as source, (raw_dir / filename).open('wb') as target:
                shutil.copyfileobj(source, target)


def reference_metrics(tables: dict[str, pd.DataFrame]) -> dict:
    orders = tables['orders']
    delivered = orders[orders.order_status == 'delivered']
    ids = set(delivered.order_id)
    items = tables['order_items'][tables['order_items'].order_id.isin(ids)]
    payments = tables['payments'][tables['payments'].order_id.isin(ids)]
    customer_keys = tables['customers'].set_index('customer_id').customer_unique_id.to_dict()
    customer_orders = delivered.assign(unique=delivered.customer_id.map(customer_keys)).groupby('unique').order_id.nunique()
    total = lambda values: str(sum((Decimal(str(value)) for value in values if str(value) != ''), Decimal(0)))
    return {'delivered_orders': int(delivered.order_id.nunique()),
            'item_amount': total(items.price), 'freight_amount': total(items.freight_value),
            'payment_amount': total(payments.payment_value),
            'purchasing_customers': int(len(customer_orders)),
            'repeat_customers': int((customer_orders > 1).sum())}


def prepare_demo(raw_dir: Path, output_dir: Path, customer_limit: int = 5000) -> dict:
    if not 1 <= customer_limit <= 5000:
        raise ValueError('演示客户数应在 1—5000 之间。')
    tables = {name: pd.read_csv(raw_dir / filename, dtype=str, keep_default_na=False)
              for name, filename in SOURCE_FILES.items()}
    customers = tables['customers']
    unique = sorted(set(customers.customer_unique_id) - {''},
                    key=lambda value: hashlib.sha256(('datapilot-olist-v1:' + value).encode()).hexdigest())[:customer_limit]
    tables['customers'] = customers[customers.customer_unique_id.isin(unique)]
    tables['orders'] = tables['orders'][tables['orders'].customer_id.isin(tables['customers'].customer_id)]
    for name in ['order_items', 'payments', 'reviews']:
        tables[name] = tables[name][tables[name].order_id.isin(tables['orders'].order_id)]
    tables['products'] = tables['products'][tables['products'].product_id.isin(tables['order_items'].product_id)]
    tables['sellers'] = tables['sellers'][tables['sellers'].seller_id.isin(tables['order_items'].seller_id)]
    tables['category_translation'] = tables['category_translation'][
        tables['category_translation'].product_category_name.isin(tables['products'].product_category_name)]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {'profile': 'olist_v1', 'source': 'https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce',
                'license': 'CC BY-NC-SA 4.0', 'currency': 'BRL', 'sample': True,
                'selection': 'sha256(datapilot-olist-v1:customer_unique_id); complete selected customer histories',
                'selected_customers': len(unique), 'generated_at': datetime.now(timezone.utc).isoformat(),
                'period': [tables['orders'].order_purchase_timestamp.min(), tables['orders'].order_purchase_timestamp.max()],
                'tables': {}, 'metrics': reference_metrics(tables)}
    for name, frame in tables.items():
        data = frame.to_csv(index=False, lineterminator='\n').encode('utf-8')
        if len(data) > 10 * 1024 * 1024 or len(frame) > 500_000 or len(frame.columns) > 200:
            raise ValueError(f'{name} 超过现有导入限制，未截断。')
        destination = output_dir / (name + '.csv')
        destination.write_bytes(data)
        manifest['tables'][name] = {'rows': len(frame), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                    'source_sha256': hashlib.sha256((raw_dir / SOURCE_FILES[name]).read_bytes()).hexdigest()}
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, default=Path('.local/ecommerce/raw'))
    parser.add_argument('--output-dir', type=Path, default=Path('.local/ecommerce/demo'))
    parser.add_argument('--customer-limit', type=int, default=5000)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        download_source(args.raw_dir)
    manifest = prepare_demo(args.raw_dir, args.output_dir, args.customer_limit)
    print(json.dumps({'selected_customers': manifest['selected_customers'], 'period': manifest['period'],
                      'tables': {name: item['rows'] for name, item in manifest['tables'].items()},
                      'metrics': manifest['metrics']}, ensure_ascii=False))
