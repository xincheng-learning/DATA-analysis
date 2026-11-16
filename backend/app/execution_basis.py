"""Freeze supported calculations; no second model reinterprets the same plan."""
import hashlib
import json
import duckdb

from app.calculation_contract import CalculationMismatch, CalculationPlan, compile_plan


class BasisChanged(CalculationMismatch):
    check_id='execution_basis'
    retryable=False


def plan_digest(raw):
    return hashlib.sha256(json.dumps(CalculationPlan.model_validate(raw).model_dump(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def compile_basis(state,dataset):
    from app.answer_acceptance import calculation_brief
    from app.formula_contract import verify_user_formulas
    from app.temporal_scope import verify_temporal_scope
    from app.tools.data_workbench import workbench_snapshot
    if dataset is None:raise BasisChanged('当前没有可执行的数据版本。')
    brief=calculation_brief(state['analysis_brief'])
    question=state.get('user_request') or state['question']
    plan=CalculationPlan.model_validate(brief['calculation_plan'])
    requested=list(dict.fromkeys(c for s in brief.get('acceptance',[]) for c in s.get('result_columns',[])))
    confirmed=[s[k] for s in [*brief.get('scope_sources',[]),*brief.get('continuation_sources',[])] for k in ('text','source_question') if s.get(k)]
    from app.business_contract_binding import contract_compile_inputs
    sources,windows=contract_compile_inputs(brief,state['session_id'],dataset)
    confirmed.extend(sources)
    verify_temporal_scope(brief,question)
    query,columns=compile_plan(dataset,plan,question,confirmed,requested,authorized_windows=windows)
    checks=verify_user_formulas(dataset,brief,plan,question)
    with dataset.query_lock:
        try:dataset.get_connection().execute('DESCRIBE '+query).fetchall()
        except duckdb.Error as exc:raise BasisChanged('固定计划暂不能执行，请修正计划而非重试SQL：'+str(exc)) from exc
        version=workbench_snapshot(dataset)
    return {'origin':'compiled_plan','sql':query,'columns':columns,'plan_sha256':plan_digest(brief['calculation_plan']),
        'data_version':version,'checks':checks,'business_contract_hash':brief.get('business_contract_hash')}


def check_basis(state,dataset):
    basis=state.get('analysis_context',{}).get('execution_basis')
    if not basis:return False
    from app.business_contract_binding import contract_compile_inputs
    contract_compile_inputs(state['analysis_brief'],state['session_id'],dataset)
    from app.tools.data_workbench import workbench_snapshot
    if (basis.get('business_contract_hash')!=state['analysis_brief'].get('business_contract_hash')
            or basis['data_version']!=workbench_snapshot(dataset)
            or basis['plan_sha256']!=plan_digest(state['analysis_brief']['calculation_plan'])
            or basis['sql'].strip().rstrip(';')!=state.get('sql','').strip().rstrip(';')):
        raise BasisChanged('固定计算依据、数据版本或执行代码已变化；请重新准备计划，未继续执行。')
    return True
