"""Original synthetic semantic canaries; teachers do not call product calculations."""
from collections import Counter
from decimal import Decimal
import statistics

import duckdb
import pandas as pd

from evals.gold_dataset import compare_rows

VERSION = '1.0.0'
JAN = "status='completed' AND CAST(ordered_at AS TIMESTAMP)>=TIMESTAMP '2026-01-01' AND CAST(ordered_at AS TIMESTAMP)<TIMESTAMP '2026-02-01'"
BASE = f"WITH per_order AS (SELECT o.order_id,o.customer_id,o.region,SUM(l.amount) AS amount FROM orders o JOIN lines l USING(order_id) WHERE {JAN} GROUP BY ALL) "
RULES = ('合成数据；orders一行一笔订单，lines一行一条明细，amount是该条明细金额。只按completed计算；'
         '一月为2026-01-01含至2026-02-01不含。targets按month+region唯一；缺失目标保持NULL，目标为0的达成率保持NULL。'
         '退款仅settled。复购率分母为同月有已完成订单的去重客户。observations中0是有效观测，NULL是未知，重复值是不同观测。'
         '保留完整数值精度，不清洗原表。')


def profiles():
    variants = {}
    for variant, scale in enumerate((1, 3, 7), 1):
        variants[f'variant_{variant}'] = {
            'orders': [dict(order_id=key, customer_id=buyer, region=region, status=status, ordered_at=when)
                for key, buyer, region, status, when in [
                    ('o1', 'A', '华东', 'completed', '2026-01-01 00:00:00'),
                    ('o2', 'A', '华东', 'completed', '2026-01-31 23:59:59'),
                    ('o3', 'B', '华北', 'completed', '2026-01-15 12:00:00'),
                    ('o4', 'C', '华东', 'cancelled', '2026-01-10 00:00:00'),
                    ('o5', 'D', '华东', 'completed', '2026-02-01 00:00:00')]],
            'lines': [dict(line_id=f'l{i}', order_id=key, amount=amount) for i, (key, amount) in enumerate([
                ('o1', 10 * scale), ('o1', 10 * scale), ('o2', 40 * scale + variant),
                ('o3', 30 * scale + 2 * variant), ('o4', 999 * scale), ('o5', 100 * scale + variant)], 1)],
            'targets': [{'month': '2026-01', 'region': '华东', 'target': 100 * scale + variant},
                        {'month': '2026-02', 'region': '华东', 'target': 0}],
            'refunds': [{'refund_id': f'r{i}', 'status': status, 'amount': value * scale}
                        for i, (status, value) in enumerate([('settled', 3), ('pending', 50), ('settled', 2)], 1)],
            'observations': [{'record_id': i, 'value': value, 'tiny': tiny}
                for i, (value, tiny) in enumerate(zip([0, None, 2 * scale, 2 * scale, 5 * scale],
                    [scale * 1e-12, scale * 2e-12, None, scale * 2e-12, scale * 5e-12]), 1)],
        }
    return variants


