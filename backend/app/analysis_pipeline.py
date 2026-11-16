"""Shared LangGraph delivery: executed evidence -> coverage review -> analyst report.

Child computations stop before delivery. API-only tools and resumed workflows use
the same two-node graph, so route choice cannot bypass the reporting boundary.
"""
from __future__ import annotations

from contextlib import nullcontext

from app.agent.state import AgentState
from app.analysis_report import compose_report, evidence_signature
from app.answer_acceptance import prepare_brief,assess_coverage,dependency_answer,dependency_requirements
from app.execution import controlled_node, save_partial_result
from app.report_presentation import report_input


def prepare_request(state,brief=None):
    from app.tools.dataset_manager import get_dataset_manager
    brief=brief or state.get('analysis_brief') or {'goal':state.get('question',''),'requirements':[state.get('question','')],'confirmed_scope':[]}
    dataset=get_dataset_manager().get(state['session_id']) if state.get('session_id') else None
    messages=[]
    if state.get('session_id') and (state.get('session_context') or brief.get('scope_sources') or brief.get('metric_bindings') or brief.get('revision_quote') or brief.get('metric_requests')):
        from app.session import get_session_store
        messages=get_session_store()._backend.get_messages(state['session_id'])
    prepared=prepare_brief(state,brief,dataset=dataset,messages=messages)
    if prepared.get('metric_requests'):
        from app.business_contract_binding import bind_contracts
        import duckdb
        try:
            with dataset.query_lock if dataset else nullcontext():
                prepared=bind_contracts(state,prepared,dataset,messages)
        except (ValueError,duckdb.Error,NotImplementedError) as exc:
            prepared={**prepared,'calculation_plan':None,'contract_error':str(exc)}
    response=dependency_answer(prepared)
    update={'analysis_brief':prepared,'dependency_response':response}
    if dataset and dataset.files and any(spec.get('metric_ids') or spec.get('result_columns') for spec in prepared['acceptance']):
        from app.analysis_scope import create_scope
        from app.tools.data_workbench import workbench_snapshot
        update['analysis_scope']=create_scope(prepared,messages,data_version=workbench_snapshot(dataset),
            table_versions={file.table_name:file.data_version for file in dataset.files})
    return update


def prepare_analysis_node(state):
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import diagnose_data,workbench_snapshot
    dataset=get_dataset_manager().get(state.get('session_id',''))
    if state.get('analysis_brief',{}).get('acceptance'):
        from app.analysis_readiness import prepare_execution
        with dataset.query_lock if dataset else nullcontext():
            return prepare_execution(state,dataset)
    if not dataset or not dataset.files:return {'analysis_context':{}}
    with dataset.query_lock:
        snapshot=workbench_snapshot(dataset)
        cached=getattr(dataset,'_analysis_context',None)
        if cached and cached['data_version']==snapshot:return {'analysis_context':cached}
        diagnosis=diagnose_data(dataset)
        context={'data_version':snapshot,'scope':'当前活动版本的整表体检，不等于用户筛选后的有效分母。',
            'tables':[{'table':t['table'],'rows':t['rows'],'duplicate_rows':t['duplicate_rows'],
                'fields':[{key:f[key] for key in ('column','dtype','null_count')} for f in t['fields']],
                'temporal_checks':t['temporal_checks'],'temporal_summary':t['temporal_summary']} for t in diagnosis['tables']]}
        dataset._analysis_context=context
        return {'analysis_context':context}


def review_evidence_node(state):
    goal=state.get('user_request') or state.get('question','分析结果')
    brief=state.get('analysis_brief') or {'goal':goal,'requirements':[goal],'confirmed_scope':[]}
    workflow=state.get('workflow') or {}
    if workflow.get('requirements') and not any(s.get('answer_type') for s in brief.get('acceptance',[])):
        brief={**brief,'requirements':workflow['requirements'],'acceptance':[],'contract_version':0}
    if brief.get('contract_version')!=1:brief=prepare_request(state,brief)['analysis_brief']
    brief={**brief,'goal':goal}
    result,paths=report_input({**state,'analysis_brief':brief})
    usable=[(index,step) for index,step in enumerate(result['steps']) if step['status']=='succeeded'
        and isinstance(step.get('rows'),list) and step.get('columns') and not (step.get('error') or step.get('validation_failure'))]
    versions={step.get('data_version') for _,step in usable if step.get('data_version')}
    allowed=bool(usable) and not (state.get('error') or state.get('validation_failure'))
    allowed=allowed and result['status'] not in {'waiting','running','clarify','cancelled'}
    signature=evidence_signature(goal,result,brief)
    process={'version':1,'brief':brief,'evidence_signature':signature,
        'preparation':{'data_version':state.get('analysis_context',{}).get('data_version'),
            'execution_basis':{k:v for k,v in state.get('analysis_context',{}).get('execution_basis',{}).items() if k in {'origin','plan_sha256','data_version','columns'}},
            'tables_checked':len(state.get('analysis_context',{}).get('tables',[])),
            'readiness':state.get('analysis_context',{}).get('readiness')},
        'sources':[{'key':f'S{index+1}','path':paths[index],'question':step.get('question','实际结果'),
            'row_count':step.get('row_count',len(step['rows'])),'data_version':step.get('data_version')}
            for index,step in usable],
        'evidence_status':'different_versions' if len(versions)>1 else 'available' if allowed else 'no_analytical_result',
        'report_status':'pending' if allowed else 'not_required'}
    bundle={'result':result,'brief':brief,'signature':signature,'can_report':allowed and len(versions)<=1}
    save_partial_result({**state,'analysis_brief':brief,'analysis_process':process})
    return {'analysis_brief':brief,'analysis_evidence':bundle,'analysis_process':process}


