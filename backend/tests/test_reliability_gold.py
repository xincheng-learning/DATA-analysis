"""The teacher and runnable wrong answers must disagree without calling a model."""
from copy import deepcopy

from evals.gold_scoring import score_turn


def test_multiple_data_variants_have_independent_teachers_and_runnable_wrong_queries():
    from collections import Counter
    from evals.reliability_dataset import build_cases, verify_references
    cases = build_cases()
    assert len(cases) == 36 and len({c['id'] for c in cases}) == 36
    assert set(Counter(c['family'] for c in cases).values()) == {3}
    assert all(c['split'] == 'development' for c in cases)
    assert all(c['teacher_matches_sql'] and c['wrong_sql_executed'] and c['wrong_answer_rejected'] for c in verify_references())


def test_same_schema_variants_change_the_answers_and_model_input_hides_the_teacher():
    from evals.reliability_dataset import build_cases
    from scripts.run_gold_eval import public_question
    cases = build_cases()
    samples = [c for c in cases if c['family'] == 'month_endpoint']
    assert len({c['turns'][0]['expected_rows'][0]['amount'] for c in samples}) == 3
    for case in cases:
        prompt = public_question(case, case['turns'][0])
        assert case['id'] not in prompt and case['wrong_sql'] not in prompt
        assert case['turns'][0]['oracle_sql'] not in prompt


def test_tiny_nonzero_null_duplicates_and_order_cannot_be_laundered():
    from evals.gold_dataset import compare_rows
    assert not compare_rows([{'v': 0}], [{'v': 2e-12}], abs_tol=0, rel_tol=1e-8)
    assert not compare_rows([{'v': 0}], [{'v': None}])
    assert not compare_rows([{'v': 1}], [{'v': 1}, {'v': 1}])
    assert not compare_rows([{'v': 2}, {'v': 1}], [{'v': 1}, {'v': 2}], ordered=True)
    assert compare_rows([{'v': 2}, {'v': 1}], [{'v': 1}, {'v': 2}])


def test_new_pack_is_frozen_and_the_existing_runner_accepts_it():
    from scripts.build_reliability_gold import check
    from scripts.run_gold_eval import load_gold
    assert check()['cases'] == 36
    assert load_gold('reliability_v1').name == 'reliability_v1'


def test_structured_python_result_is_scored_without_manual_retyping():
    record = {'status': 'ran', 'trace': [{'node': 'execute_python'}], 'final': {
        'route': 'python', 'columns': ['mean'], 'rows': [{'mean': 2e-12}], 'row_count': 1,
        'checks': [{'id': 'python_result', 'status': 'passed'}]}}
    expected = {'columns': ['mean'], 'rows': [{'mean': 2e-12}],
                'comparison': {'rel_tol': 1e-8, 'abs_tol': 0}}
    assert score_turn(record, expected, {})['outcome'] == 'pass'
    record['final']['rows'][0]['mean'] = 0
    assert score_turn(record, expected, {})['outcome'] == 'fail'


def test_review_cannot_replace_a_wrong_structured_python_result():
    import hashlib
    output = 'mean=2e-12'
    record = {'status': 'ran', 'trace': [{'node': 'execute_python'}], 'final': {
        'route': 'python', 'columns': ['mean'], 'rows': [{'mean': 0}], 'row_count': 1,
        'python_output': output, 'checks': [{'id': 'python_result', 'status': 'passed'}]}}
    expected = {'columns': ['mean'], 'rows': [{'mean': 2e-12}],
                'comparison': {'rel_tol': 1e-8, 'abs_tol': 0}}
    review = {'note': 'legacy transcription', 'python_rows': deepcopy(expected['rows']),
              'python_output_sha256': hashlib.sha256(output.encode()).hexdigest()}
    assert score_turn(record, expected, review)['outcome'] == 'fail'


def test_legitimate_null_is_not_retried_into_a_zero():
    from app.agent.validator import validator_node
    state = {'question': '目标为0时达成率保留NULL', 'columns': ['attainment_pct'],
             'rows': [{'attainment_pct': None}], 'row_count': 1, 'checks': []}
    result = validator_node(state)
    assert result['validation_failure'] == '' and not result.get('previous_attempts')
    assert any(c['status'] == 'warning' and 'NULL' in c['detail'] for c in result['checks'])


def test_successful_execution_does_not_silently_certify_the_analytical_method():
    from app.analysis_pipeline import run_delivery
    from app.report_presentation import build_analysis_report
    state = {'route': 'sql', 'question': '列出各组金额', 'sql': 'SELECT * FROM computed',
             'columns': ['group', 'amount'], 'rows': [{'group': 'A', 'amount': 5}], 'row_count': 1,
             'answer': '实际金额为5', 'analysis_brief': {'requirements': ['各组金额'], 'acceptance': [
                 {'requirement_index': 0, 'answer_type': 'table', 'result_columns': ['group', 'amount']}]}}
    final = run_delivery(state)
    assert '尚未完整独立核验' in final['analysis_report']['verification_note']
    assert final['analysis_report']['verification_note'] in build_analysis_report(final)['answer']
    assert final['rows'] == state['rows']


def test_python_receipt_preserves_tiny_and_full_float_values(monkeypatch, capsys):
    import pandas as pd
    from app.tools import python_tool
    values = [2.5e-12, 1.7320508075688772e-12, 8.276505322196654e-20, 1.123456789012345]
    monkeypatch.setattr(pd, 'read_parquet', lambda path: pd.DataFrame({'value': values}))
    # Authored fixture code only; generated model code is never executed on the host.
    exec(python_tool._build_harness('result = df', ['/values.parquet']), {})
    result = python_tool._parse_output('trusted fixture', capsys.readouterr().out)
    assert result.ok and [r['value'] for r in result.rows] == values


def test_sql_evidence_is_not_rounded_before_display():
    import pandas as pd
    from app.agent.nodes import _clean_dataframe
    values = [2.5e-12, 8.276505322196654e-20, 1.123456789012345]
    assert _clean_dataframe(pd.DataFrame({'value': values}))['value'].tolist() == values


def test_tiny_decimal_that_survives_float_roundtrip_is_not_rejected():
    from app.tools.dataset_manager import SessionDataset
    ds = SessionDataset('tiny-precision')
    try:
        result = ds.execute('SELECT CAST(0.00000000000001 AS DECIMAL(18,14)) AS amount', as_frame=True)
        assert result.iloc[0, 0] == 1e-14
    finally:
        ds.close()


def test_refund_output_alias_does_not_invent_a_period_metric_request():
    from app.answer_acceptance import prepare_brief
    state = {'question': '退款仅settled；计算表内已结算退款总额，输出refund_amount'}
    brief = {'requirements': ['表内已结算退款总额'], 'acceptance': [
        {'requirement_index': 0, 'answer_type': 'value', 'result_columns': ['refund_amount']}]}
    prepared = prepare_brief(state, brief)
    assert not prepared['metric_dependencies']
    assert not prepared['acceptance'][0].get('metric_ids')
    brief['acceptance'][0]['metric_ids'] = ['refund_amount']
    declared = prepare_brief(state, brief)
    assert declared['metric_dependencies'][0]['status'] == 'needs_input'
