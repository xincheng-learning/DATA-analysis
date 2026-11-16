"""Accept the supported answer parts from actual metric evidence and deliverables."""
import re

from app.metrics import METRICS


def metric_id(column):
    name=re.sub(r'_(brl|cny|usd|eur|gbp)$','',column.lower())
    aliases={'avg_item_amount_per_order':'average_order_amount','average_item_amount_per_order':'average_order_amount',
        'avg_order_item_amount':'average_order_amount','delivered_order_count':'delivered_orders'}
    name=aliases.get(name,name)
    return name if name in METRICS else None


def prepare_brief(state,brief,*,dataset=None,messages=()):
    question=state.get('user_request') or state.get('question','')
    users={m.get('id'):m['content'] for m in messages if m['role']=='user' and (m.get('metadata') or {}).get('type')!='upload'}
    bodies={question,*users.values()}
    plan=brief.get('calculation_plan')
    if plan:
        actual=[q for q in plan.get('source_quotes',[]) if q and any(q in body for body in bodies)]
        brief={**brief,'calculation_plan':{**plan,'source_quotes':list(dict.fromkeys([question[:1000],*actual]))[:8]},
            'plan_provenance':'程序绑定实际用户请求；来源记录不证明计划理解正确。',
            'unmatched_plan_quotes':[q for q in plan.get('source_quotes',[]) if q not in actual]}
    def confirmed(source):
        if source['text'] in question:
            source={**source,'message_id':None,'source_question':question}
        body=users.get(source['message_id'],'') if source.get('message_id') else source.get('source_question',question)
        if not source.get('message_id') and not source.get('source_question') and source['text'] not in body:
            match=next(((mid,text) for mid,text in reversed(list(users.items())) if source['text'] in text),None)
            if match:
                source={**source,'message_id':match[0]};body=match[1]
        if body not in bodies or source['text'] not in body:return None
        if body!=question and not source.get('message_id'):
            source={**source,'message_id':next(mid for mid,text in reversed(list(users.items())) if text==body)}
        return {**source,'source_question':body}
    previous=next((m.get('metadata',{}).get('analysis_brief',{}) for m in reversed(messages)
        if m['role']=='assistant' and m.get('metadata',{}).get('analysis_brief')), {})
    update_report=any(re.search(r'更新(?:这|该)份(?:简报|报告)',clause)
        and not re.search(r'(?:不要|不必|无需|不用|不需要|别|没有|尚未|未曾)(?:再)?更新|(?:为什么|为何|怎么|如何|是否).*更新',clause)
        for clause in re.split(r'[，,。；;\n]',question))
    if not state.get('internal_analysis') and update_report:
        requirements=list(brief.get('requirements',[]));criteria=list(brief.get('acceptance',[]))
        only_values=bool(re.search(r'(?:只要|只需(?:要)?|仅需(?:要)?|仅(?:输出|提供|保留))(?:新的?|本轮)?(?:指标|金额|数值|数字|结果|总额|合计|销售额|订单数|收入|利润|净额|汇总)',question))
        for old in previous.get('acceptance',[]):
            kind=old.get('answer_type');columns=old.get('result_columns',[]);metrics=old.get('metric_ids',[])
            if kind not in {'chart','table'} or not (columns or metrics):continue
            target=r'(?:图表|图)' if kind=='chart' else r'(?:表格|结果表|明细表|数据表)'
            cancelled=re.search(r'(?:不要|不需要|无需|不必|不用|取消|去掉|移除)(?:再|任何)?(?:绘制|画|生成|输出|展示|提供|保留)?'
                r'(?:(?:图表|图|表格|结果表|明细表|数据表)(?:和|与|及|、|或))*'+target,question)
            if only_values or cancelled:continue
            if any(c.get('answer_type')==kind and set(columns)<=set(c.get('result_columns',[]))
                and set(metrics)<=set(c.get('metric_ids',[])) for c in criteria):continue
            index=len(requirements)
            if index>=12:break
            requirements.append(('沿用图表' if kind=='chart' else '沿用结果表')+'（按本轮条件更新）：'+'、'.join(columns or metrics))
            # Carry delivery targets only; old stages and execution conditions must be replanned.
            criteria.append({'answer_type':kind,'metric_ids':metrics,'result_columns':columns,
                'requirement_index':index,'request_quote':question,'result_stage':'','inherited_delivery':True})
        brief={**brief,'requirements':requirements,'acceptance':criteria}
    continuation=[]
    if brief.get('revision_quote') and brief['revision_quote'] in question or re.search(r'其他(?:条件)?不变|沿用|按当前范围|按刚才|继续',question):
        origins=[*previous.get('continuation_sources',[]),*[
            {'key':'prior_request','text':s['request_quote']} for s in previous.get('acceptance',[]) if s.get('request_quote')]]
        for origin in origins:
            valid=confirmed(origin)
            if valid and valid not in continuation:continuation.append(valid)
    scopes={}
    if '当前问题：' in question:
        context=question.rsplit('当前问题：',1)[0].strip()
        for i in range(0,len(context),1000):
            scopes[f'provided_context:{i//1000}']={'key':f'provided_context:{i//1000}','text':context[i:i+1000],'message_id':None,'source_question':question}
    for source in [*previous.get('scope_sources',[]),*brief.get('scope_sources',[])]:
        valid=confirmed(source)
        if valid:
            scopes[valid['key']]=valid
    current=question.rsplit('当前问题：',1)[-1]
    calendar=re.compile(r'\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?|(?:\d{4}年)?(?:\d{1,2}|[一二三四五六七八九十]+)月(?:\d{1,2}(?:[—~～至-]\d{1,2})?日)?|(?:\d{4}年)?[一二三四]季度|(?:最近|过去|近)[一二三四五六七八九十两\d]+个?(?:自然)?[天周月年]|本月|上月|本季度|上季度|今年|去年|本周|上周')
    linked=bool(continuation or brief.get('revision_quote') and brief['revision_quote'] in question
        or re.search(r'^(?:那|再|现在|仍|只|改)|沿用|继续|其他(?:条件)?不变|对应|上述|这些|它们|这份|这张|刚才',current))
    if re.search(r'取消时间(?:筛选|限制)|不限时间|所有月份|全部期间|不沿用(?:原|之前)?(?:时间|月份)',current):
        scopes={k:v for k,v in scopes.items() if k not in {'period','selected_period'}}
        continuation=[]
    else:
        prior=next((s for s in previous.get('scope_sources',[]) if s['key']=='selected_period'),None)
        if not linked:scopes.pop('selected_period',None)
        if linked and prior and (valid:=confirmed(prior)):scopes['selected_period']=valid
        periods=list(calendar.finditer(current))
        if periods:
            quote=current[periods[0].start():periods[-1].end()]
            if len(quote)<=1000:scopes['selected_period']={'key':'selected_period','text':quote,'message_id':None,'source_question':question}
        elif linked and 'selected_period' not in scopes:
            for message in reversed(messages):
                if message['role']!='user' or (message.get('metadata') or {}).get('type')=='upload':continue
                body=message['content'].rsplit('当前问题：',1)[-1]
                if re.search(r'取消时间(?:筛选|限制)|不限时间|所有月份|全部期间',body):break
                periods=list(calendar.finditer(body))
                if periods:
                    quote=body[periods[0].start():periods[-1].end()]
                    if len(quote)<=1000:scopes['selected_period']={'key':'selected_period','text':quote,'message_id':message['id'],'source_question':message['content']}
                    break
    from app.formula_contract import explicit_products
    for key in list(scopes):
        if key.startswith('formula:') and re.search(re.escape(key.split(':',1)[1])+r'\s*(?:改为|调整为|定义为|为|等于|[=＝])',current):
            del scopes[key]
    for label, _, _, quote in explicit_products(question):
        scopes[('formula:'+label)[:50]]={'key':('formula:'+label)[:50],'text':quote,'message_id':None,'source_question':question}
    columns={f'{f.table_name}.{column}' for f in dataset.files for column in f.columns} if dataset else set()
    bindings={}
    for binding in [*previous.get('metric_bindings',[]),*brief.get('metric_bindings',[])]:
        valid=confirmed(binding['confirmation'])
        if valid and set(binding['columns'])<=columns:
            bindings[binding['role']]={**binding,'confirmation':valid}
    acceptance=[]
    for index,requirement in enumerate(brief.get('requirements') or [question]):
        specs=[s for s in brief.get('acceptance',[]) if s['requirement_index']==index]
        spec=specs[0] if len(specs)==1 else {}
        inferred=[m for column in spec.get('result_columns',[]) if (m:=metric_id(column)) and METRICS[m].get('profile')=='requires_mapping'
            and not METRICS[m].get('requires_explicit_metric_id')]
        if inferred:spec={**spec,'metric_ids':list(dict.fromkeys([*spec.get('metric_ids',[]),*inferred]))}
        # shortcut: explicit all-time settled sum, not the period refund catalog metric.
        stages=(brief.get('calculation_plan') or {}).get('stages',[])
        if spec.get('metric_ids')==['refund_amount'] and len(stages)==1:
            stage=stages[0];selected=stage.get('select',[]);filters=stage.get('filters',[])
            from app.temporal_scope import PERIOD
            if (spec.get('answer_type') in {'value','table'} and spec.get('request_quote') and spec['request_quote'] in question and not PERIOD.search(spec['request_quote'])
                and not any(s['key'] in {'period','time_range','date_range'} for s in scopes.values())
                and len(selected)==1 and selected[0].get('op')=='sum' and selected[0].get('name') in spec.get('result_columns',[])
                and selected[0].get('field') in columns and selected[0]['field'].startswith(stage['source']+'.')
                and not stage.get('joins') and not stage.get('group_by') and len(filters)==1
                and filters[0].get('field') in columns and filters[0]['field'].startswith(stage['source']+'.')
                and filters[0].get('op')=='eq' and filters[0].get('value')=='settled'
                and re.search(r'(?:退款|refunds?)[^。;\n]{0,30}(?:仅|only|=|为)\s*[\'\"]?settled\b',question,re.I)):
                spec={**spec,'metric_ids':[],'custom_metric_reason':'按用户明确settled规则直接求和，不套用期间退款字典。'}
        quote=spec.get('request_quote','')
        if quote and quote not in question:
            spec={**spec,'request_quote':'','unverified_request_quote':quote}
        if spec.get('result_stage') and spec.get('result_columns'):
            matches=[s['name'] for s in (brief.get('calculation_plan') or {}).get('stages',[]) if set(spec['result_columns'])<={e['name'] for e in s['select']}]
            if spec['result_stage'] not in matches and len(matches)==1:
                spec={**spec,'result_stage':matches[0],'binding_note':'原阶段不含要求列；按唯一完整输出阶段修正引用，未改变要求列或计算。'}
        acceptance.append({**spec,'id':f'R{index+1}','requirement_index':index,'requirement':requirement})
    if brief.get('calculation_plan'):
        from app.calculation_contract import prepare_daily_window
        brief={**brief,'calculation_plan':prepare_daily_window(brief['calculation_plan'],acceptance,
            '\n'.join([question,*[s['text'] for s in scopes.values()]]))}
    dependencies=[]
    for identifier in dict.fromkeys(m for spec in acceptance for m in spec.get('metric_ids',[])):
        definition=METRICS.get(identifier,{})
        if definition.get('profile')!='requires_mapping':continue
        missing=[role for role in definition['required_roles'] if role not in bindings]
        policy=identifier+'_policy' in scopes or 'metric_policy' in scopes
        needs_policy=definition.get('requires_policy_confirmation') and not policy
        labels={'revenue':'收入字段及其含义','costs':'实际商品成本','payments':'已结算支付流水','refunds':'已结算退款流水'}
        gaps=[labels.get(role,role) for role in missing]+(['核算范围与费用分摊口径'] if needs_policy else [])
        dependencies.append({'metric_id':identifier,'status':'needs_input' if gaps else 'ready',
            'missing':gaps,'definition':definition['definition'],
            'reason':('缺少或尚未确认：'+'、'.join(gaps)+'。'+('收付净额不能替代利润。' if identifier=='profit' else '')) if gaps else '所需字段映射及用户口径已确认；仍需执行计算并核对方法。'})
    return {**brief,'goal':brief.get('goal') or question,'requirements':[s['requirement'] for s in acceptance],
        'acceptance':acceptance,'scope_sources':list(scopes.values()),'confirmed_scope':[s['text'] for s in scopes.values()],
        'metric_bindings':list(bindings.values()),'metric_dependencies':dependencies,'contract_version':1,'continuation_sources':continuation[:12],
        'revision_quote':brief.get('revision_quote','') if brief.get('revision_quote','') in question else '',
        'intent_review':{'status':'quote_bound' if all(s.get('request_quote') and not s.get('intent_gap') for s in acceptance) else 'unverified',
            'quote_bound_ids':[s['id'] for s in acceptance if s.get('request_quote')],
            'unverified_ids':[s['id'] for s in acceptance if not s.get('request_quote')],
            'gaps':[{'requirement_id':s['id'],'reason':s['intent_gap']} for s in acceptance if s.get('intent_gap')]}}