def write_report_node(state):
    bundle=state['analysis_evidence'];process={**state['analysis_process']}
    if not bundle['can_report']:
        process['report_status']='facts_only' if process['evidence_status']=='different_versions' else 'not_required'
        from app.analysis_report import build_evidence
        process['coverage']=assess_coverage(bundle['brief'],{'evidence':build_evidence(bundle['result']),
            'dependency_response':state.get('dependency_response','')})
        return {'analysis_process':process}
    result,brief=bundle['result'],bundle['brief']
    covered=dependency_requirements(brief)
    facts_only=brief.get('acceptance') and all(spec.get('answer_type') in {'value','table','scope','constraint'} or spec['id'] in covered for spec in brief['acceptance'])
    usable=[step for step in result['steps'] if step['status']=='succeeded' and isinstance(step.get('rows'),list) and step.get('columns')]
    single_value=state.get('route') in {'sql','viz','python'} and len(usable)==1 and usable[0].get('row_count')==1 and len(usable[0]['columns'])==1 and len(brief['requirements'])==1
    facts_only=bool(facts_only or single_value or state.get('sql_source') or usable and all(step.get('row_count')==0 for step in usable))
    existing=state.get('analysis_report') or {}
    if facts_only:
        from app.analysis_report import build_evidence
        cards=build_evidence(result)
        report={'status':'facts','findings':[],'evidence':cards,'warnings':[],
            'coverage':[{'requirement':spec['requirement'],'evidence_ids':[card['id'] for card in cards if card['kind'] in {'row','execution_receipt'}]} for spec in brief['acceptance']],
            'answer':state.get('answer',''),'evidence_signature':bundle['signature']}
    else:report=existing if existing.get('evidence_signature')==bundle['signature'] else compose_report(state.get('user_request') or state.get('question',''),result,brief)
    report={**report,'findings':list(report.get('findings',[])),'method_boundaries':[]}
    contracts = [[check for check in step.get('checks', []) if check.get('id') == 'calculation_contract'] for step in usable]
    checked = bool(contracts) and all(checks and all(check.get('status') == 'passed' for check in checks) for checks in contracts)
    report['verification_note']=('本轮完整结果已与声明计算计划独立对照；核对范围见执行依据。计划的用户意图理解与统计适用前提尚未独立认证。'
        if checked else '本轮已返回实际计算结果；业务筛选、关联粒度、分母及统计方法尚未完整独立核验，请结合执行依据核对。')
    if not checked and usable and all(any(c.get('id')=='compiled_execution' and c.get('status')=='passed' for c in step.get('checks',[])) for step in usable):
        report['verification_note']='本轮已按固定计算计划执行，未独立数值复算；计划对原问题的理解、业务定义与结论仍需分别核验。'
    if any(check.get('status') == 'failed' for checks in contracts for check in checks):
        report['verification_note']='本轮存在计算核对失败，不能作为已核验结论使用；实际结果和失败依据保留，请修正计划或计算后重算。'
    report['answer']+='\n\n'+report['verification_note']
    for spec in brief.get('acceptance',[]):
        if spec.get('answer_type') not in {'definition','explanation'} or spec.get('method_topic')!='causal_boundary':continue
        refs=[card['id'] for card in report['evidence'] if card['kind']=='execution_receipt' and spec['id'] in card.get('requirement_ids',[])]
        if not refs:continue
        report['method_boundaries'].append({'requirement_id':spec['id'],'evidence_ids':refs})
        text='统计关联不能单独证明因果'
        reason='因果判断还需要研究设计与识别假设等证据；当前计算结果不能替代这些证据。'
        if not any(f.get('method_topic')=='causal_boundary' and spec['id'] in f.get('requirement_ids',[]) for f in report['findings']):
            report['findings'].append({'kind':'method','method_topic':'causal_boundary','text':text,'reason':reason,
                'requirement_ids':[spec['id']],'evidence_ids':refs,'recommendation':''})
        report['answer']+='\n\n'+text+'。'+reason
    report['scope_declarations']=[source['text'] for source in brief.get('scope_sources',[])]
    if report['scope_declarations']:
        report['answer']+='\n\n'+'\n'.join('按用户声明：'+text for text in report['scope_declarations'])
    report['dependency_response']=state.get('dependency_response','')
    report['unverified_constraints']=brief.get('unverified_constraints',[])
    if any(spec.get('unverified_request_quote') for spec in brief.get('acceptance',[])):
        warning='部分问题拆解的原话引用未能与本轮请求对应，意图理解尚未核验；已有结果保留，请按原问题核对。'
        report['warnings'].append(warning)
        report['answer']+='\n\n'+warning
    report['coverage']=assess_coverage(brief,report)
    report['acceptance_version']=1
    process.update(report_status='facts_only' if state.get('sql_source') else 'single_value' if single_value else 'generated' if report['status']=='generated' else 'facts' if facts_only else 'facts_only',coverage=report.get('coverage',[]))
    report['report_status']=process['report_status']
    update={'analysis_report':report,'analysis_process':process,'answer':report['answer']}
    if state.get('investigation'):
        update['investigation']={**state['investigation'],'synthesis':report}
    save_partial_result({**state,**update})
    return update


def run_delivery(state):
    """Use the same graph for entry points that execute tools outside the main graph."""
    if state.get('analysis_process'):
        return state
    from langgraph.graph import END, START, StateGraph
    graph=StateGraph(AgentState)
    graph.add_node('review_evidence',controlled_node('review_evidence',review_evidence_node))
    graph.add_node('write_report',controlled_node('write_report',write_report_node))
    graph.add_edge(START,'review_evidence');graph.add_edge('review_evidence','write_report');graph.add_edge('write_report',END)
    return {**state,**dict(graph.compile().invoke(state))}
