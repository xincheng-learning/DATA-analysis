"""Independent synthetic teachers for SM01–SM24; never imports product code."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cases():
    monthly = [{'month': m, 'amount_cny': a, 'other_cny': b} for m, a, b in [
        ('2026-01', 100, 60), ('2026-02', 0, 20), ('2026-03', None, None), ('2026-04', 160, 80)]]
    groups = [{'category': c, 'amount_cny': a, 'other_cny': b} for c, a, b in [('甲', 10, 4), ('乙', 0, 5), ('丙', 30, 20)]]
    grid = [{'month': m, 'region': r, 'amount_cny': a, 'orders': n} for m, r, a, n in [
        ('一月', '华东', 0, 5), ('二月', '华东', 30, 10), ('三月', '华东', None, 15),
        ('一月', '华南', 10, 20), ('二月', '华南', 60, 25), ('三月', '华南', 90, 30)]]
    old = json.loads((ROOT/'evals/tableau_reference_v1/families.json').read_text(encoding='utf-8'))
    box = [{'group': g, 'amount_cny': v} for g, values in [('A', [1, 2, 3, 4, 5, 100]), ('B', [2, 4, 6, 8])] for v in values]
    data = [
        ('文本表', 'text_table', monthly, '用文本表展示month、amount_cny、other_cny的全部原始行，精确保留零和NULL。'),
        ('热图', 'heatmap_marks', grid, '绘制大小和颜色双编码热图：横轴month、纵轴region，圆面积表示orders，颜色表示amount_cny；NULL不能补0。'),
        ('高亮表', 'highlight_table', grid, '绘制高亮表：横轴month、纵轴region，amount_cny同时作为颜色与单元格数值，零与未知分开。'),
        ('符号地图', 'symbol_map', old['TB006']['rows'], '用符号地图按longitude、latitude真实位置绘点，place为标签，amount表示圆面积；不联网或推断地理位置。'),
        ('填充地图', 'filled_map', old['TB007']['rows'], '按geometry提供的Polygon和region标识绘制填充地图，用amount填色；仅用提供的示意边界，0不能当未知。'),
        ('饼图', 'pie', [{'category': c, 'amount_cny': v} for c, v in [('甲', 40), ('乙', 35), ('丙', 25), ('零值', 0)]], '绘制category的amount_cny饼图；同口径非负金额可加总，零保持0。'),
        ('水平条形', 'bar', groups, '绘制水平条形图：category为分类、amount_cny为金额，数值轴从0开始。'),
        ('堆叠条形', 'bar', groups, '按category绘制amount_cny和other_cny堆叠条形图，两列都是CNY可加总；保留0。'),
        ('并排条形', 'bar', groups, '按category绘制amount_cny和other_cny并排条形图，两列均CNY，保留0。'),
        ('圆点视图', 'circle', groups, '用固定大小圆点图比较各category的amount_cny，不画连接线、不用面积表示金额。'),
        ('并排圆点', 'circle', groups, '按category绘制amount_cny和other_cny的并排圆点图，同单位、两个系列，点不连线。'),
        ('连续折线', 'line', monthly, '绘制month连续时间轴的amount_cny折线，0保留，NULL处断开，不跨空值连接。'),
        ('离散折线', 'line', monthly, '绘制离散折线，month作为有序分类而非连续时间轴，显示amount_cny，NULL处断开。'),
        ('双轴折线', 'dual_line', old['TB005']['rows'], '明确绘制双轴折线：month为横轴，amount_cny用左轴CNY，conversion_ratio用右轴百分比，原数据0到1不乘100，NULL处断开。'),
        ('连续面积', 'area', monthly, '绘制连续时间轴面积图，month横轴，amount_cny和other_cny为同单位CNY的两个堆叠系列；零和NULL保留，不跨空值。'),
        ('离散面积', 'area', groups, '绘制离散分类面积图，category按甲乙丙排列，amount_cny和other_cny同为CNY，堆叠可加总。'),
        ('双轴组合', 'combo', old['TB005']['rows'], '明确绘制双轴柱线组合：amount_cny用左轴柱状，conversion_ratio用右轴折线百分比；month横轴，NULL处断开，原比例不乘100。'),
        ('散点', 'scatter', [{'x_value': x, 'y_value': y} for x, y in [(0, 0), (1, 2), (2, None), (3, 5), (4, 8)]], '绘制x_value和y_value散点图，零是有效值，未知点不能伪造位置。'),
        ('直方图', 'histogram', [{'amount_cny': v} for v in [0, 0, 1, 1, 2, 3, 3, 4, None]], '用完整amount_cny原始观测绘直方图，NULL不当0，0是有效观测；不先求均值或去重。'),
        ('箱线图', 'box', box, '按group绘制amount_cny箱线图，用全部原始观测；linear四分位、1.5IQR须，保留100离群值，同时让主体箱体清楚可读。'),
        ('甘特图', 'gantt', old['TB002']['rows'], '绘制甘特图，task为任务，start为实际开始、end为实际结束，跨度按结束减开始，不含首尾加一天。'),
        ('子弹图', 'bullet', old['TB003']['rows'], '绘制group的actual对target子弹图，target是用户给定目标，不能猜测额外阈值。'),
        ('紧密气泡', 'packed_bubble', old['TB004']['rows'], '绘制不重叠的紧密气泡，group为类别，amount为圆面积，面积比1:4:9；零值不能补造面积。'),
        ('树图', 'treemap', [{'region': r, 'category': c, 'amount_cny': v} for r, c, v in [('东区', '甲', 20), ('东区', '乙', 30), ('西区', '甲', 10), ('西区', '乙', 40)]], '绘制region→category两层矩形树图，amount_cny为叶子面积，重复类别按完整路径区分，父节点等于其子项之和。'),
    ]
    return [dict(id=f'SM{i:02}', name=name, kind=kind, rows=deepcopy(rows), question=question,
        scope='fixed-input development chart acceptance; not a blind or complete SQL-agent test')
        for i, (name, kind, rows, question) in enumerate(data, 1)]


def oracle(case):
    rows = case['rows'];kind=case['kind']
    result = {'row_count': len(rows), 'null_count': sum(v is None for r in rows for v in r.values()),
        'input_sha256': hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    if kind == 'box':
        result['stats'] = [dict(group='A', n=6, q1=2, median=3.5, q3=5, low=1, high=5, outliers=[100]),
            dict(group='B', n=4, q1=3, median=5, q3=7, low=2, high=8, outliers=[])]
    if kind == 'histogram':
        result.update(valid_n=8, missing_n=1, distinct_n=5, teacher_edges=[0, 2, 4, 6], teacher_counts=[4, 3, 1],
            bin_policy='Other disclosed bins are accepted if every nonmissing observation contributes exactly once.')
    if kind == 'gantt':
        result['days']=[(date.fromisoformat(r['end'])-date.fromisoformat(r['start'])).days for r in rows]
        assert result['days'] == [2, 5]
    if kind == 'bullet':
        result['gaps']=[r['actual']-r['target'] for r in rows];assert result['gaps']==[-20, 10]
    if kind in {'packed_bubble', 'symbol_map'}:
        result['areas']=[r['amount'] for r in rows]
    if kind == 'treemap':
        result.update(total=100, parents={'东区': 50, '西区': 50}, leaf_weights=[20, 30, 10, 40])
        assert sum(r['amount_cny'] for r in rows)==100
    return result


def render(out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import dates, font_manager
    from matplotlib.patches import Circle, Polygon, Rectangle
    from matplotlib.ticker import PercentFormatter
    import numpy as np
    out=Path(out);out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname='C:/Windows/Fonts/msyh.ttc').get_name(),
        'font.size':12, 'axes.unicode_minus':False})
    refs=[]
    for case in cases():
        rows=case['rows'];kind=case['kind'];ident=case['id'];expected=oracle(case)
        fig,ax=plt.subplots(figsize=(10,5.6),layout='constrained')
        vals=lambda col:[float('nan') if r[col] is None else r[col] for r in rows]
        labels=lambda col:[r[col] for r in rows]
        if kind=='text_table':
            ax.axis('off');ax.table(cellText=[['未知' if v is None else str(v) for v in r.values()] for r in rows],colLabels=list(rows[0]),loc='center',cellLoc='center').scale(1,2)
        elif kind in {'heatmap_marks','highlight_table'}:
            xs=['一月','二月','三月'];ys=['华东','华南'];ax.set(xticks=range(3),xticklabels=xs,yticks=range(2),yticklabels=ys,xlim=(-.5,2.5),ylim=(1.5,-.5))
            if kind=='highlight_table':
                z=np.array([[next(r['amount_cny'] for r in rows if r['month']==m and r['region']==g) for m in xs] for g in ys],dtype=float)
                im=ax.imshow(np.ma.masked_invalid(z),cmap='Blues',vmin=0,vmax=90);fig.colorbar(im,ax=ax,label='金额（CNY）；空白为未知')
            else:
                known=[r for r in rows if r['amount_cny'] is not None]
                im=ax.scatter([xs.index(r['month']) for r in known],[ys.index(r['region']) for r in known],s=[r['orders']*32 for r in known],c=[r['amount_cny'] for r in known],cmap='Blues',vmin=0,vmax=90,edgecolors='#32677b')
                fig.colorbar(im,ax=ax,label='金额（CNY）；圆面积=订单数')
                ax.scatter([2],[0],s=[15*32],facecolors='none',edgecolors='#666',linestyle='--')
            for r in rows:ax.text(xs.index(r['month']),ys.index(r['region']),str(r['amount_cny']) if r['amount_cny'] is not None else '未知',ha='center',va='center')
        elif kind=='symbol_map':
            ax.scatter(labels('longitude'),labels('latitude'),s=[r['amount']*80 for r in rows],color='#397d90');ax.set(xlabel='经度',ylabel='纬度',xlim=(114,124),ylim=(29,42))
            for r in rows:ax.annotate(r['place'],(r['longitude'],r['latitude']),xytext=(12,0),textcoords='offset points')
        elif kind=='filled_map':
            for r in rows:
                ax.add_patch(Polygon(r['geometry']['coordinates'][0],facecolor='#e9f0f5' if r['amount']==0 else '#397d90',edgecolor='white',lw=3));ax.text(100.5 if r['region']=='西区' else 101.5,30.5,f"{r['region']}\n{r['amount']}",ha='center')
            ax.set(xlim=(99.8,102.2),ylim=(29.8,31.2),aspect='equal',xlabel='经度',ylabel='纬度')
        elif kind=='pie':
            ax.pie(labels('amount_cny'),labels=labels('category'),autopct=lambda p:f'{p:g}%' if p else '',startangle=90)
        elif kind=='bar':
            x=np.arange(len(rows));a=np.array(vals('amount_cny'));b=np.array(vals('other_cny'))
            if ident=='SM07':ax.barh(labels('category'),a,color='#397d90');ax.set_xlim(left=0);ax.set_xlabel('金额（CNY）')
            elif ident=='SM08':ax.bar(x,a,label='amount_cny');ax.bar(x,b,bottom=a,label='other_cny');ax.set_xticks(x,labels('category'));ax.legend()
            else:ax.bar(x-.2,a,width=.4,label='amount_cny');ax.bar(x+.2,b,width=.4,label='other_cny');ax.set_xticks(x,labels('category'));ax.legend()
        elif kind in {'line','area','circle'}:
            col='month' if 'month' in rows[0] else 'category';x=np.arange(len(rows));a=np.array(vals('amount_cny'));b=np.array(vals('other_cny'))
            if kind=='circle':
                ax.scatter(x-.08,a,label='amount_cny',s=60)
                if ident=='SM11':ax.scatter(x+.08,b,label='other_cny',s=60)
            elif kind=='area':ax.stackplot(x,a,b,labels=['amount_cny','other_cny'],colors=['#397d90','#c79a5a'])
            else:ax.plot(x,a,'o-',color='#397d90')
            ax.set_xticks(x,labels(col));ax.set_ylabel('金额（CNY）')
            if kind!='line':ax.legend()
        elif kind in {'dual_line','combo'}:
            x=np.arange(len(rows));right=ax.twinx()
            if kind=='combo':ax.bar(x,vals('amount_cny'),width=.4,color='#397d90')
            else:ax.plot(x,vals('amount_cny'),'o-',color='#397d90')
            right.plot(x,vals('conversion_ratio'),'o-',color='#b66349');right.yaxis.set_major_formatter(PercentFormatter(1));right.set_ylabel('转化率（%）');ax.set_ylabel('金额（CNY）');ax.set_xticks(x,labels('month'))
        elif kind=='scatter':ax.scatter(vals('x_value'),vals('y_value'),s=65,color='#397d90');ax.set(xlabel='x_value',ylabel='y_value')
        elif kind=='histogram':
            n,_,_=ax.hist([r['amount_cny'] for r in rows if r['amount_cny'] is not None],bins=[0,2,4,6],color='#397d90',edgecolor='white');assert list(n)==expected['teacher_counts'];ax.set(xlabel='金额（CNY）',ylabel='非空观测数');fig.supxlabel('有效8条，未知1条；原始零值与重复观测保留')
        elif kind=='box':
            fig.clear();ax,full=fig.subplots(1,2,gridspec_kw={'width_ratios':[4,1]})
            stats=[dict(label=r['group'],q1=r['q1'],med=r['median'],q3=r['q3'],whislo=r['low'],whishi=r['high'],fliers=r['outliers']) for r in expected['stats']]
            for target in (ax,full):target.bxp(stats,patch_artist=True,boxprops={'facecolor':'#b7d7df'});target.set_ylabel('金额（CNY）')
            ax.set_ylim(0,10);ax.set_title('主体共同尺度');full.set_ylim(0,105);full.set_title('全量含离群100');fig.supxlabel('linear：A Q1=2、中位3.5、Q3=5；B Q1=3、中位5、Q3=7；须为1.5IQR范围内实际值')
        elif kind=='gantt':
            starts=[date.fromisoformat(r['start']) for r in rows];ax.barh(labels('task'),expected['days'],left=dates.date2num(starts),height=.45,color='#397d90');ax.xaxis_date();ax.xaxis.set_major_formatter(dates.DateFormatter('%m-%d'));ax.set_xlabel('实际日期跨度：2天、5天')
        elif kind=='bullet':
            ax.barh(labels('group'),labels('actual'),height=.35,color='#397d90')
            for i,r in enumerate(rows):ax.plot([r['target']]*2,[i-.3,i+.3],color='#b66349',lw=3)
            ax.set_xlim(0,140);ax.set_xlabel('实际值（横条）与明确目标（竖线）')
        elif kind=='packed_bubble':
            for r,(x,y) in zip(rows,[(-2,0),(1,0),(1,5)]):
                ax.add_patch(Circle((x,y),math.sqrt(r['amount']),facecolor='#397d90',edgecolor='white'));ax.text(x,y,f"{r['group']}\n{r['amount']}",ha='center',va='center',color='white')
            ax.set(xlim=(-4,5),ylim=(-3,9),aspect='equal');ax.axis('off');fig.supxlabel('圆面积1:4:9，无重叠；0保留在数据里，不补造面积。布局允许不同。')
        elif kind=='treemap':
            for r,(x,y,w,h) in zip(rows,[(0,0,.4,.5),(.4,0,.6,.5),(0,.5,.2,.5),(.2,.5,.8,.5)]):
                ax.add_patch(Rectangle((x,y),w,h,facecolor='#397d90' if r['region']=='东区' else '#c79a5a',edgecolor='white',lw=3));ax.text(x+w/2,y+h/2,f"{r['region']} › {r['category']}\n{r['amount_cny']}",ha='center',va='center',color='white')
            ax.set(xlim=(0,1),ylim=(0,1));ax.axis('off');fig.supxlabel('叶子面积20:30:10:40，父节点各50，总计100；允许不同合法布局')
        ax.set_title(case['name']+' · 独立教师',loc='left',pad=15)
        fig.savefig(out/(ident+'-teacher.png'),dpi=160);plt.close(fig)
        refs.append({**case,'expected':expected})
    manifest={'reference_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'families_source_sha256':hashlib.sha256((ROOT/'evals/tableau_reference_v1/families.json').read_bytes()).hexdigest(),
        'product_imports':False,'cases':refs}
    (out/'reference.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=render(args.output)
    print(f"Rendered {len(result['cases'])} independent chart teachers.")
