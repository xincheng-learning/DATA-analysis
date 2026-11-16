"""Original development chart gold: frozen v1 inputs plus structural challenges.

Numerical teachers use standard Python only. No product code, model or plotting.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import math

from evals.chart24_reference import cases as basic_cases

VERSION = '2.0.0'
REVIEW = {
    'pie': ['同口径非负权重，扇区角度与原始占比一致', '零值保持零；完整类别和值在图例中，不伪造面积'],
    'line': ['按真实日期间隔显示；日/小时不能都标成月份', '未知处断开；孤立有效点必须可见', '系列颜色/线型稳定，原始值可追溯'],
    'area': ['只在各系列均已知期间堆叠；不跨未知区间涂色', '显示孤立有效期的累计高度并说明原值', '各层可加总，零值不能与未知混淆'],
    'bar': ['零基线或明确的正负轴，不能裁掉实际值', '每一类别/系列都能追溯，不静默截取Top N', '长标签、负值和零仍可读'],
    'circle': ['固定大小只比较位置，不误编码成面积', '并排系列有明确图例，不连线'],
    'histogram': ['每个非空原始观测恰好进入一个分箱，重复与零保留', '明确分箱边界、右端点规则、计数而非累计或密度', '偏态尾部仍可见，放大视图不得代替全量'],
    'box': ['linear四分位，1.5IQR范围内实际观测作须', '不等样本量单列n，全部离群值仍可见', '共同主体尺度与全量视图明确区分'],
    'treemap': ['按完整路径区分同名节点；父节点等于其子项之和', '所有叶子都有正确面积，不凭标签元数据冒充可见编码', '小单元不塞重叠标签，完整路径与值可查'],
    'sunburst': ['每层角度合计360度，父子权重守恒', '深层同名类别按路径区分，不丢叶子', '核心总量、层级顺序与颜色含义清晰'],
    'sankey': ['来源和去向按角色分开，同名不能合并成自环', '每条边及两端节点流量守恒，宽度同尺度', '多边完整保留；不把零伪造为正流量'],
    'highlight_table': ['完整二维网格，零与未知区分', '正负值使用以零为中心的发散色标', '两轴分类和颜色单位明确'],
    'heatmap_marks': ['大小与颜色各自绑定真实字段', '未知颜色不能变成零色，已有大小仍能辨认', '图例明确面积而非直径'],
    'dual_line': ['左右轴、各自单位及系列绑定明确', '百分比按原比例显示，不乘两次100', '不因线条交叉或走势相似推断关系'],
    'combo': ['柱和线分属明确单位与独立左右轴', '比例保留正确换算，缺失处不跨接', '不暗示两个尺度的高度可以直接比较'],
    'gantt': ['跨度等于实际结束减开始，不偷偷含首尾加一天', '同日零跨度任务也有明确标记', '每个任务ID、起止时间与长标签可追溯'],
    'bullet': ['实际值与明确目标分别编码', '不猜测阈值或把目标画成趋势连线'],
    'packed_bubble': ['圆面积按权重比例，不用直径比冒充面积比', '所有正值圆可见且不重叠，零值不补造面积', '位置无地理/时间含义，完整值可查'],
    'symbol_map': ['只用给定经纬度，不在线猜位置', '大小编码面积，标签和位置对应'],
    'filled_map': ['只用给定示意边界，说明非真实行政区', '边界、区域标识、颜色值绑定，零仍为有效值'],
    'text_table': ['全部行列与空值/零如实显示', '原始值不被格式化为另一个数'],
    'scatter': ['位置由两真实数值字段决定，未知不补造位置', '重叠可用透明度，但不能删观测'],
}


def cases():
    items = basic_cases()
    for c in items:
        c.update(tier='basic', challenge='保留v1输入；修订教师完整性及排版', unit='CNY' if 'amount_cny' in c['rows'][0] else '原字段单位',
                 x='month' if 'month' in c['rows'][0] else 'category', ys=['amount_cny'], dimensions=[])
        if c['kind'] == 'area' or c['id'] in {'SM08', 'SM09', 'SM11'}: c['ys'] = ['amount_cny', 'other_cny']
        if c['kind'] in {'dual_line', 'combo'}: c['ys'] = ['amount_cny', 'conversion_ratio']
        if c['kind'] == 'scatter': c.update(x='x_value', ys=['y_value'])
        if c['kind'] == 'box': c.update(x='group')
        if c['kind'] == 'treemap': c.update(dimensions=['region', 'category'])
    start = date(2026, 1, 1)
    daily = [{'time': (start + timedelta(days=d)).isoformat(), 'series': f'渠道{s+1}',
              'value': None if (d + s * 11) % 41 in (9, 10, 11) else 100 + s * 43 + (d * 7 + s * 13) % 97}
             for s in range(6) for d in range(180)]
    hourly = [{'time': (datetime(2026, 5, 1) + timedelta(hours=h)).isoformat(), 'series': f'传感器{s+1}',
               'value': None if h in (31, 32, 70) else round(30 + 9 * math.sin(h / 5 + s) + s * 16, 2)}
              for s in range(3) for h in range(72)]
    rank = [{'category': f'区域{i//10+1} · 企业客户成功与服务事业部 {i+1:02}', 'amount_cny': (i * 239) % 14000 - 3200} for i in range(80)]
    rank[10]['amount_cny'] = 0
    boxes = [{'group': f'组{g+1:02}', 'amount_cny': None if i == 3 else (2000 + g * 200 if i == 4 else (i % 11) ** 2 + g * 7)}
             for g in range(12) for i in range(8 + g * 7)]
    hist = [{'amount_cny': None if i % 97 == 0 else 3000 + i if i % 113 == 0 else (i % 29) ** 2} for i in range(1200)]
    matrix = [{'category': f'业务线{g+1:02}', 'period': f'W{w+1:02}', 'value': None if (g * 7 + w) % 31 == 0 else ((g * 13 + w * 7) % 101 - 50)}
              for g in range(18) for w in range(24)]
    hierarchy = [{'region': f'区域{r+1}', 'channel': f'渠道{ch+1}', 'category': ['标准', '专属'][ca],
                  'product': f'产品{p+1:02}', 'amount_cny': 10 + (r * 47 + ch * 29 + ca * 17 + p * 13) % 89}
                 for r in range(4) for ch in range(5) for ca in range(2) for p in range(5)]
    rings = [{'region': f'区域{r+1}', 'channel': f'渠道{ch+1}', 'category': f'产品{ca+1}',
              'amount_cny': 20 + (r * 31 + ch * 17 + ca * 7) % 80}
             for r in range(4) for ch in range(5) for ca in range(4)]
    flows = [{'source': '线上' if s == 0 else f'获客渠道{s+1:02}', 'target': '线上' if t == 0 else f'承接业务{t+1:02}',
              'amount': 0 if (s + t) % 13 == 0 else 10 + (s * 37 + t * 23) % 101}
             for s in range(12) for t in range(8)]
    dual = [{'month': (start + timedelta(days=d)).isoformat(), 'amount_cny': 80000 + d * 317 + (d % 9) * 1200,
             'conversion_ratio': None if d in (35, 36, 70) else round(.002 + ((d * 7) % 90) / 10000, 4)} for d in range(90)]
    tasks = [{'task': f'项目{i//8+1} · 数据质量治理任务{i+1:02}', 'start': (start + timedelta(days=i * 3)).isoformat(),
              'end': (start + timedelta(days=i * 3 + (0 if i % 17 == 0 else 2 + i % 19))).isoformat()} for i in range(48)]
    bubbles = [{'group': f'客户群{i+1:02}', 'amount': 0 if i % 23 == 0 else (4 + i * 13 % 40) ** 2} for i in range(75)]
    extra = [
        ('180日六渠道 · 不对称缺失', 'line', daily, dict(x='time', ys=['value'], series='series'), '密集日轴、6系列、不同缺失区间'),
        ('72小时三传感器 · 孤立末段', 'line', hourly, dict(x='time', ys=['value'], series='series'), '跨日小时轴、缺口、孤立有效点'),
        ('80个长标签 · 正负与零', 'bar', rank, dict(x='category', ys=['amount_cny']), '高基数长标签、负值、零、分栏完整排序'),
        ('12组偏态 · 不等样本量', 'box', boxes, dict(x='group', ys=['amount_cny']), '12组、不同n、缺失、长尾离群值、主体与全量'),
        ('1200个原始观测 · 偏态尾部', 'histogram', hist, dict(ys=['amount_cny'], edges=list(range(0, 5001, 100))), '千级原始观测、重复/零/空值、完整尾部与主体放大'),
        ('18业务线 × 24周 · 正负变化', 'highlight_table', matrix, dict(x='period', y='category', ys=['value']), '432格、发散色标、未知格、完整分类'),
        ('四层业务层级 · 200个叶子', 'treemap', hierarchy, dict(dimensions=['region', 'channel', 'category', 'product'], ys=['amount_cny']), '4层200叶、跨父节点重复名称、面积守恒'),
        ('三级旭日 · 80个叶子', 'sunburst', rings, dict(dimensions=['region', 'channel', 'category'], ys=['amount_cny']), '3层80叶、角度守恒、相同子类不同路径'),
        ('来源到承接 · 96条流量记录', 'sankey', flows, dict(dimensions=['source', 'target'], ys=['amount']), '20个角色节点、96条记录、零流量、两端同名'),
        ('90日双轴 · 金额与转化率', 'dual_line', dual, dict(x='month', ys=['amount_cny', 'conversion_ratio']), '数量级与单位不同、比例缺口、日轴'),
        ('48项跨月任务 · 含同日任务', 'gantt', tasks, dict(x='task', ys=[]), '长标签、48任务、多月跨度、零持续时长'),
        ('75类气泡 · 面积与零', 'packed_bubble', bubbles, dict(x='group', ys=['amount']), '高基数、面积尺度差异、零面积、不重叠'),
    ]
    for i, (name, kind, rows, binding, challenge) in enumerate(extra, 1):
        items.append(dict(id=f'CX{i:02}', name=name, kind=kind, rows=rows, tier='complex', unit='CNY',
                          question=f'用{name}的数据展示{challenge}；保留完整输入，未知不补零，给出方法和编码说明。',
                          challenge=challenge, **binding))
    items[25]['unit'] = '信号值'
    items[32]['unit'] = '流量权重'
    for c in items:
        c['review'] = REVIEW[c['kind']]
        c['scope'] = 'public development teacher; not blind or a product/model pass'
    return items


def quantile(values, probability):
    values = sorted(values)
    at = (len(values) - 1) * probability
    lower = int(at)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (at - lower)


def oracle(c):
    rows, kind = c['rows'], c['kind']
    result = dict(row_count=len(rows), input_sha256=hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  missing_cells=sum(v is None for r in rows for v in r.values()))
    if kind == 'box':
        groups = []
        for group in dict.fromkeys(r['group'] for r in rows):
            raw = [r['amount_cny'] for r in rows if r['group'] == group]
            v = sorted(x for x in raw if x is not None)
            q1, median, q3 = [quantile(v, p) for p in [.25, .5, .75]]
            low = min(x for x in v if x >= q1 - 1.5 * (q3 - q1))
            high = max(x for x in v if x <= q3 + 1.5 * (q3 - q1))
            groups.append(dict(group=group, n=len(v), missing=len(raw)-len(v), q1=q1, median=median, q3=q3,
                               low=low, high=high, outliers=[x for x in v if x < low or x > high]))
        result['groups'] = groups
    if kind == 'histogram':
        edges = c.get('edges', [0, 2, 4, 6]); counts = [0] * (len(edges) - 1); assignments = []
        for row in rows:
            v = row['amount_cny']; index = None
            if v is not None:
                index = next(i for i, (a, b) in enumerate(zip(edges, edges[1:])) if a <= v < b or i == len(counts)-1 and v == b)
                counts[index] += 1
            assignments.append(index)
        result.update(edges=edges, counts=counts, bin_by_row=assignments, valid_n=sum(counts), missing_n=assignments.count(None))
    if kind in {'treemap', 'sunburst'}:
        dimensions = c['dimensions']; measure = c['ys'][0]; nodes = Counter(); leaves = []
        for row in rows:
            path = tuple(row[d] for d in dimensions); value = row[measure]
            leaves.append(dict(path=list(path), value=value))
            for depth in range(len(path)+1): nodes[path[:depth]] += value
        result.update(total=nodes[()], leaves=leaves, nodes=[dict(path=list(path), value=value) for path, value in nodes.items()])
    if kind == 'sankey':
        sources, targets = Counter(), Counter()
        for r in rows: sources[r['source']] += r['amount']; targets[r['target']] += r['amount']
        result.update(sources=dict(sources), targets=dict(targets), total=sum(sources.values()), positive_edges=sum(r['amount'] > 0 for r in rows))
    if kind == 'gantt':
        result['days'] = [(datetime.fromisoformat(r['end'])-datetime.fromisoformat(r['start'])).total_seconds()/86400 for r in rows]
    return result