def build_cases():
    cases = []
    for profile, tables in profiles().items():
        # Independent teacher arithmetic over original records, not SQL/product output.
        orders = [o for o in tables['orders'] if o['status'] == 'completed' and o['ordered_at'][:7] == '2026-01']
        totals = {o['order_id']: sum(Decimal(str(l['amount'])) for l in tables['lines'] if l['order_id'] == o['order_id']) for o in orders}
        by_region = {region: sum(totals[o['order_id']] for o in orders if o['region'] == region) for region in ('华东', '华北')}
        target = tables['targets'][0]['target']
        customers = Counter(o['customer_id'] for o in orders)
        values = [r['value'] for r in tables['observations'] if r['value'] is not None]
        tiny = [r['tiny'] for r in tables['observations'] if r['tiny'] is not None]

        def add(family, question, sql, rows, wrong, *, routes=('sql',), columns=None, ordered=False, comparison=None):
            columns = columns or list(rows[0])
            cases.append({'id': f'RG{len(cases)+1:03}', 'family': family, 'profile': profile,
                'split': 'development', 'direction': family, 'business_rules': RULES, 'mode': 'auto',
                'route_scored': True, 'failure_trap': family,
                'turns': [{'question': question, 'allowed_routes': list(routes), 'oracle_sql': sql,
                    'expected_rows': rows, 'expected_columns': columns,
                    'comparison': comparison or {'ordered': ordered, 'abs_tol': 1e-9, 'rel_tol': 1e-9}}],
                'wrong_sql': wrong})

        add('join_target', '列出一月各地区商品金额和目标。输出region、amount、target，缺目标保留NULL。',
            BASE + "SELECT p.region,SUM(p.amount) AS amount,MAX(t.target) AS target FROM per_order p LEFT JOIN targets t ON p.region=t.region AND t.month='2026-01' GROUP BY p.region",
            [{'region': r, 'amount': float(by_region[r]), 'target': target if r == '华东' else None} for r in by_region],
            BASE + "SELECT p.region,SUM(p.amount) AS amount,SUM(t.target) AS target FROM per_order p LEFT JOIN targets t ON p.region=t.region AND t.month='2026-01' GROUP BY p.region")
        add('order_grain', '计算一月已完成的订单数，输出order_count；一笔订单多明细仍只计一次。',
            BASE + 'SELECT COUNT(*) AS order_count FROM per_order', [{'order_count': len(orders)}],
            f"SELECT COUNT(*) AS order_count FROM orders o JOIN lines l USING(order_id) WHERE {JAN}")
        add('order_denominator', '计算一月每笔已完成订单的平均商品金额，输出average_amount。',
            BASE + 'SELECT AVG(amount) AS average_amount FROM per_order',
            [{'average_amount': float(sum(totals.values()) / len(totals))}],
            f'SELECT AVG(l.amount) AS average_amount FROM orders o JOIN lines l USING(order_id) WHERE {JAN}')
        add('missing_target', '列出一月华北目标target；缺失保留NULL，仍返回一行。',
            "SELECT (SELECT target FROM targets WHERE month='2026-01' AND region='华北') AS target", [{'target': None}],
            "SELECT COALESCE((SELECT target FROM targets WHERE month='2026-01' AND region='华北'),0) AS target")
        add('zero_denominator', '二月华东目标为0时达成率为NULL。只返回attainment_pct一列一行。',
            "SELECT CASE WHEN target=0 THEN NULL ELSE 100.0/target END AS attainment_pct FROM targets WHERE month='2026-02' AND region='华东'", [{'attainment_pct': None}],
            "SELECT CASE WHEN target=0 THEN 0 ELSE 100.0/target END AS attainment_pct FROM targets WHERE month='2026-02' AND region='华东'")
        add('refund_status', '仅计算已结算退款总额，输出refund_amount。',
            "SELECT SUM(amount) AS refund_amount FROM refunds WHERE status='settled'",
            [{'refund_amount': sum(r['amount'] for r in tables['refunds'] if r['status'] == 'settled')}],
            'SELECT SUM(amount) AS refund_amount FROM refunds')
        add('repeat_denominator', '计算一月复购客户占比百分数，输出repeat_pct；分母为该月有已完成订单的客户。',
            BASE + 'SELECT 100.0*SUM(CASE WHEN n>=2 THEN 1 ELSE 0 END)/COUNT(*) AS repeat_pct FROM (SELECT customer_id,COUNT(*) AS n FROM per_order GROUP BY customer_id)',
            [{'repeat_pct': 100 * sum(n >= 2 for n in customers.values()) / len(customers)}],
            "SELECT 100.0*SUM(CASE WHEN n>=2 THEN 1 ELSE 0 END)/COUNT(*) AS repeat_pct FROM (SELECT customer_id,COUNT(*) AS n FROM orders WHERE ordered_at LIKE '2026-01%' GROUP BY customer_id)")
        add('month_endpoint', '一月已完成订单商品金额合计amount，包含1月31日全天，不含2月1日。',
            BASE + 'SELECT SUM(amount) AS amount FROM per_order', [{'amount': float(sum(totals.values()))}],
            "SELECT SUM(l.amount) AS amount FROM orders o JOIN lines l USING(order_id) WHERE status='completed' AND CAST(ordered_at AS TIMESTAMP) BETWEEN TIMESTAMP '2026-01-01' AND TIMESTAMP '2026-01-31'")
        add('null_mean', 'observations.value的均值mean；NULL不当0，0参与，不删除原记录。',
            'SELECT AVG(value) AS mean FROM observations', [{'mean': statistics.mean(values)}],
            'SELECT AVG(COALESCE(value,0)) AS mean FROM observations')
        add('duplicate_observation', '按record_id顺序返回每条value，重复值和NULL都保留。',
            'SELECT value FROM observations ORDER BY record_id', [{'value': r['value']} for r in tables['observations']],
            'SELECT DISTINCT value FROM observations ORDER BY value', ordered=True)
        add('legitimate_empty', '列出西区已完成订单order_id；无匹配时返回空表。',
            "SELECT order_id FROM orders WHERE region='西区' AND status='completed'", [],
            "SELECT order_id FROM orders WHERE status='completed'", columns=['order_id'])
        add('tiny_sample_statistics', '用隔离Python计算observations.tiny非空样本数n、均值mean、样本标准差std（ddof=1），只输出一行三列，保留非零小数，不舍入。',
            'SELECT COUNT(tiny) AS n,AVG(tiny) AS mean,STDDEV_SAMP(tiny) AS std FROM observations',
            [{'n': len(tiny), 'mean': statistics.mean(tiny), 'std': statistics.stdev(tiny)}],
            'SELECT COUNT(tiny) AS n,AVG(tiny) AS mean,STDDEV_POP(tiny) AS std FROM observations', routes=('python',),
            comparison={'ordered': False, 'abs_tol': 0, 'rel_tol': 1e-8})
    return cases


def connection(tables):
    con = duckdb.connect(':memory:')
    for name, rows in tables.items():
        con.register(name, pd.DataFrame(rows))
    return con


def query_rows(con, sql):
    result = con.execute(sql)
    columns = [c[0] for c in result.description]
    return columns, [dict(zip(columns, row)) for row in result.fetchall()]


def verify_references():
    cases = build_cases()
    checked = []
    for profile, tables in profiles().items():
        with connection(tables) as con:
            for case in (c for c in cases if c['profile'] == profile):
                turn = case['turns'][0]
                columns, actual = query_rows(con, turn['oracle_sql'])
                assert columns == turn['expected_columns'], case['id']
                assert compare_rows(actual, turn['expected_rows'], **turn['comparison']), case['id']
                _, wrong = query_rows(con, case['wrong_sql'])
                assert not compare_rows(wrong, turn['expected_rows'], **turn['comparison']), case['id']
                checked.append({'case': case['id'], 'family': case['family'], 'profile': profile,
                                'teacher_matches_sql': True, 'wrong_sql_executed': True, 'wrong_answer_rejected': True})
    return checked
