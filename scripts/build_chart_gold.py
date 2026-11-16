"""Build or verify the independent, public chart teacher package (no model)."""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = Path('evals/chart_gold_v2')
SOURCES = ['evals/chart_gold_v2.py', 'evals/chart_gold_render.py', 'scripts/build_chart_gold.py',
           'evals/chart24_reference.py', 'evals/tableau_reference_v1/families.json']


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))


def build(output):
    from evals.chart_gold_v2 import cases, oracle, VERSION
    from evals.chart_gold_render import render_case, audit_figure, configure
    import matplotlib
    import matplotlib.pyplot as plt
    if output.exists() and any(output.iterdir()): raise ValueError('保留旧教师记录；请选择一个空的新输出目录。')
    (output / 'figures').mkdir(parents=True, exist_ok=True); (output / 'data').mkdir()
    items = cases(); outcomes = []; cards = []
    write_json(output / 'cases.json', dict(version=VERSION, split='public development; not blind', cases=items))
    write_json(output / 'answers.json', {c['id']: oracle(c) for c in items})
    for c in items:
        ident = c['id']; print(f'Rendering {ident}: {c["name"]} ({len(c["rows"])} rows)', flush=True)
        with (output / 'data' / (ident + '.csv')).open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(c['rows'][0]), lineterminator='\n'); writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in c['rows'])
        fig = render_case(c)
        try:
            checked = audit_figure(c, fig)
            fig.savefig(output / 'figures' / (ident + '.svg'), metadata={'Date': None})
            fig.savefig(output / 'figures' / (ident + '.png'), dpi=160)
            fig.canvas.draw()
            width, height = fig.canvas.get_width_height()
            escaped_text = []
            unused_ticks = set()
            for ax in fig.axes:
                for axis, bounds in [(ax.xaxis, ax.get_xlim()), (ax.yaxis, ax.get_ylim())]:
                    low, high = sorted(bounds)
                    for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
                        if not low <= tick.get_loc() <= high:
                            unused_ticks.update([tick.label1, tick.label2])
            for text in fig.findobj(matplotlib.text.Text):
                if text not in unused_ticks and text.get_visible() and text.get_text().strip():
                    b = text.get_window_extent(fig.canvas.get_renderer())
                    if b.x0 < -2 or b.y0 < -2 or b.x1 > width + 2 or b.y1 > height + 2:
                        escaped_text.append(text.get_text())
            if escaped_text: raise AssertionError(f'{ident} 教师文字越界：{escaped_text}')
            outcomes.append(dict(id=ident, **checked, text_outside_canvas=escaped_text))
        finally: plt.close(fig)
        checks = ''.join('<li>' + html.escape(s) + '</li>' for s in c['review'])
        cards.append((c['tier'], f'<article id="{ident}" data-tier="{c["tier"]}"><div class="eyebrow">{ident} · {"复杂挑战" if c["tier"] == "complex" else "基础回归"} · {len(c["rows"]):,} 行</div><h2>{html.escape(c["name"])}</h2><p>{html.escape(c["challenge"])}</p><a href="figures/{ident}.svg" target="_blank"><img loading="lazy" src="figures/{ident}.svg" alt="{html.escape(c["name"])}独立教师图"></a><details><summary>逐项验收标准与完整数据</summary><ul>{checks}</ul><a href="data/{ident}.csv">完整CSV</a> · <a href="answers.json">独立计算答案</a></details></article>'))
    write_json(output / 'teacher-audit.json', dict(scope='actual static teacher marks; not product/model certification',
               font=configure(), matplotlib=matplotlib.__version__, figures=len(outcomes), outcomes=outcomes))
    complex_count = sum(c['tier'] == 'complex' for c in items); complex_rows = sum(len(c['rows']) for c in items if c['tier'] == 'complex')
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chart Gold v2 · 独立教师参考</title><style>
body{margin:0;background:#f3f6f9;color:#182f42;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1460px;margin:auto;padding:44px 32px}.eyebrow{font-size:12px;letter-spacing:1px;color:#406783;font-weight:650}h1{font-size:38px;line-height:1.2;max-width:850px}h2{font-size:21px;margin:8px 0}.lead{max-width:940px;color:#526a7d}.stats{display:flex;gap:32px;border-top:1px solid #cbd7e2;border-bottom:1px solid #cbd7e2;padding:18px 0;margin:28px 0}.stats strong{font-size:25px;display:block}nav{display:flex;gap:20px;margin:25px 0}a{color:#006ca6}section{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:white;border:1px solid #dce5ed;border-radius:10px;padding:22px}article p{color:#5c7283;min-height:25px}img{width:100%;height:360px;object-fit:contain;background:#fcfdfe;border:1px solid #eef2f6}details{padding-top:14px}summary{cursor:pointer;font-weight:600}li{margin:6px 0}.section-title{margin-top:46px;font-size:25px}@media(max-width:850px){section{grid-template-columns:1fr}main{padding:24px 16px}h1{font-size:29px}.stats{gap:18px;flex-wrap:wrap}img{height:300px}}</style><main><div class="eyebrow">CHART GOLD / VERSION 2.0.0</div><h1>先有完整、可核对的教师，<br>再判断图形是否合格。</h1><p class="lead">原创合成数据 · 独立标准库计算 · Matplotlib教师 · 实际图元完整性检查。点击图查看可缩放SVG；每题提供完整CSV、计算答案和明确验收清单。</p>'''
    document += f'<div class="stats"><div><strong>{complex_count}</strong>复杂结构挑战</div><div><strong>{complex_rows:,}</strong>复杂场景原始行</div><div><strong>24</strong>基础形式回归</div><div><strong>SVG + CSV</strong>可审阅、可复现</div></div>'
    document += '<p class="lead">本页是教师参考，不是产品通过成绩。完整数值及拓扑应一致；允许不同合法布局。小单元可不强塞文字，但不得漏掉数据；全部路径和值在CSV。公开开发集已用于改进，不冒充盲测。</p><nav><a href="#complex">复杂挑战</a><a href="#basic">基础教师</a><a href="teacher-audit.json">图元核对</a><a href="cases.json">完整题目</a></nav>'
    for tier, title in [('complex', '复杂结构与真实困难'), ('basic', '24种基础形式 · 修订教师')]:
        document += f'<h2 id="{tier}" class="section-title">{title}</h2><section>' + ''.join(card for t, card in cards if t == tier) + '</section>'
    (output / 'index.html').write_bytes((document + '</main></html>').encode('utf-8'))
    files = {s: digest(ROOT / s) for s in SOURCES}
    for p in sorted(output.rglob('*')):
        if p.is_file() and p.suffix != '.png': files[(PACKAGE / p.relative_to(output)).as_posix()] = digest(p)
    write_json(output / 'manifest.json', dict(version=VERSION, cases=len(items), complex_cases=complex_count,
               complex_rows=complex_rows, original_synthetic_data=True, license='project MIT',
               scope='frozen public development teachers, not product pass or blind benchmark', files=files))
    print(json.dumps({'figures': len(items), 'complex_rows': complex_rows, 'output': str(output)}, ensure_ascii=False))


def check(directory):
    from evals.chart_gold_v2 import cases, oracle
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    for path, expected in manifest['files'].items():
        p=Path(path)
        file=directory/p.relative_to(PACKAGE) if p.is_relative_to(PACKAGE) else ROOT/p
        if digest(file)!=expected: raise ValueError('冻结内容已变化：'+path)
    saved=json.loads((directory/'cases.json').read_text(encoding='utf-8'))['cases']
    assert saved==cases() and json.loads((directory/'answers.json').read_text(encoding='utf-8'))=={c['id']:oracle(c) for c in cases()}
    print('36教师输入、答案、图元记录、公开SVG及源码哈希一致；未认证产品通过。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=ROOT/PACKAGE); parser.add_argument('--check', action='store_true')
    args=parser.parse_args(); check(args.output) if args.check else build(args.output)
