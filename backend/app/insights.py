"""Deterministic descriptive evidence; SQL results never imply business certification."""
from __future__ import annotations

import math
from decimal import Decimal

import pandas as pd

from app.tools.ingest import _is_identifier
from app.tools.dataset_manager import is_sensitive_column
from app.metrics import METRICS

METRIC_LABELS = {'item_amount':'商品金额','product_amount':'商品金额','revenue':'销售额','sales':'销售额',
    'payment_amount':'支付金额','freight_amount':'运费','order_count':'订单数','delivered_orders':'已送达订单数',
    'customer_count':'购买客户数','repeat_customers':'复购客户数','repeat_rate':'复购率',
    'retention_rate':'留存率','late_rate':'延期率','eligible_orders':'有效订单数',
    'conversion_rate':'转化率','stage_count':'到达订单数'}
METRIC_LABELS.update({identifier: metric['label'] for identifier, metric in METRICS.items()})
METRIC_LABELS['avg_order_amount'] = METRICS['average_order_amount']['label']
METRIC_LABELS['avg_order_item_amount'] = METRICS['average_order_amount']['label']
METRIC_LABELS['average_order_item_amount'] = METRICS['average_order_amount']['label']
METRIC_LABELS['avg_item_amount_per_order'] = '每单商品金额'
METRIC_LABELS['average_item_amount_per_order'] = '每单商品金额'


def result_facts(frame: pd.DataFrame, sql: str, data_version: str | None):
    shared = {'sql': sql, 'data_version': data_version, 'scope': '本次 SQL 返回结果，受筛选、聚合及 LIMIT 约束',
              'validation': '探索结果，未经业务验证'}
    facts = [{'id': 'F1', 'label': '本次查询返回行数', 'value': len(frame), 'unit': '行', **shared}]
    dimensions = [name for name in frame.columns if not pd.api.types.is_numeric_dtype(frame[name].dtype)
                  and not is_sensitive_column(name)]
    for column in frame.select_dtypes(include='number').columns:
        if _is_identifier(column) or is_sensitive_column(column) or column.lower() in {'step_order','sort_order','ord','rn','rank','month_offset','quarter_offset','window_days','horizon_days','窗口天数'}:
            continue
        values = frame[column]
        valid = values[values.notna() & values.map(lambda value: math.isfinite(value) if pd.notna(value) else False)]
        missing = len(values) - len(valid)
        for statistic in ['min', 'max']:
            index = (valid.idxmin() if statistic == 'min' else valid.idxmax()) if len(valid) else None
            value = frame.at[index, column] if index is not None else None
            value = value.item() if hasattr(value, 'item') else value
            labels = {name: str(frame.at[index, name]) for name in dimensions[:3] if pd.notna(frame.at[index, name])} if index is not None else {}
            facts.append({'id': f'F{len(facts)+1}', 'label': column, 'statistic': statistic, 'value': value,
                          'dimensions': labels, 'missing': missing, 'unit': '结果列原单位（未推断币种）', **shared})
        time_column = next((name for name in dimensions if name.lower() in {'month','order_month','purchase_month','date','day','week','月份','日期'}),None)
        if time_column and len(frame)>1 and frame[time_column].is_unique:
            times=pd.to_datetime(frame[time_column],format='mixed',errors='coerce')
            if times.notna().all():
                ordered=frame.loc[times.sort_values().index]
                first,last=ordered.iloc[0],ordered.iloc[-1]
                if pd.notna(first[column]) and pd.notna(last[column]) and math.isfinite(first[column]) and math.isfinite(last[column]):
                    facts.append({'id':f'F{len(facts)+1}','label':column,'statistic':'change',
                        'start_label':str(first[time_column])[:10],'end_label':str(last[time_column])[:10],
                        'start_value':float(first[column]),'end_value':float(last[column]),**shared})
    flag_labels = {'is_partial_month':'该月份可能覆盖不完整','is_partial_period':'该期间可能覆盖不完整',
                   'is_incomplete_period':'该期间可能覆盖不完整'}
    for column in frame.columns:
        tokens = set(column.lower().split('_'))
        period_status = bool(tokens & {'month','period','window'} and tokens & {'partial','incomplete','complete','completeness','status'})
        boolean = pd.api.types.is_bool_dtype(frame[column].dtype)
        if not boolean and not period_status:
            continue
        values = frame[column]
        marked = values.fillna(False) if boolean else values.astype('string').str.lower().str.contains(
            r'(?<![a-z])(?:incomplete|partial)(?![a-z])|不完整|未完整',regex=True,na=False)
        if boolean and period_status and 'complete' in tokens:
            marked = (~values).fillna(False)
        labels = [{name:str(row[name]) for name in dimensions[:3] if pd.notna(row[name])}
                  for row in frame[marked].head(3).to_dict('records')]
        warning = flag_labels.get(column,'')
        if not warning and period_status:
            warning = '该期间数据覆盖不完整'
        facts.append({'id':f'F{len(facts)+1}','label':column,'statistic':'flag_count','value':int(marked.sum()),
                      'false_count':int((~marked & values.notna()).sum()),'missing':int(values.isna().sum()),
                      'warning':warning,'dimensions':labels,'unit':'行',**shared})
    return facts


