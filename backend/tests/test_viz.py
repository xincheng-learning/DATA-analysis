"""Tests for the visualization tool.

The chart-type selection uses the LLM and is exercised via integration tests
elsewhere; these unit tests focus on the deterministic spec-builder so we
can catch regressions without spending API calls.
"""
from __future__ import annotations

import pytest

from app.tools.viz import ChartSpec, build_plotly_spec


@pytest.mark.parametrize('orientation', ['v', 'h'])
def test_mixed_units_use_independent_panels_without_losing_values(orientation):
    from app.checks import verify_chart
    columns = ['item_amount_brl', 'delivered_orders', 'avg_item_amount_per_order_brl']
    rows = [dict(month='2018-06', **dict(zip(columns, [42481.28, 287, 148.018397212544]))),
            dict(month='2018-07', **dict(zip(columns, [50588.49, 359, None])))]
    chart = build_plotly_spec(_spec(chart_type='bar', y_column=None, y_columns=columns, orientation=orientation), rows)
    assert chart['layout']['meta']['unit_panels'] == 3
    assert chart['layout']['showlegend'] is False
    for index, column in enumerate(columns):
        suffix = str(index + 1) if index else ''
        trace = chart['data'][index]
        assert trace['xaxis'] == f'x{suffix}' and trace['yaxis'] == f'y{suffix}'
        assert trace['x' if orientation == 'h' else 'y'] == [row[column] for row in rows]
        assert trace['meta']['y_column'] == column
        assert chart['layout'][f'yaxis{suffix}']['domain'][0] < chart['layout'][f'yaxis{suffix}']['domain'][1]
    assert verify_chart(chart, rows, len(rows))['status'] == 'passed'


def test_explicit_same_currency_keeps_grouped_comparison():
    chart = build_plotly_spec(_spec(chart_type='bar', y_columns=['june_amount_brl', 'july_amount_brl']),
                              [dict(month='SP', june_amount_brl=10, july_amount_brl=12)])
    assert 'meta' not in chart['layout']
    assert all('yaxis' not in trace for trace in chart['data'])


def test_independent_money_ratio_and_percent_keep_explicit_scales():
    columns = ['item_amount_brl', 'late_rate', 'conversion_pct']
    chart = build_plotly_spec(_spec(y_columns=columns, value_scale='ratio'),
                              [dict(month='June', **dict(zip(columns, [100, .25, 25])))])
    assert [trace['meta']['value_scale'] for trace in chart['data']] == ['number', 'ratio', 'percent']
    assert chart['layout']['yaxis2']['tickformat'] == '.1%'
    assert chart['layout']['yaxis3']['ticksuffix'] == '%'
    assert [trace['y'][0] for trace in chart['data']] == [100, .25, 25]


def test_grouped_top_n_uses_one_explicit_ranking_for_all_periods():
    rows = [{'month':'large','revenue':1000,'later':999,'abs_change':1},
            {'month':'loss','revenue':200,'later':10,'abs_change':190},
            {'month':'gain','revenue':20,'later':100,'abs_change':80}]
    spec = _spec(chart_type='bar', y_column=None, y_columns=['revenue','later'], row_limit=2, sort_column='abs_change')
    chart = build_plotly_spec(spec, rows)
    assert [trace['x'] for trace in chart['data']] == [['loss','gain'],['loss','gain']]
    assert [trace['y'] for trace in chart['data']] == [[200,20],[10,100]]
    signed=[{**row,'change':row['later']-row['revenue']} for row in rows]
    absolute=build_plotly_spec(spec.model_copy(update={'sort_column':'change','sort_absolute':True}),signed)
    assert [trace['x'] for trace in absolute['data']] == [['loss','gain'],['loss','gain']]
    with pytest.raises(ValueError, match='排名'):
        build_plotly_spec(spec.model_copy(update={'sort_column':None}), rows)
    for invalid in [None, float('inf'), True, '190']:
        with pytest.raises(ValueError, match='排名'):
            build_plotly_spec(spec, [{**rows[1], 'abs_change':invalid}])


