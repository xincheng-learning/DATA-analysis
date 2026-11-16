"""Exercise real Python routing; replace only the model and Docker boundaries."""
from types import SimpleNamespace

import pytest

from app.agent import graph, nodes, workflow
from app.config import settings
from app.tools import dataset_manager, python_tool, query_results


@pytest.fixture
def python_run(monkeypatch, tmp_path):
    dataset = SimpleNamespace(files=[SimpleNamespace(table_name='observations', parquet_path=tmp_path/'observations.parquet')])
    monkeypatch.setattr(dataset_manager, 'get_dataset_manager', lambda: SimpleNamespace(get=lambda _: dataset))
    monkeypatch.setattr(query_results, 'save_result', lambda *args: 'verified-result')
    monkeypatch.setattr(graph, 'prepare_analysis_node', lambda state: {})
    monkeypatch.setattr('app.analysis_pipeline.prepare_analysis_node', lambda state: {})
    monkeypatch.setattr('app.tools.relationships.business_context', lambda dataset: '')
    monkeypatch.setattr('app.tools.data_workbench.workbench_snapshot', lambda dataset: 'frozen-v1')
    monkeypatch.setattr(workflow, 'workbench_snapshot', lambda dataset: 'frozen-v1')
    dataset.schema_summary = lambda: 'observations: amount DOUBLE'
    monkeypatch.setattr(graph, 'router_node', lambda state: {'route': 'python'})
    monkeypatch.setattr(settings, 'max_agent_retries', 3)

    def run(codes, receipts, *, workflow_mode=False, **initial):
        prompts, executed = [], []

        def invoke(messages):
            if messages[0].content.startswith(nodes.PYTHON_SYSTEM_PROMPT):
                prompts.append(messages)
                item = codes[min(len(prompts)-1, len(codes)-1)]
                return item if not isinstance(item, str) else SimpleNamespace(content=item)
            return SimpleNamespace(content='本次执行未完成。')

        def execute(code, **kwargs):
            executed.append(code)
            receipt = receipts[min(len(executed)-1, len(receipts)-1)]
            if callable(receipt): receipt = receipt()
            return python_tool.PythonResult(code=code, **receipt)

        monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=invoke))
        monkeypatch.setattr(python_tool, 'execute_python', execute)
        if workflow_mode:
            from app.agent.workflow import Analyze, _analyze
            result = _analyze(Analyze(question='统计amount；保留零和未知；不要清洗或绘图', mode='python'),
                {'session_id':'python-retry'}, '金额单位CNY')
            return result, prompts, executed
        result = graph.build_graph().invoke({
            'question': '统计amount；保留零和未知；不要清洗或绘图',
            'schema': 'observations: amount DOUBLE',
            'session_id': 'python-retry', 'session_context': '金额单位CNY',
            'internal_analysis': True, 'data_version': 'frozen-v1', **initial,
        }, {'recursion_limit': 30})
        return result, prompts, executed

    return run


SUCCESS = {'output': 'n=61', 'columns': ['n'], 'rows': [{'n': 61}]}


def test_python_error_is_corrected_with_actual_code_and_error(python_run):
    bad = "result = broken['拒绝']"
    final, prompts, executed = python_run([bad, 'result = computed'], [
        {'error': "KeyError: '拒绝'"}, SUCCESS,
    ])
    assert len(executed) == len(prompts) == 2
    assert bad in prompts[1][1].content and "KeyError: '拒绝'" in prompts[1][1].content
    assert '金额单位CNY' in prompts[1][1].content and '不要清洗或绘图' in prompts[1][1].content
    assert final['rows'] == [{'n': 61}] and not final['error']
    assert [a['error'] for a in final['python_attempts']] == ["KeyError: '拒绝'", '']
    assert final['data_version'] == 'frozen-v1'