def dependency_requirements(brief):
    dependencies={d['metric_id']:d for d in brief.get('metric_dependencies',[])}
    return [spec['id'] for spec in brief.get('acceptance',[]) if spec.get('intent_gap') or spec.get('metric_ids')
        and set(spec['metric_ids'])<=dependencies.keys() and (spec.get('answer_type') in {'definition','feasibility'}
        or all(dependencies[m]['status']!='ready' for m in spec['metric_ids']))]


def dependency_answer(brief):
    definitions={d['metric_id']:d for d in brief.get('metric_dependencies',[])}
    requested=[s for s in brief.get('acceptance',[]) if any(m in definitions and
        (s.get('answer_type') in {'definition','feasibility'} or definitions[m]['status']!='ready') for m in s.get('metric_ids',[]))]
    lines=[f"**{spec['requirement']}**：需确认——{spec['intent_gap']}" for spec in brief.get('acceptance',[]) if spec.get('intent_gap')]
    if not requested:return '\n\n'.join(lines)
    for spec in requested:
        for identifier in spec['metric_ids']:
            if identifier not in definitions or spec.get('answer_type') not in {'definition','feasibility'} and definitions[identifier]['status']=='ready':continue
            item=definitions[identifier];label=METRICS[identifier]['label']
            if spec.get('answer_type')=='feasibility':lines.append(f"{label}当前{'具备已确认的必需输入，可进入计算；尚未生成结果' if item['status']=='ready' else '不能计算'}。")
            lines.append(f"**{label}**：{item['definition']}。")
            if spec.get('answer_type')!='definition' and item['status']!='ready':lines.append(item['reason'])
    scope=brief.get('confirmed_scope',[])
    if scope:lines.append('沿用已确认范围：'+'；'.join(scope)+'。')
    if any(s.get('answer_type')!='definition' for s in requested) and any(d['status']!='ready' for d in definitions.values()):lines.append('请补充缺少的数据及字段映射，并确认核算口径；已有计算结果继续保留。')
    return '\n\n'.join(dict.fromkeys(lines))


