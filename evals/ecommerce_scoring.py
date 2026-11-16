"""Frozen numeric/chart criteria; a successful API response alone never passes."""
from decimal import Decimal, InvalidOperation
import re

ALIASES = {
 'stage':['stage','stage_name','step_name','阶段','履约阶段'],
 'stage_count':['stage_count','order_count','orders','reached_orders','count','cnt','订单数','到达数','到达订单数'],
 'conversion_rate':['conversion_rate','overall_rate','overall_conversion_rate','cumulative_rate','total_conversion_rate','累计转化率','总转化率','整体转化率'],
 'quarter_offset':['quarter_offset','quarters_since_first','quarter_index','period_index','季度偏移','相对季度'],
 'order_id':['order_id','订单号','订单编号'],
 'purchase_time':['purchase_time','order_purchase_timestamp','下单时间'],
 'order_count':['order_count','orders','total_orders','delivered_orders','delivered_order_count','订单数','订单数量','去重订单数','n','cnt','count'],
 'month':['month','order_month','purchase_month','月份','下单月份','下单月','order_purchase_month'],
 'status':['status','order_status','订单状态','状态'],
 'item_amount':['item_amount','product_amount','total_price','total_item_amount','revenue','sales','商品金额','商品总额','销售额','商品销售额'],
 'freight_amount':['freight_amount','freight','freight_value','total_freight','freight_total','运费','运费金额'],
 'payment_amount':['payment_amount','payment','payment_value','total_payment','payments','支付金额','支付额','支付总额'],
 'customer_count':['customer_count','total_customers','customers','purchasing_customers','客户数','购买客户数','客户总数','已购客户数'],
 'repeat_customers':['repeat_customers','repeat_customer_count','repurchase_customers','复购客户数','复购人数'],
 'repeat_rate':['repeat_rate','repeat_customer_rate','repurchase_rate','复购客户占比','复购率','复购占比'],
 'region':['region','state','customer_state','州','客户州','客户所在州','地区'],
 'category':['category','product_category','product_category_name','product_category_name_english','product_category_en','商品类别','商品分类','类别'],
 'may_amount':['may_amount','may_revenue','may_sales','may_total','sales_may','amount_may','2018_05','5月金额','五月金额','5月商品金额'],
 'june_amount':['june_amount','june_revenue','june_sales','june_total','sales_june','amount_june','2018_06','6月金额','六月金额','6月商品金额'],
 'delta':['delta','change','difference','diff','amount_change','revenue_change','increment','差额','增量','变化额','金额变化','商品金额差额'],
 'cohort':['cohort','cohort_month','cohort_quarter','first_month','first_purchase_month','首次购买月份','首次下单月份','客户群月份','首次月份','首购季度'],
 'month_offset':['month_offset','months_since_first','month_index','month_number','retention_month','offset','period_index','相对月份','月龄','第几月'],
 'retention_rate':['retention_rate','retention','rate','留存率','留存比例','客户留存率'],
 'payment_type':['payment_type','payment_method','支付方式'],
 'payment_count':['payment_count','payment_records','payment_times','支付笔数','支付次数'],
 'eligible_orders':['eligible_orders','valid_orders','valid_date_orders','有效日期订单数','有效订单数'],
 'late_orders':['late_orders','delayed_orders','延期单数','延期订单数'],
 'invalid_dates':['invalid_dates','invalid_date_orders','missing_dates','日期无效单数','无效日期订单数'],
 'late_rate':['late_rate','delay_rate','late_delivery_rate','延期率'],
 'cumulative_share':['cumulative_share','cumulative_ratio','cum_share','cumulative_pct','累计占比','累计贡献率'],
 'segment':['segment','abc_class','abc_category','contribution_tier','class','ABC分层','分层'],
 'eligible_customers':['eligible_customers','valid_customers','eligible_customer_count','有效客户数','有效客户数量'],
 'item_count':['item_count','detail_count','total_items','明细数','明细数量'],
 'outlier_count':['outlier_count','anomaly_count','异常条数','异常数'],
 'outlier_rate':['outlier_rate','anomaly_rate','anomaly_ratio','异常占比','异常率'],
}
TEXT = {'month','status','region','category','cohort','payment_type','segment','order_id','stage'}