@pytest.mark.parametrize('scale,value', [('ratio', .25), ('percent', 25), ('number', 25)])
def test_chart_keeps_explicit_measure_scale_without_changing_values(scale, value):
    spec = _spec(chart_type='bar', value_scale=scale)
    chart = build_plotly_spec(spec, [{'month': 'Jan', 'revenue': value}])
    assert chart['data'][0]['meta']['value_scale'] == scale
    assert chart['data'][0]['meta']['metric_column'] == 'revenue'
    assert chart['data'][0]['y'] == [value]


def test_multiseries_lines_and_stacked_bars_preserve_nulls():
    rows = [{'month':'Jan','region':'East','revenue':100}, {'month':'Feb','region':'East','revenue':None},
            {'month':'Jan','region':'West','revenue':60}]
    line = build_plotly_spec(_spec(series_column='region'), rows)
    assert len(line['data']) == 2 and line['data'][0]['y'] == [100, None]
    stacked = build_plotly_spec(_spec(chart_type='bar',series_column='region',bar_mode='stack'), rows)
    assert stacked['layout']['barmode'] == 'stack'
    assert [trace['name'] for trace in stacked['data']] == ['East','West']


def test_horizontal_bars_keep_category_value_pairs_and_swap_axis_titles():
    rows=[{'month':'Jan','revenue':-12},{'month':'Feb','revenue':30}]
    chart=build_plotly_spec(_spec(chart_type='bar',orientation='h'),rows)
    assert chart['data'][0]['orientation']=='h'
    assert chart['data'][0]['x']==[-12,30] and chart['data'][0]['y']==['Jan','Feb']
    assert chart['layout']['xaxis']['title']=='Revenue'
    assert chart['layout']['yaxis']['title']=='Month'
    discrete=build_plotly_spec(_spec(chart_type='bar',orientation='h',axis_mode='discrete'),rows)
    assert discrete['layout']['yaxis']['type']=='category' and discrete['layout']['yaxis']['categoryarray']==['Jan','Feb']
    assert discrete['layout']['xaxis'].get('type')!='category'


@pytest.mark.parametrize('order,expected', [('asc', ['SC','RJ','MA']), ('desc', ['MG','MA','RJ'])])
def test_ranked_chart_limits_real_points_without_truncating_sql_rows(order, expected):
    rows = [{'month':'MG','revenue':1458.15}, {'month':'RJ','revenue':-1452.77},
            {'month':'SC','revenue':-1785.46}, {'month':'MA','revenue':-1349.19}]
    original = [dict(row) for row in rows]
    chart = build_plotly_spec(_spec(chart_type='bar',orientation='h',row_limit=3,sort_order=order),rows)
    assert chart['data'][0]['y'] == expected
    assert len(chart['data'][0]['x']) == 3 and rows == original


@pytest.mark.parametrize('changes,rows', [
    ({'series_column':'region'}, [{'month':'Jan','revenue':1,'region':'East'}]),
    ({}, [{'month':'Jan','revenue':None}]),
])
def test_rank_limit_rejects_ambiguous_series_or_unknown_rank_values(changes, rows):
    with pytest.raises(ValueError,match='排名'):
        build_plotly_spec(_spec(chart_type='bar',row_limit=3,**changes),rows)


def test_top_states_can_filter_explicit_other_aggregate_before_ranking():
    rows = [{'month':state,'revenue':amount,'row_type':'state'} for state,amount in [('SC',-1785.46),('RJ',-1452.77),('MA',-1349.19)]]
    rows.append({'month':'Other states','revenue':-2597.24,'row_type':'other'})
    spec = _spec(chart_type='bar',row_limit=3,sort_order='asc',row_filter={'row_type':'state'})
    chart = build_plotly_spec(spec,rows)
    assert chart['data'][0]['x']==['SC','RJ','MA'] and len(rows)==4
    with pytest.raises(ValueError,match='字段'):
        build_plotly_spec(spec.model_copy(update={'row_filter':{'missing':'state'}}),rows)
    with pytest.raises(ValueError,match='排名'):
        build_plotly_spec(spec.model_copy(update={'row_limit':None}),rows)


