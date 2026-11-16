"""Visualization tool: turn a tabular SQL result into a Plotly chart spec.

We do NOT render images server-side. Instead we emit a Plotly JSON spec
that the frontend (or any Plotly-compatible client) can render. This keeps
the backend stateless and chart-library-agnostic at the boundary.

The LLM picks chart type + axes via structured output; we then construct
the Plotly dict deterministically from the data so we don't trust the
model to emit valid JSON for the whole spec.
"""
from __future__ import annotations

import logging
import math
import re
from decimal import Decimal
from numbers import Integral, Real
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm import get_structured_llm
from app.execution import invoke_model
from app.answer_quality import analyst_guidance

logger = logging.getLogger(__name__)
MAX_CHART_ROWS = 5000


def box_percentile(values: list, fraction: float) -> float:
    """Plotly 3.7 linear (Langford #10): clipped position n*p-0.5.

    Source: plotly.js/src/lib/stats.js interp; type-7 teachers use a different rule.
    """
    position = max(0, min(len(values) - 1, len(values) * fraction - .5))
    lower = math.floor(position)
    upper = math.ceil(position)
    return float(values[lower]) + (float(values[upper]) - float(values[lower])) * (position - lower)

ChartType = Literal["line", "bar", "histogram", "scatter", "pie", "heatmap", "waterfall", "funnel",
                    "area", "box", "circle", "bubble", "heatmap_marks", "text_table", "highlight_table",
                    "gantt", "bullet", "packed_bubble", "dual_line", "combo", "symbol_map", "filled_map"]


class ChartSpec(BaseModel):
    """LLM-produced chart description. We build the Plotly JSON from this."""

    chart_type: ChartType = Field(..., description="Best chart type for the data.")
    x_column: str = Field(..., description="Column to use on the x-axis (or labels for pie).")
    y_column: str | None = Field(
        default=None,
        description=(
            "Column to use on the y-axis (or values for pie). "
            "Leave null only for histograms where x is the value being binned."
        ),
    )
    title: str = Field(..., description="Short, human-readable chart title.")
    x_label: str = Field(..., description="X-axis label.")
    y_label: str = Field(..., description="Y-axis label.")
    series_column: str | None = Field(default=None, description='Long-form category column for multiple line/bar/scatter series.')
    y_columns: list[str] = Field(default_factory=list, max_length=8, description='Wide-form numeric columns sharing the SAME unit. Use instead of series_column.')
    bar_mode: Literal['group', 'stack'] = 'group'
    orientation: Literal['v','h'] = Field(default='v',description='For bar charts use h when a horizontal ranking/bar chart is requested; x_column remains the category and y_column the measure.')
    row_limit: int | None = Field(default=None, ge=1, le=1000, description='For an explicitly requested top-N bar ranking, plot N categories using sort_column or the single y_column. Otherwise null.')
    sort_column: str | None = Field(default=None, description='Explicit numeric ranking column when different from y_column; required for top-N wide-form grouped bars. For absolute change rankings use a SQL-computed absolute change column, not a period amount.')
    sort_absolute: bool = Field(default=False, description='With row_limit, rank by ABS(sort_column) for largest impact / absolute change; keep the plotted signed amounts unchanged.')
    sort_order: Literal['asc','desc'] = Field(default='desc', description='With row_limit, asc for the largest decreases/smallest values; desc for the largest values/increases.')
    row_filter: dict[str,str | bool | int | float] = Field(default_factory=dict, max_length=1, description='For a top-N ranking with explicitly marked aggregate/Other rows, keep only the category grain by equality, e.g. {"row_type":"state"}. Keep JSON value types: a Boolean flag uses true, never the string "True". Do not rank Other/total as an individual category. Otherwise empty.')
    z_column: str | None = Field(default=None, description='Numeric cell value for heatmap; x and y are its two dimensions.')
    measure_column: str | None = Field(default=None, description='Waterfall step type: absolute/start, relative/change/contribution, total/end.')
    value_scale: Literal['number', 'ratio', 'percent'] = Field(default='number', description='Measure scale: number for raw amounts/counts; ratio for proportions expressed as 0–1; percent for percentage values expressed as 0–100. Follow SQL and question, never guess from magnitude alone.')
    axis_mode:Literal['auto','continuous','discrete']=Field(default='auto',description='Use discrete only for an explicitly categorical ordered axis; continuous requires real numbers or ISO dates.')
    size_column:str|None=Field(default=None,description='Actual nonnegative area measure for bubble/heatmap_marks; never invent marker sizes.')
    color_column:str|None=Field(default=None,description='Actual numeric measure encoded by marker color, separate from area.')
    table_columns:list[str]=Field(default_factory=list,max_length=30,description='Explicit columns for text_table; empty keeps all result columns.')
    start_column:str|None=None
    end_column:str|None=None
    target_column:str|None=Field(default=None,description='User-provided actual target, never inferred from maximum/mean.')
    secondary_column:str|None=Field(default=None,description='Second numeric measure, only for explicitly requested dual_line/combo.')
    secondary_label:str|None=None
    secondary_scale:Literal['number','ratio','percent']='number'
    longitude_column:str|None=None
    latitude_column:str|None=None
    geometry_column:str|None=Field(default=None,description='Actual per-region GeoJSON Polygon/MultiPolygon geometry object or JSON string; no URL or guessed boundary.')