def _name(value):
    return str(value).lower().replace(' ','').replace('-','_').removesuffix('_brl')


def _value(value, key, labels):
    if value is None:
        return None
    if key in {'month','cohort'}:
        quarter=re.match(r'^(\d{4})-?Q([1-4])$',str(value),re.I)
        if quarter:
            return f'{quarter[1]}-{(int(quarter[2])-1)*3+1:02}'
        return str(value)[:7]
    if key=='stage':
        terms={'下单':['下单','订单创建','placed','created','purchase'], '审批':['审批','批准','已确认','approved','approval'],
               '发货':['发货','交付承运','shipped','carrier'], '签收':['签收','送达','delivered','received']}
        return next((label for label,aliases in terms.items() if any(term in str(value).lower() for term in aliases)),str(value))
    if key == 'purchase_time':
        return str(value).replace('T',' ')
    if key == 'category':
        return labels.get(str(value),str(value)) if str(value).lower() not in {'unknown','none','nan','未分类',''} else '未知'
    if key in TEXT:
        return str(value)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return object()


def match_rows(actual, expected, labels=None, subset=False):
    if not expected or not actual or (not subset and len(actual) != len(expected)):
        return False
    labels = labels or {}
    keys = list(expected[0])
    mapping = {}
    for key in keys:
        candidates = [_name(alias) for alias in ALIASES.get(key,[key])]
        exact = [name for name in actual[0] if _name(name) == _name(key)]
        matched = exact or [name for name in actual[0] if _name(name) in candidates]
        if not matched and key in {'may_amount','june_amount'}:
            month = r'(may|05)' if key == 'may_amount' else r'(jun|june|06)'
            matched = [name for name in actual[0] if re.search(r'(^|_)'+month+r'(_|$)',_name(name))
                       and re.search(r'amount|revenue|sales|total',_name(name))]
        if not matched and key == 'delta':
            matched = [name for name in actual[0] if re.search(r'(^|_)(diff|difference|delta|change)(_|$)',_name(name))
                       and not re.search(r'(^|_)(abs|absolute)(_|$)',_name(name))]
            amounts=[name for name in matched if re.search(r'amount|revenue|sales',_name(name))]
            if amounts:
                matched=amounts
        if not matched:
            matched = [name for name in actual[0] if any(len(alias)>=8 and '_' in alias and
                       (_name(name).startswith(alias+'_') or _name(name).endswith('_'+alias)) for alias in candidates)]
        if len(matched) != 1:
            return False
        mapping[key] = matched[0]
    remaining = list(actual)
    for row in expected:
        found = None
        for index, candidate in enumerate(remaining):
            correct = True
            for key in keys:
                a, b = _value(candidate.get(mapping[key]),key,labels), _value(row[key],key,labels)
                tolerance = Decimal('0.00005') if key in {'repeat_rate','retention_rate','late_rate','cumulative_share','outlier_rate','conversion_rate'} else Decimal('0') if key.endswith('count') or key in {'repeat_customers','eligible_customers','eligible_orders','late_orders','invalid_dates','month_offset','quarter_offset'} else Decimal('0.005')
                if isinstance(a,Decimal) and isinstance(b,Decimal):
                    equal = a.is_finite() and b.is_finite() and abs(a-b) <= tolerance
                else:
                    equal = a == b
                if not equal:
                    correct = False
                    break
            if correct:
                found = index
                break
        if found is None:
            return False
        remaining.pop(found)
    return True


