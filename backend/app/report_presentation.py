"""A report view over executed evidence; presentation never queries or calls a model."""
from decimal import Decimal

import pandas as pd

from app.analysis_report import build_evidence,result_origin
from app.insights import METRIC_LABELS, result_facts


def saved_result_source(metadata, path=(), step_id=''):
    """Resolve only saved workflow/investigation leaves, using report source paths."""
    if step_id:
        if path:
            raise ValueError('来源路径与旧步骤编号不能同时指定。')
        matches=[index for index,step in enumerate((metadata.get('workflow') or {}).get('steps',[])) if step.get('id')==step_id]
        if len(matches)!=1:
            raise ValueError('历史步骤不存在或编号不唯一。')
        path=['workflow','steps',matches[0],'result']
    remaining=list(path)
    while remaining:
        kind=remaining[0]
        size=4 if kind=='workflow' else 3
        if kind not in {'workflow','investigation'} or len(remaining)<size or remaining[1]!='steps' or type(remaining[2]) is not int:
            raise ValueError('结果来源路径无效。')
        steps=(metadata.get(kind) or {}).get('steps',[])
        index=remaining[2]
        if index<0 or index>=len(steps) or kind=='workflow' and remaining[3]!='result':
            raise ValueError('结果来源步骤不存在。')
        step=steps[index]
        if step.get('status')!='succeeded' or step.get('superseded'):
            raise ValueError('结果来源已失败或被替代。')
        metadata=step.get('result',{}) if kind=='workflow' else step
        remaining=remaining[size:]
    if any((metadata.get(kind) or {}).get('steps') for kind in ('workflow','investigation')):
        raise ValueError('请选择具体的调查子结果。')
    return metadata


def report_input(final):
    steps, paths = [], []
    def collect(meta, path, title, status='succeeded'):
        workflow = meta.get('workflow') or {}
        investigation = meta.get('investigation') or {}
        if workflow.get('steps'):
            for index, step in enumerate(workflow['steps']):
                collect(step.get('result', {}), path+['workflow','steps',index,'result'], step['request'], step['status'] if status=='succeeded' else status)
        elif investigation.get('steps'):
            for index, step in enumerate(investigation['steps']):
                collect(step, path+['investigation','steps',index], step['question'],
                    'skipped' if step.get('superseded') else step['status'] if status=='succeeded' else status)
        else:
            step = {**meta, 'question':title, 'status':status}
            if step.get('rows') and not step.get('facts'):
                step['facts'] = result_facts(pd.DataFrame(step['rows'], columns=step['columns']), step.get('sql',''), step.get('data_version'))
            steps.append(step); paths.append(path)
    collect(final, [], final.get('question','分析结果'))
    status=(final.get('workflow') or final.get('investigation') or {}).get('status','completed')
    return {'steps':steps,'status':status,'reason':''}, paths


def _label(column):
    unit='（BRL）' if column.lower().endswith('_brl') else ''
    return METRIC_LABELS.get(column.lower().removesuffix('_brl'),column.removesuffix('_brl'))+unit


