from copy import deepcopy
import pytest
from evals.chart24_reference import cases, oracle
from evals.chart24_scoring import check, corrupt


def test_chart_teacher_covers_fixed_24_and_preserves_independent_statistics():
    items=cases()
    assert [c['id'] for c in items]==[f'SM{i:02}' for i in range(1,25)]
    assert oracle(items[18])['valid_n']==8 and oracle(items[19])['stats'][0]['q1']==2
    assert oracle(items[20])['days']==[2,5]


def test_chart_reference_sources_and_inputs_match_the_frozen_manifest():
    import ast
    import hashlib
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    source=root/'evals/chart24_reference.py'
    manifest=json.loads((root/'evals/chart24_v1/manifest.json').read_text(encoding='utf-8'))
    assert manifest['reference_source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest['families_sha256']==hashlib.sha256((root/'evals/tableau_reference_v1/families.json').read_bytes()).hexdigest()
    assert [oracle(c) for c in cases()]==[c['oracle'] for c in manifest['cases']]
    tree=ast.parse(source.read_text(encoding='utf-8'))
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('app')
        or isinstance(n,ast.Import) and any(a.name.startswith('app') for a in n.names) for n in ast.walk(tree))


def test_packed_bubble_negative_changes_the_area_input_consumed_by_packing():
    case=cases()[22]
    figure={'data':[{'meta':{'family':'packed_bubble'},'text':['A','B','C','零值'],
        'x':['A','B','C','零值'],'y':[1,4,9,0],'marker':{'size':[1,4,9,0],'sizemode':'area'}}]}
    assert check(case,figure)
    altered=corrupt(figure)
    assert altered['data'][0]['y']!=figure['data'][0]['y']
    with pytest.raises(AssertionError):check(case,altered)
