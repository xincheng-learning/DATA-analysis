"""Independent teacher figures and checks of the actual Matplotlib artists.

No product builder is imported. SVG/PNG are examples of valid encodings, not
pixel-identical targets for an agent. Full values are shipped beside every plot.
"""
from datetime import datetime
import math
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import dates, font_manager, colors
from matplotlib.patches import Circle, Rectangle, Wedge, PathPatch, Polygon
from matplotlib.path import Path as MplPath
import numpy as np

from evals.chart_gold_v2 import oracle

PALETTE = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#56B4E9', '#9B7E35']
INK, MUTED = '#182F42', '#566B7B'


def configure():
    available = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ['Microsoft YaHei', 'Noto Sans CJK SC', 'Source Han Sans SC', 'SimHei'] if f in available), 'DejaVu Sans')
    plt.rcParams.update({'font.family': font, 'font.size': 10, 'axes.unicode_minus': False,
                         'axes.labelcolor': MUTED, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.edgecolor': '#CBD5DF', 'svg.fonttype': 'none', 'svg.hashsalt': 'chart-gold-v2',
                         'figure.facecolor': '#FCFDFE', 'axes.facecolor': '#FCFDFE', 'savefig.facecolor': '#FCFDFE'})
    return font


def series(c):
    rows = c['rows']
    if c.get('series'):
        return [(name, [r for r in rows if r[c['series']] == name], c['ys'][0]) for name in dict.fromkeys(r[c['series']] for r in rows)]
    return [(name, rows, name) for name in c['ys']]


def coordinates(c, rows):
    raw = [r[c['x']] for r in rows]
    temporal = c.get('axis_mode') != 'discrete' and c['id'] not in {'SM13', 'SM16'} and all(isinstance(v, str) and v[:4].isdigit() and '-' in v for v in raw)
    if temporal: return [dates.date2num(datetime.fromisoformat(v+'-01' if len(v)==7 else v)) for v in raw], True
    return list(range(len(raw))), False


def time_axis(ax, x, raw, temporal):
    if temporal:
        if any('T' in str(v) for v in raw):
            ax.xaxis.set_major_locator(dates.HourLocator(interval=12)); ax.xaxis.set_major_formatter(dates.DateFormatter('%m-%d\n%H:%M'))
        else:
            locator = dates.AutoDateLocator(minticks=3, maxticks=7)
            ax.xaxis.set_major_locator(locator); ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator))
    else: ax.set_xticks(x, raw)
    ax.tick_params(axis='both', labelsize=9)


def plot_series(ax, c, name, rows, field, color, *, values=None, index=0):
    x, temporal = coordinates(c, rows)
    y = [np.nan if r[field] is None else r[field] for r in rows] if values is None else values
    line, = ax.plot(x, y, color=color, lw=1.5, marker=['o', 's', '^'][index % 3], markersize=2.7,
                    label=name, gid='series:' + name, linestyle=['-', '--', '-.'][index % 3])
    time_axis(ax, x, [r[c['x']] for r in rows], temporal)
    return line