def test_python_stops_after_total_attempt_limit_and_clears_stale_results(python_run):
    final, prompts, executed = python_run(['bad1', 'bad2', 'bad3', 'bad4'], [{'error': 'KeyError: amount'}],
        rows=[{'stale': 999}], columns=['stale'], row_count=1, result_id='stale-id',
        python_summary='old', chart_spec={'data': ['old']})
    assert len(executed) == len(prompts) == 3
    assert len(final['python_attempts']) == 3 and final['error']
    assert final['rows'] == [] and final['columns'] == [] and final['row_count'] == 0
    assert final['result_id'] is None and not final['chart_spec'] and not final['python_summary']


def test_identical_failed_python_is_not_executed_again(python_run):
    final, prompts, executed = python_run(['bad'], [{'error': 'KeyError: amount'}])
    assert len(prompts) == 2 and executed == ['bad']
    assert final['error'] and len(final['python_attempts']) == 1


def test_unavailable_sandbox_does_not_trigger_model_repair(python_run):
    final, prompts, executed = python_run(['result=1'], [{'error': '隔离环境尚未启动', 'retryable': False}])
    assert len(executed) == len(prompts) == 1
    assert final['error'] and not final['python_retryable']


def test_truncated_python_is_neither_executed_nor_regenerated(python_run):
    final, prompts, executed = python_run([
        SimpleNamespace(content='result = pd.DataFrame(', response_metadata={'finish_reason': 'length'}),
    ], [SUCCESS])
    assert final['output_limit_reached'] and final['error']
    assert not executed and len(prompts) == 1


def test_python_success_is_not_retried(python_run):
    final, prompts, executed = python_run(['result=computed'], [SUCCESS])
    assert len(prompts) == len(executed) == 1 and not final['error']
    assert final['python_attempts'] == [{'code': 'result=computed', 'error': ''}]


def test_python_generation_leaves_explanation_to_delivery(python_run):
    _, prompts, _ = python_run(['result=computed'], [SUCCESS])
    prompt = prompts[0][0].content
    assert "Set result.attrs['summary']" not in prompt
    assert 'Do not generate narrative explanations' in prompt


def test_python_retry_obeys_the_existing_model_budget(python_run):
    from app import execution
    task = execution.AnalysisTask('budget-python', 'python-retry', max_model_calls=1)
    token = execution._current.set(task)
    try:
        with pytest.raises(execution.ModelBudgetExceeded):
            python_run(['bad', 'good'], [{'error': 'KeyError: amount'}, SUCCESS])
        assert task.model_calls == 1 and task.budget_exhausted
    finally:
        execution._current.reset(token)


def test_workflow_python_uses_the_same_bounded_repair(python_run):
    final, prompts, executed = python_run(['bad', 'good'], [{'error':'KeyError: amount'}, SUCCESS], workflow_mode=True)
    assert len(executed) == len(prompts) == 2 and not final['error']
    assert final['python_attempts'][0]['error'] == 'KeyError: amount'


def test_cancellation_after_failure_prevents_another_model_call(python_run):
    from app import execution
    task = execution.AnalysisTask('cancel-python', 'python-retry')
    token = execution._current.set(task)
    def cancel():
        task.cancelled.set()
        return {'error':'KeyError: amount'}
    try:
        with pytest.raises(execution.AnalysisCancelled):
            python_run(['bad', 'good'], [cancel, SUCCESS])
        assert task.model_calls == 1
    finally:
        execution._current.reset(token)


def test_unrequested_numeric_rounding_is_repaired_before_execution(python_run):
    bad='result = pd.DataFrame({"p_value":[round(p_value,6)]})'
    final,prompts,executed=python_run([bad,'result = raw_result'],[
        {'output':'actual','columns':['p_value'],'rows':[{'p_value':8.276505322196654e-20}]}])
    assert len(prompts)==2 and executed==['result = raw_result']
    assert final['rows'][0]['p_value']>0 and final['python_attempts'][0]['error']
    assert '精度' in prompts[1][1].content


def test_explicit_confirmed_rounding_request_can_be_executed(python_run):
    code='result = computed.round(2)'
    final,prompts,executed=python_run([code],[SUCCESS],question='结果保留两位小数',
        analysis_brief={'scope_sources':[{'key':'precision','text':'保留两位小数','source_question':'结果保留两位小数'}]})
    assert executed==[code] and not final['error']