def _table(step, cards):
    changes=[card for card in step.get('facts',[]) if card.get('statistic')=='change']
    decomposition=next((card for card in cards if card['kind']=='quantity_value'),None)
    if step.get('row_count')==2 and changes and not any(f.get('missing',0) for f in step.get('facts',[])) and len({(c['start_label'],c['end_label']) for c in changes})==1:
        start,end=changes[0]['start_label'],changes[0]['end_label']
        metrics=[{'label':_label(c['label']),**{k:c[k] for k in ('start_value','end_value')}} for c in changes]
        if decomposition and len(metrics)==2 and not any(c['label'].startswith('每单') or '客单' in c['label'] for c in metrics):
            metrics.append(decomposition['comparison'])
        rows=[]
        for metric in metrics:
            before,after=(Decimal(str(metric[key])) for key in ('start_value','end_value'))
            rows.append({'指标':metric['label'],start:float(before),end:float(after),'差额':float(after-before),
                '相对变化':f'{(after-before)/abs(before):+.2%}' if before else '起点为零，不计算变化率'})
        return {'kind':'comparison','columns':['指标',start,end,'差额','相对变化'],'rows':rows,
            'total_rows':len(rows),'shown_rows':len(rows),'caption':'差额和相对变化由同一查询的实际两期结果计算；金额单位沿用本轮确认口径，原始结果保留实际精度。'}
    rows=step.get('rows',[])[:12]
    columns=step.get('columns',[])
    formats={}
    for trace in (step.get('chart_spec') or {}).get('data',[]):
        meta=trace.get('meta') or {}
        if meta.get('y_column') in columns and meta.get('value_scale') in {'ratio','percent'}:
            formats[meta['y_column']]=meta['value_scale']
    return {'kind':'result','columns':columns,'rows':rows,'formats':formats,'total_rows':step.get('row_count',len(rows)),
        'shown_rows':len(rows),'caption':f"展示 {len(rows)} / {step.get('row_count',len(rows))} 行实际结果；未知值保留，不当作零。"+('表中说明为工具原文，不作为已核对结论。' if step.get('python_code') else '')}