CHART_SYSTEM_PROMPT = """You decide how to visualize a tabular SQL result.

Given the user's question, the result columns, and a sample of rows,
choose the BEST chart type and the columns for each axis.
Honor an explicitly requested supported chart type before these defaults.
For a histogram of raw observations, use the numeric values on x and y=null.

Guidelines:
- Time-series (date/timestamp on x, numeric on y) → "line"
- One categorical column + one numeric column → "bar"
- A single numeric column to study its distribution → "histogram"
  (in this case, x_column = the numeric column, y_column = null)
- Two numeric columns to study their relationship → "scatter"
- Small set of category shares (≤ 8 categories, parts-of-a-whole) → "pie"
- Category x category with numeric cell values → "heatmap", set z_column.
- An ordered conversion or fulfillment path → "funnel": x_column=stage label,
  y_column=number reaching that stage. Preserve process order. Each stage must
  be a subset of the preceding stage at the SAME entity grain and cohort.
  Mutually exclusive final order statuses are not a conversion path.
- Multiple categories over time: set series_column; do not connect different series.
- Multiple numeric columns of the SAME unit: set y_columns; never mix amounts and percentages on one axis.
- Grouped comparison: bar_mode="group"; additive composition: bar_mode="stack".
- Horizontal bar rankings: orientation="h". Still use x_column for categories and
  y_column for numeric values; the builder swaps the displayed axes and labels.
- For a requested top-N bar ranking, set row_limit=N and sort_order=asc for
  the largest decreases, desc for the largest values/increases. This limits the
  plotted points only; SQL can retain the full breakdown for reconciliation.
  If the result has an explicit row_type/grain column, use row_filter to keep
  actual categories before ranking; an Other/total aggregate is not a category.
  For wide-form grouped bars, set sort_column to the requested ranking measure
  (e.g. absolute change); both periods must use the same selected categories.
  Greatest impact / absolute change means sort_absolute=true, sort_order=desc
  on the signed change column. It includes both gains and losses, not just losses.
- Repeated (x, series) or heatmap (x,y) cells must be aggregated in SQL first.
- Always specify value_scale: number for amounts/counts, ratio when SQL returns
  fractions (0.25 means 25%), percent when SQL already returns percentages
  (25 means 25%). Check SQL column names and the question; do not rescale data.
- For a reconciled contribution bridge use "waterfall": x=step label,
  y=amount, measure_column=step type. A starting total, relative contributions
  and an ending total are required; the builder checks that they reconcile.

Pick column names EXACTLY as shown. Title and labels should be concise.

LANGUAGE: Always write `title`, `x_label`, and `y_label` in Simplified Chinese.
Keep column identifiers and original data values unchanged."""
CHART_SYSTEM_PROMPT += '''
Additional Tableau basic families: area for same-unit additive series (stacked when multiple),
box for complete raw grouped observations (linear quartiles, 1.5 IQR whiskers, outliers),
circle for categorical dot views, bubble for two numeric coordinates plus a real size_column,
heatmap_marks for two categories with real size_column and optional color_column,
text_table for exact result fields, highlight_table for numeric cells with labels.
Never build a box/distribution from a preview or replace raw observations by category averages.
Use axis_mode=discrete to preserve categorical order; continuous needs real numeric/ISO date values.
Do not invent business targets, sizes, geographical coordinates or statistical observations.
gantt: x_column=task label, start_column/end_column=actual ISO dates/timestamps; y_column=null.
bullet: x_column=category, y_column=actual, target_column=explicit target.
packed_bubble: x_column=category, y_column=nonnegative area; this is NOT an xy bubble chart.
dual_line/combo: ONLY for explicit dual-axis requests, y_column=left numeric measure,
secondary_column=right numeric measure, secondary_label and secondary_scale mandatory in meaning.
combo uses left bars and right line; dual_line uses two lines. Default mixed units still use panels.
symbol_map: actual longitude_column/latitude_column; x_column=place label, y_column=null;
optional size_column/color_column. filled_map: x_column=unique region ID, y_column=value,
geometry_column=actual per-row GeoJSON Polygon/MultiPolygon. Never manufacture coordinates,
business targets or boundaries in SQL. Maps use only provided data and no online basemap.
'''


