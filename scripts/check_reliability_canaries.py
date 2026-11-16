"""Measure production gates on runnable semantic canaries; no model or real history."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
from evals.reliability_dataset import build_cases, connection, profiles, query_rows
from evals.gold_dataset import compare_rows
from app.agent.validator import validator_node
from app.answer_acceptance import prepare_brief, assess_coverage
from app.analysis_pipeline import run_delivery
from app.analysis_report import build_evidence
from app.report_presentation import build_analysis_report
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import validate_and_convert


def run(declared=False, plan_run=None):
    from scripts.build_reliability_gold import check
    check()
    reviews = json.loads((plan_run / 'reviews.json').read_text(encoding='utf-8')) if plan_run and (plan_run / 'reviews.json').exists() else {}
    execute_node = declared or plan_run is not None
    outcomes = []
    for profile, tables in profiles().items():
        with tempfile.TemporaryDirectory(prefix='datapilot-semantic-') as directory, connection(tables) as teacher_db:
            manager = DatasetManager()
            from app.tools import dataset_manager
            previous_manager = dataset_manager._manager
            dataset_manager._manager = manager
            ds = manager.get_or_create('semantic-canary', Path(directory))
            try:
                for file in sorted((ROOT / 'evals/reliability_v1/data' / profile).glob('*.csv')):
                    with file.open('rb') as stream:
                        manager.add_file(ds.session_id, validate_and_convert(file.name, stream, Path(directory)))
                for case in (c for c in build_cases() if c['profile'] == profile):
                    saved = plan_run / 'cases' / (case['id']+'.json') if plan_run else None
                    if saved and not saved.exists():
                        continue
                    turn = case['turns'][0]
                    for role, sql in [('correct', turn['oracle_sql']), ('wrong', case['wrong_sql'])]:
                        columns, raw = query_rows(teacher_db, sql)
                        matches = compare_rows(raw, turn['expected_rows'], **turn['comparison'])
                        state = {'question': turn['question'], 'route': 'sql', 'sql': sql}
                        if execute_node:
                            from evals.declared_plan_fixtures import declared_plan
                            from scripts.run_gold_eval import public_question
                            plan = declared_plan(case['family'], turn['question']) if declared else deepcopy(
                                (json.loads(saved.read_text(encoding='utf-8'))['turns'][0]['final'].get('analysis_brief') or {}).get('calculation_plan'))
                            if plan and not declared:
                                review = reviews.get(case['id']+':1', {})
                                aliases = review.get('column_map', {}) if review.get('note') else {}
                                final = plan['stages'][-1]
                                for item in final['select']:
                                    item['name'] = aliases.get(item['name'], item['name'])
                                    if item['op'] == 'ratio':
                                        for key in ('numerator', 'denominator'):
                                            item[key] = aliases.get(item[key], item[key])
                                for item in final.get('order_by', []):
                                    item['field'] = aliases.get(item['field'], item['field'])
                            state.update(session_id=ds.session_id, question=public_question(case, turn))
                            state['analysis_brief'] = prepare_brief(state, {
                                'requirements': [turn['question']], 'calculation_plan': plan,
                                'acceptance': [{'requirement_index': 0, 'answer_type': 'table',
                                    'result_columns': query_rows(teacher_db, turn['oracle_sql'])[0]}]})
                        blocked = False
                        error = ''
                        try:
                            if execute_node:
                                from app.agent.nodes import execute_sql_node
                                state.update(execute_sql_node(state))
                                if state.get('error'):
                                    raise ValueError(state['error'])
                            else:
                                frame = ds.execute(sql, as_frame=True)
                                rows = [{k: None if pd.isna(v) else v for k, v in row.items()} for row in frame.to_dict('records')]
                                state.update(columns=list(frame.columns), rows=rows, row_count=len(rows),
                                             error='', checks=list(ds.last_query_checks))
                            verdict = validator_node(state)
                            state.update(verdict)
                            error = verdict['validation_failure']
                            blocked = bool(error)
                        except ValueError as exc:
                            blocked = True
                            error = str(exc)
                        outcome = {'case': case['id'], 'family': case['family'], 'role': role,
                                   'has_declared_plan': bool(state.get('analysis_brief', {}).get('calculation_plan')),
                                   'raw_execution_ok': True, 'teacher_matches': matches,
                                   'product_blocked': blocked, 'reason': error}
                        if not blocked:
                            brief = state['analysis_brief'] if execute_node else prepare_brief(state, {'goal': turn['question'], 'requirements': [turn['question']],
                                'acceptance': [{'requirement_index': 0, 'answer_type': 'table', 'result_columns': columns}]})
                            evidence = build_evidence({'steps': [{**state, 'status': 'succeeded', 'analysis_brief': brief}]})
                            report = {'status': 'facts', 'evidence': evidence,
                                      'coverage': [{'requirement_id': 'R1', 'evidence_ids': [c['id'] for c in evidence]}]}
                            coverage = assess_coverage(brief, report)[0]
                            delivered = run_delivery({**state, 'analysis_brief': brief, 'answer': '工具已返回结果。'})
                            presentation = build_analysis_report(delivered)
                            note = delivered['analysis_report'].get('verification_note', '')
                            shown = presentation['answer'] if presentation else delivered['answer']
                            outcome.update(coverage=coverage,
                                insufficient_evidence_notice=bool(note and note in shown),
                                declared_plan_proof=any(c.get('id') == 'calculation_contract' and c.get('status') == 'passed' for c in state['checks']),
                                semantic_proof=any(c.get('id') == 'metric_definition' and c.get('status') == 'passed' for c in state['checks']))
                        outcomes.append(outcome)
            finally:
                manager.close_all()
                dataset_manager._manager = previous_manager
    if not outcomes:
        raise ValueError('没有可评分的案例，不能将空回放记为门禁通过。')
    wrong = [r for r in outcomes if r['role'] == 'wrong']
    correct = [r for r in outcomes if r['role'] == 'correct']
    return {'mode': 'controlled SQL; real import, DuckDB and production gates; no live model or browser',
        'plan_source': ('saved application briefs, reviewed aliases only; provenance requires audited live run: '+str(plan_run)) if plan_run else 'authored fixture contracts; NOT model generated' if declared else 'none',
        'product_gate_passed': all(r['product_blocked'] for r in wrong) and not any(r['product_blocked'] for r in correct),
        'raw_execution_success': len(outcomes), 'cases': len(correct),
        'evaluator_rejected_wrong': sum(not r['teacher_matches'] for r in wrong),
        'product_blocked_wrong': sum(r['product_blocked'] for r in wrong),
        'product_wrong_not_blocked': sum(not r['product_blocked'] for r in wrong),
        'product_blocked_correct': sum(r['product_blocked'] for r in correct),
        'wrong_marked_answered': sum(not r['product_blocked'] and r['coverage']['status'] == 'answered' for r in wrong),
        'wrong_insufficient_coverage': sum(not r['product_blocked'] and r['coverage']['status'] != 'answered' for r in wrong),
        'wrong_unblocked_with_scope_notice': sum(not r['product_blocked'] and r.get('insufficient_evidence_notice', False) for r in wrong),
        'outcomes': outcomes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/semantic-gold-20261115/product-canaries.json')
    parser.add_argument('--require-product-gates', action='store_true', help='Fail when a known wrong answer escapes or a correct answer is blocked')
    plans = parser.add_mutually_exclusive_group()
    plans.add_argument('--declared-plans', action='store_true', help='Controlled authored scope plans; not a model planning accuracy test')
    plans.add_argument('--plan-run', type=Path, help='Replay saved briefs; reviewed aliases are normalized, no calculation semantics changed')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('保留已有运行记录；请为新结果选择不同--output路径。')
    result = run(declared=args.declared_plans, plan_run=args.plan_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'outcomes'}, ensure_ascii=False, indent=2))
    if args.require_product_gates and not result['product_gate_passed']:
        raise SystemExit(1)
