"""Deterministic task preparation; readiness never certifies source completeness."""
import hashlib
import json

import duckdb

from app.answer_acceptance import calculation_brief
from app.calculation_contract import CalculationPlan,Expression,CalculationMismatch,compile_plan
from app.tools.data_workbench import workbench_snapshot


class PreparationChanged(CalculationMismatch):
    check_id='analysis_preparation'
    retryable=False


def _input_coverage(dataset,plan,question,confirmed):
    # shortcut: scoped null coverage for one-table plans; joins/multi-stage inputs stay unverified.
    if len(plan.stages)!=1 or plan.stages[0].joins:
        return None
    stage=plan.stages[0]
    fields=list(dict.fromkeys(item.field for item in stage.select if item.op in {'sum','mean','min','max','std_samp','std_pop'}))
    fields=list(dict.fromkeys([*fields,*[field for item in stage.select if item.op=='sum_product' for field in (item.numerator,item.denominator)]]))
    if not fields or len(fields)>31:return None
    probe=plan.model_copy(update={'ordered_rows':False,'ordered_columns':False,'row_count':None,
        'stages':[stage.model_copy(update={'select':[Expression(name='input_rows',op='count_rows'),
            *[Expression(name=f'known_{i}',op='count',field=field) for i,field in enumerate(fields)]],
            'group_by':[],'order_by':[]})]})
    query,_=compile_plan(dataset,probe,question,confirmed)
    checks=dataset.last_query_checks
    try:row=dataset.execute(query,as_frame=True).iloc[0]
    finally:dataset.last_query_checks=checks
    return {'input_rows':int(row['input_rows']),
        'missing_rows':{field:int(row['input_rows']-row[f'known_{i}']) for i,field in enumerate(fields)}}


