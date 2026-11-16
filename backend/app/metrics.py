"""Versioned reference definitions; available templates do not certify free SQL."""
from copy import deepcopy

DEFINITION_VERSION = 'commerce-definitions/2026-11-15.v4'

_DEFINITIONS = [
    dict(id='delivered_orders', label='已送达订单数', unit='单', grain='order_id', aggregation='distinct_count',
         calculation={'role':'orders','field':'order_id','op':'count_distinct'},
         required_roles=['orders'], definition='订单主键唯一的已送达订单数'),
    dict(id='item_amount', label='商品金额', unit='BRL', grain='order_id + order_item_id', aggregation='sum',
         calculation={'role':'order_items','field':'price','op':'sum'},
         required_roles=['orders', 'order_items'], definition='已送达订单明细 price 独立求和，不含运费、支付或退款，不等于GMV或会计收入'),
    dict(id='freight_amount', label='运费金额', unit='BRL', grain='order_id + order_item_id', aggregation='sum',
         calculation={'role':'order_items','field':'freight_value','op':'sum'},
         required_roles=['orders', 'order_items'], definition='已送达订单明细 freight_value 独立求和，不与支付或评论明细相乘'),
    dict(id='payment_amount', label='支付金额', unit='BRL', grain='一笔订单的一次支付', aggregation='sum',
         calculation={'role':'payments','field':'payment_value','op':'sum'},
         required_roles=['orders', 'payments'], definition='已送达订单的 payment_value 独立求和；含支付覆盖的运费等，不等于商品金额或会计收入'),
    dict(id='purchasing_customers', label='已送达订单客户数', unit='人', grain='customer_unique_id', aggregation='distinct_count',
         required_roles=['orders', 'customers'], definition='当前观察窗口内有已送达订单的去重 customer_unique_id 数'),
    dict(id='repeat_customers', label='复购客户数', unit='人', grain='customer_unique_id', aggregation='distinct_count',
         required_roles=['orders', 'customers'], definition='当前观察窗口内至少两笔不同已送达订单的 customer_unique_id 数，不把 customer_id 当跨订单客户'),
    dict(id='repeat_customer_rate', label='复购客户占比', unit='ratio', grain='customer_unique_id', aggregation='ratio_of_counts',
         required_roles=['orders', 'customers'], customer_key='customer_unique_id',
         denominator='相同观察窗口内有已送达订单的去重客户数',
         definition='同一观察窗口内复购客户数 / 有已送达订单的客户数；不是指定首购群的定期留存或累计复购率'),
    dict(id='average_order_amount', label='客单商品金额', unit='BRL', grain='order_id', aggregation='ratio_of_sums',
         required_roles=['orders', 'order_items'], denominator='同状态、同期间和同筛选条件的去重订单数',
         definition='商品金额 / 已送达订单数，不含运费；合并期间必须用总金额除以总订单数，不能平均各月客单价'),
    dict(id='late_rate', label='延期率', unit='ratio', grain='order_id', aggregation='ratio_of_counts',
         required_roles=['orders'], date_comparison='calendar_date',
         required_columns={'orders': ['order_delivered_customer_date', 'order_estimated_delivery_date']},
         denominator='实际与预计送达日期均有效的已送达订单数',
         definition='按自然日比较实际与预计送达日期；同日送达不算延期。延期订单 / 两个日期均有效的已送达订单'),
    dict(id='refund_amount', label='期间已结算退款额', unit='source_currency', grain='唯一退款流水', aggregation='sum',
         required_roles=['refunds'], date_basis='退款实际结算发生日', profile='requires_mapping',
         requires_explicit_metric_id=True,
         definition='本期间退款成功且已结算的真实退款流水金额，不把申请中退款计入；不按原订单日期归属，不得用取消状态、支付差额或订单金额近似退款'),
    dict(id='net_cash_receipts', label='期间收付净额', unit='source_currency', grain='唯一收付流水', aggregation='difference_of_sums',
         required_roles=['payments', 'refunds'], date_basis='各笔支付或退款实际结算发生日', profile='requires_mapping',
         definition='本期间支付成功入账金额减本期间已结算退款金额；可包含前期订单退款，不称为会计收入'),
    dict(id='order_net_amount', label='订单归属净支付额', unit='source_currency', grain='order_id', aggregation='difference_of_sums',
         required_roles=['orders', 'payments', 'refunds'], date_basis='原订单日期，退款截至明确的观察截止日', profile='requires_mapping',
         requires_policy_confirmation=True,
         definition='选定订单群的已结算支付减其截至观察截止日的已结算退款；必须明确截止日，不与期间收付净额混用'),
    dict(id='profit', label='利润', unit='source_currency', grain='需确认核算粒度', aggregation='requires_policy',
         required_roles=['revenue','costs'], profile='requires_mapping', requires_policy_confirmation=True,
         definition='先确认收入、成本、运费、平台佣金、优惠及税费的范围与分摊；缺少实际成本不能补零计算利润'),
]

METRICS = {item['id']: {'version': DEFINITION_VERSION, 'profile': 'olist_v1',
                      'date_basis': 'order_purchase_timestamp；月份按下单日期归属',
                      'filters': {'order_status': 'delivered'}, 'missing_policy': 'unknown',
                      'zero_denominator': 'unknown', **item} for item in _DEFINITIONS}
for _metric in METRICS.values():
    if _metric['profile'] != 'olist_v1':
        _metric['filters'] = {'settlement_status': '需映射并确认，不假定源字段值'}


def reference_catalog():
    return deepcopy(list(METRICS.values()))


def metric_context(dataset):
    from app.commerce import detect_olist
    from app.tools.relationships import commerce_confirmed
    roles = detect_olist(dataset.files) if dataset else {}
    confirmed = bool(dataset and dataset.files and commerce_confirmed(dataset))
    columns = {role: set(next(file.columns for file in dataset.files if file.table_name == name)) for role, name in roles.items()}
    definitions = reference_catalog()
    for metric in definitions:
        metric['unmapped_roles'] = sorted(set(metric['required_roles']) - roles.keys())
        metric['missing_bound_columns'] = [f'{role}.{column}' for role, names in metric.get('required_columns', {}).items()
                                          for column in names if column not in columns.get(role, set())]
        metric['template_active'] = confirmed and metric['profile'] == 'olist_v1' and not metric['unmapped_roles'] and not metric['missing_bound_columns']
    return {'version': DEFINITION_VERSION, 'profile': 'olist_v1' if roles else 'unmapped', 'confirmed': confirmed,
            'currency': 'BRL' if confirmed else None, 'definitions': definitions,
            'validation': '参照定义及模板可用性，不证明本次自由SQL采用了全部条件；未映射不等于源数据中没有该字段。',
            'ambiguities': {'销售额': ['商品金额', '支付金额', '退款后净额'],
                            'GMV': ['下单商品金额', '支付商品金额，是否含运费和优惠需明确'],
                            '净额': ['按资金发生日的期间收付净额', '按原订单归属的净支付额']}}