def test_rank_filter_preserves_boolean_type_and_refuses_mixed_grain_ranking():
    rows=[{'month':'SC','revenue':-1785.46,'selected':True}, {'month':'MG','revenue':1458.15,'selected':False}]
    chart=build_plotly_spec(_spec(chart_type='bar',row_limit=3,sort_order='asc',row_filter={'selected':True}),rows)
    assert chart['data'][0]['x']==['SC']
    rows=[{'month':'SC','revenue':-1785.46,'row_type':'state'}, {'month':'Other','revenue':-2597.24,'row_type':'other'}]
    with pytest.raises(ValueError,match='粒度'):
        build_plotly_spec(_spec(chart_type='bar',row_limit=3),rows)


def test_wide_series_and_sparse_heatmap_require_unambiguous_cells():
    rows = [{'month':'Jan','revenue':100,'payments':106}]
    chart = build_plotly_spec(_spec(y_columns=['revenue','payments']), rows)
    assert len(chart['data']) == 2 and chart['data'][1]['y'] == [106]
    rows = [{'month':'Jan','region':'East','value':5}, {'month':'Feb','region':'West','value':8}]
    heatmap = _spec(chart_type='heatmap', y_column='region', z_column='value')
    chart = build_plotly_spec(heatmap, rows)
    assert chart['data'][0]['z'] == [[5,None],[None,8]]
    with pytest.raises(ValueError, match='重复'):
        build_plotly_spec(heatmap, rows+rows[:1])
    with pytest.raises(ValueError):
        build_plotly_spec(_spec(series_column='missing'), rows)


def test_chart_rejects_text_as_numeric_measure():
    with pytest.raises(ValueError, match='数值'):
        build_plotly_spec(_spec(), [{'month':'Jan','revenue':'not a number'}])
    with pytest.raises(ValueError, match='数值'):
        build_plotly_spec(_spec(chart_type='heatmap',y_column='region',z_column='value'),
                          [{'month':'Jan','region':'East','value':'wrong'}])


def test_waterfall_reconciles_start_contributions_and_end_before_rendering():
    spec = _spec(chart_type='waterfall',x_column='label',y_column='amount',measure_column='kind')
    rows = [{'label':'May','amount':100,'kind':'start'}, {'label':'A','amount':-20,'kind':'change'},
            {'label':'B','amount':5,'kind':'change'}, {'label':'June','amount':85,'kind':'end'}]
    result = build_plotly_spec(spec,rows)
    assert result['data'][0]['type'] == 'waterfall'
    assert result['data'][0]['measure'] == ['absolute','relative','relative','total']
    rows[-1]['amount'] = 90
    with pytest.raises(ValueError,match='加总'):
        build_plotly_spec(spec,rows)
    with pytest.raises(ValueError):
        build_plotly_spec(spec,rows[:-1])


def _spec(**overrides) -> ChartSpec:
    base = {
        "chart_type": "line",
        "x_column": "month",
        "y_column": "revenue",
        "title": "Monthly revenue",
        "x_label": "Month",
        "y_label": "Revenue",
    }
    base.update(overrides)
    return ChartSpec(**base)


@pytest.mark.parametrize('kind',['area','box','circle','bubble','heatmap_marks','text_table','highlight_table'])
def test_tableau_first_family_uses_actual_fields_and_program_source_checks(kind):
    from app.checks import verify_chart
    rows=[{'month':'Jan','region':'East','revenue':0,'size':10,'color':2},
          {'month':'Feb','region':'West','revenue':30,'size':20,'color':4}]
    extra={'size_column':'size','color_column':'color'} if kind in {'bubble','heatmap_marks'} else {}
    if kind=='bubble':extra['x_column']='color'
    if kind=='highlight_table':extra={'y_column':'region','z_column':'revenue'}
    if kind=='heatmap_marks':extra.update(y_column='region')
    chart=build_plotly_spec(_spec(chart_type=kind,**extra),rows)
    assert verify_chart(chart,rows,len(rows))['status']=='passed'
    if kind=='box':assert chart['data'][0]['quartilemethod']=='linear'
    if kind=='area':assert chart['data'][0]['fill']=='tozeroy'
    if kind=='bubble':
        chart['data'][0]['marker']['size'][1]=999
        with pytest.raises(ValueError,match='来源'):verify_chart(chart,rows,len(rows))