def render_case(c):
    configure()
    rows, kind, ident = c['rows'], c['kind'], c['id']; expected = oracle(c)
    height = 7.4
    if ident == 'CX03': height = 23
    if kind == 'gantt' and len(rows) > 10: height = 17
    if kind == 'sankey': height = 10
    fig, ax = plt.subplots(figsize=(13.5, height))
    fig.subplots_adjust(left=.09, right=.94, top=.81 if height < 10 else .89, bottom=.18 if height < 10 else .08, hspace=.55, wspace=.28)
    fig.text(.065, .97, f'{ident}  {c["name"]}', fontsize=20, fontweight='bold', color=INK, va='top')
    fig.text(.065, .914 if height < 10 else .94, textwrap.fill(c['challenge'], 65), fontsize=10.5, color=MUTED, va='top')
    method = '方法  ·  ' + c['review'][0]
    footer = f'独立教师 v2  |  输入 {len(rows):,} 行 · 未知单元格 {expected["missing_cells"]}  |  完整值与逐题标准见配套CSV/JSON'
    fig.text(.065, .055 if height < 10 else .035, textwrap.fill(method, 72), fontsize=9, color=MUTED, va='bottom')
    fig.text(.065, .021 if height < 10 else .018, footer, fontsize=8.5, color=MUTED)
    if kind in {'line', 'area', 'circle'}:
        entries = series(c)
        if c.get('series'):
            fig.delaxes(ax)
            axes = fig.subplots(3, 2, sharex=True, sharey=True).flat if len(entries) == 6 else fig.subplots(3, 1, sharex=True).flat
        else: axes = [ax] * len(entries)
        previous = np.zeros(len(rows))
        for i, ((name, selected, field), target) in enumerate(zip(entries, axes)):
            color = PALETTE[i % len(PALETTE)]
            if kind == 'area':
                raw = np.array([np.nan if r[field] is None else r[field] for r in selected])
                complete = np.array([all(r[f] is not None for f in c['ys']) for r in selected])
                top = previous + raw; x, temporal = coordinates(c, selected)
                target.fill_between(x, previous, top, where=complete, color=color, alpha=.24, interpolate=False)
                visible = np.where(complete, top, np.nan)
                plot_series(target, c, name, selected, field, color, values=visible, index=i)
                previous = top
                for j in range(len(x)):
                    if complete[j] and (j == 0 or not complete[j-1]) and (j == len(x)-1 or not complete[j+1]):
                        target.annotate(f'{field} 原值 {selected[j][field]:g}\n累计 {top[j]:g}', (x[j], top[j]),
                                        xytext=(-12, 12 + i * 18), textcoords='offset points', ha='right', fontsize=8, color=color)
            else:
                line = plot_series(target, c, name, selected, field, color, index=i)
                if kind == 'circle':
                    line.set_linestyle('None'); line.set_markersize(7)
                    if ident == 'SM11': line.set_xdata(np.asarray(line.get_xdata()) + (i-.5)*.18)
            target.set_ylabel('数值' if ident == 'CX02' else '金额 / CNY')
            if c.get('series'): target.set_title(name, loc='left', fontsize=11, color=color, pad=8)
        if not c.get('series') and len(entries) > 1: ax.legend(frameon=False, loc='upper left')
    elif kind in {'dual_line', 'combo'}:
        right = ax.twinx()
        x, temporal = coordinates(c, rows)
        if kind == 'combo':
            bars = ax.bar(x, [r['amount_cny'] for r in rows], width=.65, color=PALETTE[0], alpha=.75)
            for i, p in enumerate(bars): p.set_gid(f'bar:amount_cny:{i}')
        else: plot_series(ax, c, 'amount_cny', rows, 'amount_cny', PALETTE[0])
        plot_series(right, c, 'conversion_ratio', rows, 'conversion_ratio', PALETTE[1], index=1)
        ax.set_ylabel('金额 / CNY', color=PALETTE[0]); right.set_ylabel('转化率 / %', color=PALETTE[1])
        right.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
        time_axis(ax, x, [r[c['x']] for r in rows], temporal)
        right.grid(False)
        ax.set_title('左右尺度独立，不能凭交叉或高度推断关系', loc='left', fontsize=10, color=MUTED)
    elif kind == 'bar':
        order = sorted(range(len(rows)), key=lambda i: rows[i]['amount_cny'], reverse=True) if ident == 'CX03' else list(range(len(rows)))
        horizontal = ident in {'SM07', 'CX03'}
        if ident == 'CX03': fig.subplots_adjust(left=.34)
        base = np.zeros(len(rows))
        for s, field in enumerate(c['ys']):
            v = [rows[i][field] for i in order]; x = np.arange(len(rows))
            offset = (s - .5) * .34 if ident == 'SM09' else 0
            if horizontal:
                bars = ax.barh(x, v, color=[PALETTE[0] if v0 >= 0 else PALETTE[1] for v0 in v], height=.62)
                ax.set_yticks(x, [rows[i]['category'] for i in order]); ax.invert_yaxis(); ax.set_xlabel('金额 / CNY'); ax.axvline(0, color=INK, lw=.8)
            else:
                bars = ax.bar(x + offset, v, width=.34 if ident == 'SM09' else .6,
                              bottom=base if ident == 'SM08' else None, color=PALETTE[s], label=field)
                ax.set_xticks(x, [rows[i]['category'] for i in order]); ax.set_ylabel('金额 / CNY')
            for i, p in zip(order, bars): p.set_gid(f'bar:{field}:{i}')
            if ident == 'SM08': base += np.array(v)
        if len(c['ys']) > 1: ax.legend(frameon=False)
    elif kind == 'histogram':
        ax.bar(expected['edges'][:-1], expected['counts'], width=np.diff(expected['edges']), align='edge', color=PALETTE[0], edgecolor='white')
        for i, p in enumerate(ax.patches): p.set_gid(f'bin:{i}')
        ax.set(xlabel='金额 / CNY', ylabel='原始观测数（非累计）')
        if ident == 'CX05':
            inset = ax.inset_axes([.49, .42, .47, .5]); inset.bar(range(9), expected['counts'][:9], color=PALETTE[0])
            inset.set_xticks(range(9), [f'{a:g}—{b:g}' for a, b in zip(expected['edges'][:9], expected['edges'][1:10])], rotation=40, fontsize=7)
            inset.set_title('主体0—900 · 全量图保留尾部', fontsize=9)
        ax.set_title(f'有效 n={expected["valid_n"]} · 未知 {expected["missing_n"]}；左闭右开，最右箱含右端点', loc='left', color=MUTED, fontsize=9, pad=13)
    elif kind == 'box':
        fig.delaxes(ax); body, full = fig.subplots(1, 2, gridspec_kw={'width_ratios': [4, 1.4]})
        stats = [dict(label=g['group'], q1=g['q1'], med=g['median'], q3=g['q3'], whislo=g['low'], whishi=g['high'], fliers=g['outliers']) for g in expected['groups']]
        for panel, target in [('body', body), ('full', full)]:
            artists = target.bxp(stats, patch_artist=True, widths=.52, boxprops={'facecolor': '#C1DDEB', 'edgecolor': PALETTE[0]},
                                 medianprops={'color': INK, 'linewidth': 1.6}, flierprops={'marker': 'o', 'markersize': 3, 'markerfacecolor': PALETTE[1], 'markeredgecolor': PALETTE[1]})
            for i, group in enumerate(expected['groups']):
                artists['medians'][i].set_gid(f'median:{panel}:{i}'); artists['boxes'][i].set_gid(f'box:{panel}:{i}')
                artists['fliers'][i].set_gid(f'outliers:{panel}:{i}')
            target.set_ylabel('金额 / CNY'); target.tick_params(axis='x', labelrotation=45 if len(stats) > 4 else 0, labelsize=8)
        core_lo = min(g['low'] for g in expected['groups']); core_hi = max(g['high'] for g in expected['groups']); pad = max(1, (core_hi-core_lo)*.12)
        body.set_ylim(core_lo-pad, core_hi+pad)
        body.set_title('A  主体共同尺度 · 须内范围', loc='left', fontsize=11, color=INK)
        full.set_title('B  全量含全部离群值', loc='left', fontsize=10, color=INK)
        body.set_xticklabels([f'{g["group"]}\nn={g["n"]}' for g in expected['groups']])
    elif kind in {'heatmap_marks', 'highlight_table'}:
        xfield = c.get('x') if ident == 'CX06' else 'month'; yfield = c.get('y', 'region')
        xs = list(dict.fromkeys(r[xfield] for r in rows)); ys = list(dict.fromkeys(r[yfield] for r in rows)); field = 'value' if ident == 'CX06' else 'amount_cny'
        z = np.array([[next(r[field] for r in rows if r[xfield] == x and r[yfield] == y) for x in xs] for y in ys], dtype=float)
        if kind == 'highlight_table':
            image = ax.imshow(np.ma.masked_invalid(z), aspect='auto', cmap='RdBu_r' if ident == 'CX06' else 'Blues',
                              norm=colors.TwoSlopeNorm(0, vmin=-50, vmax=50) if ident == 'CX06' else None)
            image.set_gid('matrix')
            for y in range(len(ys)):
                for x in range(len(xs)):
                    if np.isnan(z[y, x]):
                        ax.add_patch(Rectangle((x-.5, y-.5), 1, 1, facecolor='#E6EAF0', hatch='///', edgecolor='#AAB6C1', lw=.3))
                    elif len(rows) < 20:
                        rgb=image.cmap(image.norm(z[y,x]))[:3]
                        text_color='white' if sum(a*b for a,b in zip(rgb,[.299,.587,.114]))<.5 else INK
                        ax.text(x, y, f'{z[y,x]:g}', ha='center', va='center', color=text_color)
        else:
            palette=plt.get_cmap('Blues').copy(); palette.set_bad('#D8DDE3')
            image = ax.scatter([xs.index(r[xfield]) for r in rows], [ys.index(r[yfield]) for r in rows], s=[r['orders']*28 for r in rows],
                               c=[np.nan if r[field] is None else r[field] for r in rows], cmap=palette, plotnonfinite=True, edgecolors='#61849A')
            image.set_gid('matrix-marks')
            for r in rows:
                if r[field] is None: ax.text(xs.index(r[xfield]), ys.index(r[yfield]), '未知', ha='center', va='center', fontsize=8, color=INK)
        fig.colorbar(image, ax=ax, shrink=.8, pad=.02, label='变化值 / CNY' if ident == 'CX06' else '金额 / CNY')
        ax.set_xticks(range(len(xs)), xs, rotation=45 if len(xs)>8 else 0, fontsize=8); ax.set_yticks(range(len(ys)), ys, fontsize=8)
        ax.set_title('斜线格为未知；零为真实观测' if ident == 'CX06' else '圆面积 = 订单数；灰色“未知” = 金额缺失' if kind=='heatmap_marks' else '未知与零独立表示', loc='left', fontsize=10, color=MUTED)
    elif kind in {'treemap', 'sunburst'}:
        ax.axis('off'); nodes = {tuple(n['path']): n['value'] for n in expected['nodes']}; total = expected['total']
        def children(prefix): return [p for p in nodes if len(p) == len(prefix)+1 and p[:-1] == prefix]
        def tile(prefix, box):
            x, y, w, h = box; offset = 0
            for j, path in enumerate(children(prefix)):
                fraction = nodes[path]/nodes[prefix]; depth = len(path); root = list(dict.fromkeys(r[c['dimensions'][0]] for r in rows)).index(path[0])
                b = (x + offset*w, y, fraction*w, h) if depth % 2 else (x, y + offset*h, w, fraction*h)
                offset += fraction
                if depth == len(c['dimensions']):
                    color = colors.to_rgba(PALETTE[root % len(PALETTE)], .52 + .42*(j+1)/max(1, len(children(prefix))))
                    p = Rectangle(b[:2], b[2], b[3], facecolor=color, edgecolor='white', lw=.9); p.set_gid('leaf:' + json_path(path)); ax.add_patch(p)
                    if b[2] > .06 and b[3] > .07: ax.text(b[0]+b[2]/2, b[1]+b[3]/2, f'{path[-1]}\n{nodes[path]:g}', ha='center', va='center', fontsize=7, color=INK)
                else:
                    tile(path, b)
                    ax.add_patch(Rectangle(b[:2], b[2], b[3], facecolor='none', edgecolor='#FCFDFE', lw=3 if depth==1 else 1.6))
                    if depth == 1: ax.text(b[0]+.008, b[1]+b[3]-.008, path[-1], va='top', fontsize=10, fontweight='bold', color=INK,
                                           bbox={'facecolor': '#FFFFFF', 'alpha': .88, 'edgecolor': 'none', 'pad': 2})
                    elif depth == 3 and len(c['dimensions']) == 4:
                        ax.text(b[0]+.002, b[1]+.003, f'{path[1]}·{path[2]}', va='bottom', fontsize=6.2, color=INK,
                                bbox={'facecolor': '#FFFFFF', 'alpha': .82, 'edgecolor': 'none', 'pad': .5})
        def ring(prefix, a, b):
            offset = a
            for path in children(prefix):
                end = offset + (b-a)*nodes[path]/nodes[prefix]; depth = len(path)
                root = list(dict.fromkeys(r[c['dimensions'][0]] for r in rows)).index(path[0]); color = colors.to_rgba(PALETTE[root % len(PALETTE)], .5 + .14*depth)
                p = Wedge((0, 0), depth+.65, offset, end, width=.86, facecolor=color, edgecolor='white', lw=1); p.set_gid('wedge:' + json_path(path)); ax.add_patch(p)
                if depth == 1 or end-offset > 10:
                    mid = math.radians((offset+end)/2); radius=depth+.2
                    ax.text(math.cos(mid)*radius, math.sin(mid)*radius, path[-1], ha='center', va='center', fontsize=8 if depth<3 else 6.5, color=INK)
                ring(path, offset, end); offset = end
        if kind == 'treemap': tile((), (0, 0, 1, 1)); ax.set(xlim=(0, 1), ylim=(0, 1))
        else:
            ring((), 90, 450); radius=len(c['dimensions'])+.75; ax.set(xlim=(-radius, radius), ylim=(-radius, radius), aspect='equal')
            ax.text(0, 0, f'总额\n{total:,.0f}', ha='center', va='center', fontsize=12, color=INK)
        ax.set_title(f'全部 {len(expected["leaves"])} 个叶子 · 合计 {total:,.0f} CNY；小标签详见完整路径表', loc='left', fontsize=10, color=MUTED)
    elif kind == 'sankey':
        ax.axis('off'); total=expected['total']; scale=.8/total; positions={}; gap=.012
        for role, totals, x in [('source', expected['sources'], .12), ('target', expected['targets'], .86)]:
            y=.98
            for j, (name, value) in enumerate(totals.items()):
                height=value*scale; y-=height; positions[role, name]=[x, y, height, 0]
                p=Rectangle((x, y), .018, height, facecolor=PALETTE[j % len(PALETTE)], edgecolor='white'); p.set_gid(f'node:{role}:{name}'); ax.add_patch(p)
                ax.text(x-.012 if role=='source' else x+.03, y+height/2, f'{name}  {value:,}', ha='right' if role=='source' else 'left', va='center', fontsize=8, color=INK); y-=gap
        for i, row in enumerate(rows):
            a=positions['source', row['source']]; b=positions['target', row['target']]; height=row['amount']*scale
            sy=a[1]+a[3]; ty=b[1]+b[3]; a[3]+=height; b[3]+=height; sx=a[0]+.018; tx=b[0]
            vertices=[(sx, sy), (.38, sy), (.62, ty), (tx, ty), (tx, ty+height), (.62, ty+height), (.38, sy+height), (sx, sy+height), (sx, sy)]
            p=PathPatch(MplPath(vertices, [1,4,4,4,2,4,4,4,79]), facecolor=PALETTE[list(expected['sources']).index(row['source']) % len(PALETTE)], alpha=.22, lw=0)
            p.set_gid(f'flow:{i}'); ax.add_patch(p)
        ax.set(xlim=(-.04, 1.07), ylim=(0, 1.03)); ax.set_title(f'来源角色 ≠ 去向角色 · {len(rows)}条记录 · 总流量 {total:,}', loc='left', fontsize=11, color=INK)
    elif kind == 'gantt':
        if len(rows)>10: fig.subplots_adjust(left=.32)
        starts=dates.date2num([datetime.fromisoformat(r['start']) for r in rows]); days=expected['days']
        bars=ax.barh(range(len(rows)), days, left=starts, height=.6, color=PALETTE[0])
        for i, p in enumerate(bars):
            p.set_gid(f'task:{i}')
            if days[i] == 0: ax.plot(starts[i], i, marker='D', color=PALETTE[1], markersize=4, gid=f'zero-task:{i}')
        ax.set_yticks(range(len(rows)), [r['task'] for r in rows], fontsize=8); ax.invert_yaxis()
        locator=dates.AutoDateLocator(maxticks=8); ax.xaxis.set_major_locator(locator); ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator)); ax.set_xlabel('实际开始—结束日期；菱形表示零跨度')
    elif kind == 'bullet':
        bars=ax.barh(range(len(rows)), [r['actual'] for r in rows], height=.4, color=PALETTE[0])
        for i, (p, r) in enumerate(zip(bars, rows)):
            p.set_gid(f'actual:{i}'); ax.plot([r['target']]*2, [i-.3, i+.3], color=PALETTE[1], lw=3, gid=f'target:{i}')
        ax.set_yticks(range(len(rows)), [r['group'] for r in rows]); ax.set_xlabel('实际值（横条） / 明确目标（竖线）'); ax.set_xlim(left=0)
    elif kind == 'packed_bubble':
        ax.axis('off'); max_radius=math.sqrt(max(r['amount'] for r in rows)); x=y=0.; shelf=0.
        for i, r in enumerate(rows):
            radius=math.sqrt(r['amount'])/max_radius; diameter=2*radius
            if x+diameter>12: x=0.; y+=shelf+.16; shelf=0.
            p=Circle((x+radius, y+radius), radius, facecolor=PALETTE[i % len(PALETTE)], alpha=.82, edgecolor='white', lw=1.2); p.set_gid(f'circle:{i}'); ax.add_patch(p)
            if radius>.27: ax.text(x+radius, y+radius, f'{i+1:02}\n{r["amount"]:g}', ha='center', va='center', fontsize=7, color='white')
            x+=diameter+.1; shelf=max(shelf, diameter)
        ax.set(xlim=(-.1, 12.1), ylim=(y+shelf+.12, -.12), aspect='equal')
        ax.set_title('独立排布允许不同；面积∝权重；编号对应完整类别表，零值不补造圆', loc='left', fontsize=10, color=MUTED)
    elif kind == 'scatter':
        valid=[r for r in rows if r['x_value'] is not None and r['y_value'] is not None]
        ax.scatter([r['x_value'] for r in valid], [r['y_value'] for r in valid], color=PALETTE[0], s=50, gid='scatter'); ax.set(xlabel='x_value', ylabel='y_value')
    elif kind == 'pie':
        wedges, _=ax.pie([r['amount_cny'] for r in rows], startangle=90, colors=PALETTE, wedgeprops={'edgecolor': '#FCFDFE', 'linewidth': 2})
        for i, p in enumerate(wedges): p.set_gid(f'slice:{i}')
        ax.legend(wedges, [f'{r["category"]} · {r["amount_cny"]} CNY' for r in rows], frameon=False, loc='center left', bbox_to_anchor=(1, .5))
    elif kind == 'text_table':
        ax.axis('off'); columns=list(rows[0]); table=ax.table(cellText=[['未知' if v is None else str(v) for v in r.values()] for r in rows], colLabels=columns, loc='center', cellLoc='left')
        table.set_gid('table'); table.scale(1, 2.5)
        for (r, col), cell in table.get_celld().items():
            cell.set_edgecolor('#E0E7ED'); cell.set_facecolor('#E8F2F8' if r==0 else '#FFFFFF' if r%2 else '#F2F6F9')
    elif kind == 'symbol_map':
        ax.scatter([r['longitude'] for r in rows], [r['latitude'] for r in rows], s=[r['amount']*75 for r in rows], color=PALETTE[0], alpha=.65, gid='map')
        for r in rows: ax.annotate(r['place'], (r['longitude'], r['latitude']), xytext=(12, 5), textcoords='offset points')
        ax.set(xlabel='经度', ylabel='纬度'); ax.margins(.2)
    elif kind == 'filled_map':
        points=[]
        for i, r in enumerate(rows):
            ring=r['geometry']['coordinates'][0]; points.extend(ring)
            p=Polygon(ring, facecolor='#DFEBF2' if r['amount']==0 else PALETTE[0], edgecolor='white', lw=3); p.set_gid(f'polygon:{i}'); ax.add_patch(p)
            ax.text(sum(v[0] for v in ring[:-1])/(len(ring)-1), sum(v[1] for v in ring[:-1])/(len(ring)-1), f'{r["region"]}\n{r["amount"]}', ha='center', va='center', color='white' if r['amount']>0 else INK)
        ax.set(xlim=(min(p[0] for p in points)-.2, max(p[0] for p in points)+.2), ylim=(min(p[1] for p in points)-.2, max(p[1] for p in points)+.2), aspect='equal', xlabel='经度 · 提供的示意边界', ylabel='纬度')
    else: raise ValueError('Missing teacher renderer: '+kind)
    for axis in fig.axes:
        if axis.axison:
            axis.spines[['top', 'right']].set_visible(False); axis.set_axisbelow(True)
            if not axis.images and kind not in {'heatmap_marks', 'highlight_table'}: axis.grid(axis='y', color='#E3EAF0', lw=.7)
    fig.canvas.draw()
    return fig


