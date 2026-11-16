"""Replay preserved real model choices through the corrected chart tool; no model calls."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.checks import verify_chart
from app.report_presentation import build_analysis_report
from app.session import SQLiteBackend
from app.tools.viz import ChartSpec, build_plotly_spec


def main():
    output = ROOT / 'output/report-coaching-2026-11-11'
    original_path = output / 'fresh-agent-question-rerouted.json'
    original = json.loads(original_path.read_text(encoding='utf-8'))['final']
    before = original['chart_spec']
    spec = ChartSpec(chart_type='bar', x_column=before['data'][0]['meta']['x_column'],
                     y_columns=[trace['meta']['y_column'] for trace in before['data']],
                     title=before['layout']['title'], x_label=before['layout']['xaxis']['title'],
                     y_label=before['layout']['yaxis']['title'])
    chart = build_plotly_spec(spec, original['rows'])
    checked = verify_chart(chart, original['rows'], original['row_count'])
    assert chart['layout']['meta']['unit_panels'] == 3
    for old, new in zip(before['data'], chart['data']):
        assert old['x'] == new['x'] and old['y'] == new['y']
    repaired = copy.deepcopy(original)
    repaired['chart_spec'] = chart
    repaired['question'] = '工具修复后重放：相同的6、7月经营结果，分别展示金额、订单数和每单金额。没有重新请求模型或执行查询。'
    repaired['evidence'] = {'replay': {'source': str(original_path.relative_to(ROOT)),
        'kind': 'tool-only-replay', 'model_calls': 0, 'queries': 0, 'original_failure_preserved': True},
        'checks': [checked]}
    report = build_analysis_report(repaired)
    assert report and report['sources']
    repaired['analysis_report'] = report
    repaired['answer'] = report['answer']
    result = {'passed': True, 'method': repaired['evidence']['replay'], 'chart_check': checked, 'final': repaired}
    (output / 'mixed-unit-tool-replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    backend = SQLiteBackend(ROOT / '.local/qa-report-coaching-20261111-fixed/datapilot.sqlite3')
    sid = original['session_id']
    assert backend.get_messages(sid), 'Only append to the isolated preserved QA conversation.'
    if not any(message['metadata'].get('evidence', {}).get('replay') and message['metadata'].get('chart_spec') == chart
               for message in backend.get_messages(sid)):
        backend.save_messages(sid, [('user', repaired['question'], {}),
            ('assistant', repaired['answer'], {key: value for key, value in repaired.items()
                                             if key not in {'question', 'answer', 'session_id'}})])
    backend.set_title(sid, '混合单位修复｜真实结果重放')
    print(json.dumps({'passed': True, 'panels': 3, 'plotted_points': checked['plotted_points'],
                      'new_model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
