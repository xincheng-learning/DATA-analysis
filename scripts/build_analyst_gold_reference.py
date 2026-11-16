"""Independent teacher reference: original CSV + Decimal, cross-checked with DuckDB.

Run with the existing ml-pytorch Conda interpreter. No Agent imports or model calls.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / '.venv/Lib/site-packages'))  # Reuse the project's DuckDB; keep Conda statistics/plotting packages first.
import duckdb
import numpy as np
from scipy.stats import fisher_exact, norm
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from analyze_olist_business import calculate

SOURCE = ROOT / '.local/ecommerce/demo'
GOLD = ROOT / 'evals/analyst_gold_v3_draft'
OUT = ROOT / 'output/analyst-gold-v3-review-2026-11-11'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda item:
        str(item) if isinstance(item, Decimal) else item.isoformat() if hasattr(item, 'isoformat') else item.item(), allow_nan=False), encoding='utf-8')


def wilson(success, total):
    if not total:
        return [None, None]
    z = norm.ppf(.975); rate = success / total
    center = (rate + z*z/(2*total)) / (1+z*z/total)
    half = z * math.sqrt(rate*(1-rate)/total+z*z/(4*total*total)) / (1+z*z/total)
    return [center-half, center+half]


def build():
    manifest = json.loads((SOURCE/'manifest.json').read_text(encoding='utf-8'))
    tables = {}
    for name, expected in manifest['tables'].items():
        path = SOURCE / (name+'.csv')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256'], name
        with path.open(encoding='utf-8-sig', newline='') as handle:
            tables[name] = list(csv.DictReader(handle))
        assert len(tables[name]) == expected['rows']
    GOLD.mkdir(parents=True, exist_ok=True)
    (OUT/'data').mkdir(parents=True, exist_ok=True)
    (OUT/'sql').mkdir(parents=True, exist_ok=True)
    baseline = calculate(SOURCE)
    con = duckdb.connect()
    con.execute("SET threads=1")
    for name in tables:
        con.execute(f"CREATE TABLE raw_{name} AS SELECT row_number() OVER () AS source_row,* FROM read_csv_auto(?,all_varchar=true,nullstr='')", [str(SOURCE/(name+'.csv'))])
    queries = {}
    def query(name, sql):
        queries[name] = sql
        (OUT/'sql'/f'{name}.sql').write_text(sql.strip()+'\n', encoding='utf-8')
        cursor = con.execute(sql)
        columns = [item[0] for item in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        write_json(OUT/'data'/f'{name}.json', {'columns':columns, 'rows':rows})
        with (OUT/'data'/f'{name}.csv').open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader();writer.writerows(rows)
        return rows
    setup = """
CREATE TABLE item_agg AS SELECT order_id, SUM(TRY_CAST(price AS DECIMAL(18,2))) AS item_amount,
 SUM(TRY_CAST(freight_value AS DECIMAL(18,2))) AS freight_amount,COUNT(*) AS item_lines,
 COUNT(price) AS price_known FROM raw_order_items GROUP BY order_id;
CREATE TABLE payment_agg AS SELECT order_id,SUM(TRY_CAST(payment_value AS DECIMAL(18,2))) AS payment_amount,
 COUNT(*) AS payment_rows FROM raw_payments GROUP BY order_id;
CREATE TABLE latest_review AS SELECT * EXCLUDE(rn) FROM
 (SELECT *,row_number() OVER(PARTITION BY order_id ORDER BY TRY_CAST(review_answer_timestamp AS TIMESTAMP) DESC NULLS LAST,
 TRY_CAST(review_creation_date AS TIMESTAMP) DESC NULLS LAST,review_id DESC NULLS LAST,source_row ASC) AS rn FROM raw_reviews) WHERE rn=1;