def test_area_stacks_only_compatible_units_and_discrete_axes_keep_order():
    rows=[{'month':'Feb','sales_cny':10,'payments_cny':20},{'month':'Jan','sales_cny':20,'payments_cny':30}]
    chart=build_plotly_spec(_spec(chart_type='area',y_columns=['sales_cny','payments_cny'],axis_mode='discrete'),rows)
    assert all(trace['stackgroup']=='same-unit' for trace in chart['data'])
    assert chart['layout']['xaxis']['type']=='category'
    assert chart['layout']['xaxis']['categoryarray']==['Feb','Jan']
    with pytest.raises(ValueError,match='同一单位'):
        build_plotly_spec(_spec(chart_type='area',y_columns=['sales_cny','order_count']),[{'month':'Jan','sales_cny':10,'order_count':3}])


def test_extreme_box_outlier_has_readable_main_scale_and_explicit_full_range_view():
    from app.checks import verify_chart
    rows=[{'month':'A','revenue':value} for value in [1,2,3,4,5,100]]+[{'month':'B','revenue':value} for value in [2,4,6,8]]
    chart=build_plotly_spec(_spec(chart_type='box'),rows)
    assert len(chart['data'])==2 and chart['layout']['meta']['box_focus'] is True
    assert chart['layout']['yaxis']['range']==[0,10] and chart['layout']['yaxis2']['range'][1]>100
    assert chart['data'][0]['y']==chart['data'][1]['y'] and 100 in chart['data'][1]['y']
    assert '两侧纵轴范围不同' in chart['layout']['meta']['chart_note']
    assert verify_chart(chart,rows,10)['status']=='passed'
    ordinary=build_plotly_spec(_spec(chart_type='box'),[{'month':'A','revenue':value} for value in [1,2,3,4,5]])
    assert len(ordinary['data'])==1


def test_boxplot_refuses_query_statistics_from_a_different_quartile_rule():
    rows=[{'month':'A','revenue':value,'q1':2.25,'median':3.5,'q3':4.75,'iqr':2.5,'whisker_low':1,'whisker_high':5} for value in [1,2,3,4,5,100]]
    with pytest.raises(ValueError,match='四分位/须规则不一致'):
        build_plotly_spec(_spec(chart_type='box'),rows)


@pytest.mark.parametrize('values',[[0],[2,2,2],[-8,-6,-4,-2,-1,-100],[None,1,2,3]])
def test_boxplot_finite_edge_cases_preserve_observations_without_inventing_spread(values):
    rows=[{'month':'A','revenue':value} for value in values]
    chart=build_plotly_spec(_spec(chart_type='box'),rows)
    assert chart['data'][0]['y']==values
    stat=chart['layout']['meta']['box_statistics'][0]
    assert stat['n']==sum(value is not None for value in values)
    assert stat['whisker_low']<=stat['median']<=stat['whisker_high']


# --- happy paths ------------------------------------------------------------


def test_line_spec_has_lines_plus_markers() -> None:
    rows = [{"month": "2017-01", "revenue": 100}, {"month": "2017-02", "revenue": 200}]
    out = build_plotly_spec(_spec(), rows)
    assert out["data"][0]["type"] == "scatter"
    assert out["data"][0]["mode"] == "lines+markers"
    assert out["data"][0]["x"] == ["2017-01", "2017-02"]
    assert out["data"][0]["y"] == [100, 200]
    assert out["layout"]["title"] == "Monthly revenue"
    assert out["layout"]["xaxis"]["title"] == "Month"
    assert out["layout"]["yaxis"]["title"] == "Revenue"


def test_bar_spec() -> None:
    rows = [{"month": "Jan", "revenue": 1}, {"month": "Feb", "revenue": 2}]
    out = build_plotly_spec(_spec(chart_type="bar"), rows)
    assert out["data"][0]["type"] == "bar"


