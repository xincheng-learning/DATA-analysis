"""Independent frozen teaching figures; never import product calculations or rendering."""
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import dates, font_manager
from matplotlib.patches import Circle, Polygon

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'evals/tableau_reference_v1/families.json'
OUT=ROOT/'output/tableau-teacher-2026-11-14'


def run():
    cases=json.loads(SOURCE.read_text('utf-8'))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname='C:/Windows/Fonts/msyh.ttc').get_name(),'font.size':12,'axes.unicode_minus':False})
    OUT.mkdir(parents=True,exist_ok=True)
    for key,case in cases.items():
        fig,ax=plt.subplots(figsize=(10,5),layout='constrained');rows=case['rows']
        if key=='TB002':
            starts=[date.fromisoformat(r['start']) for r in rows];days=[(date.fromisoformat(r['end'])-start).days for r,start in zip(rows,starts)]
            assert days==[2,5]==case['expected_days']
            ax.barh([r['task'] for r in rows],days,left=dates.date2num(starts),height=.45,color='#367989');ax.xaxis_date();ax.xaxis.set_major_formatter(dates.DateFormatter('%m-%d'));ax.set_xlabel('实际日期 · 结束减开始，不按含首尾天数')
        elif key=='TB003':
            assert [r['actual']-r['target'] for r in rows]==[-20,10]
            ax.barh([r['group'] for r in rows],[r['actual'] for r in rows],height=.35,color='#367989')
            for i,r in enumerate(rows):ax.plot([r['target']]*2,[i-.3,i+.3],color='#b15e43',lw=3)
            ax.set_xlim(0,140);ax.set_xlabel('实际值（条形）与用户提供的目标（竖线）')
        elif key=='TB004':
            # Independent feasible packing, layout coordinates are not an analytical quantity.
            positions=[(-2,0),(1,0),(1,5)]
            for r,(x,y) in zip(rows,positions):
                radius=math.sqrt(r['amount']);ax.add_patch(Circle((x,y),radius,facecolor='#367989',edgecolor='white'));ax.text(x,y,f"{r['group']}\n{r['amount']}",ha='center',va='center',color='white')
            assert [r['amount'] for r in rows]==[1,4,9,0]
            ax.set(xlim=(-4,5),ylim=(-3,9),aspect='equal');ax.axis('off');fig.supxlabel('圆面积比 1:4:9；零值保留在原始数据中，不补造可见面积。布局可不同，不能重叠。')
        elif key=='TB005':
            ax.bar([r['month'] for r in rows],[r['amount_cny'] for r in rows],width=.4,color='#367989',label='金额');right=ax.twinx()
            right.plot([r['month'] for r in rows],[float('nan') if r['conversion_ratio'] is None else r['conversion_ratio'] for r in rows],marker='o',color='#b15e43')
            from matplotlib.ticker import PercentFormatter
            right.yaxis.set_major_formatter(PercentFormatter(1));right.set_ylabel('转化比例（%）');ax.set_ylabel('金额（CNY）');ax.set_xlabel('月份 · 金额0保留，比例空值不连线')
        elif key=='TB006':
            ax.scatter([r['longitude'] for r in rows],[r['latitude'] for r in rows],s=[r['amount']*80 for r in rows],color='#367989')
            for r in rows:ax.annotate(r['place'],(r['longitude'],r['latitude']),xytext=(12,0),textcoords='offset points')
            ax.set(xlim=(114,124),ylim=(29,42),xlabel='经度',ylabel='纬度');fig.supxlabel('仅提供的坐标 · 无在线地名补全、底图或图块 · 面积比4:9')
        else:
            for r in rows:
                ax.add_patch(Polygon(r['geometry']['coordinates'][0],facecolor='#e9f0f5' if r['amount']==0 else '#367989',edgecolor='white',lw=3))
                ax.text(100.5 if r['region']=='西区' else 101.5,30.5,f"{r['region']}\n{r['amount']}",ha='center',color='#162e3a')
            ax.set(xlim=(99.8,102.2),ylim=(29.8,31.2),xlabel='经度',ylabel='纬度',aspect='equal');fig.supxlabel('明确提供的示意边界 · 西区0，东区10；无边界时拒绝猜测')
        ax.set_title(case['question'],loc='left',fontsize=15,pad=16)
        if key!='TB004':ax.spines[['top','right']].set_visible(False)
        fig.savefig(OUT/f'{key}-teacher.png',dpi=160);plt.close(fig)
    (OUT/'families-reference.json').write_text(json.dumps({'cases':cases,'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'product_imports':False,'status':'calibration teacher candidate, not blind test'},ensure_ascii=False,indent=2),'utf-8')
    print('Independent references TB002–TB007 rendered; numerical assertions passed.')


if __name__=='__main__':run()