def findings_text(facts, total_rows, retained_rows, chart, formatter, chart_source=None):
    complete = bool(facts and facts[0]['value'] == total_rows)
    scope = f'本次 SQL 完整返回的 {total_rows} 行' if complete else f'仅依据已保留的 {retained_rows} 行；本次查询共返回 {total_rows} 行'
    maxima = [fact for fact in facts if fact.get('statistic') == 'max'][:8 if total_rows == 1 else 3]
    evidence, findings = [], []
    traces = chart.get('data',[]) if isinstance(chart,dict) else []
    if len(traces)==1 and traces[0].get('type')=='bar':
        trace=traces[0];heading=str(chart.get('layout',{}))+str(trace.get('name',''))
        monetary=any(term in heading.lower() for term in ['金额','销售额','amount','revenue']) and not any(term in heading.lower() for term in ['%','占比','率','percentage','ratio','share'])
        change=any(term in heading.lower() for term in ['变化','差额','增减','change','delta'])
        values=trace.get('x' if trace.get('orientation')=='h' else 'y',[])
        labels=trace.get('y' if trace.get('orientation')=='h' else 'x',[])
        if monetary and change and values and len(labels)==len(values) and all(value is not None for value in values):
            amounts=[Decimal(str(value)) for value in values];net=sum(amounts)
            findings.append(f'图示各项金额变化合计为 {formatter(float(net))}（单位见坐标轴）。')
            index=amounts.index(min(amounts) if net<0 else max(amounts))
            share=f'，占图示净变化的 {amounts[index]/net:.2%}' if net else ''
            findings.append(f'主要变化项：{labels[index]}，变动 {formatter(float(amounts[index]))}{share}。')
            evidence.append('[图表数据 / SQL] 以上为图示变化额的算术贡献；上涨与下降会相互抵消，净变化贡献比例可能超过100%，不代表流失率或因果解释。')
            maxima=[]
    if len(traces)==1 and traces[0].get('type')=='funnel':
        trace=traces[0]; values=trace['x']
        stage_labels={'purchase':'下单','placed':'下单','approved':'审批','shipped':'发货','delivered':'签收'}
        stages=[stage_labels.get(str(stage).lower(),stage) for stage in trace['y']]
        rate=f'{values[-1]/values[0]:.2%}' if values[0] else '不适用（起点为零）'
        findings.append(f'从{stages[0]}的 {formatter(values[0])} 到{stages[-1]}的 {formatter(values[-1])}，全程到达率为 {rate}。')
        drops=[(stages[i-1],stages[i],values[i-1]-values[i]) for i in range(1,len(values))]
        before,after,lost=max(drops,key=lambda item:item[2])
        findings.append(f'绝对流失最多的环节：{before} → {after}，减少 {formatter(lost)}。')
        evidence.append('[图表数据 / SQL] 各阶段沿用同一统计对象和查询范围；绝对流失为相邻阶段差额，不代表流失原因。')
        maxima=[]
    heatmap=traces[0] if len(traces)==1 and traces[0].get('type')=='heatmap' else {}
    offset_axes={axis:bool(heatmap.get(axis)) and all(str(value).replace('.','',1).isdigit() for value in heatmap[axis]) for axis in ['x','y']}
    retention_rates=bool(heatmap) and all(value is None or 0<=value<=1 for row in heatmap.get('z',[]) for value in row)
    if heatmap and any(offset_axes.values()) and retention_rates and any(term in str(chart) for term in ['retention','留存','复购']):
        trace=traces[0]; xs,ys=trace['x'],trace['y']; grid=trace['z']
        offsets_on_x=offset_axes['x']
        later=[value for yi,row in enumerate(grid) for xi,value in enumerate(row)
               if value is not None and float(xs[xi] if offsets_on_x else ys[yi])>0]
        if later:
            metric='累计复购率' if '累计' in str(chart) and '复购' in str(chart) else '后续购买留存'
            findings.append(f'图中 {len(ys) if offsets_on_x else len(xs)} 个首购群组的{metric}为 {min(later):.2%}–{max(later):.2%}（不含首购期）。')
        else:
            findings.append('尚无可计算的后续购买留存，不能把首购期当作复购结论。')
        unavailable=sum(value is None for row in grid for value in row)
        if unavailable:
            findings.append(f'{unavailable} 个格子为未知或未满观察期，保留空白，不当作0%留存。')
        observed=next((fact['value'] for fact in facts if fact.get('statistic')=='min' and fact['label'] in {'observable_customers','eligible_customers','可观测客户数'}),None)
        if observed is not None and 0<observed<=10:
            findings.append(f'本次结果最小可观测样本仅为 {formatter(observed)}，比例估计不稳定，不能凭微小差异判定群组优劣。')
        evidence.append('[图表数据 / SQL] 分子为后续期间再次购买客户，分母与可观测性按各格SQL定义；观察不足不能当作0%。')
        maxima=[]
    if len(traces)==1 and traces[0].get('type') == 'waterfall':
        trace = traces[0]
        amounts = [Decimal(str(value)) for value in trace['y']]
        start,end = amounts[0],amounts[-1]
        change = end-start
        direction = '增加' if change>=0 else '减少'
        rate = f'，幅度 {abs(change/start)*100:.2f}%' if start else '，起点为零，变化率不适用'
        findings.append(f'{trace["x"][0]}为 {formatter(float(start))}，{trace["x"][-1]}为 {formatter(float(end))}；{direction} {formatter(float(abs(change)))}{rate}（单位见图表纵轴）。')
        contributions = sorted(zip(trace['x'][1:-1],amounts[1:-1]),key=lambda pair:abs(pair[1]),reverse=True)
        findings.append('绝对影响最大的贡献：'+'；'.join(f'{label}：{formatter(float(value))}' for label,value in contributions[:3])+'。')
        evidence.append(f'[图表数据 / SQL 前 {len(amounts)} 行] 加总核对：起点 + 全部贡献 = {formatter(float(start+sum(amounts[1:-1])))}；与终点差额 {formatter(float(start+sum(amounts[1:-1])-end))}。')
        maxima = []
    for maximum in maxima:
        minimum = next(f for f in facts if f.get('statistic') == 'min' and f['label'] == maximum['label'])
        raw_label = maximum['label']
        label = METRIC_LABELS.get(raw_label.lower().removesuffix('_brl'),raw_label)
        change=next((fact for fact in facts if fact.get('statistic')=='change' and fact['label']==raw_label),None)
        if maximum['value'] is None:
            findings.append(f'{label} 没有可用数值，不能得出高低或合计结论。')
        else:
            dimensions = '（' + '、'.join(str(value)[:10] for key,value in maximum['dimensions'].items() if not any(token in key for token in ['status','complete','partial'])) + '）' if maximum['dimensions'] else ''
            if total_rows == 1:
                findings.append(f'{label}：{formatter(maximum["value"])}{dimensions}。')
            elif change:
                delta=change['end_value']-change['start_value']; direction='增加' if delta>=0 else '减少'
                rate=f'，{abs(delta/change["start_value"]):.2%}' if change['start_value'] else '（起点为零，不计算变化率）'
                findings.append(f'{label}从{change["start_label"]}的 {formatter(change["start_value"])} 变为{change["end_label"]}的 {formatter(change["end_value"])}，{direction} {formatter(abs(delta))}{rate}；最高为 {formatter(maximum["value"])}{dimensions}。')
            else:
                findings.append(f'{label}最高为 {formatter(maximum["value"])}{dimensions}；最低为 {formatter(minimum["value"])}。')
        evidence.append(f'[{minimum["id"]} / {maximum["id"]}] {label}：{formatter(minimum["value"])} — {formatter(maximum["value"])}；缺失或非有限值 {maximum["missing"]} 行。')
    for fact in facts:
        if fact.get('statistic') == 'flag_count':
            evidence.append(f'[{fact["id"]}] {fact["label"]}：真 {fact["value"]} 行，假 {fact["false_count"]} 行，空值 {fact["missing"]} 行。')
            if fact.get('warning') and fact['value']:
                periods='、'.join(str(value)[:10] for row in fact['dimensions'] for name,value in row.items() if name in {'month','order_month','date','月份','日期'})
                findings.append(f'{periods}数据覆盖不完整，涉及 {fact["value"]} 条记录；不可直接与完整期间比较。')
    if not findings:
        findings.append('已取得查询结果；当前结果未包含可概括的数值指标，具体内容见结果表。')
    if not evidence:
        evidence.append(f'[F1] 已取得 {retained_rows} 行可见记录；未推断数值趋势。')
    chart_note = ''
    if chart:
        chart_note = (f"图表来源为查询返回的 {chart_source['source_rows']} 行，按图表规则选择绘制 {chart_source['plotted_points']} 个数据点；" if chart_source else '图表范围以实际绘图数据为准；') + '展开表格不会改变图表。'
    return ('主要发现\n' + '\n'.join(findings) + '\n\n数字依据\n' + '\n'.join(evidence) +
            '\n\n适用范围\n' + scope + '；受 SQL 筛选、聚合及 LIMIT 约束，不代表原始数据全量。' + chart_note +
            '自由 SQL 结果未经业务验证；关联粒度、币种和缺失值处理仍需核对。描述差异不能证明因果。' +
            '\n\n继续核对\n可继续按时间、地区或商品拆分，并核对极值对应记录与未匹配数据。')
