"""Offline review figures; consume independently calculated results, never call an LLM."""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MPath
from matplotlib.ticker import FuncFormatter
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output/analyst-gold-v3-review-2026-11-11'
INK, TEAL, GOLD, RED, MUTED = '#183449', '#087f8c', '#b58432', '#bf5b51', '#697985'
COLORS = [TEAL, '#528ca6', GOLD, '#788b65', '#9b839f', '#94a5ad', '#bbc8cf']
CAT = {'health_beauty':'健康与美容', 'watches_gifts':'钟表与礼品', 'sports_leisure':'运动与休闲',
       'bed_bath_table':'床浴与餐桌用品', 'computers_accessories':'电脑及配件', 'furniture_decor':'家具与装饰',
       'housewares':'家居用品','cool_stuff':'创意商品','auto':'汽车用品','toys':'玩具','perfumery':'香水',
       'telephony':'电话通讯','garden_tools':'园艺工具','baby':'婴童用品','office_furniture':'办公家具'}


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def render(r, cleaning):
    chartdir=OUT/'charts'; chartdir.mkdir(exist_ok=True)
    assets=OUT/'assets';assets.mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'frontend/node_modules/plotly.js/dist/plotly.min.js',assets/'plotly.min.js')
    shutil.copyfile(ROOT/'frontend/node_modules/plotly.js/LICENSE',assets/'plotly-LICENSE')
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family':'Microsoft YaHei','font.size':11,'axes.edgecolor':'#d5dee3',
        'axes.labelcolor':MUTED,'text.color':INK,'axes.spines.top':False,'axes.spines.right':False,
        'axes.spines.left':False,'axes.titleweight':'bold','xtick.color':MUTED,'ytick.color':MUTED,
        'figure.facecolor':'white','axes.facecolor':'white','svg.fonttype':'none','axes.unicode_minus':False})
    charts=[]

    def canvas(title, subtitle, axes=1, height=6.4):
        fig,ax=plt.subplots(axes,1,figsize=(13.5,height),squeeze=False)
        fig.subplots_adjust(left=.13,right=.95,bottom=.15,top=.75,hspace=.65)
        fig.text(.045,.94,title,fontsize=21,weight='bold')
        fig.text(.045,.875,subtitle,fontsize=10.5,color=MUTED)
        fig.text(.045,.025,'DATA analysis  /  TEACHER REFERENCE · 候选稿，待审核',fontsize=9,color=MUTED)
        return fig,ax[:,0]

    def finish(num,fig,spec,title,scope,rows):
        key=f'AG3{num:02d}'
        fig.savefig(chartdir/f'{key}.png',dpi=160)
        fig.savefig(chartdir/f'{key}.svg')
        plt.close(fig)
        spec.setdefault('layout',{}).update({'title':{'text':title,'subtitle':{'text':scope,'font':{'size':12}},'x':.04},
            'font':{'family':'Microsoft YaHei, sans-serif','color':INK},'paper_bgcolor':'white','plot_bgcolor':'white',
            'colorway':COLORS,'margin':{'t':105,'l':100,'r':60,'b':85},'height':680})
        # The installed Plotly schema requires title containers, even when older versions accepted strings.
        for name,axis in spec['layout'].items():
            if name.startswith(('xaxis','yaxis')) and isinstance(axis.get('title'),str):axis['title']={'text':axis['title']}
        for trace in spec['data']:
            if isinstance(trace.get('colorbar',{}).get('title'),str):trace['colorbar']['title']={'text':trace['colorbar']['title']}
        if spec['data'][0]['type']=='sankey':spec['layout'].pop('plot_bgcolor')
        save_json(chartdir/f'{key}.plotly.json',spec)
        save_json(chartdir/f'{key}.data.json',{'scope':scope,'rows':rows})
        # Plotly's built-in editor changes titles, labels and traces; persist all edits by exporting the full spec.
        html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>可编辑参考图</title>