def build_analysis_report(final):
    if final.get('error') or final.get('validation_failure') or final.get('route') in {'chat','clarify','refuse','cleaning'}:
        return None
    if final.get('workflow') and not any(step.get('tool')=='analyze' for step in final['workflow']['steps']):
        return None
    result,paths=report_input(final)
    if result['status'] in {'waiting','running','clarify','cancelled'}:
        return None
    usable=[step for step in result['steps'] if step['status']=='succeeded' and isinstance(step.get('rows'),list) and step.get('columns') and not (step.get('error') or step.get('validation_failure'))]
    if not usable or len(usable)==1 and usable[0].get('row_count')==1 and len(usable[0]['columns'])==1:
        return None
    cards=build_evidence(result); by_id={card['id']:card for card in cards}
    sources=[]
    for index,step in enumerate(result['steps'],1):
        if step not in usable:
            continue
        selected=[card for card in cards if card['step']==index]
        tool=result_origin(step)
        sources.append({'key':f'S{index}','path':paths[index-1],'label':step['question'],'tool':tool,
            'has_chart':bool((step.get('chart_spec') or {}).get('data')),
            'scope':selected[0]['scope'] if selected else f"{tool}实际结果，共{step.get('row_count',0)}行",
            'table':_table(step,selected)})
    synthesis=(final.get('investigation') or {}).get('synthesis') or final.get('analysis_report') or {}
    if final.get('workflow') and not final.get('analysis_report'):
        synthesis=next((step.get('result',{}).get('analysis_report') for step in reversed(final['workflow']['steps'])
            if step['status']=='succeeded' and step.get('result',{}).get('analysis_report')),synthesis)
    findings=[]
    for finding in synthesis.get('findings',[]):
        refs=[ref for ref in finding.get('evidence_ids',[]) if ref in by_id]
        if refs:
            linked=[by_id[ref] for ref in refs]
            computed=next((card for card in linked if card['kind'] in {'quantity_value','contribution'}),None)
            title,separator,continuation=(finding.get('text') or finding['title']).partition('；')
            reason=finding.get('reason') or (computed['text'] if computed else '')
            if separator and continuation not in reason:
                reason=continuation+'。'+reason
            findings.append({'title':computed.get('title',title) if computed else title,'reason':reason,'kind':finding.get('kind','observation'),
                'recommendation':finding.get('recommendation',''),'evidence_ids':refs,
                'source_keys':list(dict.fromkeys(ref.split('.')[0] for ref in refs))})
    # Related volume/value findings belong to one readable comparison, not repeated full tables.
    for source in sources:
        derived=next((card for card in cards if card['kind']=='quantity_value' and f"S{card['step']}"==source['key']),None)
        related=[finding for finding in findings if finding['source_keys']==[source['key']]]
        if derived and len(related)>1:
            first=related[0]
            first.update(title=derived['title'],reason='\n\n'.join(dict.fromkeys([derived['text'],*[finding['reason'] for finding in related if finding['reason'] and finding['reason']!=derived['text']]])),
                evidence_ids=list(dict.fromkeys(ref for finding in related for ref in finding['evidence_ids'])),
                recommendation='；'.join(dict.fromkeys(finding['recommendation'] for finding in related if finding['recommendation'])))
            findings=[finding for finding in findings if finding not in related[1:]]
        boxes=[finding for finding in findings if finding['source_keys']==[source['key']] and finding['title']=='箱线图分组统计与离群观测核对']
        if len(boxes)>1:
            boxes[0].update(reason='\n\n'.join(dict.fromkeys(part for finding in boxes for part in finding['reason'].split('\n\n'))),
                evidence_ids=list(dict.fromkeys(ref for finding in boxes for ref in finding['evidence_ids'])),
                recommendation='；'.join(dict.fromkeys(finding['recommendation'] for finding in boxes if finding['recommendation'])))
            findings=[finding for finding in findings if finding not in boxes[1:]]
    covered={key for finding in findings if finding.get('kind')!='method' for key in finding['source_keys']}
    for source in sources:
        if source['key'] in covered:
            continue
        index=int(source['key'][1:]);step=result['steps'][index-1]
        selected=[card for card in cards if card['step']==index]
        derived=next((card for card in selected if card['kind']=='quantity_value'),None)
        changes=[card for card in selected if card['kind']=='change']
        references=[card['id'] for card in ([derived] if derived else changes or [card for card in selected if card['kind']=='row'][:2])]
        title=derived['title'] if derived else ('；'.join(_label(f['label'])+('上升' if f['end_value']>f['start_value'] else '下降' if f['end_value']<f['start_value'] else '持平')
            for f in step.get('facts',[]) if f.get('statistic')=='change') or _label(step['columns'][-1])+'结果对照')
        reason=derived['text'] if derived else '；'.join(card['text'] for card in changes)
        if step.get('row_count')==0:
            title='没有符合当前筛选条件的记录'
            reason='查询已成功执行，返回0行；字段结构与执行依据已保留。这不表示源表没有数据，也不把未知指标当作零。'
            references=[card['id'] for card in selected if card['kind']=='execution_receipt']
        elif not reason:
            reason='以下为实际计算结果，按原始粒度和单位展示；分组差异本身不能解释业务原因。'
        findings.append({'title':title,'reason':reason,'recommendation':'','evidence_ids':references,'source_keys':[source['key']]})
    editorial=[warning for warning in synthesis.get('warnings',[]) if warning.startswith(('部分解释或建议','一项理由或建议','综合解释'))]
    warnings=[warning for warning in synthesis.get('warnings',[]) if warning not in editorial]
    for step in result['steps']:
        if step['status'] in {'failed','skipped','cancelled'}:
            warnings.append(step['question']+'：未完成，不能作为已回答的结论。')
        warnings.extend(check['detail'] for check in step.get('checks',[]) if check.get('status') in {'warning','failed'})
        if step.get('chart_error'):
            warnings.append('图表未完成：'+step['chart_error']+'；实际计算结果仍保留。')
        if step.get('result_error'):warnings.append(step['result_error'])
    versions={step.get('data_version') for step in usable if step.get('data_version')}
    if len(versions)>1:
        warnings.append('不同步骤来自不同数据版本，不能直接合并比较。')
    scope='仅依据当前导入数据及对应成功查询；筛选、观察窗口和分母以各项证据为准。算术贡献、统计关联与预测不能证明业务因果。'
    if synthesis.get('scope_declarations'):
        scope+='\n\n'+'\n'.join('按用户声明：'+text for text in synthesis['scope_declarations'])
    summary='\n'.join(card['summary'] for card in cards if card.get('summary')) if len(versions)<=1 else ''
    coverage=synthesis.get('coverage',[])
    report={'version':1,'status':'partial' if any(item['status']!='answered' for item in coverage) else result['status'],
        'acceptance_version':synthesis.get('acceptance_version'),'report_status':synthesis.get('report_status'),
        'summary':summary,'coverage':coverage,'brief':final.get('analysis_brief',{}),'findings':findings,'sources':sources,'evidence':cards,
        'warnings':list(dict.fromkeys(warnings)),'editorial_notes':list(dict.fromkeys(editorial)),'hypotheses':synthesis.get('hypotheses',[]),
        'verification_note':synthesis.get('verification_note',''),
        'next_checks':synthesis.get('next_checks',[]),'scope':scope}
    report['answer']='\n\n'.join(part for part in (final.get('dependency_response',''),report_markdown(report)) if part)
    return report


