import hashlib
import json
from pathlib import Path

import pytest

from evals.chart_gold_v2 import cases, oracle, quantile


def test_complex_set_has_real_structure_and_preserves_old_inputs():
    from evals.chart24_reference import cases as original
    items = cases()
    assert len(items) == 36
    assert [c['id'] for c in items[:24]] == [c['id'] for c in original()]
    assert [c['rows'] for c in items[:24]] == [c['rows'] for c in original()]
    complex_cases = items[24:]
    assert len(complex_cases) == 12 and sum(len(c['rows']) for c in complex_cases) > 4000
    assert {'sunburst', 'sankey', 'treemap', 'box', 'histogram', 'gantt'} <= {c['kind'] for c in complex_cases}
    assert all(c['challenge'] and c['review'] and c['question'] for c in items)


def test_teacher_quantiles_are_type7_linear_and_basic_box_statistics():
    # 教师这里用的是 numpy 默认的 type-7 线性插值：(n-1)*p。
    assert [quantile([1, 2, 3, 4, 5, 100], p) for p in [.25, .5, .75]] == [2.25, 3.5, 4.75]
    box = oracle(cases()[19])
    assert box['groups'][0]['outliers'] == [100]
    assert box['groups'][1]['median'] == 5


def test_type7_teacher_is_not_the_plotly_linear_scoring_contract():
    from app.tools.viz import box_percentile

    teacher = quantile([1, 2, 3, 4, 5, 100], .25)
    assert teacher == 2.25
    assert box_percentile([1, 2, 3, 4, 5, 100], .25) == 2


def test_histogram_accounts_for_every_known_duplicate_zero_and_edge():
    for c in [cases()[18], cases()[28]]:
        o = oracle(c)
        assert sum(o['counts']) == o['valid_n']
        assert len(o['bin_by_row']) == len(c['rows'])
        assert sum(v is None for v in o['bin_by_row']) == o['missing_n']
    assert oracle(cases()[18])['counts'] == [4, 3, 1]


def test_deep_paths_and_role_specific_flow_nodes_conserve_weights():
    items = {c['id']: c for c in cases()}
    tree = oracle(items['CX07'])
    assert len(tree['leaves']) == 200
    assert tree['total'] == sum(x['value'] for x in tree['leaves'])
    flow = oracle(items['CX09'])
    assert sum(flow['sources'].values()) == sum(flow['targets'].values()) == flow['total']
    assert '线上' in flow['sources'] and '线上' in flow['targets']


def test_new_reference_is_frozen_and_does_not_change_v1():
    root = Path(__file__).resolve().parents[2]
    old = json.loads((root / 'evals/chart24_v1/manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((root / 'evals/chart24_reference.py').read_bytes()).hexdigest() == old['reference_source_sha256']
    manifest = json.loads((root / 'evals/chart_gold_v2/manifest.json').read_text(encoding='utf-8'))
    for file, digest in manifest['files'].items():
        assert hashlib.sha256((root / file).read_bytes()).hexdigest() == digest, file


def test_teacher_visible_marks_include_isolated_april_and_audit_rejects_omission():
    pytest.importorskip('matplotlib')
    from evals.chart_gold_render import render_case, audit_figure
    import matplotlib.pyplot as plt
    c = cases()[14]
    fig = render_case(c)
    try:
        result = audit_figure(c, fig)
        assert result['checked_observations'] == 6
        line = next(line for ax in fig.axes for line in ax.lines if line.get_gid() == 'series:amount_cny')
        line.set_ydata([100, 0, float('nan'), float('nan')])
        with pytest.raises(AssertionError):
            audit_figure(c, fig)
    finally:
        plt.close(fig)


@pytest.mark.parametrize('ident, marker, change', [
    ('CX05', 'bin:0', 'histogram'),
    ('CX07', 'leaf:["区域1","渠道1","标准","产品01"]', 'area'),
    ('CX09', 'flow:1', 'flow'),
])
def test_actual_teacher_audit_rejects_wrong_aggregation_area_and_omitted_flow(ident, marker, change):
    pytest.importorskip('matplotlib')
    from evals.chart_gold_render import render_case, audit_figure
    import matplotlib.pyplot as plt
    c = next(c for c in cases() if c['id'] == ident)
    fig = render_case(c)
    try:
        assert audit_figure(c, fig)['passed']
        artist = next(a for a in fig.findobj() if a.get_gid() == marker)
        if change == 'histogram': artist.set_height(0)
        elif change == 'area': artist.set_width(artist.get_width() * 2)
        else: artist.remove()
        with pytest.raises((AssertionError, KeyError)):
            audit_figure(c, fig)
    finally:
        plt.close(fig)


def test_reference_numerics_and_renderer_never_import_product_builders():
    import ast
    root = Path(__file__).resolve().parents[2]
    for file in ['evals/chart_gold_v2.py', 'evals/chart_gold_render.py']:
        tree = ast.parse((root / file).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom): assert not (node.module or '').startswith('app')
            if isinstance(node, ast.Import): assert not any(n.name.startswith('app') for n in node.names)
