"""Independent Olist analysis: CSV dictionaries and Decimal, with no Agent/SQL imports."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def analyze(directory):
    tables={path.stem:list(csv.DictReader(path.open(encoding='utf-8-sig',newline=''))) for path in directory.glob('*.csv')}
    orders={row['order_id']:row for row in tables['orders']}
    customers={row['customer_id']:row for row in tables['customers']}
    products={row['product_id']:row for row in tables['products']}
    translations={row['product_category_name']:row['product_category_name_english'] for row in tables['category_translation']}
    assert len(orders)==len(tables['orders']) and len(customers)==len(tables['customers'])
    delivered={key:row for key,row in orders.items() if row['order_status']=='delivered'}
    amount,freight,paid=defaultdict(Decimal),defaultdict(Decimal),defaultdict(Decimal)
    item_counts,payment_counts=Counter(),Counter()
    states,categories=defaultdict(lambda:defaultdict(Decimal)),defaultdict(lambda:defaultdict(Decimal))
    for row in tables['order_items']:
        oid=row['order_id'];item_counts[oid]+=1
        if oid not in delivered:continue
        assert row['price'] and row['freight_value']
        price=Decimal(row['price']);amount[oid]+=price;freight[oid]+=Decimal(row['freight_value'])
        order=orders[oid];month=order['order_purchase_timestamp'][:7]
        states[customers[order['customer_id']]['customer_state']][month]+=price
        category=products[row['product_id']]['product_category_name']
        categories[translations.get(category,category) or '未知'][month]+=price
    for row in tables['payments']:
        oid=row['order_id'];payment_counts[oid]+=1
        if oid in delivered:paid[oid]+=Decimal(row['payment_value'])
    monthly=defaultdict(lambda:{'orders':0,'item_lines':0,'item_amount':Decimal(0),'freight_amount':Decimal(0),'payment_amount':Decimal(0)})
    customer_orders=Counter();customer_months=defaultdict(set)
    for oid,order in delivered.items():
        month=order['order_purchase_timestamp'][:7];record=monthly[month]
        record['orders']+=1;record['item_lines']+=item_counts[oid]
        record['item_amount']+=amount[oid];record['freight_amount']+=freight[oid];record['payment_amount']+=paid[oid]
        customer_orders[customers[order['customer_id']]['customer_unique_id']]+=1
        customer_months[customers[order['customer_id']]['customer_unique_id']].add(month)
    retention=[];last_month=max(order['order_purchase_timestamp'][:7] for order in orders.values())
    for month in range(1,7):
        cohort=f'2018-{month:02d}'
        members={customer for customer,months in customer_months.items() if min(months)==cohort}
        for offset in range(4):
            observed=f'{2018+(month+offset-1)//12}-{(month+offset-1)%12+1:02d}'
            rate=None if observed>=last_month or not members else Decimal(sum(observed in customer_months[customer] for customer in members))/len(members)
            retention.append({'cohort':cohort,'month_offset':offset,'retention_rate':rate})
    before,after=monthly['2018-05'],monthly['2018-06']
    q0,q1=Decimal(before['orders']),Decimal(after['orders'])
    p0,p1=before['item_amount']/q0,after['item_amount']/q1
    change=after['item_amount']-before['item_amount']
    quantity=(q1-q0)*(p0+p1)/2
    order_value=(p1-p0)*(q0+q1)/2
    assert abs(quantity+order_value-change)<Decimal('0.000000001')
    def breakdown(groups):
        rows=[{'name':name,'may':values['2018-05'],'june':values['2018-06'],
               'change':values['2018-06']-values['2018-05']} for name,values in groups.items()
              if '2018-05' in values or '2018-06' in values]
        rows.sort(key=lambda row:(-abs(row['change']),row['name']))
        assert sum(row['change'] for row in rows)==change
        return {'all':rows,'top5':rows[:5],'other_change':sum(row['change'] for row in rows[5:])}
    state_change=breakdown(states)
    ce_june=sorted([{'order_id':oid,'amount':amount[oid]} for oid,order in delivered.items()
                    if order['order_purchase_timestamp'].startswith('2018-06') and customers[order['customer_id']]['customer_state']=='CE'],key=lambda row:-row['amount'])
    total=sum(amount.values());payment_total=sum(paid.values())
    naive_item=sum(value*payment_counts[oid] for oid,value in amount.items())
    naive_payment=sum(value*item_counts[oid] for oid,value in paid.items())
    return {'source':{'rows':{name:len(rows) for name,rows in tables.items()},
        'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.glob('*.csv')},
        'population':'公开Olist 5000客户完整历史子集；非实时、非全平台总体','currency':'BRL','amount_definition':'已送达订单明细price，不含运费'},
        'delivered_orders':len(delivered),'purchasing_customers':len(customer_orders),
        'repeat_customers':sum(count>=2 for count in customer_orders.values()),
        'repeat_rate':Decimal(sum(count>=2 for count in customer_orders.values()))/len(customer_orders),
        'delivered_without_items':sum(oid not in item_counts for oid in delivered),
        'delivered_without_payment':sum(oid not in payment_counts for oid in delivered),
        'monthly':dict(sorted(monthly.items())),'retention':retention,'total_item_amount':total,'total_payment_amount':payment_total,
        'change':{'before':before,'after':after,'amount_change':change,'amount_change_rate':change/before['item_amount'],
                  'aov_before':p0,'aov_after':p1,'aov_change_rate':p1/p0-1,
                  'quantity_effect':quantity,'order_value_effect':order_value,'reconciliation_error':quantity+order_value-change},
        'state_change':state_change,'category_change':breakdown(categories),'ce_june_orders':ce_june,
        'naive_join':{'item_amount':naive_item,'item_inflation':naive_item-total,'payment_amount':naive_payment,
                      'payment_inflation':naive_payment-payment_total}}


def main():
    result=analyze(ROOT/'.local/ecommerce/demo')
    manifest=json.loads((ROOT/'.local/ecommerce/demo/manifest.json').read_text(encoding='utf-8'))
    assert all(result['source']['sha256'][name+'.csv']==item['sha256'] for name,item in manifest['tables'].items())
    output=ROOT/'output/qa-2026-11-10';output.mkdir(parents=True,exist_ok=True)
    (output/'independent-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    movement=result['change'];states=result['state_change'];fmt=lambda value:f'{value:,.2f}'
    lines=['# 独立电商分析与 Agent 对照依据','',
        '直接读取原始CSV，用字典、计数器和Decimal计算；计算过程不导入项目Agent、Agent SQL或评分器。',
        '范围：公开Olist 5000客户历史子集；按下单月归属，已送达订单，商品金额不含运费，单位BRL。样本内发现不能外推为全平台变化。','',
        f"2018年6月商品金额从5月的{fmt(movement['before']['item_amount'])}降至{fmt(movement['after']['item_amount'])}，减少{fmt(-movement['amount_change'])}（{movement['amount_change_rate']:.2%}）。",
        f"订单数从{movement['before']['orders']}降至{movement['after']['orders']}；每单商品金额却从{fmt(movement['aov_before'])}升至{fmt(movement['aov_after'])}（{movement['aov_change_rate']:.2%}）。",
        f"用对称量价分解：订单数量效应{fmt(movement['quantity_effect'])}，每单商品金额效应{fmt(movement['order_value_effect'])}，两者加总为{fmt(movement['amount_change'])}；按分核对差额为0.00BRL。这解释金额变动的算术构成，不证明订单量下降的业务原因。",'',
        '| 州 | 5月商品金额 | 6月商品金额 | 差额 |','| --- | ---: | ---: | ---: |']
    lines += [f"| {row['name']} | {fmt(row['may'])} | {fmt(row['june'])} | {fmt(row['change'])} |" for row in states['top5']]
    lines += [f"| 其余州合计 | — | — | {fmt(states['other_change'])} |",'',
        f"CE的6月金额只来自{len(result['ce_june_orders'])}笔订单，不能把少量高金额订单描述为稳定增长；应回看订单分布。",
        f"全观察期有已送达订单的客户{result['purchasing_customers']}人，其中复购客户{result['repeat_customers']}人，占比{result['repeat_rate']:.4%}。跨订单按customer_unique_id识别。",
        f"将商品明细与支付明细直接关联，商品金额会虚增{fmt(result['naive_join']['item_inflation'])}，支付金额会虚增{fmt(result['naive_join']['payment_inflation'])}。必须先分别聚合。",'',
        '建议优先核对订单数量下降在SP州的贡献，以及CE大单结构；当前数据缺少流量、成本、实际退款账和营销活动，不能据此认定流量下降、降价、营销失败或利润下降。',
        '独立金额、流向、客户、分解明细和原始文件哈希保存在 independent-analysis.json。']
    (output/'独立电商分析.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ['delivered_orders','purchasing_customers','repeat_customers','total_item_amount','change','naive_join']},ensure_ascii=False,default=str))


if __name__=='__main__':
    main()
