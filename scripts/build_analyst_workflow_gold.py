"""Build once, or check, the analyst-led workflow gold. No model calls."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.analyst_workflow_dataset import SOURCE, VERSION, build_cases, profiles, verify_references

PACK = ROOT / 'evals/analyst_workflow_gold_v1'
SOURCES = [SOURCE, ROOT / 'evals/analyst_workflow_dataset.py', Path(__file__).resolve(), ROOT / 'evals/gold_dataset.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build():
    if (PACK / 'manifest.json').exists():
        raise ValueError('黄金集已冻结，请用 --check；修订另建版本，不覆盖原标准。')
    cases, data, answers = build_cases(), profiles(), verify_references()
    PACK.mkdir(parents=True, exist_ok=True)
    files = []
    for name, value in [('cases.json', {'version': VERSION, 'cases': cases}),
                        ('answers.json', {'version': VERSION, 'answers': answers}),
                        ('fixtures.json', {'version': VERSION, 'profiles': data})]:
        write_json(PACK / name, value)
        files.append(name)
    for profile, tables in data.items():
        target = PACK / 'data' / profile
        target.mkdir(parents=True, exist_ok=True)
        for table, rows in tables.items():
            path = target / f'{table}.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            files.append(path.relative_to(PACK).as_posix())
    questions = ['# 分析师工作流黄金集：题目', '', '8个完整业务任务；每任务独立会话，任务内顺序进行。仅发送本轮数据字典、业务背景及当前消息，不把本题后续答复提前发给Agent。', '']
    standards = ['# 标准答案与验收依据', '', '仅供评分者使用，不作为Agent输入。数值不是全部验收：必须同时阅读README.md和验收规则.md。', '']
    rules = ['# 验收规则', '', '四种审阅状态：通过、失败、待人工判断、不适用。缺证据保持待判断，未运行保持未运行；均不得计为通过。合法的部分完成可通过边界诚实性检查，不能代替未完成的计算、清洗或交付能力。', '',
             '数字按实际业务含义对应，不按列位置猜。合理别名、不同SQL和图型可接受，须记录映射依据。不得把答案手工填入Agent结果来获得通过。', '',
             '金额以元为单位；金额及客单价可展示到分，百分比可到两位小数。允许相应四舍五入，底层结果仍按参考精度核对；NULL、缺记录和0不能互换。', '',
             '关键错误：错误数字、未授权改变数据、关键口径未确认即正式计算、将旧结果当新结果、把不完整数据包装为业务全量。不能用其他环节的高分抵消。', '',
             'A02标准采用基期客单价的顺序分解；也接受明确声明且复核正确的反向顺序或对称分解。此夹具两月订单数相等，两种方法均应得到订单量效应0、客单价效应-125。', '',
             'A06允许原侧试算能力有限时明确未核验；诚实披露记边界项通过，同计划对照能力记未完成，不能把未核验当作整题通过。', '',
             'A08参考表中的known_amount是已知单元的合计；unknown_cells仅数已记录但未知的单元，recorded_cells为实际收到的记录数，0条记录不代表0销售。图可用断线、标记或说明表达缺口；不能把部分已知合计标为完整日总额。', '',
             '环境不可用、模型连接失败、停止、合法澄清分别记录；不归为数值正确，也不自动归为算法错误。不依靠关键词或模型自评认证结论。', '']
    for scenario in cases:
        questions += [f"## {scenario['id']} {scenario['title']}", '',
            f"数据：data/{scenario['profile']}/ 下全部CSV；开始时导入原始版本。", '',
            scenario['public_input']['data_dictionary'], '', scenario['public_input']['briefing'], '']
        for turn in scenario['turns']:
            questions += [f"### {turn['id']} · {turn['kind']}", '', turn['message'], '']
        standards += [f"## {scenario['id']} {scenario['title']}", '']
        for turn in scenario['turns']:
            standards += [f"{turn['id']} 对应标准：{', '.join(turn['expected_fact_ids']) or '无强制数值产出：先做只读准备与必要的业务判断。'}", '']
        for key in scenario['expected_fact_ids']:
            standards += [f'### {key}', '', '```json', json.dumps(answers[key]['rows'], ensure_ascii=False, indent=2), '```', '']
        rules += [f"## {scenario['id']} {scenario['title']}", '', *['- ' + item for item in scenario['required_evidence']], '']
    for name, lines in [('仅题目.md', questions), ('标准答案.md', standards), ('验收规则.md', rules)]:
        (PACK / name).write_text('\n'.join(lines), encoding='utf-8')
        files.append(name)
    with (PACK / '审阅记录模板.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['case_id', 'dimension', 'status', 'evidence_path', 'reason'])
        for scenario in cases:
            for dimension in ['需求覆盖', '数据准备', '人工决定', '数据与计算', '清洗影响', '交付与接续']:
                applicable = dimension != '清洗影响' or scenario['applicability']['cleaning']
                applicable = applicable and (dimension != '人工决定' or scenario['applicability']['business_decision'])
                writer.writerow([scenario['id'], dimension, '未运行' if applicable else '不适用', '', ''])
    files.append('审阅记录模板.csv')
    summary = {'version': VERSION, 'status': 'frozen_reference_not_agent_score', 'cases': len(cases),
        'scripted_messages': sum(len(c['turns']) for c in cases), 'profiles': len(data), 'facts_cross_checked': len(answers),
        'actual_agent_run': False, 'live_model_called': False, 'api_spend_cny': 0,
        'scope': '8 analyst-led workflow scenarios; partly reused development data, not blind or cross-industry evaluation'}
    write_json(PACK / 'reference-check.json', summary)
    files.append('reference-check.json')
    write_json(PACK / 'manifest.json', {'version': VERSION, 'sources': {p.relative_to(ROOT).as_posix(): digest(p) for p in SOURCES},
        'files': {name: digest(PACK / name) for name in files}})
    return check()


def check():
    manifest = json.loads((PACK / 'manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['sources'].items():
        assert digest(ROOT / relative) == expected, relative
    for relative, expected in manifest['files'].items():
        path = (PACK / relative).resolve()
        assert path.is_relative_to(PACK.resolve()), relative
        assert digest(path) == expected, relative
    assert json.loads((PACK / 'fixtures.json').read_text(encoding='utf-8'))['profiles'] == profiles()
    assert json.loads((PACK / 'cases.json').read_text(encoding='utf-8'))['cases'] == build_cases()
    assert json.loads((PACK / 'answers.json').read_text(encoding='utf-8'))['answers'] == verify_references()
    # Check files consumed by future uploads, not only the in-memory fixture.
    for profile, tables in profiles().items():
        for table, expected in tables.items():
            with (PACK / 'data' / profile / f'{table}.csv').open(encoding='utf-8', newline='') as stream:
                actual = list(csv.DictReader(stream))
            assert actual == [{k: '' if v is None else str(v) for k, v in row.items()} for row in expected], (profile, table)
    return json.loads((PACK / 'reference-check.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(check() if args.check else build(), ensure_ascii=False, indent=2))
