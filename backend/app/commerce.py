"""Explicit Olist business definitions and order-grain facts; no model arithmetic."""
from __future__ import annotations

from decimal import Decimal

import duckdb
from app.metrics import METRICS, DEFINITION_VERSION

ROLE_COLUMNS = {
    'orders': {'order_id', 'customer_id', 'order_status', 'order_purchase_timestamp'},
    'order_items': {'order_id', 'order_item_id', 'product_id', 'seller_id', 'price', 'freight_value'},
    'customers': {'customer_id', 'customer_unique_id'},
    'payments': {'order_id', 'payment_value'},
    'products': {'product_id', 'product_category_name'},
    'sellers': {'seller_id', 'seller_state'},
    'reviews': {'order_id', 'review_score'},
}

RELATION_KEYS = [
    ('orders', 'customers', ['customer_id'], ['customer_id']),
    ('order_items', 'orders', ['order_id'], ['order_id']),
    ('payments', 'orders', ['order_id'], ['order_id']),
    ('reviews', 'orders', ['order_id'], ['order_id']),
    ('order_items', 'products', ['product_id'], ['product_id']),
    ('order_items', 'sellers', ['seller_id'], ['seller_id']),
]


def detect_olist(tables) -> dict[str, str]:
    roles = {}
    for role, columns in ROLE_COLUMNS.items():
        matches = [table.table_name for table in tables if columns.issubset(table.columns)]
        if len(matches) == 1:
            roles[role] = matches[0]
    return roles


def _value(value):
    return format(value.normalize(), 'f') if isinstance(value, Decimal) else value


def commerce_facts(dataset, replacements=None) -> dict:
    roles = detect_olist(dataset.files)
    result = {'profile': None, 'currency': 'BRL', 'period': [], 'facts': [], 'warnings': [], 'roles': roles,
              'scope': '当前导入数据，按 Olist 已送达订单口径；不代表全平台或会计收入。'}
    if not {'orders', 'order_items', 'customers'}.issubset(roles):
        result['warnings'].append('尚未识别完整 Olist 订单、明细和客户结构；可继续通用分析，不套用电商金额口径。')
        return result
    result['profile'] = 'olist_v1'
    replacements = replacements or {}
    with duckdb.connect(':memory:', config={'threads': 1, 'memory_limit': '128MB'}) as con:
        for role, name in roles.items():
            file = next(file for file in dataset.files if file.table_name == name)
            if name in replacements:
                con.register(role, replacements[name])
            else:
                path = file.parquet_path.as_posix().replace("'", "''")
                con.execute(f"CREATE VIEW {role} AS SELECT * FROM read_parquet('{path}')")
        for role, keys in [('orders', ['order_id']), ('customers', ['customer_id']),
                           ('order_items', ['order_id', 'order_item_id'])]:
            key_sql = ','.join(keys)
            invalid = con.execute(f'SELECT count(*) FROM {role} WHERE ' + ' OR '.join(f'{key} IS NULL' for key in keys)).fetchone()[0]
            duplicates = con.execute(f'SELECT count(*) FROM (SELECT {key_sql} FROM {role} GROUP BY {key_sql} HAVING count(*)>1)').fetchone()[0]
            if invalid or duplicates:
                result['warnings'].append(f'{role} 的业务键存在缺失或重复，指标暂不计算；先确认数据粒度。')
        if result['warnings']:
            return result
        period = con.execute('SELECT min(TRY_CAST(order_purchase_timestamp AS TIMESTAMP)), max(TRY_CAST(order_purchase_timestamp AS TIMESTAMP)) FROM orders').fetchone()
        result['period'] = [value.isoformat() if value else None for value in period]
        con.execute("CREATE VIEW delivered AS SELECT * FROM orders WHERE order_status='delivered'")
        count = con.execute('SELECT count(*) FROM delivered').fetchone()[0]

        def fact(identifier, value, **extra):
            metric = METRICS[identifier]
            label, unit, definition = (metric[key] for key in ('label', 'unit', 'definition'))
            result['facts'].append({'id': identifier, 'label': label, 'value': _value(value), 'unit': unit,
                                    'definition': definition, 'filters': {'order_status': 'delivered'},
                                    'definition_version': DEFINITION_VERSION, 'grain': metric['grain'], 'date_basis': metric['date_basis'],
                                    'currency': 'BRL' if unit == 'BRL' else None, **extra})

        fact('delivered_orders', count)
        for role, column, identifier, label in [
            ('order_items', 'price', 'item_amount', '商品金额'),
            ('order_items', 'freight_value', 'freight_amount', '运费金额'),
            ('payments', 'payment_value', 'payment_amount', '支付金额'),
        ]:
            if role not in roles:
                fact(identifier, None, missing_tables=[role])
                continue
            value, missing = con.execute(f'''
                SELECT SUM(value), count(*) FILTER (WHERE value IS NULL) FROM (
                    SELECT TRY_CAST(t.{column} AS DECIMAL(24,4)) AS value
                    FROM delivered d LEFT JOIN {role} t USING(order_id)
                )
            ''').fetchone()
            if missing:
                result['warnings'].append(f'{label} 有 {missing} 条缺失、非数值或未匹配记录，完整合计未知；没有补零。')
            fact(identifier, None if missing else value if count else Decimal(0), missing_records=missing)
        unmatched = con.execute('SELECT count(*) FROM delivered d LEFT JOIN customers c USING(customer_id) WHERE c.customer_unique_id IS NULL').fetchone()[0]
        customer_count, repeats = con.execute('''SELECT count(*), count(*) FILTER (WHERE orders>1) FROM (
            SELECT customer_unique_id,count(DISTINCT order_id) AS orders FROM delivered
            JOIN customers USING(customer_id) GROUP BY customer_unique_id)''').fetchone()
        if unmatched:
            result['warnings'].append('部分订单未匹配客户，客户和复购指标不完整。')
        fact('purchasing_customers', None if unmatched else customer_count)
        fact('repeat_customers', None if unmatched else repeats)
        fact('repeat_customer_rate', None if unmatched or not customer_count else repeats/customer_count, numerator=repeats, denominator=customer_count)
        amount = next(item['value'] for item in result['facts'] if item['id'] == 'item_amount')
        fact('average_order_amount', Decimal(amount)/count if amount is not None and count else None, denominator=count)
        columns = set(next(file.columns for file in dataset.files if file.table_name == roles['orders']))
        if {'order_delivered_customer_date', 'order_estimated_delivery_date'}.issubset(columns):
            eligible, late = con.execute('''SELECT count(*) FILTER(WHERE actual IS NOT NULL AND expected IS NOT NULL),
                count(*) FILTER(WHERE actual>expected) FROM (SELECT
                TRY_CAST(order_delivered_customer_date AS DATE) actual,
                TRY_CAST(order_estimated_delivery_date AS DATE) expected FROM delivered)''').fetchone()
            fact('late_rate', late/eligible if eligible else None, numerator=late, denominator=eligible)
            if eligible != count:
                result['warnings'].append(f'{count-eligible} 笔已送达订单缺少有效日期，未进入延期率分母。')
    return result