<style>body{font:14px 'Microsoft YaHei',sans-serif;margin:22px;color:#183449}button{padding:10px 16px;border:0;border-radius:6px;background:#087f8c;color:white;cursor:pointer}#chart{min-height:680px}p{line-height:1.8}</style>
<p>可缩放、悬停、导出PNG；双击标题、图例或轴标题可修改文字。修改后点“保存编辑配置”，可保留完整图形配置与数据。原始PNG/SVG为老师静态排版，交互版保持同一口径和数值。</p>
<button id="save">保存编辑配置 JSON</button><div id="chart"></div><script src="../assets/plotly.min.js"></script><script>
const spec=__SPEC__;const el=document.getElementById('chart');
Plotly.newPlot(el,spec.data,spec.layout,{responsive:true,editable:true,displaylogo:false,toImageButtonOptions:{format:'png',scale:2}});
document.getElementById('save').onclick=()=>{const a=document.createElement('a');const u=URL.createObjectURL(new Blob([JSON.stringify({data:el.data,layout:el.layout},null,2)],{type:'application/json'}));a.href=u;a.download='__KEY__-edited.plotly.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
</script></html>'''.replace('__SPEC__',json.dumps(spec,ensure_ascii=False).replace('</','<\/')).replace('__KEY__',key)
        (chartdir/f'{key}.html').write_text(html,encoding='utf-8')
        charts.append({'case_id':key,'title':title,'scope':scope,'png':f'charts/{key}.png','svg':f'charts/{key}.svg',
            'editable':f'charts/{key}.html','config':f'charts/{key}.plotly.json','data':f'charts/{key}.data.json'})

    def hbar(num,title,scope,labels,values,unit='BRL',colors=None,rows=None):
        fig,(ax,)=canvas(title,scope,height=max(6.4,len(labels)*.42+2.7))
        longest=max(map(len,labels))
        fig.subplots_adjust(left=.38 if longest>30 else .27 if longest>17 else .16)
        colors=colors or [TEAL]*len(labels)
        ax.barh(labels,values,color=colors,height=.62);ax.invert_yaxis();ax.axvline(0,color='#cbd6dd',lw=.8)
        ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True);ax.set_xlabel(unit)
        span=max(values)-min(0,min(values));pad=span*.02 if span else 1
        for i,v in enumerate(values):ax.text(v+(pad if v>=0 else -pad),i,f'{v:,.2f}' if unit=='BRL' else f'{v:,.0f}',ha='left' if v>=0 else 'right',va='center',fontsize=9)
        ax.margins(x=.2)
        spec={'data':[{'type':'bar','orientation':'h','y':labels,'x':values,'marker':{'color':colors}}],
            'layout':{'xaxis':{'title':unit,'zeroline':True},'yaxis':{'autorange':'reversed'}}}
        finish(num,fig,spec,title,scope,rows if rows is not None else list(zip(labels,values)))

    # 01: real table relationships, with keys and cardinality visible together.
    fig,(ax,)=canvas('先认清数据粒度，再开始关联','8张表 · 47个原始字段 · 7条候选关联均按实际非空键核验',height=7.6)
    ax.set_axis_off();ax.set_xlim(0,1);ax.set_ylim(0,1)
    pos={'customers':(.04,.68),'orders':(.35,.68),'reviews':(.7,.88),'payments':(.7,.55),
         'order_items':(.35,.22),'products':(.7,.22),'sellers':(.04,.22),'category_translation':(.7,-.04)}
    rowcounts={p['table']:p['rows'] for p in r['profile']}
    for name,(x,y) in pos.items():
        ax.add_patch(FancyBboxPatch((x,y),.25,.16,boxstyle='round,pad=.012',fc='#eff6f7',ec='#c7dade'))
        ax.text(x+.012,y+.105,name,fontsize=10.2,weight='bold');ax.text(x+.012,y+.035,f'{rowcounts[name]:,} 行',fontsize=10,color=TEAL)
    for a,b,label in [('customers','orders','customer_id · 1:1*'),('orders','order_items','order_id · 1:N'),
        ('orders','payments','order_id · 1:N'),('orders','reviews','order_id · 1:N'),
        ('products','order_items','product_id · 1:N'),('sellers','order_items','seller_id · 1:N'),
        ('category_translation','products','品类名 · 1:N')]:
        x,y=pos[a];u,v=pos[b];start=(x+.125,y+.08);end=(u+.125,v+.08)
        ax.annotate('',xy=end,xytext=start,arrowprops={'arrowstyle':'->','color':'#8aa1ae','shrinkA':47,'shrinkB':47})
        # Exact keys remain legible in the matching relationship evidence table.
    ax.text(.0,-.18,'*本样本 orders.customer_id 与 customers.customer_id 为一对一；复购须用 customer_unique_id。\n完整7条关联键、缺失与未匹配数见同题依据表。',fontsize=10,color=MUTED)
    nodes=list(pos)
    spec={'data':[{'type':'scatter','mode':'markers+text','x':[pos[n][0] for n in nodes],'y':[pos[n][1] for n in nodes],
        'text':[f'{n}<br>{rowcounts[n]:,}行' for n in nodes],'textposition':'top center','marker':{'size':24,'color':TEAL}}],
        'layout':{'xaxis':{'visible':False},'yaxis':{'visible':False},'shapes':[{'type':'line','x0':pos[e['child']][0],'y0':pos[e['child']][1],
            'x1':pos[e['parent']][0],'y1':pos[e['parent']][1],'line':{'color':'#bbc8cf'}} for e in r['relationships']]}}
    finish(1,fig,spec,'数据地图与逐表粒度','Olist原始样本；关系含非空未匹配检查',r['relationships'])
    missing=[(p['table']+'.'+f['column'],f['missing']) for p in r['profile'] for f in p['fields'] if f['missing']]
    hbar(2,'缺失首先是业务语义问题','仅列实际测得的非空缺口；分母为各字段所属表行数',
         [x[0] for x in missing],[x[1] for x in missing],'缺失记录数',rows=missing)
    # Long field names require enough left margin in static figures.
    # 03: distinguish overlapping missing sets from an additive count.
    labels=['审核缺失 ∩ 发货缺失','仅发货缺失','审核与发货均有值'];values=[7,86,5076]
    hbar(3,'这两个缺失集合重叠，不能相加扣除','全部5,169单；已送达5,008单审核/发货均完整',labels,values,'订单数',rows=r['missing_scope'])
    months=[x for x in r['monthly'] if '2018-01'<=x['month']<='2018-07']
    fig,axes=canvas('七月金额恢复，增长来自订单量','已送达 · 按下单月 · 2018年1—7月 · 商品金额不含运费',axes=2,height=8)
    for ax,key,label in zip(axes,['item_amount','orders'],['商品金额 / BRL','订单数 / 单']):
        ax.plot([x['month'][5:] for x in months],[float(x[key]) for x in months],'-o',color=TEAL,lw=2.5)
        ax.set_ylabel(label);ax.grid(axis='y',alpha=.2);ax.set_ylim(bottom=0)
    spec={'data':[{'type':'scatter','mode':'lines+markers','x':[x['month'] for x in months],'y':[float(x['item_amount']) for x in months],'name':'商品金额','yaxis':'y'},
        {'type':'scatter','mode':'lines+markers','x':[x['month'] for x in months],'y':[x['orders'] for x in months],'name':'订单数','yaxis':'y2'}],
        'layout':{'yaxis':{'title':'商品金额 / BRL','domain':[.57,1],'rangemode':'tozero'},'yaxis2':{'title':'订单数 / 单','domain':[0,.38],'rangemode':'tozero'},'showlegend':False}}
    finish(4,fig,spec,'经营趋势：金额与订单量','2018年1—7月；独立单位小多图',months)
    for num,m in zip([5,6],r['movements']):
        title='六月下滑：订单减少抵消客单提升' if num==5 else '七月恢复：订单增长抵消客单回落'
        scope=f"{m['before']} → {m['after']} · 已送达 · 对称量价分解 · BRL"
        vals=[float(m['before_amount']),float(m['quantity_effect']),float(m['order_value_effect']),float(m['after_amount'])]
        starts=[0,vals[0],vals[0]+vals[1],0]
        bottoms=[0,min(starts[1],starts[1]+vals[1]),min(starts[2],starts[2]+vals[2]),0]
        fig,(ax,)=canvas(title,scope)
        names=['前期金额','订单量效应','每单金额效应','本期金额']
        ax.bar(names,[abs(x) for x in vals],bottom=bottoms,color=[INK,TEAL if vals[1]>0 else RED,TEAL if vals[2]>0 else RED,INK],width=.6)
        for i,v in enumerate(vals):ax.text(i,bottoms[i]+abs(v)+1700,f'{v:+,.2f}' if i in [1,2] else f'{v:,.2f}',ha='center',fontsize=11)
        ax.set_ylabel('商品金额 / BRL');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.set_ylim(0,max(vals[0],vals[3])*1.22)
        spec={'data':[{'type':'waterfall','x':names,'measure':['absolute','relative','relative','total'],
            'y':[vals[0],vals[1],vals[2],0],'increasing':{'marker':{'color':TEAL}},'decreasing':{'marker':{'color':RED}},'totals':{'marker':{'color':INK}}}],
            'layout':{'yaxis':{'title':'BRL','rangemode':'tozero'}}}
        finish(num,fig,spec,title,scope,m)
    states=r['states'][:10];others=sum(float(x['change']) for x in r['states'][10:])
    hbar(7,'地域贡献：PR拉动最多，CE形成抵消','2018年6→7月 · 买家州 · 前10个绝对变动州＋其余15州合计',
        [x['buyer_state'] for x in states]+['其余15州（合计）'],[float(x['change']) for x in states]+[others],
        colors=[TEAL if float(x['change'])>=0 else RED for x in states]+['#94a5ad'],rows=r['states'])
    cats=r['categories'][:8];rest=sum(float(x['amount']) for x in r['categories'][8:] if x['category']!='未知/未译');unknown=next(x for x in r['categories'] if x['category']=='未知/未译')
    share3=sum(float(x['amount']) for x in r['categories'][:3])/float(r['kpis']['item_amount'])*100
    hbar(8,f'品类结构：前三占{share3:.2f}%，未知仍须保留','全部已送达 · 商品金额 · 65个展示组（含未知/未译）',
        [CAT.get(x['category'],x['category']) for x in cats]+['其余已知品类合计','未知/未译'],
        [float(x['amount']) for x in cats]+[rest,float(unknown['amount'])],colors=[TEAL]*8+['#94a5ad',GOLD],rows=r['categories'])
    eligible=sorted([x for x in r['sellers'] if x['eligible']>=30],key=lambda x:x['late_rate'],reverse=True)[:10]
    fig,(ax,)=canvas('卖家延迟率：看点估计，也看不确定性','仅展示订单数≥30的20位卖家中延迟率最高10位；区间为95% Wilson',height=8)
    ys=list(range(len(eligible)));rates=np.array([x['late_rate']*100 for x in eligible]);lo=np.array([x['wilson'][0]*100 for x in eligible]);hi=np.array([x['wilson'][1]*100 for x in eligible])
    ax.errorbar(rates,ys,xerr=[rates-lo,hi-rates],fmt='o',color=TEAL,ecolor='#a0bec8',capsize=4)
    labels=[x['seller_id'][:8]+f"…  n={x['eligible']}" for x in eligible]
    ax.set_yticks(ys,labels);ax.invert_yaxis();ax.set_xlim(left=0);ax.set_xlabel('延迟率 / %');ax.grid(axis='x',alpha=.2)
    spec={'data':[{'type':'scatter','mode':'markers','x':rates.tolist(),'y':labels,'error_x':{'type':'data','symmetric':False,'array':(hi-rates).tolist(),'arrayminus':(rates-lo).tolist()},
        'customdata':[x['seller_id'] for x in eligible],'hovertemplate':'%{customdata}<br>延迟率 %{x:.2f}%<extra></extra>'}],
        'layout':{'xaxis':{'title':'延迟率 / %','rangemode':'tozero'},'yaxis':{'autorange':'reversed'}}}
    finish(9,fig,spec,'卖家表现与95%区间','卖家×订单去重；以整单交付结果关联卖家，不能归因卖家责任',eligible)
    hbar(10,'直接关联两张明细表，会把金额放大','全部已送达 · 正确：两侧先按订单聚合；错误：直接多对多关联',
        ['商品金额：正确','商品金额：错误关联','支付金额：正确','支付金额：错误关联'],
        [float(r['kpis']['item_amount']),float(r['naive']['naive_item']),float(r['kpis']['payment_amount']),float(r['naive']['naive_payment'])],
        colors=[TEAL,RED,TEAL,RED],rows={'correct':r['kpis'],'wrong':r['naive']})
    rec=r['reconciliation']['orders']
    hbar(11,'13笔订单需要进一步对账','已送达 · |差额|>0.01 BRL · 清单合计146.09，全体净差146.04 BRL · 未确认原因',
        [x['order_id'][:8]+'…' for x in rec],[float(x['difference']) for x in rec],rows=rec)
    hbar(12,'信用卡贡献79.40%的支付金额','已送达 · 支付记录金额可相加；跨支付方式的去重订单数不可相加',
        ['信用卡','Boleto付款单','代金券','借记卡'],[float(x['amount']) for x in r['payments']],rows=r['payments'])
    seg=r['segments']
    hbar(13,'RF分层：多数客户仅观察到一次购买','主动选择截止2018-08-01；客户去重标识；非全量生命周期或自然人数',
        [x['recency']+' / '+x['frequency_group'] for x in seg],[x['customers'] for x in seg],'客户去重标识数',rows=seg)
    cohorts=sorted({x['cohort'] for x in r['cohort']});z=np.full((6,4),np.nan);sizes={}
    for x in r['cohort']:
        sizes[x['cohort']]=x['cohort_size']
        if x['retention_rate'] is not None:z[cohorts.index(x['cohort']),x['month_offset']]=x['retention_rate']*100
    fig,(ax,)=canvas('留存热图：零次复购与尚未观察必须分开','样本内首次已送达购买月分组；M0基准100%，M1—M3采用独立色阶',height=7)
    cmap=plt.get_cmap('GnBu').copy();cmap.set_bad('#e6e9ed')
    # Exclude the mechanical M0=100% from the color scale to preserve the small follow-up differences.
    ax.imshow(np.ma.masked_invalid(z[:,1:]),cmap=cmap,vmin=0,vmax=1.5,aspect='auto',extent=(.5,3.5,5.5,-.5))
    ax.add_patch(Rectangle((-.5,-.5),1,6,facecolor='#e0edf0'))
    for i in range(6):
        for j in range(4):ax.text(j,i,'未观察' if np.isnan(z[i,j]) else f'{z[i,j]:.2f}%',ha='center',va='center',color=INK,fontsize=11)
    ax.set_xlim(-.5,3.5);ax.set_xticks(range(4),['M0（基准）','M1','M2','M3']);ax.set_yticks(range(6),[f'{c}  n={sizes[c]}' for c in cohorts]);ax.tick_params(length=0)
    spec={'data':[{'type':'heatmap','x':['M0','M1','M2','M3'],'y':[f'{c} n={sizes[c]}' for c in cohorts],
        'z':[[None if np.isnan(v) else float(v) for v in row] for row in z], 'text':[['未观察' if np.isnan(v) else f'{v:.2f}%' for v in row] for row in z],
        'texttemplate':'%{text}','zmin':0,'zmax':1.5,'colorscale':[[0,'#f7fcf0'],[.5,'#a8ddb5'],[1,'#0868ac']],'hoverongaps':False,'colorbar':{'title':'M1—M3 / %'}}],
        'layout':{'yaxis':{'autorange':'reversed'},'plot_bgcolor':'#e6e9ed'}}
    finish(14,fig,spec,'客户留存与观察窗','M0固定100%（超出复购色阶）；灰格未观察，0为已观察无复购',r['cohort'])
    groups=r['statistics']['groups'];rates=np.array([x['low_rate']*100 for x in groups]);lo=np.array([x['wilson'][0]*100 for x in groups]);hi=np.array([x['wilson'][1]*100 for x in groups])
    fig,(ax,)=canvas('延迟订单的低评价率明显更高','低评价=1—2分 · 分母仅有评价订单 · 95% Wilson区间 · 相关关系',height=6.2)
    labels=[f"{x['delivery_group']}  {x['low_reviews']}/{x['reviewed']}" for x in groups]
    ax.barh(labels,rates,color=[RED,TEAL],height=.45,xerr=[rates-lo,hi-rates],capsize=5)
    for i,v in enumerate(rates):ax.text(v+6,i,f'{v:.2f}%',va='center',fontsize=16,weight='bold')
    ax.set_xlim(0,100);ax.invert_yaxis();ax.set_xlabel('低评价率 / %');ax.grid(axis='x',alpha=.15);ax.set_axisbelow(True)
    spec={'data':[{'type':'bar','orientation':'h','x':rates.tolist(),'y':labels,'marker':{'color':[RED,TEAL]},
        'error_x':{'type':'data','symmetric':False,'array':(hi-rates).tolist(),'arrayminus':(rates-lo).tolist()}}],
        'layout':{'xaxis':{'title':'低评价率 / %','range':[0,100]},'yaxis':{'autorange':'reversed'}}}
    finish(15,fig,spec,'履约与低评价关联','延迟352单有评价；按时4,626单有评价；另有30单无评价',r['statistics'])
    model=r['model'];cal=model['calibration'];fig,axes=canvas('预测模型还不能部署','训练标签须在2018-07-01前已知；7月测试359单/18例延迟；Brier越低越好',axes=2,height=8.4)
    axes[0].barh(['固定训练事件率基线','逻辑回归'],[model['baseline_brier'],model['brier']],color=[TEAL,RED],height=.5)
    axes[0].set_xlim(0,.065);axes[0].set_xlabel('Brier分数');axes[0].grid(axis='x',alpha=.15)
    axes[1].plot([0,.25],[0,.25],ls='--',color='#a4b2bb',label='理想校准')
    axes[1].plot([x['mean_prediction'] for x in cal],[x['observed_rate'] for x in cal],'-o',color=TEAL,label='5个等频组')
    axes[1].set_xlabel('平均预测概率');axes[1].set_ylabel('实际延迟率');axes[1].set_xlim(0,.25);axes[1].set_ylim(0,.25);axes[1].legend(frameon=False)
    spec={'data':[{'type':'bar','x':['训练事件率基线','逻辑回归'],'y':[model['baseline_brier'],model['brier']],'name':'Brier','yaxis':'y'},
        {'type':'scatter','x':[x['mean_prediction'] for x in cal],'y':[x['observed_rate'] for x in cal],'mode':'lines+markers','name':'5组校准','xaxis':'x2','yaxis':'y2'},
        {'type':'scatter','x':[0,.25],'y':[0,.25],'mode':'lines','line':{'dash':'dash','color':'#a4b2bb'},'name':'理想校准','xaxis':'x2','yaxis':'y2'}],
        'layout':{'xaxis':{'domain':[0,1],'anchor':'y'},'yaxis':{'domain':[.66,1],'title':'Brier','rangemode':'tozero'},
        'xaxis2':{'domain':[0,1],'anchor':'y2','title':'预测概率'},'yaxis2':{'domain':[0,.4],'anchor':'x2','title':'实际延迟率'},'showlegend':False}}
    finish(16,fig,spec,'模型相对基线与校准','历史探索；排除86条未来才可用的训练标签；未证明实时特征冻结',model)
    hbar(17,'清洗必须看得到改变，也能够撤销','明确合成演示数据，非Olist原始缺陷；重复/冲突/无效值分别处理',
        ['原始行数','仅去完全重复后','可用于当前金额分析','隔离待核对','撤销恢复原始行数'],
        [12,11,cleaning['usable_rows'],cleaning['quarantined_rows'],12],'记录数',colors=[INK,TEAL,TEAL,GOLD,INK],rows=cleaning)
    # 18: genuine, conserved item-amount flow; Other is a group, never an individual ranking.
    totals_by=[]
    for key,n in [('buyer_state',5),('category',6),('seller_state',4)]:
        counts=defaultdict(Decimal)
        for row in r['flows']:counts[row[key]]+=Decimal(row['amount'])
        top=[k for k,v in sorted(counts.items(),key=lambda kv:-kv[1])[:n]]
        totals_by.append((key,top))
    nodes=[];mapped=[]
    for col,(key,top) in enumerate(totals_by):
        for name in top+['其他合计']:nodes.append((col,name))
    for row in r['flows']:mapped.append(([row[key] if row[key] in top else '其他合计' for key,top in totals_by],Decimal(row['amount'])))
    edge=defaultdict(Decimal);nt=defaultdict(Decimal)
    for names,amount in mapped:
        for col,name in enumerate(names):nt[(col,name)]+=amount
        for col in [0,1]:edge[((col,names[col]),(col+1,names[col+1]))]+=amount
    total=Decimal(r['kpis']['item_amount'])
    for col in [0,1,2]:assert sum(v for k,v in nt.items() if k[0]==col)==total
    fig,(ax,)=canvas('商品金额从哪里来，流向哪些品类与卖家州','已送达 · 686,136.43 BRL · 三列为同一金额的不同切面，不能将两段流量加总',height=10)
    ax.set_axis_off();ax.set_xlim(-.1,2.8);ax.set_ylim(-.03,1.08)
    scale=.77/float(total);loc={};w=.04
    for col in [0,1,2]:
        subset=[n for n in nodes if n[0]==col];cursor=1
        for i,node in enumerate(subset):
            h=float(nt[node])*scale;loc[node]=(col*1.25,cursor-h,cursor);cursor-=h+.025
            ax.add_patch(Rectangle((col*1.25,cursor+.025),w,h,fc=COLORS[i%len(COLORS)],ec='none'))
            label=CAT.get(node[1],node[1]);ax.text(col*1.25+.055,cursor+.025+h/2,label+f'  {float(nt[node])/1000:.1f}千',fontsize=8.8,va='center')
        ax.text(col*1.25,1.055,['买家州','商品品类','卖家州'][col],weight='bold',fontsize=12)
    used_out=defaultdict(float);used_in=defaultdict(float)
    for (a,b),value in sorted(edge.items(),key=lambda kv:(nodes.index(kv[0][0]),nodes.index(kv[0][1]))):
        x0,bot0,top0=loc[a];x1,bot1,top1=loc[b];h=float(value)*scale
        y0=top0-used_out[a];y1=top1-used_in[b];used_out[a]+=h;used_in[b]+=h
        verts=[(x0+w,y0),(x0+.65,y0),(x1-.6,y1),(x1,y1),(x1,y1-h),(x1-.6,y1-h),(x0+.65,y0-h),(x0+w,y0-h),(x0+w,y0)]
        path=MPath(verts,[MPath.MOVETO,*([MPath.CURVE4]*3),MPath.LINETO,*([MPath.CURVE4]*3),MPath.CLOSEPOLY])
        ax.add_patch(PathPatch(path,fc=COLORS[nodes.index(a)%len(COLORS)],alpha=.16,ec='none',zorder=0))
    links=list(edge)
    rgba=lambda color:'rgba('+','.join(str(int(color[i:i+2],16)) for i in [1,3,5])+',0.23)'
    spec={'data':[{'type':'sankey','arrangement':'freeform','textfont':{'color':INK,'size':12},
        'node':{'pad':22,'thickness':12,'line':{'width':0},
        'label':[['买家·','品类·','卖家·'][col]+CAT.get(name,name)+f' · {float(nt[(col,name)])/1000:.1f}千' for col,name in nodes],
        'x':[.005 if col==0 else .5 if col==1 else .995 for col,name in nodes],
        'y':[1-(loc[n][1]+loc[n][2])/2 for n in nodes],
        'color':[COLORS[i%len(COLORS)] for i in range(len(nodes))]},'link':{'source':[nodes.index(a) for a,b in links],
        'target':[nodes.index(b) for a,b in links],'value':[float(edge[e]) for e in links],
        'color':[rgba(COLORS[nodes.index(a)%len(COLORS)]) for a,b in links]},'valueformat':',.2f','valuesuffix':' BRL'}],
        'layout':{'height':850,'margin':{'t':110,'l':40,'r':70,'b':50}}}
    finish(18,fig,spec,'商品金额流向：买家州 → 品类 → 卖家州','每列合计686,136.43 BRL；节点标值为千BRL；其余合计保留；不是物流路线或利润',
        [{'source':list(a),'target':list(b),'amount':str(edge[(a,b)])} for a,b in links])
    save_json(OUT/'chart-manifest.json',charts)
    return charts