def report_markdown(report):
    from app.agent.nodes import _fmt_cell
    sources={source['key']:source for source in report['sources']}; evidence={card['id']:card for card in report['evidence']}
    lines=['## 分析结论与建议']
    shown_refs=set();shown_tables=set()
    if report.get('summary'):
        lines.append(report['summary'])
    if report.get('verification_note'):
        lines.append(report['verification_note'])
    if report.get('coverage'):
        lines.extend(['### 本轮问题覆盖',*['- '+item['requirement']+'：'+{'answered':'已回答','linked':'已提供结果，尚有缺口','needs_input':'需要补充数据或口径','unanswered':'尚未回答'}[item['status']]+('；'+item['reason'] if item.get('reason') else '') for item in report['coverage']]])
        for item in report['coverage']:
            for ref in item.get('evidence_ids',[]):
                if ref in evidence and ref not in shown_refs:
                    lines.append(f"- [{ref}] {evidence[ref]['text']}");shown_refs.add(ref)
    def cell(value,money=False,scale=None):
        if isinstance(value,(int,float)) and scale in {'ratio','percent'}:
            percent=value*(100 if scale=='ratio' else 1)
            text=(f'{percent:.4g}' if percent and abs(percent)<.01 else f'{percent:.2f}')+'%'
        elif money and isinstance(value,(int,float)) and (abs(value)>=.01 or value==0):
            text=f'{value:,.2f}'
        else:
            text=_fmt_cell(value)
        return text.replace('|','\\|').replace('\n',' ')
    for index,finding in enumerate(report['findings'],1):
        lines.extend([f"### {index}. {finding['title']}",finding['reason']])
        for key in ([] if finding.get('kind')=='method' else finding['source_keys']):
            if key in shown_tables:continue
            shown_tables.add(key)
            source=sources[key];table=source['table'];columns=table['columns']
            table_lines=['| '+' | '.join(cell(c) for c in columns)+' |','| '+' | '.join('---' for _ in columns)+' |',
                *['| '+' | '.join(cell(row.get(c),money='（BRL）' in str(row.get('指标')),scale=table.get('formats',{}).get(c)) for c in columns)+' |' for row in table['rows']]]
            lines.extend(['\n'.join(table_lines),table['caption']])
        for ref in finding['evidence_ids']:
            if ref not in shown_refs:
                lines.append(f"- [{ref}] {evidence[ref]['text']}");shown_refs.add(ref)
        if finding['recommendation']:
            lines.append('建议：'+finding['recommendation'])
    if report['hypotheses']:
        lines.extend(['### 值得追查的解释（尚未证实）',*report['hypotheses']])
    if report['next_checks']:
        lines.extend(['### 下一步分析',*report['next_checks']])
    lines.extend(['### 适用范围',report['scope'],*report['warnings']])
    if report.get('editorial_notes'):
        lines.extend(['### 汇报依据核对记录',*report['editorial_notes']])
    return '\n\n'.join(lines)