def _get_chart_llm():
    return get_structured_llm(ChartSpec)


def choose_chart(
    question: str,
    columns: list[str],
    sample_rows: list[dict[str, Any]],
) -> ChartSpec:
    """Ask the LLM to pick chart type + axes from the result data."""
    from app.tools.dataset_manager import model_safe_rows
    sample_str = "\n".join(str(row) for row in model_safe_rows(sample_rows[:5]))
    prompt = (
        f"Question: {question}\n\n"
        f"Result columns: {columns}\n\n"
        f"Sample rows (up to 5):\n{sample_str}\n\n"
        "Choose the chart."
    )
    spec: ChartSpec = invoke_model(_get_chart_llm(), [
        SystemMessage(content=CHART_SYSTEM_PROMPT+analyst_guidance(question)),
        HumanMessage(content=prompt),
    ])
    logger.info("Chart: %s on x=%s y=%s", spec.chart_type, spec.x_column, spec.y_column)
    return spec


def _column_unit(column: str) -> str | None:
    """Use explicit identifiers/catalog units, never the size of observations."""
    from app.metrics import METRICS
    name = column.lower()
    if name.endswith(('_pct', '_percent', '_percentage')):
        return '%'
    if name.endswith('_ratio'):
        return 'ratio'
    currency = re.search(r'_(brl|usd|cny|eur|gbp)$', name)
    canonical = name[:currency.start()] if currency else name
    definition = METRICS.get(canonical, {})
    unit = currency.group(1).upper() if currency else definition.get('unit')
    if unit and unit != 'source_currency':
        if canonical == 'average_order_amount' or 'per_order' in canonical:
            return f'{unit}/单'
        return str(unit)
    if re.search(r'(^|_)(orders|order_count)$', name):
        return '单'
    if re.search(r'(^|_)(customers|users|customer_count|user_count)$', name):
        return '人'
    return None