def calculation_brief(brief):
    """Keep original acceptance intact; ask executors only for available targets."""
    blocked={d['metric_id'] for d in brief.get('metric_dependencies',[]) if d['status']!='ready'}
    covered=dependency_requirements(brief)
    active=brief.get('active_requirement_ids',[s['id'] for s in brief.get('acceptance',[])])
    specs=[]
    for spec in brief.get('acceptance',[]):
        if spec['id'] not in active or spec['id'] in covered or spec.get('answer_type')=='scope':continue
        if set(spec.get('metric_ids',[]))&blocked:
            metrics=[m for m in spec.get('metric_ids',[]) if m not in blocked]
            columns=[c for c in spec.get('result_columns',[]) if metric_id(c) not in blocked]
            targets=[METRICS.get(m,{}).get('label',m) for m in metrics]+columns
            spec={**spec,'metric_ids':metrics,'result_columns':columns,'requirement':'本项只计算：'+'、'.join(targets)+'；缺少依赖的指标不生成数值。'}
        specs.append(spec)
    return {**{key:value for key,value in brief.items() if key!='unverified_constraints'},'acceptance':specs,'requirements':[s['requirement'] for s in specs]}


def assess_coverage(brief,report):
    cards={c['id']:c for c in report.get('evidence',[])}
    dependencies={d['metric_id']:d for d in brief.get('metric_dependencies',[])}
    coverage=[]
    for index,spec in enumerate(brief.get('acceptance',[])):
        original=next((c for i,c in enumerate(report.get('coverage',[])) if c.get('requirement_id')==spec['id'] or not c.get('requirement_id') and i==index),{})
        refs=original.get('evidence_ids',[])
        if not refs and report.get('status')=='fallback' and spec.get('answer_type') in {'value','table'}:
            refs=[card['id'] for card in cards.values() if card['kind']=='execution_receipt' and spec['id'] in card.get('requirement_ids',[])
                and (spec['answer_type']=='table' or card.get('row_count',0)>0)]
        linked=bool(refs) and all(ref in cards for ref in refs)
        selected=[cards[ref] for ref in refs if ref in cards]
        steps={c['step'] for c in selected}
        receipts=[c for c in cards.values() if c['kind']=='execution_receipt' and c['step'] in steps and spec['id'] in c.get('requirement_ids',[])]
        bound_steps={c['step'] for c in receipts}
        selected=[c for c in selected if c['step'] in bound_steps]
        metrics=set(spec.get('metric_ids',[]));columns=set(spec.get('result_columns',[]))
        observed={c.get('metric_id') for c in selected}|{m for c in selected for m in c.get('metric_ids',[])}
        present={column for c in receipts for column in c.get('columns',[])}|{column for c in selected if c['kind']=='box_statistics' for column in c.get('columns',[])}
        blocked=[dependencies[m] for m in metrics if m in dependencies and dependencies[m]['status']!='ready']
        status='linked' if linked else 'unanswered'
        reason='证据已关联，回答要求尚未完成核对。' if linked else '尚无对应的有效计算证据。'
        kind=spec.get('answer_type')
        expected=dependency_answer({**brief,'acceptance':[spec]}) if kind in {'definition','feasibility'} else ''
        boundary=next((item for item in report.get('method_boundaries',[]) if item['requirement_id']==spec['id']),None)
        if spec.get('intent_gap'):
            status='needs_input';reason=spec['intent_gap']
        elif kind in {'definition','explanation'} and spec.get('method_topic')=='causal_boundary' and boundary:
            refs=boundary['evidence_ids'];linked=True;status='answered';reason='已给出程序生成的因果解释边界并关联实际执行；不宣称识别了因果效应。'
        elif kind=='scope':
            declared=next((source for source in brief.get('scope_sources',[]) if source['key']==spec.get('scope_key')),None)
            if declared:status='answered';reason='按用户声明：'+declared['text']+'（未作为程序独立证明的数据来源）。'
            else:status='unanswered';reason='尚无可核对的用户原话，未确认此来源声明。'
        elif expected and set(expected.split('\n\n'))<=set(report.get('dependency_response','').split('\n\n')):
            status='answered';reason='已说明指标定义、可计算性与实际依赖缺口，没有编造指标值。'
        elif blocked:
            status='needs_input';reason='；'.join(d['reason'] for d in blocked)
        elif not kind:
            reason='这项要求没有可核对的指标或交付约束；保留已有结果。'
        elif linked and not receipts:
            reason='引用来源尚未绑定本项要求。'
        elif kind=='constraint':
            checked=any(check.get('id')=='execution_constraint' and check.get('status')=='passed'
                and check.get('requirement_id')==spec['id'] for receipt in receipts for check in receipt.get('checks',[]))
            if checked:status='answered';reason='执行限制有本项对应的实际核对回执。'
            else:reason='执行限制已保留；尚无本项的独立核对回执，不能仅因计算成功认定完成。'
        elif kind=='chart' and not (metrics or columns):
            status='unanswered';reason='图表要求尚未绑定明确的指标或结果列；已有表格可用，不据此认定图表已回答。'
        elif linked and receipts and not (metrics or columns) and spec.get('subject'):
            explained=kind in {'definition','explanation'} and report.get('status')=='generated' and any(
                spec['id'] in f.get('requirement_ids',[]) and f.get('reason') and f.get('evidence_ids')
                and set(f['evidence_ids'])<={c['id'] for c in selected} for f in report.get('findings',[]))
            if explained:status='answered';reason='已给出本项文字对象的说明并关联实际执行；不等同统计方法已被独立认证。'
            else:reason='文字说明对象已保留；尚无通过核对且绑定本项的说明。'
        elif linked and (metrics or columns) and metrics<=observed and columns<=present:
            complete=all(c.get('complete_result_available') for c in receipts) and bool(receipts)
            changes={value for c in selected if c['kind']=='change' for value in (c.get('metric_id'),c.get('metric')) if value}
            verified={m for c in receipts for m in c.get('verified_metric_ids',[])}
            calculation_checked = any(check.get('id') == 'calculation_contract' and check.get('status') == 'passed'
                and spec['id'] in check.get('requirement_ids', []) for receipt in receipts for check in receipt.get('checks', []))
            failed_calculation = any(check.get('id') == 'calculation_contract' and check.get('status') == 'failed'
                for receipt in receipts for check in receipt.get('checks', []))
            if failed_calculation:
                reason='对应计算核对未通过，不能认定本项已回答；已有结果与失败依据保留。'
            elif metrics and not metrics<=verified:
                reason='结果列已取得，但尚无对应指标定义与业务口径的核对回执。'
            elif kind=='comparison' and (metrics or columns)<=(changes):status='answered';reason='已按实际两期结果给出比较关系。'
            elif kind=='value':status='answered';reason='实际数值与声明计算计划一致，用户意图理解和方法适用性另行核对。' if calculation_checked else '所需指标在对应实际结果中可核对。'
            elif kind=='table' and complete:status='answered';reason='完整结果与声明计算计划一致；不据此认证计划理解了全部用户意图。' if calculation_checked else '完整结果可查看；聊天预览不代替完整表。'
            elif kind=='table':reason='完整结果尚不可取用；已保留可用的预览。'
            elif kind=='explanation' and report.get('status')=='generated' and any(f.get('reason') and f.get('evidence_ids') and set(f['evidence_ids'])<={c['id'] for c in selected} for f in report.get('findings',[])):
                status='answered';reason='说明与本项实际指标证据对应。'
            elif kind=='explanation':reason='计算结果已保留，分析解释尚未通过核对。'
            elif kind=='chart':
                drawn={m for c in selected if c['kind']=='chart_artifact' for m in c.get('metric_ids',[])}
                drawn_columns={m for c in selected if c['kind']=='chart_artifact' for m in [*c.get('columns',[]),*c.get('dimension_columns',[])]}
                if metrics<=drawn and columns<=drawn_columns:status='answered';reason='所需指标已出现在本项图表回执中。'
                else:reason='图表未覆盖本项要求的全部指标。'
        elif linked and kind:
            reason='引用的指标或结果列与本项要求不匹配。'
        coverage.append({'requirement_id':spec['id'],'requirement':spec['requirement'],'answer_type':kind,
            'linked':linked,'status':status,'evidence_ids':refs if linked else [],'reason':reason})
    return coverage