def score_case(case, record, reference):
    final = record.get('final',{})
    investigation = final.get('investigation') or {}
    tables = [final, *[s for s in investigation.get('steps',[]) if s.get('status') == 'succeeded']]
    labels = reference.get('category_labels',{})
    if case['target']=='data_overview':
        expected=reference['data_overview'];previews=final.get('table_previews',[])
        actual={table['table_name']:table for table in previews}
        profile_ok=len(actual)==len(previews) and set(actual)==set(expected)
        samples_ok=profile_ok
        for name,table in actual.items():
            wanted=expected.get(name,{})
            profile_ok=profile_ok and all(table.get(key)==wanted.get(key) for key in ['rows','columns','null_counts'])
            sample=table.get('preview',[]);truth=wanted.get('preview',[])
            samples_ok=samples_ok and len(sample)==len(truth)
            for row,expected_row in zip(sample,truth):
                samples_ok=samples_ok and set(row)==set(expected_row)
                for key,value in expected_row.items():
                    got=row.get(key)
                    if value is None or got is None:
                        samples_ok=samples_ok and value is None and got is None
                    elif key.endswith(('_id','_code','_zip_code_prefix')):
                        samples_ok=samples_ok and str(got)==str(value)
                    else:
                        try:
                            equal=abs(Decimal(str(got))-Decimal(str(value)))<=Decimal('0.00000001')
                        except InvalidOperation:
                            equal=str(got)==str(value)
                        samples_ok=samples_ok and equal
        checks={'real_api':bool(record.get('request_ids')) and record.get('model_calls',0)>0,
            'whole_table_profiles':bool(profile_ok),'real_sample_rows':bool(samples_ok),
            'no_invented_sql_or_chart':final.get('route')=='chat' and not final.get('sql') and not final.get('chart_spec'),
            'explanation':bool(final.get('answer','').strip())}
        return {'passed':all(checks.values()),'checks':checks,'failed':[key for key,value in checks.items() if not value]}
    if case.get('allowed_routes'):
        answer = final.get('answer','')
        checks = {'real_api':bool(record.get('request_ids')) and record.get('model_calls',0)>0,
                  'appropriate_boundary':record.get('status') == 'ran' and final.get('route') in case['allowed_routes'] and not final.get('rows'),
                  'explains_missing_input_or_definition':all(term.lower() in answer.lower() for term in case.get('required_terms',[]))}
        if case['target'] == 'rfm_definition':
            checks['explains_missing_input_or_definition'] = all(any(word.lower() in answer.lower() for word in group) for group in [
                ['RFM'],['最近','recency','最后','距'],['频次','频率','frequency','F（单）','去重计数'],['金额','Monetary']])
        return {'passed':all(checks.values()),'checks':checks,'failed':[key for key,value in checks.items() if not value]}
    expected = reference[case['target']]
    if case['target'].startswith('waterfall'):
        traces = (final.get('chart_spec') or {}).get('data',[])
        numbers = False
        if len(traces) == 1 and traces[0].get('type') == 'waterfall':
            trace = traces[0]
            y,measure = trace.get('y',[]),trace.get('measure',[])
            if y and measure and measure[0] == 'absolute' and measure[-1] == 'total':
                def label(value):
                    return '其他' if str(value).lower() in {'其他','其他类别','other','others','other categories'} else labels.get(str(value),str(value))
                xs = trace['x']
                if case['target'] == 'waterfall':
                    rows = final.get('rows',[])
                    dimension = next((key for key in (rows[0] if rows else []) if _name(key) in ALIASES['region']),None)
                    if dimension and len(rows)==len(xs):
                        xs = [row.get(dimension) for row in rows]
                amounts = {label(x):Decimal(str(v)) for x,v,m in zip(xs,y,measure) if m == 'relative'}
                wanted = {row.get('region',row.get('category')):Decimal(row['delta']) for row in expected['contributions']}
                extras = set(amounts)-set(wanted)
                numbers = (len(amounts)==measure.count('relative') and set(wanted)<=set(amounts)
                    and extras<=set(expected.get('zero_regions',[])) and all(amounts[k]==0 for k in extras)
                    and all(abs(amounts[k]-v)<Decimal('.005') for k,v in wanted.items()))
                numbers = numbers and abs(Decimal(str(y[0]))-Decimal(expected['before']))<Decimal('.005') and abs(Decimal(str(y[-1]))-Decimal(expected['after']))<Decimal('.005')
    elif isinstance(expected,dict):
        top = expected['top']
        top_delta = [{k:v for k,v in row.items() if k not in {'may_amount','june_amount'}} for row in top]
        numbers = any(match_rows(t.get('rows',[]), expected['totals'], labels) or
                      match_rows(t.get('rows',[]), expected.get('totals_wide',[]), labels) or
                      match_rows(t.get('rows',[]), expected.get('totals_repeated',[]), labels,subset=True) for t in tables)
        if not numbers:
            numbers = any(match_rows([row],expected.get('totals_wide',[]),labels) for t in tables for row in t.get('rows',[])
                          if any(str(value).upper() in {'TOTAL','ALL','总计','合计','全部'} for value in row.values()))
        numbers = numbers and any(match_rows(t.get('rows',[]),top,labels,subset=True) or
            match_rows(t.get('rows',[]),top_delta,labels,subset=True) or
            match_rows(t.get('rows',[]),expected.get('top_long',[]),labels,subset=True) for t in tables)
    else:
        numbers = any(match_rows(t.get('rows',[]),expected,labels) for t in tables)
    if case['target'] == 'order_sample':
        numbers = numbers and final.get('row_count') == 123
    chart = final.get('chart_spec') or {}
    traces, layout = chart.get('data',[]), chart.get('layout',{})
    kind = case['chart']
    chart_ok = kind is None
    if kind in {'line','multi_line'}:
        chart_ok = bool(traces) and all(t.get('type') == 'scatter' and 'lines' in t.get('mode','') for t in traces)
        if kind == 'multi_line':
            chart_ok = chart_ok and len(traces) >= 3
        chart_ok = chart_ok and all(t.get('x',[]) == sorted(t.get('x',[])) for t in traces)
    elif kind in {'bar','stacked_bar','grouped_bar'}:
        chart_ok = bool(traces) and all(t.get('type') == 'bar' for t in traces)
        if kind == 'stacked_bar':
            chart_ok = chart_ok and len(traces) == 3 and layout.get('barmode') == 'stack'
        if kind == 'grouped_bar':
            chart_ok = chart_ok and len(traces) >= 2 and layout.get('barmode','group') == 'group'
    elif kind == 'heatmap':
        chart_ok = len(traces) == 1 and traces[0].get('type') == 'heatmap'
        if chart_ok:
            cells = []
            trace = traces[0]
            for yi,y in enumerate(trace.get('y',[])):
                for xi,x in enumerate(trace.get('x',[])):
                    cohort, offset = (x,y) if re.match(r'^\d{4}(?:-|Q)',str(x)) else (y,x)
                    cells.append({'cohort':cohort,'quarter_offset' if case['target']=='retention_quarter' else 'month_offset':offset,'retention_rate':trace['z'][yi][xi]})
            chart_ok = match_rows(cells, expected, labels)
    elif kind == 'funnel':
        chart_ok=len(traces)==1 and traces[0].get('type')=='funnel'
        if chart_ok:
            chart_ok=match_rows([{'stage':stage,'stage_count':count} for stage,count in zip(traces[0]['y'],traces[0]['x'])],
                [{key:row[key] for key in ['stage','stage_count']} for row in expected])
    elif kind == 'waterfall':
        chart_ok = len(traces) == 1 and traces[0].get('type') == 'waterfall'
    if kind and isinstance(expected,dict) and chart_ok and 'top' in expected:
        dimension = next(key for key in expected['top'][0] if key in {'region','category'})
        wanted = {row[dimension] for row in expected['top']}
        known = {row[dimension] for row in expected.get('all',expected['top'])}
        shown = {labels.get(str(value),str(value)) for trace in traces for value in [
            *trace.get('y' if trace.get('orientation')=='h' else 'x',[]),trace.get('name','')]}
        chart_ok = shown & known == wanted and not shown & {'TOTAL','ALL','总计','合计'}
    answer = final.get('answer','')
    checks = {'real_api':bool(record.get('request_ids')) and record.get('model_calls',0)>0,
              'execution':record.get('status') == 'ran' and not final.get('error') and not final.get('validation_failure')
                    and final.get('route') in {'sql','viz','investigate'} and (case['mode'] != 'investigate' or investigation.get('status') == 'completed'),
              'numbers':bool(numbers), 'chart':bool(chart_ok),
              'text_evidence':bool(answer.strip()) and bool(final.get('sql') or investigation.get('steps'))
                    and ('范围' in answer or '口径' in answer) and ('依据' in answer or 'F1' in answer)}
    if case.get('required_terms'):
        checks['scope_warning'] = any(term in answer for term in case['required_terms'])
    if case.get('causal_guard'):
        checks['no_unsupported_causality'] = ('不能' in answer or '无法' in answer or '不代表' in answer) and '因果' in answer
    return {'passed':all(checks.values()),'checks':checks,'failed':[key for key,value in checks.items() if not value]}