def test_histogram_uses_only_x() -> None:
    rows = [{"price": v} for v in [10, 12, 15, 20, 25, 30]]
    out = build_plotly_spec(
        _spec(chart_type="histogram", x_column="price", y_column=None,
              x_label="Price", y_label="Count"),
        rows,
    )
    assert out["data"][0]["type"] == "histogram"
    assert "x" in out["data"][0]
    assert "y" not in out["data"][0]


def test_histogram_preserves_preaggregated_frequencies() -> None:
    rows = [{"price": 25, "n": 3}, {"price": 100, "n": 6}]
    out = build_plotly_spec(_spec(chart_type="histogram", x_column="price", y_column="n"), rows)
    trace = out["data"][0]
    assert trace["x"] == [25, 100]
    assert trace["y"] == [3, 6] and trace["histfunc"] == "sum"


def test_pie_uses_labels_and_values() -> None:
    rows = [{"month": "Jan", "revenue": 30}, {"month": "Feb", "revenue": 70}]
    out = build_plotly_spec(_spec(chart_type="pie"), rows)
    assert out["data"][0]["type"] == "pie"
    assert out["data"][0]["labels"] == ["Jan", "Feb"]
    assert out["data"][0]["values"] == [30, 70]
    # Pie charts shouldn't have axis labels in layout
    assert "xaxis" not in out["layout"]
    assert "yaxis" not in out["layout"]


# --- error / edge cases -----------------------------------------------------


def test_empty_rows_returns_empty_data() -> None:
    out = build_plotly_spec(_spec(), [])
    assert out["data"] == []
    assert out["layout"]["title"] == "Monthly revenue"


def test_unsafe_browser_integers_are_not_silently_rounded_in_a_chart():
    rows=[{'month':'Jan','revenue':9007199254740993}]
    with pytest.raises(ValueError,match='精确整数'):build_plotly_spec(_spec(),rows)
    table=build_plotly_spec(_spec(chart_type='text_table'),rows)
    assert table['data'][0]['cells']['values'][1]==['9007199254740993']


def test_missing_x_column_raises() -> None:
    with pytest.raises(ValueError, match="x_column"):
        build_plotly_spec(_spec(x_column="not_there"), [{"month": "Jan", "revenue": 1}])


def test_missing_y_column_raises() -> None:
    with pytest.raises(ValueError, match="y_column"):
        build_plotly_spec(_spec(y_column="not_there"), [{"month": "Jan", "revenue": 1}])
def test_waterfall_category_and_other_contributions_remain_reconciled():
    from app.tools.viz import ChartSpec, build_plotly_spec
    import pytest
    spec=ChartSpec(chart_type='waterfall',x_column='label',y_column='amount',measure_column='kind',
                   title='贡献',x_label='步骤',y_label='BRL')
    rows=[{'label':'起点','amount':100,'kind':'start'}, {'label':'A','amount':-40,'kind':'category'},
          {'label':'其他','amount':10,'kind':'other'},{'label':'终点','amount':70,'kind':'end'}]
    assert build_plotly_spec(spec,rows)['data'][0]['measure'] == ['absolute','relative','relative','total']
    rows[-1]['amount']=80
    with pytest.raises(ValueError,match='加总不一致'):
        build_plotly_spec(spec,rows)


def test_funnel_keeps_stage_order_and_rejects_growth_or_missing_counts():
    spec=_spec(chart_type='funnel',x_column='stage',y_column='orders',x_label='履约阶段',y_label='订单数')
    rows=[{'stage':'下单','orders':100},{'stage':'审批','orders':80},{'stage':'签收','orders':60}]
    trace=build_plotly_spec(spec,rows)['data'][0]
    assert trace['type']=='funnel' and trace['y']==['下单','审批','签收'] and trace['x']==[100,80,60]
    for value in [101,None,-1]:
        rows[1]['orders']=value
        with pytest.raises(ValueError):
            build_plotly_spec(spec,rows)
    for row in rows:
        row['orders']=0
    with pytest.raises(ValueError,match='没有到达样本'):
        build_plotly_spec(spec,rows)