def json_path(path):
    import json
    return json.dumps(list(path), ensure_ascii=False, separators=(',', ':'))


def audit_figure(c, fig):
    """Inspect live artists, not a renderer's self-reported success flag."""
    expected=oracle(c); kind=c['kind']; rows=c['rows']
    artists={a.get_gid(): a for a in fig.findobj() if a.get_gid()}
    count=0
    def equal(actual, wanted): np.testing.assert_allclose(np.asarray(actual, dtype=float), np.asarray(wanted, dtype=float), rtol=1e-10, atol=1e-9, equal_nan=True)
    def line(name, wanted):
        nonlocal count
        artist=artists[name]; equal(artist.get_ydata(), wanted)
        assert artist.get_visible() and artist.get_alpha()!=0
        count+=sum(math.isfinite(float(v)) for v in wanted)
        return artist
    if kind in {'line', 'area', 'circle', 'dual_line', 'combo'}:
        previous=np.zeros(len(rows))
        for name, selected, field in series(c):
            wanted=np.array([np.nan if r[field] is None else r[field] for r in selected])
            if kind=='area':
                wanted=previous+wanted; previous=wanted.copy()
                wanted=np.where([all(r[f] is not None for f in c['ys']) for r in selected], wanted, np.nan)
            if kind=='combo' and field=='amount_cny':
                for i, v in enumerate(wanted): equal(artists[f'bar:{field}:{i}'].get_height(), v); count+=1
            else:
                artist=line('series:'+name, wanted)
                x=np.asarray(coordinates(c, selected)[0], dtype=float)
                if c['id']=='SM11': x+=(c['ys'].index(field)-.5)*.18
                equal(artist.get_xdata(), x)
                assert artist.get_marker() not in (None, 'None', '', ' '), 'isolated valid observations must have markers'
    elif kind=='box':
        for panel in ['body', 'full']:
            for i, g in enumerate(expected['groups']):
                line(f'median:{panel}:{i}', [g['median']]*2)
                equal(artists[f'box:{panel}:{i}'].get_path().vertices[:,1].min(), g['q1'])
                equal(artists[f'box:{panel}:{i}'].get_path().vertices[:,1].max(), g['q3'])
                line(f'outliers:{panel}:{i}', g['outliers'])
    elif kind=='histogram':
        for i, amount in enumerate(expected['counts']):
            p=artists[f'bin:{i}']; equal([p.get_x(), p.get_width(), p.get_height()], [expected['edges'][i], expected['edges'][i+1]-expected['edges'][i], amount])
        count=expected['valid_n']
    elif kind=='bar':
        for field in c['ys']:
            for i, r in enumerate(rows):
                p=artists[f'bar:{field}:{i}']; equal(p.get_width() if c['id'] in {'SM07','CX03'} else p.get_height(), r[field]); count+=1
    elif kind in {'highlight_table', 'heatmap_marks'}:
        field='value' if c['id']=='CX06' else 'amount_cny'
        if kind=='highlight_table':
            image=artists['matrix']; actual=np.ma.asarray(image.get_array())
            xs=list(dict.fromkeys(r[c['x'] if c['id']=='CX06' else 'month'] for r in rows)); ys=list(dict.fromkeys(r[c.get('y','region')] for r in rows))
            for r in rows:
                x=xs.index(r[c['x'] if c['id']=='CX06' else 'month']); y=ys.index(r[c.get('y','region')])
                if r[field] is None: assert bool(np.ma.getmaskarray(actual)[y,x])
                else: equal(actual[y,x], r[field]); count+=1
        else:
            p=artists['matrix-marks']; equal(p.get_sizes(), [r['orders']*28 for r in rows])
            equal(np.ma.filled(p.get_array(), np.nan), [np.nan if r[field] is None else r[field] for r in rows])
            count=len(rows)
    elif kind=='treemap':
        for leaf in expected['leaves']:
            p=artists['leaf:'+json_path(leaf['path'])]; equal(p.get_width()*p.get_height(), leaf['value']/expected['total']); count+=1
    elif kind=='sunburst':
        for node in expected['nodes']:
            if not node['path']: continue
            p=artists['wedge:'+json_path(node['path'])]; equal(p.theta2-p.theta1, 360*node['value']/expected['total']); count+=1
    elif kind=='sankey':
        for i, r in enumerate(rows):
            vertices=artists[f'flow:{i}'].get_path().vertices; equal(vertices[7,1]-vertices[0,1], r['amount']*.8/expected['total']); equal(vertices[4,1]-vertices[3,1], r['amount']*.8/expected['total']); count+=1
    elif kind=='gantt':
        for i, r in enumerate(rows):
            p=artists[f'task:{i}']; equal([p.get_x(), p.get_width()], [dates.date2num(datetime.fromisoformat(r['start'])), expected['days'][i]]); count+=1
            if expected['days'][i]==0: assert f'zero-task:{i}' in artists
    elif kind=='bullet':
        for i, r in enumerate(rows): equal(artists[f'actual:{i}'].get_width(), r['actual']); equal(artists[f'target:{i}'].get_xdata(), [r['target']]*2); count+=2
    elif kind=='packed_bubble':
        maximum=max(r['amount'] for r in rows); circles=[]
        for i, r in enumerate(rows):
            p=artists[f'circle:{i}']; equal(p.radius**2, r['amount']/maximum); circles.append(p); count+=r['amount']>0
        for i, a in enumerate(circles):
            for b in circles[i+1:]: assert math.dist(a.center, b.center)+1e-9>=a.radius+b.radius
    elif kind=='pie':
        total=sum(r['amount_cny'] for r in rows)
        for i, r in enumerate(rows):
            p=artists[f'slice:{i}']; assert math.isclose(p.theta2-p.theta1, r['amount_cny']/total*360, rel_tol=1e-6, abs_tol=1e-5); count+=1
    elif kind=='scatter':
        valid=[r for r in rows if r['x_value'] is not None and r['y_value'] is not None]
        equal(artists['scatter'].get_offsets(), [[r['x_value'],r['y_value']] for r in valid]); count=len(valid)
    elif kind=='text_table':
        table=artists['table']
        for i, r in enumerate(rows, 1):
            for j, value in enumerate(r.values()): assert table[i,j].get_text().get_text()==('未知' if value is None else str(value)); count+=1
    elif kind=='symbol_map':
        equal(artists['map'].get_offsets(), [[r['longitude'],r['latitude']] for r in rows]); equal(artists['map'].get_sizes(), [r['amount']*75 for r in rows]); count=len(rows)
    elif kind=='filled_map':
        for i, r in enumerate(rows): equal(artists[f'polygon:{i}'].get_xy(), r['geometry']['coordinates'][0]); count+=1
    else: raise AssertionError('No teacher audit for '+kind)
    assert count>0
    return dict(passed=True, checked_observations=int(count), input_rows=len(rows), scope='actual static teacher artists; not a product pass or complete aesthetic certification')
