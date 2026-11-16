"""Product outputs compared with frozen independently authored inputs and constants."""
import copy
import json
from pathlib import Path

import pytest

from app.checks import verify_chart
from app.tools.viz import ChartSpec,build_plotly_spec

CASES=json.loads((Path(__file__).resolve().parents[2]/'evals/tableau_reference_v1/families.json').read_text('utf-8'))
OPTIONS={
    'TB002':dict(chart_type='gantt',x_column='task',start_column='start',end_column='end'),
    'TB003':dict(chart_type='bullet',x_column='group',y_column='actual',target_column='target'),
    'TB004':dict(chart_type='packed_bubble',x_column='group',y_column='amount'),
    'TB005':dict(chart_type='combo',x_column='month',y_column='amount_cny',secondary_column='conversion_ratio',secondary_label='转化比例',secondary_scale='ratio'),
    'TB006':dict(chart_type='symbol_map',x_column='place',longitude_column='longitude',latitude_column='latitude',size_column='amount'),
    'TB007':dict(chart_type='filled_map',x_column='region',y_column='amount',geometry_column='geometry'),
}


def figure(key,**changes):
    spec=ChartSpec(title=key,x_label='分类/日期',y_label='金额',**{**OPTIONS[key],**changes})
    return build_plotly_spec(spec,copy.deepcopy(CASES[key]['rows']))


@pytest.mark.parametrize('key',OPTIONS)
def test_teacher_inputs_survive_source_verification_and_tampering_fails(key):
    rows=copy.deepcopy(CASES[key]['rows']);chart=figure(key)
    assert verify_chart(chart,rows,len(rows))['status']=='passed'
    trace=chart['data'][0]
    field='base' if key=='TB002' else 'lon' if key=='TB006' else 'z' if key=='TB007' else 'y'
    trace[field][0]=999
    with pytest.raises(ValueError):verify_chart(chart,rows,len(rows))


def test_gantt_and_bullet_match_independent_constants():
    gantt=figure('TB002');assert gantt['data'][0]['x']==[172800000,432000000]
    assert gantt['data'][0]['base']==['2026-01-01','2026-01-02']
    bullet=figure('TB003');assert bullet['data'][0]['x']==[80,120] and bullet['data'][1]['x']==[100,110]


@pytest.mark.parametrize('kind',['combo','dual_line'])
def test_explicit_axes_preserve_zero_gap_and_original_scale(kind):
    chart=figure('TB005',chart_type=kind)
    assert chart['data'][0]['y']==[100,0,150] and chart['data'][1]['y']==[.2,.3,None]
    assert chart['data'][1]['yaxis']=='y2' and chart['layout']['yaxis2']['tickformat']=='.0%'
    assert chart['layout']['yaxis2']['title']=='转化比例'


def test_map_has_exact_coordinates_and_given_geometry_without_external_assets():
    point=figure('TB006');trace=point['data'][0]
    assert trace['lon']==[116.4,121.5] and trace['lat']==[39.9,31.2]
    assert trace['marker']['size']==[4,9]
    filled=figure('TB007');assert filled['data'][0]['z']==[0,10]
    assert filled['data'][0]['geojson']['features'][0]['geometry']==CASES['TB007']['rows'][0]['geometry']
    assert 'http' not in json.dumps([point,filled])
    rows=copy.deepcopy(CASES['TB007']['rows']);rows[0]['amount']=None
    unknown=build_plotly_spec(ChartSpec(title='未知',x_label='地区',y_label='值',**OPTIONS['TB007']),rows)
    assert unknown['data'][0]['z']==[None,10] and unknown['layout']['map']['layers'][0]['source']==unknown['data'][0]['geojson']


@pytest.mark.parametrize('key,column,bad',[
    ('TB002','end','2025-01-01'),('TB002','start','unknown'),('TB003','target',None),
    ('TB004','amount',-1),('TB006','longitude',181),('TB006','latitude',None),
    ('TB007','geometry','https://example.com/boundary.json'),
    ('TB007','geometry',{'type':'Polygon','coordinates':[[[100,30],[101,30],[101,31]]]}),
])
def test_invalid_dates_targets_areas_and_boundaries_are_not_guessed(key,column,bad):
    rows=copy.deepcopy(CASES[key]['rows']);rows[0][column]=bad
    with pytest.raises(ValueError):build_plotly_spec(ChartSpec(title=key,x_label='x',y_label='y',**OPTIONS[key]),rows)
