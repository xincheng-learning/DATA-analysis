"""Independent business baseline from the original CSV, without Agent SQL or helpers."""
import csv
import hashlib
import json
from collections import Counter,defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from analyze_qa_ecommerce import analyze

ROOT=Path(__file__).resolve().parents[1]


def calculate(directory):
    baseline=analyze(directory)
    tables={}
    for path in directory.glob('*.csv'):
        with path.open(encoding='utf-8-sig',newline='') as source:tables[path.stem]=list(csv.DictReader(source))
    orders={r['order_id']:r for r in tables['orders']};customers={r['customer_id']:r for r in tables['customers']}
    products={r['product_id']:r for r in tables['products']};sellers={r['seller_id']:r for r in tables['sellers']}
    translation={r['product_category_name']:r['product_category_name_english'] for r in tables['category_translation']}
    delivered={oid:o for oid,o in orders.items() if o['order_status']=='delivered'}
    amount,freight,payment=defaultdict(Decimal),defaultdict(Decimal),defaultdict(Decimal)
    item_count=Counter();seller_states=defaultdict(set);categories=defaultdict(lambda:{'amount':Decimal(0),'orders':set(),'lines':0})
    seller_sales=defaultdict(lambda:{'amount':Decimal(0),'orders':set(),'lines':0})
    for item in tables['order_items']:
        oid=item['order_id']
        if oid not in delivered:continue
        amount[oid]+=Decimal(item['price']);freight[oid]+=Decimal(item['freight_value']);item_count[oid]+=1
        seller_states[oid].add(sellers[item['seller_id']]['seller_state'])
        category=products[item['product_id']]['product_category_name'];category=translation.get(category,category) or '未知'
        for group in (categories[category],seller_sales[item['seller_id']]):
            group['amount']+=Decimal(item['price']);group['orders'].add(oid);group['lines']+=1
    types=defaultdict(lambda:{'amount':Decimal(0),'orders':set(),'records':0})
    for row in tables['payments']:
        oid=row['order_id']
        if oid not in delivered:continue
        value=Decimal(row['payment_value']);payment[oid]+=value;group=types[row['payment_type']]
        group['amount']+=value;group['orders'].add(oid);group['records']+=1
    latest={}
    for row in tables['reviews']:
        oid=row['order_id'];key=lambda r:(r['review_answer_timestamp'],r['review_creation_date'],r['review_id'])
        if oid not in latest or key(row)>key(latest[oid]):latest[oid]=row
    review_counts=Counter(row['order_id'] for row in tables['reviews'])
    regions=defaultdict(lambda:{'orders':0,'item_amount':Decimal(0),'freight_amount':Decimal(0)})
    delivery=[];contingency=[[0,0],[0,0]]
    for oid,order in delivered.items():
        state=customers[order['customer_id']]['customer_state'];region=regions[state]
        region['orders']+=1;region['item_amount']+=amount[oid];region['freight_amount']+=freight[oid]
        actual,estimated=order['order_delivered_customer_date'],order['order_estimated_delivery_date']
        if not actual or not estimated:continue
        purchase=datetime.fromisoformat(order['order_purchase_timestamp']);actual=datetime.fromisoformat(actual);estimated=datetime.fromisoformat(estimated)
        late=actual.date()>estimated.date();score=int(latest[oid]['review_score']) if oid in latest else None
        days=Decimal(str((actual-purchase).total_seconds()))/Decimal(86400)
        delivery.append({'order_id':oid,'month':purchase.strftime('%Y-%m'),'late':late,'delivery_days':days,'score':score,
            'features':[float((estimated-purchase).total_seconds()/86400),float(amount[oid]),float(freight[oid]),item_count[oid],
                int(any(s!=state for s in seller_states[oid])),purchase.weekday()]})
        if score is not None:contingency[0 if late else 1][0 if score<=2 else 1]+=1
    diffs=[{'order_id':oid,'difference':payment[oid]-amount[oid]-freight[oid]} for oid in delivered]
    material=sorted((r for r in diffs if abs(r['difference'])>Decimal('.01')),key=lambda r:-abs(r['difference']))
    quality={name:{'rows':len(rows),'exact_duplicate_extras':len(rows)-len({tuple(row.items()) for row in rows}),
        'empty_fields':{key:sum(row[key]=='' for row in rows) for key in rows[0] if any(row[key]=='' for row in rows)}} for name,rows in tables.items()}
    for name,keys in {'orders':['order_id'],'customers':['customer_id'],'order_items':['order_id','order_item_id'],
                      'payments':['order_id','payment_sequential'],'products':['product_id'],'sellers':['seller_id']}.items():
        quality[name]['key_duplicate_extras']=len(tables[name])-len({tuple(r[k] for k in keys) for r in tables[name]})
    def ranked(groups):
        return sorted([{'name':name,**{key:len(value) if isinstance(value,set) else value for key,value in values.items()}} for name,values in groups.items()],key=lambda r:-r['amount'])
    from scipy.stats import fisher_exact
    odds,p=fisher_exact(contingency)
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss
    train=[r for r in delivery if r['month']<'2018-07'];test=[r for r in delivery if r['month']=='2018-07']
    model=make_pipeline(StandardScaler(),LogisticRegression(solver='liblinear',random_state=42,max_iter=1000))
    model.fit([r['features'] for r in train],[int(r['late']) for r in train]);pred=model.predict_proba([r['features'] for r in test])[:,1]
    y=np.array([int(r['late']) for r in test]);prior=sum(r['late'] for r in train)/len(train)
    assert len(set(y))==2 and len(set(int(r['late']) for r in train))==2
    model_result={'purpose':'下单信息对已送达样本延迟风险的探索；非生产可部署或因果模型',
        'features':['promised_days','item_amount','freight_amount','item_lines','cross_state','purchase_weekday'],
        'train_before':'2018-07-01','test_start':'2018-07-01','test_end_exclusive':'2018-08-01',
        'train_n':len(train),'test_n':len(test),'train_events':sum(r['late'] for r in train),'test_events':int(y.sum()),
        'auc':roc_auc_score(y,pred),'average_precision':average_precision_score(y,pred),'brier':brier_score_loss(y,pred),
        'baseline_train_rate':prior,'baseline_brier':brier_score_loss(y,np.full(len(y),prior)),
        'coefficients':dict(zip(['promised_days','item_amount','freight_amount','item_lines','cross_state','purchase_weekday'],model[-1].coef_[0].tolist())),
        'method':'StandardScaler fitted on train only; LogisticRegression liblinear, random_state=42, max_iter=1000, default regularization and no class weighting.'}
    customer_history=defaultdict(list)
    for oid,order in delivered.items():
        purchased=datetime.fromisoformat(order['order_purchase_timestamp'])
        if purchased<datetime(2018,8,1):customer_history[customers[order['customer_id']]['customer_unique_id']].append((purchased,amount[oid]))
    rfm=defaultdict(lambda:{'customers':0,'orders':0,'item_amount':Decimal(0),'recency_sum':0})
    for history in customer_history.values():
        recency=(datetime(2018,8,1).date()-max(date for date,_ in history).date()).days
        name=('活跃' if recency<=90 else '沉默')+('复购' if len(history)>=2 else '一次')
        group=rfm[name];group['customers']+=1;group['orders']+=len(history);group['item_amount']+=sum(value for _,value in history);group['recency_sum']+=recency
    for group in rfm.values():group['mean_recency_days']=Decimal(group['recency_sum'])/group['customers']
    values=sorted(float(r['delivery_days']) for r in delivery)
    result={**baseline,'quality':quality,'status_counts':dict(Counter(o['order_status'] for o in orders.values())),
        'legacy_change_window':['2018-05','2018-06'],'regions':dict(regions),'categories':ranked(categories),'sellers':ranked(seller_sales),'payment_types':ranked(types),
        'payment_reconciliation':{'total_difference':sum(r['difference'] for r in diffs),'orders_over_one_cent':len(material),'top_differences':material[:10]},
        'review_policy':'One review per order: latest review_answer_timestamp, then review_creation_date, then review_id, descending; original records retained.',
        'orders_with_multiple_reviews':sum(n>1 for n in review_counts.values()),'review_extra_rows':sum(n-1 for n in review_counts.values()),
        'delivery':{'known_n':len(delivery),'missing_n':len(delivered)-len(delivery),'late_n':sum(r['late'] for r in delivery),
            'mean_days':sum(r['delivery_days'] for r in delivery)/len(delivery),'median_days':float(np.median(values)),'p95_days':float(np.percentile(values,95,method='linear')),
            'late_low_late_other_ontime_low_ontime_other':contingency,'fisher_odds_ratio':odds,'fisher_pvalue':p,
            'late_low_rating_rate':Decimal(contingency[0][0])/sum(contingency[0]),'ontime_low_rating_rate':Decimal(contingency[1][0])/sum(contingency[1])},
        'rfm_asof_20180801':dict(rfm),'model':model_result}
    total=Decimal(result['total_item_amount'])
    before,after=baseline['monthly']['2018-06'],baseline['monthly']['2018-07']
    before_amount,after_amount=Decimal(str(before['item_amount'])),Decimal(str(after['item_amount']))
    before_aov,after_aov=before_amount/before['orders'],after_amount/after['orders']
    result['selected_comparison']={'before_month':'2018-06','after_month':'2018-07',
        'basis':'Explicit teaching window; not a claim that the last source month is complete.',
        'amount_change':after_amount-before_amount,'amount_change_rate':after_amount/before_amount-1,
        'order_change_rate':Decimal(after['orders'])/before['orders']-1,'aov_change_rate':after_aov/before_aov-1,
        'quantity_effect':(after['orders']-before['orders'])*(after_aov+before_aov)/2,
        'order_value_effect':(after_aov-before_aov)*(after['orders']+before['orders'])/2}
    assert abs(result['selected_comparison']['quantity_effect']+result['selected_comparison']['order_value_effect']-(after_amount-before_amount))<Decimal('1e-18')
    assert sum(r['item_amount'] for r in regions.values())==total
    assert sum(r['amount'] for r in categories.values())==sum(r['amount'] for r in seller_sales.values())==total
    assert sum(r['amount'] for r in types.values())==Decimal(result['total_payment_amount'])
    assert sum(sum(row) for row in contingency)<=len(delivery)
    assert sum(r['orders'] for r in regions.values())==len(delivered)
    return result


if __name__=='__main__':
    source=ROOT/'.local/ecommerce/demo';result=calculate(source)
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((source/(name+'.csv')).read_bytes()).hexdigest()==item['sha256'] for name,item in manifest['tables'].items())
    output=ROOT/'output/olist-business-2026-11-11';output.mkdir(parents=True,exist_ok=True)
    (output/'independent-baseline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status_counts','orders_with_multiple_reviews','delivery','model','payment_reconciliation']},ensure_ascii=False,default=str))
