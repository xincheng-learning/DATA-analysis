"""Check evaluation input/artifact plumbing without model requests."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run_analyst_workflow_eval import PACK, evidence_leaves, prompt_for


def test_runner_sends_only_current_public_question_and_preserves_followups():
    cases = json.loads((PACK / 'cases.json').read_text(encoding='utf-8'))['cases']
    for case in cases:
        first = prompt_for(case, 0)
        assert case['turns'][1]['message'] not in first
        assert 'reference_sql' not in first and 'expected_fact_ids' not in first
        for index in range(1, len(case['turns'])):
            assert prompt_for(case, index) == case['turns'][index]['message']


def test_artifacts_keep_their_actual_nested_step_paths():
    value = {'workflow': {'steps': [{'result': {'chart_spec': {'id': 'first'}}},
        {'result': {'investigation': {'steps': [{'chart_spec': {'id': 'nested'}}]}}}]}}
    charts = {path: leaf['chart_spec']['id'] for path, leaf in evidence_leaves(value) if leaf.get('chart_spec')}
    assert charts == {'root-workflow-0': 'first', 'root-workflow-1-investigation-0': 'nested'}