def prepare_execution(state,dataset):
    brief=state['analysis_brief'];question=state.get('user_request') or state.get('question','')
    snapshot=workbench_snapshot(dataset) if dataset and dataset.files else None
    fingerprint=hashlib.sha256(json.dumps([snapshot,question,brief],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    cached=getattr(dataset,'_readiness_cache',None)
    if cached and cached['key']==fingerprint:
        context=cached['context']
    else:
        effective={s['id']:s for s in calculation_brief(brief).get('acceptance',[])}
        dependencies={d['metric_id']:d for d in brief.get('metric_dependencies',[])}
        decisions=[]
        for spec in brief.get('acceptance',[]):
            identifier=spec['id'];target=effective.get(identifier,{})
            pending=[dependencies[m]['reason'] for m in spec.get('metric_ids',[]) if m in dependencies and dependencies[m]['status']!='ready']
            numerical=spec.get('answer_type') in {'value','comparison','table','chart'} or bool(spec.get('metric_ids') or spec.get('result_columns'))
            status,reason='not_applicable','本项不要求执行数值计算。'
            if spec.get('intent_gap'):status,reason='needs_decision',spec['intent_gap']
            elif numerical and pending and not (target.get('metric_ids') or target.get('result_columns')):
                status,reason='needs_input','；'.join(pending)
            elif numerical and identifier not in effective:
                status,reason='not_applicable','本步骤不执行此项要求，保留其原有状态。'
            elif numerical and not snapshot:status,reason='needs_input','当前会话没有可分析的数据，请先导入所需数据。'
            elif numerical and not (target.get('metric_ids') or target.get('result_columns')):
                status,reason='blocked','内部分析目标未绑定指标或输出字段，尚未执行；这不是需要用户补填的业务口径。'
            elif numerical:
                status,reason='limited','输入尚未完成任务级独立核验；执行及交付仍需核对，不认证来源完整性。'
                if pending:reason+='；本项仅执行具备依赖的部分：'+'；'.join(pending)
            decisions.append({'requirement_id':identifier,'requirement':spec['requirement'],'status':status,'reason':reason})
        candidates=[d for d in decisions if d['status']=='limited']
        raw=brief.get('calculation_plan')
        coverage=None;used=set()
        if raw:
            used={name for stage in raw['stages'] for name in [stage['source'],*[join['table'] for join in stage.get('joins',[])]]}
        if candidates and raw and not brief.get('workflow_plan'):
            try:
                plan=CalculationPlan.model_validate(raw)
                confirmed=[source[key] for source in [*brief.get('scope_sources',[]),*brief.get('continuation_sources',[])]
                    for key in ('text','source_question') if source.get(key)]
                requested=list(dict.fromkeys(column for d in candidates for column in effective[d['requirement_id']].get('result_columns',[])))
                from app.temporal_scope import verify_temporal_scope
                verify_temporal_scope(calculation_brief(brief),question)
                from app.business_contract_binding import contract_compile_inputs
                sources,windows=contract_compile_inputs(brief,state.get('session_id'),dataset)
                query,_=compile_plan(dataset,plan,question,[*confirmed,*sources],requested,authorized_windows=windows)
                dataset.get_connection().execute('DESCRIBE '+query).fetchall()
                coverage=_input_coverage(dataset,plan,question,confirmed)
                missing=coverage and any(coverage['missing_rows'].values())
                for decision in candidates:
                    spec=next(s for s in brief['acceptance'] if s['id']==decision['requirement_id'])
                    pending=any(m in dependencies and dependencies[m]['status']!='ready' for m in spec.get('metric_ids',[]))
                    if missing:
                        decision['reason']='本轮筛选后的输入有未知值，数值仅覆盖已知部分，不能当完整总额或全量平均；不自动补零。'
                    elif coverage and coverage['input_rows'] and not pending:
                        decision.update(status='ready',reason='声明字段和计划可解析，本轮筛选内已检查的聚合输入无空值；不证明业务来源齐全或模型理解正确。')
            except (ValueError,duckdb.Error) as exc:
                for decision in candidates:decision.update(status='blocked',reason='执行准备未通过，尚未生成代码：'+str(exc))
        runnable=[d for d in decisions if d['status'] in {'ready','limited'}]
        waiting=[d for d in decisions if d['status'] in {'needs_decision','needs_input','blocked'}]
        readiness={'fingerprint':fingerprint,'status':'partial' if runnable and waiting else 'blocked' if waiting else 'limited' if any(d['status']=='limited' for d in runnable) else 'ready',
            'source_completeness':'unverified','input_coverage':coverage,'requirements':decisions}
        context={'data_version':snapshot,'scope':'按本轮声明计划与筛选检查；未支持的输入覆盖保持未核验，不以整表体检作为执行放行依据。',
            'tables':[{'table':file.table_name,'rows':file.row_count,'data_version':file.data_version} for file in (dataset.files if dataset else []) if file.table_name in used],
            'readiness':readiness}
        if dataset:dataset._readiness_cache={'key':fingerprint,'context':context}
    decisions=context['readiness']['requirements']
    active=brief.get('active_requirement_ids',[s['id'] for s in brief.get('acceptance',[])])
    runnable=[d['requirement_id'] for d in decisions if d['requirement_id'] in active and d['status'] in {'ready','limited','not_applicable'}]
    executable=[d for d in decisions if d['status'] in {'ready','limited'}]
    waiting=[d for d in decisions if d['status'] in {'needs_decision','needs_input','blocked'}]
    notes='\n\n'.join(d['requirement']+'：'+d['reason'] for d in decisions if d['status'] in {'limited','needs_decision','needs_input','blocked'})
    existing=state.get('dependency_response','')
    update={'analysis_context':context,'analysis_brief':{**brief,'active_requirement_ids':runnable},
        'dependency_response':'\n\n'.join(dict.fromkeys(text for text in [existing,notes] if text))}
    if any(d['status']=='needs_decision' for d in decisions):
        from app.clarification import group_decisions
        update['clarification']=group_decisions(brief,state.get('clarification'))
    if waiting and not executable:
        decision=next((d for d in waiting if d['status']!='blocked'),waiting[0])
        if decision['status']=='blocked':
            update.update(route='refuse',route_reason=decision['reason'],error=decision['reason'])
        else:
            question=update.get('clarification') or {'question':decision['reason'][:1000],
                'why':'此项会影响对应结果；尚未执行受影响的计算，其他有效条件保留。','options':[]}
            update.update(route='clarify',route_reason=question['question'],clarification=question)
    return update


def check_prepared_version(state):
    context=state.get('analysis_context',{})
    if not context.get('readiness'):return
    from app.tools.dataset_manager import get_dataset_manager
    dataset=get_dataset_manager().get(state.get('session_id',''))
    if not dataset or workbench_snapshot(dataset)!=context['data_version']:
        raise PreparationChanged('数据版本已变化，原执行准备已失效；请重新准备本轮分析，未沿用旧检查执行。')
