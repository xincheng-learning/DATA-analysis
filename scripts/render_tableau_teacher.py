"""Independent teacher assets: no imports from the product or its chart helpers."""
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'evals/tableau_reference_v1/TB001.csv'
OUT=ROOT/'output/tableau-teacher-2026-11-14'


def run():
    with SOURCE.open(encoding='utf-8',newline='') as stream:rows=list(csv.DictReader(stream))
    data={name:[float(row['amount_cny']) for row in rows if row['group']==name] for name in ('A','B')}
    assert data=={'A':[1,2,3,4,5,100],'B':[2,4,6,8]}
    def percentile(values,fraction):
        ordered=sorted(values);position=min(len(ordered)-1,max(0,len(ordered)*fraction-.5))
        lower=int(position);upper=min(lower+1,len(ordered)-1)
        return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower)
    stats=[]
    for name,values in data.items():
        q1,median,q3=(percentile(values,p) for p in (.25,.5,.75));iqr=q3-q1
        inside=[value for value in values if q1-1.5*iqr<=value<=q3+1.5*iqr]
        stats.append({'label':name,'q1':q1,'med':median,'q3':q3,'whislo':min(inside),'whishi':max(inside),
            'fliers':[value for value in values if value<min(inside) or value>max(inside)]})
    assert stats==[{'label':'A','q1':2.,'med':3.5,'q3':5.,'whislo':1.,'whishi':5.,'fliers':[100.]},
                   {'label':'B','q1':3.,'med':5.,'q3':7.,'whislo':2.,'whishi':8.,'fliers':[]}]
    font=font_manager.FontProperties(fname='C:/Windows/Fonts/msyh.ttc').get_name()
    plt.rcParams.update({'font.family':font,'axes.unicode_minus':False,'font.size':12,'svg.fonttype':'none'})
    fig,(main,full)=plt.subplots(1,2,figsize=(12,6.4),gridspec_kw={'width_ratios':[4,1]},layout='constrained')
    for ax in (main,full):
        ax.bxp(stats,showfliers=True,patch_artist=True,widths=.42,
            boxprops={'facecolor':'#c7dce3','edgecolor':'#245b82','linewidth':1.8},
            medianprops={'color':'#163f5b','linewidth':2.5},whiskerprops={'color':'#245b82','linewidth':1.8},
            capprops={'color':'#245b82','linewidth':1.8},flierprops={'marker':'o','markersize':5,'markerfacecolor':'#bf5b51','markeredgecolor':'#bf5b51'})
        ax.set_axisbelow(True);ax.grid(axis='y',color='#e2e8eb',linewidth=.7)
        ax.spines[['top','right']].set_visible(False);ax.spines[['left','bottom']].set_color('#b9c5cc')
        ax.set_xlabel('组别');ax.set_ylabel('金额（CNY）')
    main.set_ylim(0,10);full.set_ylim(0,110)
    main.set_title('主体分布 · 共同纵轴 0—10',loc='left',fontsize=14,pad=14)
    full.set_title('全量 · 0—110',fontsize=13,pad=14)
    full.annotate('A：100',xy=(1,100),xytext=(1.32,96),fontsize=12,color='#a34035',
        arrowprops={'arrowstyle':'-','color':'#a34035'})
    fig.suptitle('A、B 两组的主体分布不同，A 另有一个远离主体的观测',fontsize=18,fontweight='bold',x=.02,ha='left')
    fig.supxlabel('同一份完整数据 · 左图放大主体，右图保留全量与离群值；两图纵轴范围不同。\n线性四分位；须为 1.5×IQR 内最远观测；离群不等于错误，不据此删除或推断因果。',fontsize=11,color='#5c6b73',ha='left',x=.02)
    fig.canvas.draw()
    assert main.get_window_extent().height*(5-2)/10>32
    OUT.mkdir(parents=True,exist_ok=True)
    for suffix in ('png','svg','pdf'):fig.savefig(OUT/f'TB001-teacher.{suffix}',dpi=180)
    plt.close(fig)
    manifest={'case':'TB001','status':'independent teacher candidate, calibration reference; not a blind test',
        'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'question':'比较A/B完整分布，主体可读且保留100离群值',
        'statistics':stats,'main_range':[0,10],'full_range':[0,110],
        'minimum_iqr_pixels':32,'product_imports':False}
    (OUT/'TB001-reference.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'case':'TB001','independent_statistics_checked':True,'artifacts':['PNG','SVG','PDF']},ensure_ascii=False))


if __name__=='__main__':run()
