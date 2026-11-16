"""Resume saved chart/ordered workflow receipts without replanning completed writes."""
from copy import deepcopy

from app.session import get_session_store
from app.tools.dataset_manager import get_dataset_manager
from app.tools.data_workbench import workbench_snapshot


def source_for_resume(sid, mid):
    messages=get_session_store()._backend.get_messages(sid)
    source=next((m for m in reversed(messages) if m['role']=='assistant'),None)
    if not source or source['id']!=mid:
        raise ValueError('请继续当前聊天最新的未完成任务；旧任务不会重复执行。')
    meta=deepcopy(source['metadata'])
    data=get_dataset_manager().get(sid)
    if not data or not data.files or not meta.get('data_version') or meta['data_version']!=workbench_snapshot(data):
        raise ValueError('数据版本已变化或缺少版本回执，请基于当前数据继续原目标，不能直接沿用旧计算或写入。')
    flow=meta.get('workflow') or {}
    if flow:
        if flow.get('status') not in {'partial','stopped','cancelled'} or not flow.get('plan'):
            raise ValueError('此工作流当前不支持直接恢复；待确认方案请先确认。')
        from app.agent.workflow import WorkflowPlan
        plan=WorkflowPlan.model_validate(flow['plan'])
        steps=flow.get('steps',[])
        if [s['id'] for s in steps]!=[s.id for s in plan.steps[:len(steps)]]:
            raise ValueError('保存的执行步骤与原计划不一致，未重放任何操作。')
        first=next((i for i,s in enumerate(steps) if s['status']!='succeeded'),len(steps))
        if first>=len(plan.steps) or any(s['status']=='succeeded' for s in steps[first:]):
            raise ValueError('此任务不是可直接接续的顺序断点，请基于执行记录调整剩余要求。')
        if first<len(steps) and steps[first]['status']=='failed' and plan.steps[first].tool in {'board','cleaning','undo_cleaning','superset_create'}:
            raise ValueError('写入步骤失败后的实际状态需先核对，不会自动重做该写入。')
        if first<len(steps) and steps[first]['status']=='cancelled' and plan.steps[first].tool in {'cleaning','undo_cleaning','superset_create'}:
            raise ValueError('中断的写入步骤需要先核对回执，不会自动重复修改数据。')
        for step in steps[:first]:
            result=step.get('result',{})
            if result.get('data_version') and result['data_version']!=meta['data_version']:
                raise ValueError('已完成步骤的数据版本已过期，不能作为恢复依据。')
    elif not (meta.get('chart_error') and meta.get('result_id') and meta.get('columns') and not (meta.get('error') or meta.get('validation_failure'))):
        raise ValueError('没有可直接重试的已保存查询结果；请补充条件后继续。')
    else:
        from app.tools.query_results import read_result
        try:read_result(sid,meta['result_id'],0)
        except FileNotFoundError:raise ValueError('完整查询结果已不存在，不能继续声称使用已保存结果；请重新计算。') from None
    return meta


def retry_chart(source, sid):
    from app.agent.nodes import make_chart_node, compose_answer_node
    from app.execution import controlled_node, save_partial_result
    state={key:value for key,value in source.items() if key not in {'analysis_report','analysis_process','error','chart_error','chart_spec','analysis_scope'}}
    state.update(session_id=sid,route='viz',question=(source.get('analysis_brief') or {}).get('goal') or source.get('question') or '继续原任务的图表')
    save_partial_result(state)
    state.update(controlled_node('make_chart',make_chart_node)(state))
    state.update(compose_answer_node(state))
    return state


def resume_saved(source, sid, mid):
    from app.execution import save_partial_result
    flow=source.get('workflow')
    if not flow:
        final=retry_chart(source,sid)
    else:
        from app.agent.workflow import WorkflowPlan, _run_plan, _response
        plan=WorkflowPlan.model_validate(flow['plan']);steps=deepcopy(flow['steps'])
        first=next((i for i,s in enumerate(steps) if s['status']!='succeeded'),len(steps))
        completed=steps[:first]
        state={'session_id':sid,'question':flow['goal'],'user_request':flow['goal'],'analysis_brief':source.get('analysis_brief') or {}}
        if first<len(steps) and (steps[first].get('result') or {}).get('chart_error'):
            repaired=retry_chart(steps[first]['result'],sid)
            if repaired.get('chart_error'):
                steps[first]['result']=repaired
                final=_response(state,plan,steps,flow['run_id'],'partial')
                final['continuation_source']={'message_id':mid}
                return final
            completed.append({**steps[first],'status':'succeeded','result':repaired})
        save_partial_result({**source,'continuation_source':{'message_id':mid}})
        final=_run_plan(state,plan,completed=completed,run_id=flow['run_id'])
    final['continuation_source']={'message_id':mid}
    return final