CREATE TABLE facts AS SELECT o.order_id,o.order_status,c.customer_unique_id,c.customer_state AS buyer_state,
 TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP) AS purchased_at,
 TRY_CAST(o.order_approved_at AS TIMESTAMP) AS approved_at,
 TRY_CAST(o.order_delivered_carrier_date AS TIMESTAMP) AS carrier_at,
 TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) AS delivered_at,
 TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS estimated_at,
 i.item_amount,i.freight_amount,i.item_lines,i.price_known,p.payment_amount,p.payment_rows,
 TRY_CAST(r.review_score AS INTEGER) AS review_score,
 CASE WHEN o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL
 THEN TRY_CAST(o.order_delivered_customer_date AS DATE)>TRY_CAST(o.order_estimated_delivery_date AS DATE) END AS late,
 date_diff('second',TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP),TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP))/86400.0 AS delivery_days
 FROM raw_orders o LEFT JOIN raw_customers c ON o.customer_id=c.customer_id
 LEFT JOIN item_agg i ON o.order_id=i.order_id LEFT JOIN payment_agg p ON o.order_id=p.order_id
 LEFT JOIN latest_review r ON o.order_id=r.order_id;
CREATE VIEW delivered AS SELECT * FROM facts WHERE order_status='delivered';
"""
    con.execute(setup);(OUT/'sql'/'00-reference-views.sql').write_text(setup,encoding='utf-8')
    type_checks=[]
    numeric={'price','freight_value','payment_value','order_item_id','payment_sequential','payment_installments','review_score',
        'product_name_lenght','product_description_lenght','product_photos_qty','product_weight_g','product_length_cm','product_height_cm','product_width_cm'}
    for name,rows in tables.items():
        for column in rows[0]:
            is_date=column.endswith(('_date','_timestamp','_at'))
            if not is_date and column not in numeric:continue
            values=[row[column] for row in rows if row[column]]
            invalid=[]
            for value in values:
                try:
                    if is_date:datetime.fromisoformat(value)
                    else:assert Decimal(value).is_finite()
                except (ValueError,ArithmeticError,AssertionError):invalid.append(value)
            assert not invalid,(name,column,invalid[:3])
            type_checks.append({'table':name,'column':column,'nonempty':len(values),'invalid':len(invalid)})
    key_checks=[]
    for name,columns in {'orders':['order_id'],'customers':['customer_id'],'products':['product_id'],'sellers':['seller_id'],
        'category_translation':['product_category_name'],'order_items':['order_id','order_item_id'],'payments':['order_id','payment_sequential']}.items():
        duplicate=len(tables[name])-len({tuple(row[c] for c in columns) for row in tables[name]})
        assert duplicate==0,(name,columns)
        key_checks.append({'table':name,'columns':columns,'duplicate_extras':duplicate})
    chronology=query('19-chronology-flags',"""SELECT order_id,order_status,purchased_at,approved_at,carrier_at,delivered_at,'carrier_before_purchase' AS rule
 FROM facts WHERE carrier_at<purchased_at UNION ALL
 SELECT order_id,order_status,purchased_at,approved_at,carrier_at,delivered_at,'carrier_before_approval' AS rule
 FROM facts WHERE carrier_at<approved_at UNION ALL
 SELECT order_id,order_status,purchased_at,approved_at,carrier_at,delivered_at,'carrier_after_delivery' AS rule
 FROM facts WHERE carrier_at>delivered_at ORDER BY rule,order_id""")
    chronology_counts=Counter(row['rule'] for row in chronology)
    for rule,a,b in [('carrier_before_purchase','order_delivered_carrier_date','order_purchase_timestamp'),
        ('carrier_before_approval','order_delivered_carrier_date','order_approved_at'),
        ('carrier_after_delivery','order_delivered_customer_date','order_delivered_carrier_date')]:
        independent=sum(bool(row[a] and row[b]) and datetime.fromisoformat(row[a])<datetime.fromisoformat(row[b]) for row in tables['orders'])
        assert chronology_counts[rule]==independent,(rule,chronology_counts[rule],independent)
    quality_controls={'types':type_checks,'keys':key_checks,'chronology_counts':dict(chronology_counts),
        'chronology_unique_orders':len({row['order_id'] for row in chronology}),
        'chronology_note':'规则可重叠，不累加成异常订单总数；保留原值，阶段耗时另定有效范围。'}
    write_json(OUT/'data/20-quality-controls.json',quality_controls)
    profile=[]
    for name, rows in tables.items():
        fields=[]
        for column in rows[0]:
            missing=sum(row[column]=='' for row in rows)
            example=next((row[column] for row in rows if row[column]!=''),None)
            fields.append({'column':column,'missing':missing,'missing_pct':missing/len(rows)*100,'example':example})
        profile.append({'table':name,'rows':len(rows),'fields':fields,
            'exact_duplicate_extras':len(rows)-len({tuple(row.items()) for row in rows})})
    relationships=[]
    for child, column, parent, target in [('orders','customer_id','customers','customer_id'),('order_items','order_id','orders','order_id'),
        ('payments','order_id','orders','order_id'),('reviews','order_id','orders','order_id'),('order_items','product_id','products','product_id'),
        ('order_items','seller_id','sellers','seller_id'),('products','product_category_name','category_translation','product_category_name')]:
        parent_keys={row[target] for row in tables[parent] if row[target]}
        relationships.append({'child':child,'column':column,'parent':parent,'target':target,
            'child_rows':len(tables[child]),'null_keys':sum(not row[column] for row in tables[child]),
            'unmatched_nonnull':sum(bool(row[column]) and row[column] not in parent_keys for row in tables[child]),
            'parent_duplicate_keys':len([row for row in tables[parent] if row[target]])-len(parent_keys)})
    checks=[]
    def check(name, actual, expected):
        assert Decimal(str(actual)) == Decimal(str(expected)), (name,actual,expected)
        checks.append({'check':name,'actual':str(actual),'expected':str(expected),'passed':True,'independent':'CSV/Decimal vs DuckDB'})
    coverage=query('01-order-coverage',"""SELECT COUNT(*) AS all_orders,COUNT(DISTINCT order_id) AS unique_orders,
 COUNT(*) FILTER(WHERE order_status='delivered') AS delivered_orders FROM facts""")
    totals=query('02-kpis',"""SELECT COUNT(*) AS delivered_orders,COUNT(DISTINCT customer_unique_id) AS customers,
 SUM(item_amount) AS item_amount,SUM(freight_amount) AS freight_amount,SUM(payment_amount) AS payment_amount,
 SUM(item_amount)/COUNT(*) AS average_order_amount,COUNT(*) FILTER(WHERE item_amount IS NULL OR price_known!=item_lines) AS unknown_item_orders,
 COUNT(*) FILTER(WHERE payment_amount IS NULL) AS unknown_payment_orders FROM delivered""")[0]
    for key, reference in [('delivered_orders','delivered_orders'),('customers','purchasing_customers'),('item_amount','total_item_amount'),
                           ('payment_amount','total_payment_amount')]:
        check(key,totals[key],baseline[reference])
    check('freight_amount',totals['freight_amount'],sum(row['freight_amount'] for row in baseline['monthly'].values()))
    assert totals['unknown_item_orders']==totals['unknown_payment_orders']==0
    monthly=query('03-monthly',"""SELECT strftime(purchased_at,'%Y-%m') AS month,COUNT(*) AS orders,
 SUM(item_amount) AS item_amount,SUM(freight_amount) AS freight_amount,SUM(payment_amount) AS payment_amount,
 SUM(item_amount)/COUNT(*) AS average_order_amount FROM delivered GROUP BY month ORDER BY month""")
    for row in monthly:
        check('month_amount/'+row['month'],row['item_amount'],baseline['monthly'][row['month']]['item_amount'])
        check('month_orders/'+row['month'],row['orders'],baseline['monthly'][row['month']]['orders'])
    months={row['month']:row for row in monthly}
    movements=[]
    for before,after in [('2018-05','2018-06'),('2018-06','2018-07')]:
        a,b=months[before],months[after];q0,q1=Decimal(a['orders']),Decimal(b['orders']);p0,p1=a['item_amount']/q0,b['item_amount']/q1
        quantity=(q1-q0)*(p0+p1)/2;value=(p1-p0)*(q0+q1)/2;change=b['item_amount']-a['item_amount']
        assert abs(quantity+value-change)<Decimal('1e-18')
        movements.append({'before':before,'after':after,'before_amount':a['item_amount'],'after_amount':b['item_amount'],
            'before_orders':int(q0),'after_orders':int(q1),'before_aov':p0,'after_aov':p1,'change':change,
            'change_rate':change/a['item_amount'],'order_change_rate':q1/q0-1,'aov_change_rate':p1/p0-1,
            'quantity_effect':quantity,'order_value_effect':value})
    state_change=query('04-state-change',"""SELECT buyer_state,
 COALESCE(SUM(item_amount) FILTER(WHERE purchased_at>='2018-06-01' AND purchased_at<'2018-07-01'),0)::DECIMAL(18,2) AS june,
 COALESCE(SUM(item_amount) FILTER(WHERE purchased_at>='2018-07-01' AND purchased_at<'2018-08-01'),0)::DECIMAL(18,2) AS july,
 july-june AS change,COUNT(*) FILTER(WHERE purchased_at>='2018-06-01' AND purchased_at<'2018-07-01') AS june_orders,
 COUNT(*) FILTER(WHERE purchased_at>='2018-07-01' AND purchased_at<'2018-08-01') AS july_orders
 FROM delivered WHERE purchased_at>='2018-06-01' AND purchased_at<'2018-08-01' GROUP BY buyer_state ORDER BY ABS(change) DESC,buyer_state""")
    check('state_change_sum',sum(row['change'] for row in state_change),movements[1]['change'])
    categories=query('05-categories',"""SELECT COALESCE(t.product_category_name_english,'未知/未译') AS category,
 SUM(TRY_CAST(i.price AS DECIMAL(18,2))) AS amount,COUNT(*) AS item_lines,COUNT(DISTINCT i.order_id) AS orders
 FROM raw_order_items i JOIN delivered d ON i.order_id=d.order_id LEFT JOIN raw_products p ON i.product_id=p.product_id
 LEFT JOIN raw_category_translation t ON p.product_category_name=t.product_category_name GROUP BY category ORDER BY amount DESC,category""")
    check('category_sum',sum(row['amount'] for row in categories),totals['item_amount'])
    sellers=query('06-sellers',"""WITH membership AS(SELECT DISTINCT i.seller_id,i.order_id FROM raw_order_items i JOIN delivered d ON i.order_id=d.order_id),
 performance AS(SELECT m.seller_id,COUNT(*) AS orders,COUNT(d.late) AS eligible,COUNT(*) FILTER(WHERE d.late) AS late_orders,
 AVG(d.delivery_days) AS mean_delivery_days FROM membership m JOIN delivered d ON m.order_id=d.order_id GROUP BY m.seller_id)
 SELECT i.seller_id,MAX(s.seller_state) AS seller_state,SUM(TRY_CAST(i.price AS DECIMAL(18,2))) AS amount,
 COUNT(*) AS item_lines,MAX(p.orders) AS orders,MAX(p.eligible) AS eligible,MAX(p.late_orders) AS late_orders,
 MAX(p.mean_delivery_days) AS mean_delivery_days FROM raw_order_items i JOIN delivered d ON i.order_id=d.order_id
 LEFT JOIN raw_sellers s ON i.seller_id=s.seller_id JOIN performance p ON i.seller_id=p.seller_id GROUP BY i.seller_id ORDER BY amount DESC,i.seller_id""")
    check('seller_sum',sum(row['amount'] for row in sellers),totals['item_amount'])
    for row in sellers:
        row['late_rate']=row['late_orders']/row['eligible'] if row['eligible'] else None
        row['wilson']=wilson(row['late_orders'],row['eligible'])
    payments=query('07-payments',"""SELECT payment_type,SUM(TRY_CAST(p.payment_value AS DECIMAL(18,2))) AS amount,
 COUNT(*) AS payment_records,COUNT(DISTINCT p.order_id) AS orders FROM raw_payments p JOIN delivered d ON p.order_id=d.order_id
 GROUP BY payment_type ORDER BY amount DESC,payment_type""")
    check('payment_types_sum',sum(row['amount'] for row in payments),totals['payment_amount'])
    reconciliation=query('08-reconciliation',"""SELECT order_id,item_amount,freight_amount,payment_amount,
 payment_amount-item_amount-freight_amount AS difference FROM delivered ORDER BY ABS(difference) DESC,order_id""")
    material=[row for row in reconciliation if abs(row['difference'])>Decimal('.01')]
    check('material_reconciliation_orders',len(material),baseline['payment_reconciliation']['orders_over_one_cent'])
    naive=query('09-join-inflation',"""SELECT SUM(TRY_CAST(i.price AS DECIMAL(18,2))) AS naive_item,
 SUM(TRY_CAST(p.payment_value AS DECIMAL(18,2))) AS naive_payment,COUNT(*) AS joined_rows
 FROM delivered d JOIN raw_order_items i ON d.order_id=i.order_id JOIN raw_payments p ON d.order_id=p.order_id""")[0]
    check('naive_item_inflation',naive['naive_item']-totals['item_amount'],baseline['naive_join']['item_inflation'])
    check('naive_payment_inflation',naive['naive_payment']-totals['payment_amount'],baseline['naive_join']['payment_inflation'])
    missing_patterns=query('10-missing-patterns',"""SELECT order_status='delivered' AS delivered_only,
 approved_at IS NULL AS approval_missing,carrier_at IS NULL AS carrier_missing,COUNT(*) AS orders
 FROM facts GROUP BY ALL ORDER BY delivered_only,approval_missing,carrier_missing""")
    missing_scope=query('11-missing-scope',"""SELECT CASE WHEN GROUPING(order_status)=1 THEN '全部订单' ELSE order_status END AS scope,
 COUNT(*) AS orders,COUNT(*) FILTER(WHERE approved_at IS NULL) AS approval_missing,
 COUNT(*) FILTER(WHERE carrier_at IS NULL) AS carrier_missing,COUNT(*) FILTER(WHERE delivered_at IS NULL) AS delivery_missing,
 COUNT(*) FILTER(WHERE approved_at IS NOT NULL AND carrier_at IS NOT NULL) AS jointly_nonnull
 FROM facts GROUP BY GROUPING SETS((),(order_status)) ORDER BY scope""")
    reviews=query('12-review-counts',"""SELECT COUNT(*) AS raw_reviews,COUNT(DISTINCT order_id) AS reviewed_orders,
 COUNT(*)-COUNT(DISTINCT order_id) AS extra_reviews FROM raw_reviews""")[0]
    delivery=query('13-delivery-review',"""SELECT CASE WHEN late THEN '延迟' ELSE '按时' END AS delivery_group,
 COUNT(*) AS orders,COUNT(review_score) AS reviewed,COUNT(*) FILTER(WHERE review_score<=2) AS low_reviews,
 COUNT(*) FILTER(WHERE review_score IS NULL) AS unknown_reviews,AVG(delivery_days) AS mean_days,
 median(delivery_days) AS median_days,quantile_cont(delivery_days,.95) AS p95_days
 FROM delivered WHERE late IS NOT NULL GROUP BY late ORDER BY late DESC""")
    for row in delivery:
        row['low_rate']=row['low_reviews']/row['reviewed'] if row['reviewed'] else None
        row['wilson']=wilson(row['low_reviews'],row['reviewed'])
        row['missing_outcome_bounds']=[row['low_reviews']/row['orders'],(row['low_reviews']+row['unknown_reviews'])/row['orders']]
    late,ontime=delivery
    fisher=fisher_exact([[late['low_reviews'],late['reviewed']-late['low_reviews']],
                        [ontime['low_reviews'],ontime['reviewed']-ontime['low_reviews']]])
    statistics={'groups':delivery,'odds_ratio':fisher.statistic,'pvalue':fisher.pvalue,
        'risk_difference_pp':100*(late['low_rate']-ontime['low_rate']),
        'risk_ratio':late['low_rate']/ontime['low_rate'],
        'sensitivity_difference_bounds_pp':[100*(late['missing_outcome_bounds'][0]-ontime['missing_outcome_bounds'][1]),
            100*(late['missing_outcome_bounds'][1]-ontime['missing_outcome_bounds'][0])]}
    assert late['reviewed']==352 and ontime['reviewed']==4626
    check('late_low_reviews',late['low_reviews'],baseline['delivery']['late_low_late_other_ontime_low_ontime_other'][0][0])
    rf=query('14-customer-segments',"""WITH customer AS(SELECT customer_unique_id,COUNT(*) AS frequency,SUM(item_amount) AS amount,
 date_diff('day',MAX(purchased_at)::DATE,DATE '2018-08-01') AS recency_days FROM delivered WHERE purchased_at<'2018-08-01' GROUP BY customer_unique_id)
 SELECT CASE WHEN recency_days<=90 THEN '近90天活跃' ELSE '超过90天未购' END AS recency,
 CASE WHEN frequency>=2 THEN '复购' ELSE '一次购买' END AS frequency_group,COUNT(*) AS customers,SUM(frequency) AS orders,
 SUM(amount) AS amount,AVG(amount) AS average_customer_amount FROM customer GROUP BY ALL ORDER BY recency,frequency_group""")
    repeat=query('15-repeat',"""WITH customers AS(SELECT customer_unique_id,COUNT(*) AS orders FROM delivered GROUP BY customer_unique_id)
 SELECT COUNT(*) AS customers,COUNT(*) FILTER(WHERE orders>=2) AS repeat_customers FROM customers""")[0]
    check('repeat_customers',repeat['repeat_customers'],baseline['repeat_customers'])
    cohort=query('16-cohort-retention',"""WITH customer_month AS(SELECT DISTINCT customer_unique_id,date_trunc('month',purchased_at)::DATE AS month
 FROM delivered WHERE purchased_at<'2018-08-01'),first AS(SELECT customer_unique_id,MIN(month) AS cohort FROM customer_month GROUP BY customer_unique_id),
 cohorts AS(SELECT cohort,COUNT(*) AS cohort_size FROM first WHERE cohort>='2018-01-01' AND cohort<'2018-07-01' GROUP BY cohort),
 grid AS(SELECT cohort,cohort_size,offs,(cohort+offs*INTERVAL '1 month')::DATE AS observed_month FROM cohorts CROSS JOIN range(4) t(offs))
 SELECT strftime(g.cohort,'%Y-%m') AS cohort,g.cohort_size,g.offs AS month_offset,strftime(g.observed_month,'%Y-%m') AS observed_month,
 CASE WHEN g.observed_month>=DATE '2018-08-01' THEN NULL ELSE COUNT(cm.customer_unique_id) END AS retained_customers,
 CASE WHEN g.observed_month>=DATE '2018-08-01' THEN NULL ELSE COUNT(cm.customer_unique_id)::DOUBLE/g.cohort_size END AS retention_rate
 FROM grid g LEFT JOIN first f ON f.cohort=g.cohort LEFT JOIN customer_month cm ON cm.customer_unique_id=f.customer_unique_id AND cm.month=g.observed_month
 GROUP BY g.cohort,g.cohort_size,g.offs,g.observed_month ORDER BY g.cohort,g.offs""")
    assert len(cohort)==24 and all(row['retention_rate']==1 for row in cohort if row['month_offset']==0)
    model_rows=query('17-model-input',"""WITH seller AS(SELECT i.order_id,MAX(CASE WHEN s.seller_state!=d.buyer_state THEN 1 ELSE 0 END) AS cross_state
 FROM raw_order_items i JOIN delivered d ON i.order_id=d.order_id JOIN raw_sellers s ON i.seller_id=s.seller_id GROUP BY i.order_id)
 SELECT d.order_id,d.purchased_at,d.delivered_at,d.late,
 date_diff('second',d.purchased_at,d.estimated_at)/86400.0 AS promised_days,d.item_amount,d.freight_amount,d.item_lines,s.cross_state,
 CASE WHEN dayofweek(d.purchased_at) IN(0,6) THEN 1 ELSE 0 END AS weekend FROM delivered d JOIN seller s ON d.order_id=s.order_id
 WHERE d.late IS NOT NULL AND d.estimated_at IS NOT NULL""")
    features=['promised_days','item_amount','freight_amount','item_lines','cross_state','weekend']
    split=datetime(2018,7,1)
    train=[row for row in model_rows if row['purchased_at']<split and row['delivered_at']<split]
    test=[row for row in model_rows if split<=row['purchased_at']<datetime(2018,8,1)]
    excluded_future=sum(row['purchased_at']<split and row['delivered_at']>=split for row in model_rows)
    assert max(row['delivered_at'] for row in train)<split
    X=lambda rows:np.array([[float(row[key]) for key in features] for row in rows])
    y_train=np.array([int(row['late']) for row in train]);y_test=np.array([int(row['late']) for row in test])
    model=make_pipeline(StandardScaler(),LogisticRegression(solver='liblinear',random_state=42,max_iter=1000))
    model.fit(X(train),y_train);prediction=model.predict_proba(X(test))[:,1];prior=float(y_train.mean())
    calibration=[]
    for indices in np.array_split(np.argsort(prediction),5):
        calibration.append({'n':len(indices),'mean_prediction':float(prediction[indices].mean()),'observed_rate':float(y_test[indices].mean())})
    modeling={'features':features,'train_n':len(train),'test_n':len(test),'train_late':int(y_train.sum()),'test_late':int(y_test.sum()),
        'train_label_latest':max(row['delivered_at'] for row in train),'excluded_future_labels':excluded_future,
        'auc':roc_auc_score(y_test,prediction),'average_precision':average_precision_score(y_test,prediction),
        'brier':brier_score_loss(y_test,prediction),'baseline_brier':brier_score_loss(y_test,np.full(len(test),prior)),
        'baseline_train_rate':prior,'test_event_rate':float(y_test.mean()),'calibration':calibration,
        'operational_limit':'历史探索，已送达样本选择；尚无字段在下单时刻冻结的业务证明，不作为可部署模型。'}
    flows=query('18-item-flows',"""SELECT d.buyer_state,COALESCE(t.product_category_name_english,'未知/未译') AS category,s.seller_state,
 SUM(TRY_CAST(i.price AS DECIMAL(18,2))) AS amount FROM raw_order_items i JOIN delivered d ON i.order_id=d.order_id
 LEFT JOIN raw_products p ON i.product_id=p.product_id LEFT JOIN raw_category_translation t ON p.product_category_name=t.product_category_name
 LEFT JOIN raw_sellers s ON i.seller_id=s.seller_id GROUP BY ALL ORDER BY amount DESC""")
    check('flow_sum',sum(row['amount'] for row in flows),totals['item_amount'])
    artifacts={'profile':profile,'relationships':relationships,'quality_controls':quality_controls,'coverage':coverage,'kpis':totals,'monthly':monthly,'movements':movements,
        'states':state_change,'categories':categories,'sellers':sellers,'payments':payments,'reconciliation':{'orders':material,
        'net_difference':sum(row['difference'] for row in reconciliation),'threshold':'abs(difference)>0.01 BRL, exact Decimal'},
        'naive':naive,'missing_patterns':missing_patterns,'missing_scope':missing_scope,'review_counts':reviews,
        'statistics':statistics,'segments':rf,'repeat':repeat,'cohort':cohort,'model':modeling,'flows':flows,'checks':checks}
    write_json(OUT/'reference-results.json',artifacts)
    write_json(OUT/'reference-checks.json',{'passed':True,'crosschecks':checks,'source_sha256':{name:v['sha256'] for name,v in manifest['tables'].items()},
        'money':'CSV Decimal vs DuckDB DECIMAL(18,2)','quality_controls':quality_controls,'model_temporal_label_check':True,'model_is_deployable':False})
    write_json(GOLD/'source-manifest.json',{'status':'draft_pending_user_review','approved_for_training':False,
        'source':manifest,'analysis_cutoff':'2018-08-01, intentionally selected; not an assertion of source month completeness',
        'training_label_cutoff':'2018-07-01; only labels already observed before cutoff',
        'do_not_use_for_blind_test':'All candidate examples will be reviewed; new holdout questions must be prepared before calibration.'})
    con.close()
    print(json.dumps({'checks':len(checks),'kpis':totals,'movements':movements,'statistics':statistics,'model':modeling},ensure_ascii=False,default=str))
    return artifacts


if __name__=='__main__':
    build()
