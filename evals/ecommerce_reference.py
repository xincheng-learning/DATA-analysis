"""Independent pandas/Decimal references, never imported by the application agent."""
from collections import Counter, defaultdict
from decimal import Decimal

import pandas as pd


def _sum(frame, keys, value):
    grouped = defaultdict(Decimal)
    for row in frame.to_dict('records'):
        grouped[tuple(row[key] for key in keys)] += Decimal(str(row[value]))
    return grouped


def build_reference(tables):
    orders, customers, items = (tables[name].copy() for name in ['orders','customers','order_items'])
    assert orders.order_id.is_unique and customers.customer_id.is_unique
    assert tables['products'].product_id.is_unique
    orders['month'] = orders.order_purchase_timestamp.str[:7]
    delivered = orders[orders.order_status == 'delivered']
    purchased = delivered.merge(customers, on='customer_id', validate='many_to_one')
    detail = items.merge(delivered[['order_id','customer_id','month']], on='order_id', validate='many_to_one')
    detail = detail.merge(customers[['customer_id','customer_state']], on='customer_id', validate='many_to_one')
    detail = detail.merge(tables['products'][['product_id','product_category_name']], on='product_id', how='left', validate='many_to_one')
    translations = dict(zip(tables['category_translation'].product_category_name, tables['category_translation'].product_category_name_english))
    detail['category'] = detail.product_category_name.map(lambda v: translations.get(v,v) if pd.notna(v) and v else '未知')
    payments = tables['payments'].merge(delivered[['order_id','month']], on='order_id', validate='many_to_one')
    amounts, freight, paid = (_sum(frame,['month'],column) for frame,column in [
        (detail,'price'),(detail,'freight_value'),(payments,'payment_value')])
    customer_orders = purchased.groupby('customer_unique_id').order_id.nunique()
    repeated = int((customer_orders > 1).sum())
    result = {
        'data_overview':{name:{'rows':len(frame),'columns':list(frame.columns),
            'null_counts':{column:int((frame[column].isna() | frame[column].eq('')).sum()) for column in frame},
            'preview':[{column:None if pd.isna(value) or value=='' else str(value) for column,value in row.items()}
                for row in frame.iloc[:3,:6].to_dict('records')]} for name,frame in tables.items()},
        'order_sample':delivered.sort_values(['order_purchase_timestamp','order_id']).head(50)[['order_id','order_purchase_timestamp','order_status']].rename(
            columns={'order_purchase_timestamp':'purchase_time','order_status':'status'}).to_dict('records'),
        'order_count':[{'order_count':len(delivered)}],
        'monthly_orders':[{'month':month,'order_count':int(count)} for month,count in delivered.groupby('month').order_id.nunique().items()],
        'status_orders':[{'status':status,'order_count':int(count)} for status,count in orders.groupby('order_status').order_id.nunique().items()],
        'monthly_money':[{'month':month,'item_amount':str(amounts[(month,)]),'freight_amount':str(freight[(month,)]),'payment_amount':str(paid[(month,)])}
                         for month, in sorted(amounts) if '2018-01' <= month <= '2018-06'],
        'repeat_customers':[{'customer_count':len(customer_orders),'repeat_customers':repeated,
                             'repeat_rate':str(Decimal(repeated)/Decimal(len(customer_orders))) if len(customer_orders) else None}],
        'regional_money':[{'month':month,'region':region,'item_amount':str(amount)}
                          for (month,region),amount in sorted(_sum(detail,['month','customer_state'],'price').items())
                          if '2018-01' <= month <= '2018-06' and region in {'SP','RJ','MG'}],
        'category_labels':translations,
    }
    for dimension,target in [('customer_state','state_change'),('category','category_change')]:
        grouped = _sum(detail[detail.month.isin(['2018-05','2018-06'])],[dimension,'month'],'price')
        rows = []
        key = 'region' if dimension == 'customer_state' else 'category'
        for name in {group[0] for group in grouped}:
            may, june = grouped.get((name,'2018-05'),Decimal(0)), grouped.get((name,'2018-06'),Decimal(0))
            rows.append({key:name,'may_amount':str(may),'june_amount':str(june),'delta':str(june-may)})
        top = sorted(rows,key=lambda row:(-abs(Decimal(row['delta'])),row[key]))[:5]
        result[target] = {'top':top, 'all':rows,
            'top_long':[{key:row[key],'month':month,'item_amount':row[field]} for row in top
                        for month,field in [('2018-05','may_amount'),('2018-06','june_amount')]],
            'totals':[{'month':month,'item_amount':str(amounts.get((month,),Decimal(0)))} for month in ['2018-05','2018-06']],
            'totals_wide':[{'may_amount':str(amounts.get(('2018-05',),Decimal(0))),
                            'june_amount':str(amounts.get(('2018-06',),Decimal(0))),
                            'delta':str(amounts.get(('2018-06',),Decimal(0))-amounts.get(('2018-05',),Decimal(0)))}]}
        result[target]['totals_repeated'] = [{'may_total':result[target]['totals_wide'][0]['may_amount'],
             'june_total':result[target]['totals_wide'][0]['june_amount'],'total_diff':result[target]['totals_wide'][0]['delta']}]
    first = purchased.groupby('customer_unique_id').month.min()
    observed = purchased.groupby('month').customer_unique_id.apply(set).to_dict()
    last_month = pd.Period(orders.month.max(),freq='M')
    retention = []
    for cohort in pd.period_range('2018-01','2018-06',freq='M'):
        members = set(first[first == str(cohort)].index)
        if not members:
            continue
        for offset in range(4):
            month = cohort + offset
            rate = None if month >= last_month else str(Decimal(len(members & observed.get(str(month),set())))/Decimal(len(members)))
            retention.append({'cohort':str(cohort),'month_offset':offset,'retention_rate':rate})
    result['retention'] = retention
    history_quarter=purchased.copy()
    history_quarter['quarter']=pd.to_datetime(history_quarter.order_purchase_timestamp).dt.to_period('Q')
    first_quarter=history_quarter.groupby('customer_unique_id').quarter.min()
    observed_quarters=history_quarter.groupby('quarter').customer_unique_id.apply(set).to_dict()
    last_quarter=pd.to_datetime(orders.order_purchase_timestamp).max().to_period('Q')
    result['retention_quarter']=[]
    for quarter in pd.period_range('2017Q1','2017Q4',freq='Q'):
        members=set(first_quarter[first_quarter==quarter].index)
        if not members:
            continue
        for offset in range(4):
            target=quarter+offset
            result['retention_quarter'].append({'cohort':str(quarter.start_time)[:7],'quarter_offset':offset,
                'retention_rate':None if target>=last_quarter else str(Decimal(len(members & observed_quarters.get(target,set())))/Decimal(len(members)))})
    funnel_columns={'order_approved_at','order_delivered_carrier_date','order_delivered_customer_date'}
    if funnel_columns.issubset(orders):
        cohort=orders[(orders.month>='2018-01') & (orders.month<='2018-06')].copy()
        purchase,approved,carrier,received=(pd.to_datetime(cohort[name],errors='coerce') for name in
            ['order_purchase_timestamp','order_approved_at','order_delivered_carrier_date','order_delivered_customer_date'])
        masks=[purchase.notna()]
        masks.append(masks[-1] & approved.notna() & (approved>=purchase))
        masks.append(masks[-1] & carrier.notna() & (carrier>=approved))
        masks.append(masks[-1] & received.notna() & (received>=carrier))
        counts=[int(mask.sum()) for mask in masks]
        result['fulfillment_funnel']=[{'stage':stage,'stage_count':count,
            'conversion_rate':str(Decimal(count)/Decimal(counts[0])) if counts[0] else None}
            for stage,count in zip(['下单','审批','发货','签收'],counts)]
    latest = pd.Period(orders.month.max(),freq='M')
    result['recent_money'] = [{'month':month,'item_amount':str(amounts[(month,)])} for month, in sorted(amounts)
                              if latest-2 <= pd.Period(month,freq='M') <= latest]
    result['regional_rj'] = [row for row in result['regional_money'] if row['region'] == 'RJ']
    result['payment_mix'] = []
    if 'payment_type' in payments:
        for method,group in payments.groupby('payment_type'):
            result['payment_mix'].append({'payment_type':method,'payment_count':len(group),'order_count':int(group.order_id.nunique()),
                                          'payment_amount':str(sum(map(Decimal,group.payment_value),Decimal(0)))})
    delivery = purchased.copy()
    if {'order_delivered_customer_date','order_estimated_delivery_date'}.issubset(delivery):
        actual = pd.to_datetime(delivery.order_delivered_customer_date,errors='coerce')
        expected = pd.to_datetime(delivery.order_estimated_delivery_date,errors='coerce')
        delivery['valid'] = actual.notna() & expected.notna()
        delivery['late'] = (actual>expected) & delivery['valid']
        half = delivery[(delivery.month >= '2018-01') & (delivery.month <= '2018-06')]
        result['delivery_states'] = []
        for state,group in half.groupby('customer_state'):
            valid,late = int(group.valid.sum()),int(group.late.sum())
            if valid >= 20:
                result['delivery_states'].append({'region':state,'eligible_orders':valid,'late_orders':late,
                    'invalid_dates':len(group)-valid,'late_rate':str(Decimal(late)/Decimal(valid))})
        result['monthly_delivery'] = []
        for month,group in half.groupby('month'):
            valid,late = int(group.valid.sum()),int(group.late.sum())
            result['monthly_delivery'].append({'month':month,'item_amount':str(amounts[(month,)]),
                'eligible_orders':valid,'late_rate':str(Decimal(late)/Decimal(valid)) if valid else None})
    by_category = _sum(detail,['category'],'price')
    total = sum(by_category.values(),Decimal(0))
    cumulative = Decimal(0)
    result['category_abc'] = []
    for (category,),amount in sorted(by_category.items(),key=lambda pair:(-pair[1],pair[0])):
        cumulative += amount
        ratio = cumulative/total if total else Decimal(0)
        result['category_abc'].append({'category':category,'item_amount':str(amount),'cumulative_share':str(ratio),
                                       'segment':'A' if ratio<=Decimal('.8') else 'B' if ratio<=Decimal('.95') else 'C'})
    result['category_abc'] = result['category_abc'][:10]
    history = purchased.copy()
    history['time'] = pd.to_datetime(history.order_purchase_timestamp)
    first_time = history.groupby('customer_unique_id').time.min()
    data_end = pd.to_datetime(orders.order_purchase_timestamp).max()
    eligible = set(first_time[first_time + pd.Timedelta(days=30) <= data_end].index)
    joined = history.join(first_time.rename('first'),on='customer_unique_id')
    repurchased = set(joined[(joined.time > joined['first']) & (joined.time <= joined['first']+pd.Timedelta(days=30))].customer_unique_id)
    result['repeat_30d'] = []
    for month in [str(latest-2),str(latest-1),str(latest)]:
        members = set(first_time[first_time.dt.strftime('%Y-%m') == month].index) & eligible
        repeats = len(members & repurchased)
        result['repeat_30d'].append({'cohort':month,'eligible_customers':len(members),'repeat_customers':repeats,
                                     'repeat_rate':str(Decimal(repeats)/Decimal(len(members))) if members else None})
    result['category_outliers'] = []
    for category,group in detail.groupby('category'):
        values = group.price.astype(float)
        if len(values)<20:
            continue
        q1,q3 = values.quantile(.25),values.quantile(.75)
        iqr = q3-q1
        count = int(((values<max(0,q1-1.5*iqr)) | (values>q3+1.5*iqr)).sum())
        result['category_outliers'].append({'category':category,'item_count':len(values),'outlier_count':count,
                                           'outlier_rate':str(Decimal(count)/Decimal(len(values)))})
    result['category_outliers'] = sorted(result['category_outliers'],key=lambda r:(-Decimal(r['outlier_rate']),r['category']))[:10]
    result['waterfall'] = {'before':result['state_change']['totals_wide'][0]['may_amount'],
                          'after':result['state_change']['totals_wide'][0]['june_amount'],
                          'contributions':result['state_change']['all'],
                          'zero_regions':sorted(set(customers.customer_state)-{row['region'] for row in result['state_change']['all']})}
    category_amounts = _sum(detail[detail.month.isin(['2018-06','2018-07'])],['category','month'],'price')
    differences = [{'category':category,'delta':str(category_amounts.get((category,'2018-07'),Decimal(0))-category_amounts.get((category,'2018-06'),Decimal(0)))}
                   for category in {key[0] for key in category_amounts}]
    differences.sort(key=lambda row:(-abs(Decimal(row['delta'])),row['category']))
    contributions = [*differences[:5],{'category':'其他','delta':str(sum((Decimal(row['delta']) for row in differences[5:]),Decimal(0)))}]
    result['waterfall_category'] = {'before':str(amounts.get(('2018-06',),Decimal(0))),
        'after':str(amounts.get(('2018-07',),Decimal(0))),'contributions':contributions}
    assert Decimal(result['waterfall_category']['before'])+sum((Decimal(row['delta']) for row in contributions),Decimal(0)) == Decimal(result['waterfall_category']['after'])
    result['last_two_complete_money'] = [{'month':month,'item_amount':str(amounts[(month,)])} for month, in sorted(amounts)
                                        if latest-2 <= pd.Period(month,freq='M') < latest]
    return result