def build_plotly_spec(
    spec: ChartSpec,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn a ChartSpec + rows into a Plotly JSON dict.

    Output structure: ``{"data": [...], "layout": {...}}`` — directly
    consumable by Plotly.js, plotly.py, or `plotly.io.from_json`.
    """
    if not rows:
        return {
            "data": [],
            "layout": {"title": spec.title},
        }

    # Validate columns exist
    available = set(rows[0].keys())
    if spec.x_column not in available:
        raise ValueError(
            f"x_column '{spec.x_column}' not in result columns {sorted(available)}"
        )
    required = [*(spec.y_columns or ([spec.y_column] if spec.y_column else [])),
                *([spec.series_column] if spec.series_column else []), *([spec.z_column] if spec.z_column else []),
                *([spec.measure_column] if spec.measure_column else []), *spec.row_filter,
                *[name for name in (spec.size_column,spec.color_column,spec.start_column,spec.end_column,
                    spec.target_column,spec.secondary_column,spec.longitude_column,spec.latitude_column,spec.geometry_column) if name],*spec.table_columns]
    if any(name not in available for name in required):
        raise ValueError(
            f"y_column / series_column / z_column 图表字段不在结果列中：{required}; available={sorted(available)}"
        )
    if spec.chart_type!='text_table' and any(isinstance(row[column],Integral) and abs(row[column])>2**53-1
            for row in rows for column in {spec.x_column,*required}):
        raise ValueError('绘图字段含超出浏览器精确整数范围的值；原始结果可精确查看/导出。标识字段请在SQL显式转文本，度量需先选择合适单位，未静默舍入。')

    if spec.chart_type in {'gantt','bullet','packed_bubble','dual_line','combo','symbol_map','filled_map'}:
        from app.tools.tableau import build_tableau_chart
        return build_tableau_chart(spec,rows)

    measures = [spec.z_column] if spec.chart_type in {'heatmap','highlight_table'} else [] if spec.chart_type in {'text_table','heatmap_marks'} else spec.y_columns or [spec.y_column]
    measures=[*measures,*[name for name in (spec.size_column,spec.color_column) if name]]
    if spec.chart_type=='bubble':measures.append(spec.x_column)
    if spec.chart_type == 'histogram':
        measures = [spec.x_column, *([spec.y_column] if spec.y_column else [])]
    for column in measures:
        if column and any(value is not None and (isinstance(value, bool) or not isinstance(value, (Real, Decimal)) or not math.isfinite(value))
                          for value in (row[column] for row in rows)):
            raise ValueError(f'{column} 必须是有限数值或空值，请在 SQL 中明确转换并处理失败记录。')

    if spec.chart_type=='text_table':
        from app.agent.nodes import _sanitize_records
        columns=spec.table_columns or list(rows[0])
        display=_sanitize_records([{name:row[name] for name in columns} for row in rows])
        return {'data':[{'type':'table','header':{'values':columns},'cells':{'values':[[row[name] for row in display] for name in columns]},
            'meta':{'source_columns':columns,'measure_columns':columns}}],'layout':{'title':spec.title}}
    if spec.chart_type in {'bubble','heatmap_marks'} and not spec.size_column:
        raise ValueError('气泡面积需要真实size_column字段，不能根据行号或猜测设置大小。')
    if spec.size_column and any(row[spec.size_column] is None or row[spec.size_column]<0 for row in rows):
        raise ValueError('面积字段必须非负且已知；未知不能当零。')

    if spec.row_filter and spec.row_limit is None:
        raise ValueError('排名筛选必须指定排名数量，不能静默改变完整图表的范围。')
    if spec.row_limit is not None:
        rank = spec.sort_column or (spec.y_column if not spec.y_columns else None)
        if spec.chart_type != 'bar' or spec.series_column or not rank or rank not in available:
            raise ValueError('柱图排名需要明确的数值排名字段；多系列请使用宽表并指定共同排名字段。')
        if spec.row_filter:
            rows = [row for row in rows if all(row[column] == value for column,value in spec.row_filter.items())]
            if not rows:
                raise ValueError('排名筛选没有匹配的类别，请核对字段和值。')
        if 'row_type' in available and len({row['row_type'] for row in rows}) > 1:
            raise ValueError('排名包含不同粒度的行，请先区分真实类别与汇总行。')
        if any(row[rank] is None or isinstance(row[rank], bool) or not isinstance(row[rank], (Real, Decimal)) or not math.isfinite(row[rank]) for row in rows):
            raise ValueError('排名数值必须有限且已知，不能当零或静默排除后排名。')
        rows = sorted(rows, key=lambda row: abs(row[rank]) if spec.sort_absolute else row[rank], reverse=spec.sort_order == 'desc')[:spec.row_limit]

    if spec.series_column or spec.y_columns:
        if spec.chart_type=='box':raise ValueError('箱线图请先在SQL明确组合分组列，返回分组与全部原始观测，不使用宽表/额外系列隐式拆分。')
        if spec.chart_type not in {'line','area','bar','scatter','circle','bubble','box'} or (spec.series_column and spec.y_columns):
            raise ValueError('多系列仅支持折线、柱图和散点；分类系列与多数值列请选择一种。')
        traces, layout = [], {}
        groups = list(dict.fromkeys(row[spec.series_column] for row in rows)) if spec.series_column else [None]
        for group in groups:
            subset = [row for row in rows if row[spec.series_column] == group] if spec.series_column else rows
            if spec.chart_type in {'line','area','bar'} and len({row[spec.x_column] for row in subset}) != len(subset):
                raise ValueError('同一系列存在重复横轴值，请先在 SQL 按目标粒度汇总。')
            for metric in spec.y_columns or [spec.y_column]:
                single = build_plotly_spec(spec.model_copy(update={'series_column': None, 'y_columns': [], 'y_column': metric,
                    'row_limit':None,'sort_column':None,'row_filter':{}}), subset)
                trace = single['data'][0]
                trace['name'] = str(group) if spec.series_column and group is not None else '（空值）' if spec.series_column else metric
                if spec.series_column:trace['meta'].update(series_column=spec.series_column,series_value=group)
                traces.append(trace)
                layout = single['layout']
        if spec.chart_type == 'bar':
            layout['barmode'] = spec.bar_mode
        if spec.chart_type=='circle':layout.update(scattermode='group',scattergap=.3)
        units = [_column_unit(metric) for metric in spec.y_columns]
        if spec.chart_type=='area':
            if len(set(units))>1 or any(value is not None and value<0 for trace in traces for value in trace['y']):
                raise ValueError('堆叠面积必须为同一单位的非负可加总数值。')
            for trace in traces:trace.update(stackgroup='same-unit',fill='tonexty',connectgaps=False)
        if len(set(units)) > 1:
            from app.insights import METRIC_LABELS
            count = len(traces)
            horizontal = spec.chart_type == 'bar' and spec.orientation == 'h'
            layout = {'title': spec.title, 'showlegend': False, 'barmode': 'group',
                      'meta': {'independent_units': True, 'unit_panels': count}, 'annotations': []}
            for index, (trace, metric, unit) in enumerate(zip(traces, spec.y_columns, units)):
                suffix = str(index + 1) if index else ''
                xref, yref = f'x{suffix}', f'y{suffix}'
                top = 1 - index / count
                trace.update(xaxis=xref, yaxis=yref)
                # A global scale cannot relabel mixed counts/amounts as percentages.
                scale = 'ratio' if unit == 'ratio' else 'percent' if unit == '%' else 'number' if unit else spec.value_scale
                trace['meta']['value_scale'] = scale
                canonical = re.sub(r'_(brl|usd|cny|eur|gbp)$', '', metric.lower())
                label = METRIC_LABELS.get(canonical, metric)
                layout[f'xaxis{suffix}'] = {'anchor': yref, 'title': (unit or metric) if horizontal else spec.x_label}
                layout[f'yaxis{suffix}'] = {'anchor': xref, 'domain': [top - 1 / count + .23 / count, top],
                                          'title': spec.x_label if horizontal else unit or metric}
                category_axis = layout[f'yaxis{suffix}' if horizontal else f'xaxis{suffix}']
                if index < count - 1:
                    category_axis['title'] = ''
                if index:
                    category_axis['matches'] = 'y' if horizontal else 'x'
                if scale == 'ratio':
                    layout[f'xaxis{suffix}' if horizontal else f'yaxis{suffix}']['tickformat'] = '.1%'
                elif scale == 'percent':
                    layout[f'xaxis{suffix}' if horizontal else f'yaxis{suffix}']['ticksuffix'] = '%'
                layout['annotations'].append({'text': f'{label}（{unit}）' if unit else label,
                    'xref': 'paper', 'yref': 'paper', 'x': 0, 'y': top, 'yshift': 10,
                    'xanchor': 'left', 'yanchor': 'bottom', 'showarrow': False})
        return {'data': traces, 'layout': layout}

    if spec.chart_type == 'waterfall':
        if not spec.measure_column or not spec.y_column or len(rows)<3:
            raise ValueError('瀑布图需要完整起点、贡献和终点，以及明确的步骤类型字段。')
        types = {'absolute':'absolute','start':'absolute','relative':'relative','change':'relative',
                 'contribution':'relative','state_contribution':'relative','category':'relative','other':'relative',
                 'total':'total','end':'total'}
        measures = [types.get(str(row[spec.measure_column]).lower()) for row in rows]
        if measures[0] != 'absolute' or measures[-1] != 'total' or any(value!='relative' for value in measures[1:-1]):
            raise ValueError('瀑布图步骤必须为一个起点、全部相对贡献和一个终点；不能绘制截断片段。')
        values = [row[spec.y_column] for row in rows]
        if any(value is None for value in values):
            raise ValueError('贡献金额存在未知值，不能核对加总或默认补零。')
        amounts = [Decimal(str(value)) for value in values]
        if abs(sum(amounts[:-1])-amounts[-1]) >= Decimal('.005'):
            raise ValueError('起点加全部贡献与终点加总不一致，未生成瀑布图。')
        return {'data':[{'type':'waterfall','x':[row[spec.x_column] for row in rows], 'y':values,
                         'measure':measures,'meta':{'x_column':spec.x_column,'y_column':spec.y_column},'connector':{'line':{'color':'#9aafb6'}},
                         'increasing':{'marker':{'color':'#168b82'}},'decreasing':{'marker':{'color':'#c87564'}},
                         'totals':{'marker':{'color':'#528aca'}}}],
                'layout':{'title':spec.title,'xaxis':{'title':spec.x_label},'yaxis':{'title':spec.y_label}}}

    if spec.chart_type == 'funnel':
        values=[row[spec.y_column] for row in rows] if spec.y_column else []
        stages=[row[spec.x_column] for row in rows]
        if len(values)<2 or any(value is None or value<0 for value in values):
            raise ValueError('漏斗至少需要两个阶段和非负、非空的到达数量。')
        if values[0]==0:
            raise ValueError('首阶段没有到达样本，无法计算转化率；请查看结果表或调整查询范围。')
        if len(set(stages))!=len(stages) or any(later>before for before,later in zip(values,values[1:])):
            raise ValueError('漏斗阶段不能重复或递增，请核对阶段顺序、同一分母及逐级包含关系。')
        return {'data':[{'type':'funnel','orientation':'h','y':stages,'x':values,'textinfo':'value+percent initial',
                         'textposition':'inside','meta':{'x_column':spec.x_column,'y_column':spec.y_column},'connector':{'line':{'color':'#d6e4e7','width':1}}}],
                'layout':{'title':spec.title,'xaxis':{'title':spec.y_label},'yaxis':{'title':spec.x_label}}}

    if spec.chart_type in {'heatmap','highlight_table'}:
        if not spec.y_column or not spec.z_column:
            raise ValueError('热力图需要两个维度和一个数值字段。')
        cells = {}
        for row in rows:
            key = (row[spec.x_column], row[spec.y_column])
            if key in cells:
                raise ValueError('热力图网格重复，请先在 SQL 按两个维度汇总。')
            cells[key] = row[spec.z_column]
        xs = list(dict.fromkeys(row[spec.x_column] for row in rows))
        ys = list(dict.fromkeys(row[spec.y_column] for row in rows))
        return {'data': [{'type': 'heatmap', 'x': xs, 'y': ys,
                          'meta': {'value_scale': spec.value_scale, 'metric_column': spec.z_column, 'x_column': spec.x_column, 'y_column': spec.y_column},
                          'z': [[cells.get((x, y)) for x in xs] for y in ys],
                          'colorscale': [[0, '#eef7f5'], [0.5, '#7db9ad'], [1, '#126a62']],
                          'hoverongaps': False, 'colorbar': {'title': {'text': spec.z_column}}}],
                'layout': {'title': spec.title, 'xaxis': {'title': spec.x_label}, 'yaxis': {'title': spec.y_label}}}

    if spec.chart_type != 'histogram' and spec.y_column is None:
        raise ValueError('该图表需要数值字段。')

    x_values = [row[spec.x_column] for row in rows]
    y_values = (
        [row[spec.y_column] for row in rows]
        if spec.y_column is not None
        else None
    )

    if spec.chart_type in {'line','area'}:
        trace = {
            "type": "scatter",
            "mode": "lines+markers",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
        if spec.chart_type=='area':trace.update(fill='tozeroy',connectgaps=False)
    elif spec.chart_type == "bar":
        trace = {
            "type": "bar",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
    elif spec.chart_type == "histogram":
        trace = {
            "type": "histogram",
            "x": x_values,
            "name": spec.x_label,
        }
        if y_values is not None:
            trace.update(y=y_values, histfunc="sum")
    elif spec.chart_type in {'scatter','circle','bubble','heatmap_marks'}:
        trace = {
            "type": "scatter",
            "mode": "markers",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
        if spec.size_column:
            sizes=[row[spec.size_column] for row in rows]
            trace['marker']={'size':sizes,'sizemode':'area','sizeref':2*max(sizes)/2500 if max(sizes) else 1,'sizemin':0}
        if spec.color_column:
            trace.setdefault('marker',{}).update(color=[row[spec.color_column] for row in rows],colorscale='Blues',showscale=True,colorbar={'title':spec.color_column})
        if spec.size_column or spec.color_column:
            encoded=[name for name in (spec.size_column,spec.color_column) if name]
            trace['customdata']=[[row[name] for name in encoded] for row in rows]
            trace['hovertemplate']='%{x} · %{y}<br>'+'<br>'.join(name+'：%{customdata['+str(i)+']}' for i,name in enumerate(encoded))+'<extra></extra>'
    elif spec.chart_type=='box':
        trace={'type':'box','x':x_values,'y':y_values,'name':spec.y_label,'quartilemethod':'linear','boxpoints':'outliers','jitter':0}
    elif spec.chart_type == "pie":
        trace = {
            "type": "pie",
            "labels": x_values,
            "values": y_values,
        }
    else:  # pragma: no cover — Literal already prevents this
        raise ValueError(f"Unsupported chart type: {spec.chart_type}")

    layout: dict[str, Any] = {"title": spec.title}
    if spec.chart_type=='box':
        layout['showlegend']=False
        missing=sum(row[spec.y_column] is None for row in rows)
        layout['meta']={'chart_note':f'箱线使用完整{len(rows)}条输入；其中{missing}条空值不参与分位数。四分位按Plotly linear插值，须为1.5×IQR范围内最远观测，须外显示离群值；不证明正态性。'}
    elif spec.size_column:
        layout['meta']={'chart_note':f'圆的面积表示 {spec.size_column}'+(f'；颜色表示 {spec.color_column}' if spec.color_column else '')+'。零面积保持为零，不补造大小。'}
    if spec.chart_type != "pie":
        layout["xaxis"] = {"title": spec.x_label}
        layout["yaxis"] = {"title": spec.y_label}
    if spec.chart_type=='bar' and spec.orientation=='h':
        trace.update(orientation='h',x=y_values,y=x_values)
        layout['xaxis'],layout['yaxis']=layout['yaxis'],layout['xaxis']

    if spec.chart_type=='box':
        whiskers=[];statistics=[];known=[value for value in y_values if value is not None]
        for group in dict.fromkeys(x_values):
            values=sorted(value for category,value in zip(x_values,y_values) if category==group and value is not None)
            if not values:continue
            def percentile(fraction):
                return box_percentile(values, fraction)
            q1,median,q3=percentile(.25),percentile(.5),percentile(.75);iqr=q3-q1
            inside=[float(value) for value in values if q1-1.5*iqr<=value<=q3+1.5*iqr]
            whiskers.extend([min(inside),max(inside)])
            stat={'group':group,'n':len(values),'q1':q1,'median':median,'q3':q3,'iqr':iqr,
                'min':float(values[0]),'max':float(values[-1]),
                'whisker_low':min(inside),'whisker_high':max(inside),'outliers':[float(value) for value in values if value<min(inside) or value>max(inside)]}
            statistics.append(stat)
            supplied={'q1','median','q3','iqr','whisker_low','whisker_high'}
            if supplied<=available and any(row[name] is None or not math.isclose(float(row[name]),stat[name],rel_tol=1e-10,abs_tol=1e-12)
                    for row in rows if row[spec.x_column]==group for name in supplied):
                raise ValueError('查询统计与箱线图使用的四分位/须规则不一致，未生成误导性图表；请统一算法，或仅返回原始观测由图层统一计算。')
        layout['meta']['box_statistics']=statistics
        if known and whiskers:
            full_low,full_high=float(min(known)),float(max(known));core_low,core_high=min(whiskers),max(whiskers)
            full_span=full_high-full_low;core_span=core_high-core_low
            # A separate full-range view keeps outliers visible while the main boxes remain readable.
            if full_span>0 and (core_span==0 or full_span>4*core_span):
                pad=core_span*.12 if core_span else max(abs(core_low)*.08,full_span*.005)
                raw_step=max(core_span,pad)/5;power=10**math.floor(math.log10(raw_step))
                step=next(factor*power for factor in (1,2,5,10) if factor*power>=raw_step)
                focus_range=[math.floor((core_low-pad)/step)*step,math.ceil((core_high+pad)/step)*step]
                layout['xaxis'].update(domain=[0,.72])
                layout['yaxis'].update(range=focus_range,autorange=False)
                layout.update(xaxis2={'domain':[.84,1],'anchor':'y2','title':''},
                    yaxis2={'anchor':'x2','side':'right','range':[full_low-full_span*.05,full_high+full_span*.12],'autorange':False,'title':spec.y_label},
                    annotations=[{'text':f'主体分布 · {focus_range[0]:g}—{focus_range[1]:g}','xref':'paper','yref':'paper','x':.36,'y':1.025,'xanchor':'center','yanchor':'bottom','showarrow':False},
                                 {'text':'全量范围','xref':'paper','yref':'paper','x':.92,'y':1.025,'xanchor':'center','yanchor':'bottom','showarrow':False}])
                extreme=full_high if full_high>core_high else full_low
                group=next(category for category,value in zip(x_values,y_values) if value is not None and float(value)==extreme)
                layout['annotations'].append({'text':f'{group}：{extreme:g}','xref':'x2','yref':'y2','x':group,'y':extreme,'yshift':13,'showarrow':False,'font':{'size':12}})
                layout['meta']['chart_note']='主图放大主体分布，右侧保留全量与离群值；两侧纵轴范围不同。'+layout['meta']['chart_note']
                overview={**trace,'xaxis':'x2','yaxis':'y2','name':'全量范围','showlegend':False}
                # Metadata is attached below to both views of the same source.
                layout['meta']['box_focus']=True
            else:overview=None
        else:overview=None

    trace['meta'] = {'value_scale': spec.value_scale, 'metric_column': spec.y_column or spec.x_column,
                     'x_column': spec.x_column, 'y_column': spec.y_column}
    if spec.chart_type in {'box','circle','bubble','heatmap_marks','area'}:
        trace['meta'].update(source_columns=list(dict.fromkeys([name for name in (spec.x_column,spec.y_column,spec.size_column,spec.color_column) if name])),
            measure_columns=list(dict.fromkeys([name for name in ([spec.x_column] if spec.chart_type=='bubble' else [])+([] if spec.chart_type=='heatmap_marks' else [spec.y_column])+[spec.size_column,spec.color_column] if name])),
            size_column=spec.size_column,color_column=spec.color_column)
    if spec.axis_mode=='discrete':
        layout.setdefault('yaxis' if spec.chart_type=='bar' and spec.orientation=='h' else 'xaxis',{}).update(type='category',categoryorder='array',categoryarray=list(dict.fromkeys(x_values)))
    elif spec.axis_mode=='continuous':
        numeric=all(isinstance(value,(Real,Decimal)) and not isinstance(value,bool) for value in x_values)
        if not numeric and not all(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?(?:[T ].*)?',value) for value in x_values):
            raise ValueError('连续横轴需要真实数值或ISO日期，不能把类别名称冒充连续时间。')
        layout.setdefault('yaxis' if spec.chart_type=='bar' and spec.orientation=='h' else 'xaxis',{})['type']='linear' if numeric else 'date'
    if spec.chart_type not in {'pie', 'histogram'}:
        axis = layout['xaxis' if spec.chart_type == 'bar' and spec.orientation == 'h' else 'yaxis']
        if spec.value_scale == 'ratio':
            axis['tickformat'] = '.1%'
        elif spec.value_scale == 'percent':
            axis['ticksuffix'] = '%'
    return {"data": [trace,*([{**overview,'meta':dict(trace['meta'])}] if spec.chart_type=='box' and overview else [])], "layout": layout}
