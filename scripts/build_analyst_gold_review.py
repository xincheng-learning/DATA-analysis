"""Build the teacher's draft review pack; no production Agent imports or paid calls.

Run build_analyst_gold_reference.py first, then this script with ml-pytorch Python.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import mistune
from render_analyst_gold_charts import render

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/analyst-gold-v3-review-2026-11-11'
GOLD=ROOT/'evals/analyst_gold_v3_draft'
SOURCE=ROOT/'.local/ecommerce/demo'
md=mistune.create_markdown(escape=True,plugins=['table'])


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def money(x):return f'{float(x):,.2f}'
def pct(x):return f'{100*float(x):.2f}%'


def table(headers,rows):
    def cell(x):return str(x).replace('|','\\|').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(map(cell,headers))+' |','|'+'---|'*len(headers)]+['| '+' | '.join(map(cell,row))+' |' for row in rows])


TABLES={
 'orders':('订单表','一行一笔订单','order_id',{'order_id':'订单标识','customer_id':'本订单客户记录标识','order_status':'订单状态',
 'order_purchase_timestamp':'下单时间','order_approved_at':'审核通过时间','order_delivered_carrier_date':'交给承运商时间',
 'order_delivered_customer_date':'送达客户时间','order_estimated_delivery_date':'预计送达日期'}),
 'customers':('客户表','一行一个订单使用的客户记录；复购标识可跨记录重复','customer_id',{'customer_id':'客户记录标识',
 'customer_unique_id':'跨订单客户去重标识','customer_zip_code_prefix':'客户邮编前缀','customer_city':'客户城市','customer_state':'客户州代码'}),
 'order_items':('订单明细表','一行一个订单中的商品明细项','order_id + order_item_id',{'order_id':'订单标识','order_item_id':'订单内明细序号',
 'product_id':'商品标识','seller_id':'卖家标识','shipping_limit_date':'卖家发货截止时间','price':'商品明细金额 / BRL','freight_value':'分摊到明细的运费 / BRL'}),
 'payments':('支付记录表','一行一次订单支付记录；一单可以多次或多方式支付','order_id + payment_sequential',{'order_id':'订单标识',
 'payment_sequential':'订单内支付序号','payment_type':'支付方式','payment_installments':'分期期数','payment_value':'本条支付金额 / BRL'}),
 'reviews':('评价表','一行一条评价记录；同一订单可有多条','本轮不假定review_id全表唯一',{'review_id':'评价标识','order_id':'订单标识',
 'review_score':'评价分数 / 1—5分','review_comment_title':'评价标题','review_comment_message':'评价正文',
 'review_creation_date':'评价创建时间','review_answer_timestamp':'评价回复时间'}),
 'products':('商品表','一行一个商品标识','product_id',{'product_id':'商品标识','product_category_name':'葡萄牙语品类名',
 'product_name_lenght':'商品名称字符数（源列保留lenght拼写）','product_description_lenght':'商品描述字符数（源列保留lenght拼写）',
 'product_photos_qty':'商品照片数','product_weight_g':'商品重量 / g','product_length_cm':'商品长度 / cm','product_height_cm':'商品高度 / cm','product_width_cm':'商品宽度 / cm'}),
 'sellers':('卖家表','一行一个卖家','seller_id',{'seller_id':'卖家标识','seller_zip_code_prefix':'卖家邮编前缀','seller_city':'卖家城市','seller_state':'卖家州代码'}),
 'category_translation':('品类翻译表','一行一组葡萄牙语品类名与英文译名','product_category_name',{'product_category_name':'葡萄牙语品类名','product_category_name_english':'英文品类名'})}


def dictionary(r):
    sections=[]
    impacts={'order_approved_at':'7单缺审核，不能计算这7单的审核耗时；已送达范围不受影响。',
        'order_delivered_carrier_date':'93单缺发货时间；另有14单早于下单、82单早于审核、1单晚于送达，先标记核查。',
        'order_delivered_customer_date':'160单缺送达时间；不能据此把全部非已送达订单判为延迟。',
        'review_comment_title':'标题是可选文本，空标题不影响已存在评分的统计。',
        'review_comment_message':'正文缺失影响文本分析覆盖，评分仍可使用。',
        'product_category_name':'保留未知组，不能丢弃相关明细金额。'}
    for p in r['profile']:
        name=p['table'];label,grain,key,fields=TABLES[name]
        assert set(fields)=={f['column'] for f in p['fields']},name
        rows=[]
        for f in p['fields']:
            column=f['column'];typ='文本/标识'
            if column.endswith(('_date','_timestamp','_at')):typ='衍生为日期时间'
            if column in ['price','freight_value','payment_value']:typ='衍生为DECIMAL(18,2)'
            if column in ['review_score','order_item_id','payment_sequential','payment_installments'] or column.startswith(('product_weight','product_length','product_height','product_width','product_photos','product_name_l','product_description_l')):typ='衍生为数值'
            note=impacts.get(column,'涉及该属性的分析保留未知；不影响无关指标。') if f['missing'] else '—'
            rows.append([name+'.'+column,fields[column],typ,f['example'] or '空',f"{f['missing']:,} / {p['rows']:,}（{f['missing_pct']:.2f}%）",note])
        sections.append(f'### {name} · {label}\n\n{grain}；候选键：{key}；{p["rows"]:,}行。标识和邮编按文本保留，日期/数值仅在分析副本转换。\n\n'+
            table(['原字段（含所属表）','中文含义','分析类型','实际非空样例','空值 / 本表行数','空缺影响与处理'],rows))
    return '\n\n'.join(sections)


def synthetic_cleaning():
    folder=OUT/'cleaning';folder.mkdir(exist_ok=True)
    # Deliberately authored faults; these records must never be presented as defects in Olist.
    rows=[['S01','120,00','2018-06-01','SP'],['S02','85,50','2018-06-02','RJ'],['S03','200,00','2018-06-03','MG'],
        ['S04','12x','2018-06-04','SP'],['S05','','2018-06-05','PR'],['S06','30,00','2018-02-30','BA'],
        ['S07','1.234,56','2018-06-07','SP'],['S08','45,00','2018-06-08',' sp '],['S09','-10,00','2018-06-09','RS'],
        ['S10','60,00','2018-06-10','SC']]
    rows+=[rows[1].copy(),['S03','210,00','2018-06-03','MG']]
    fields=['order_id','amount_pt_BR','purchase_date','state']
    def encode(items,columns):
        s=io.StringIO(newline='');w=csv.writer(s);w.writerow(columns);w.writerows(items);return s.getvalue().encode('utf-8-sig')
    before=encode(rows,fields);(folder/'before.csv').write_bytes(before)
    unique=list(dict.fromkeys(tuple(row) for row in rows));counts=Counter(row[0] for row in unique)
    preview=[];after=[]
    for original in unique:
        oid,raw,date,state=original;issues=[]
        if counts[oid]>1:issues.append('同键金额冲突：保留两行待核对')
        amount=None
        if not raw:issues.append('原始金额为空')
        elif not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}',raw):issues.append('金额无法按已确认pt-BR格式解析')
        else:
            amount=Decimal(raw.replace('.','').replace(',','.'))
            if amount<0:issues.append('负金额业务含义未确认')
        try:datetime.strptime(date,'%Y-%m-%d')
        except ValueError:issues.append('不存在的日期')
        clean_state=state.strip().upper()
        preview.append({'order_id':oid,'raw':dict(zip(fields,original)),'amount_clean':str(amount) if amount is not None else None,
            'state_clean':clean_state,'issues':issues,'usable':not issues})
        after.append([*original,str(amount) if amount is not None else '',clean_state,'；'.join(issues),'可用' if not issues else '隔离待核对'])
    after_bytes=encode(after,fields+['amount_clean','state_clean','issues','analysis_status'])
    # This is an independently executed teaching transaction, not a receipt from the production Agent.
    current=folder/'working-copy.csv';current.write_bytes(before)
    write_json(folder/'preview.json',{'synthetic':True,'confirmed_locale':'pt-BR','removed_exact_duplicate_extras':1,'rows':preview})
    write_json(folder/'teacher-confirmation.json',{'actor':'independent_teacher_script','simulation':True,
        'confirmed':['仅删除1条完全重复','在副本新增解析列和州规范列','6行保留并标记待核对','不补空金额、不猜无效日期、不擅自定退款'],'production_user_confirmation':False})
    current.write_bytes(after_bytes);(folder/'after.csv').write_bytes(current.read_bytes())
    current.write_bytes(before);(folder/'undo.csv').write_bytes(current.read_bytes())
    usable=sum(row['usable'] for row in preview)
    assert len(rows)==12 and len(unique)==11 and usable==5 and len(unique)-usable==6
    assert current.read_bytes()==before
    assert next(row for row in preview if row['order_id']=='S07')['amount_clean']=='1234.56'
    assert next(row for row in preview if row['order_id']=='S08')['state_clean']=='SP'
    assert next(row for row in preview if row['order_id']=='S05')['amount_clean'] is None
    summary={'synthetic':True,'before_rows':12,'after_rows':11,'usable_rows':usable,'quarantined_rows':11-usable,
        'usable_amount_BRL':str(sum(Decimal(row['amount_clean']) for row in preview if row['usable'])),
        'before_sha256':hashlib.sha256(before).hexdigest(),'undo_sha256':hashlib.sha256(current.read_bytes()).hexdigest(),
        'after_sha256':hashlib.sha256(after_bytes).hexdigest(),'undo_exact':True,'rows':preview}
    write_json(folder/'checks.json',summary);return summary


def make_cases(r,clean):
    cases=[];k=r['kpis'];mov=r['movements'];st=r['statistics'];model=r['model']
    def add(num,title,scope,steps,qa,hard,sql,assertions):
        assert len(qa)==3
        cases.append({'id':f'AG3{num:02d}','title':title,'review_status':'pending','approved_for_training':False,
            'role':'teacher_candidate_not_agent_result','scope':scope,'teacher_steps':steps,
            'turns':[{'turn':i+1,'question':q,'reference_answer':a} for i,(q,a) in enumerate(qa)],
            'evidence':sql,'numeric_assertions':assertions,'hard_failures':hard,
            'scoring':{'正确性':20,'问题覆盖':20,'理由与证据':20,'专业表达':20,'图表可交付性':20},
            'acceptance':'事实、方法与口径一致即可；不要求逐字复制。严重方法或事实错误不能用排版分数抵消。'})
    add(1,'认识数据：逐表理解字段与关联',
        '2016—2018年Olist客户抽样；8表47字段；币种BRL；先介绍数据，不执行清洗。',
        ['核对8张原CSV哈希、行数和47列','先明确订单、明细、支付、客户标识粒度','逐表给字段翻译、类型、实际样例和本表空值','用键集合核验关联，而不是比较行数猜丢失'],[
        ('我刚拿到这个电商数据集，介绍一下它，告诉我应该怎么开始。',
         '这是一份巴西电商历史客户样本，适合练习订单经营、商品结构、复购与履约分析。它包含5,169笔订单，已送达5,008笔；不是当前业务，也不代表整个巴西市场。\n\n'+
         table(['数据表','行数','一行代表什么'],[[p['table'],p['rows'],TABLES[p['table']][1]] for p in r['profile']])+
         '\n\n先以orders为订单主表；商品金额来自order_items.price，支付来自payments.payment_value，两侧都先汇总到订单，再关联。下面逐表把字段、中文含义和缺失放在一起，方便你直接回到原表理解。\n\n'+dictionary(r)),
        ('customers也是5,169行，那客户是不是5,169人？',
         '不能这样理解。customers.customer_id对应订单使用的客户记录；跨订单识别复购要用customer_unique_id。原抽样选了5,000个去重标识，其中已送达购买客户有4,851个。这里的“客户”始终指数据中的去重标识，没有核验它是否等于自然人数。'),
        ('那我先看经营和履约，你会怎么安排？',
         '先固定两套可复用口径：经营看已送达订单、按下单月、商品金额不含运费；履约比较实际送达日与预计送达日。随后按数据体检→月度经营→地域/品类/卖家→复购留存→履约评价推进。先交付这些可靠结果，再决定是否需要预测模型。')],
        ['把customer_id当自然人或复购键','仅按行数不同断言丢失关联','漏表、漏字段或捏造源列'],
        ['reference-results.json','sql/01-order-coverage.sql','data/01-order-coverage.csv'],
        {'tables':8,'fields':47,'orders':5169,'delivered_orders':5008})
    add(2,'真实数据体检：缺失与清洗决策',
        '原表只读；空字符串视为空；可选文本空缺与业务事实缺失分别处理。',
        ['逐表测空值与完全重复','结合订单状态理解时间字段缺失','保留评价历史；分析副本按明确规则取最新','给出影响范围和清洗建议，未经执行确认不写回'],[
        ('这批数据质量怎么样？哪些地方需要清洗？',
         '可以开展经营分析，但需要保留几类信息缺口；当前没有发现完全相同的重复行，没必要先全表删重。\n\n'+table(['所属表','实际发现','建议处理'],[
          ['orders（5,169行）','审核7空、发货93空、送达160空','先按状态核对；已送达5,008单这三列均完整，不插补业务时间。'],
          ['products（3,883行）','品类62空；重量与长高宽各1空','品类保留未知，尺寸分析只用完整记录并披露覆盖。'],
          ['reviews（5,173行）','标题4,572空；正文2,992空','评分不因文本空而删除；做文本分析另报覆盖。'],
          ['reviews','覆盖5,134单，多出39条历史评价','保留原记录；需要一单一评分时派生最新评价视图。']])+
         '\n\n另外，时间顺序有3类真实异常标记：发货早于下单14单、发货早于审核82单、发货晚于送达1单，规则可能重叠，不能相加当异常订单数。它们影响相应阶段耗时，原值保留并导出核查；下单到送达与是否超预计日则分别检查自己的日期条件，不能全表一删了之。类型解析未发现非法非空值，7组候选业务键未发现重复。\n\n这些问题不需要“一键补齐”。优先建立类型明确、来源可追溯的分析副本，让每一步保留前后差异。'),
        ('那你直接把缺失的时间和商品重量都补成0吧。',
         '不建议应用这条规则。orders里的空时间不能表示“0时刻”；products.product_weight_g的1条空值也不表示零克。这样会制造异常时长和失真的运费关系。我会保留空值：经营金额继续计算；时长/重量分析只在所需字段可用的记录上计算，并列明排除数量。'),
        ('同一订单多条评价，怎么确定取哪一条才稳定？',
         '原始评价一条不删。订单级分析按review_answer_timestamp降序，再按review_creation_date、review_id降序，最后按源行号升序取一条，处理时间平局。本轮得到5,134个有评价订单；多出的39条仅在该派生视图中不参与重复计算，不能把它们直接定性为脏数据。')],
        ['无授权实际补零/删记录','把39条额外评价说成39条完全重复','把全部160个未送达时间空值叫延迟'],
        ['reference-results.json','sql/12-review-counts.sql','data/12-review-counts.csv','data/19-chronology-flags.csv','data/20-quality-controls.json'],
        {'exact_duplicate_extras':0,'raw_reviews':5173,'reviewed_orders':5134,'extra_reviews':39,'chronology_flags':r['quality_controls']['chronology_counts']})
    add(3,'缺失交集与有效分母',
        'A=审核时间非空，B=发货时间非空；必须同时区分全部订单与已送达。',
        ['计算审核/发货缺失的互斥组合','核对交集、并集及样本状态','按每个指标实际需要确定分母'],[
        ('审核时间缺失7单，发货时间缺失93单，两列都有才能分析的话，剩多少单？',
         '全部5,169单中，两列都有值的是5,076单。不能直接扣7+93，因为缺审核的7单全部也缺发货。\n\n'+table(['审核时间','发货时间','订单数'],[['缺失','缺失',7],['有值','缺失',86],['缺失','有值',0],['有值','有值',5076]])+
         '\n\n因此按非空条件排除的是93笔不同订单，5,169−93=5,076。这个数只说明两列有值；若要计算审核到发货耗时，还要处理已标记的时间倒序，非空不代表先后顺序正确。'),
        ('所以同时要求两个字段，比只要求发货字段严格地少一些，对吧？',
         '本样本里数量相同，都是5,076单。一般情况下增加条件可能减少样本，但这里“发货有值”的订单也都有审核时间，所以交集没有进一步缩小。不能把“不会增加”说成“一定减少”。'),
        ('如果我只分析已送达呢，前面的分母还适用吗？',
         '要重新切换范围。已送达有5,008单，审核、发货、送达时间都完整，因此同时要求审核和发货仍保留5,008单。前面的5,076是全部状态的范围，不能直接用于已送达履约指标。')],
        ['缺失相加得到5069','把交集必然严格缩小说成定理','切范围后继续沿用旧分母'],
        ['sql/10-missing-patterns.sql','sql/11-missing-scope.sql','data/11-missing-scope.csv'],
        {'all_orders':5169,'approval_missing':7,'carrier_missing':93,'missing_both':7,'jointly_nonnull':5076,'delivered_jointly_nonnull':5008})
    add(4,'经营概览：指标清楚且完整',
        '全部已送达；按下单月；商品金额不含运费，不等于净收入/利润。',
        ['订单、明细、支付先按正确粒度整理','分别算订单数、客户数、商品、运费、支付','趋势用独立单位小多图','解释覆盖和金额边界'],[
        ('给我做经营概览：金额、订单、客单和客户都要看，也给我趋势图。',
         '这份样本共有5,008笔已送达订单，商品金额686,136.43 BRL，每单商品金额137.01 BRL。\n\n'+table(['指标','结果','口径'],[
          ['已送达订单',5008,'order_id去重'],['购买客户',4851,'customer_unique_id去重'],['商品金额','686,136.43 BRL','明细price合计，不含运费'],
          ['运费','112,905.81 BRL','明细freight_value合计'],['支付金额','799,188.28 BRL','支付记录合计'],['每单商品金额','137.01 BRL','商品金额÷5,008单']])+
         '\n\n趋势图选择2018年1—7月作连续展示：六月回落，七月恢复。商品金额与订单数分别画在两个小图中，避免把BRL与单数放在同一轴。全期明细可查看对应月度CSV；没有把未出现月份自动补成零。'),
        ('799,188.28减掉运费，不就等于商品金额吗？',
         '汇总上并不完全相等：支付减商品再减运费，净差额是146.04 BRL。差异需要逐单核对，不能强行改平。后续会按精确到分的金额筛选|差额|>0.01 BRL的13笔订单；支付也不能直接当作会计净收入。'),
        ('能不能直接告诉我利润多少？',
         '现有数据能算商品、运费和支付规模，不能算利润。缺采购成本、平台费用、履约成本和退款净额；把686,136.43 BRL称为利润会误导。你若提供这些成本和退款表，我会先确认归属日期与订单关联，再计算毛利或贡献利润。')],
        ['把支付当利润/净收入','遗漏问题中的客户或客单','混合单位共轴或空月份补零'],
        ['sql/02-kpis.sql','sql/03-monthly.sql','data/03-monthly.csv'],
        {'item_amount_BRL':'686136.43','orders':5008,'customers':4851,'freight_BRL':'112905.81','payment_BRL':'799188.28'})
    for num,m,title in [(5,mov[0],'下滑诊断：五月到六月'),(6,mov[1],'增长诊断：六月到七月')]:
        isdrop=num==5
        q='六月比五月为什么少了？金额和订单分别变了多少，能拆开看吗？' if isdrop else '七月比六月好转了吗？金额、订单和客单都要说清楚。'
        lead='六月的下滑主要体现为订单减少，客单提升只抵消了一部分损失。' if isdrop else '七月商品金额恢复增长，订单量提升是主要算术来源，客单回落抵消了一部分增量。'
        first=(lead+'\n\n'+table(['指标',m['before'],m['after'],'变化'],[
            ['商品金额 / BRL',money(m['before_amount']),money(m['after_amount']),money(m['change'])+'（'+pct(m['change_rate'])+'）'],
            ['订单数',m['before_orders'],m['after_orders'],pct(m['order_change_rate'])],
            ['每单商品金额 / BRL',money(m['before_aov']),money(m['after_aov']),pct(m['aov_change_rate'])]])+
            f"\n\n采用对称分解，订单量效应为{money(m['quantity_effect'])} BRL，每单金额效应为{money(m['order_value_effect'])} BRL，合计{money(m['change'])} BRL。它解释金额差额如何构成；尚不能据此认定流量、促销或定价造成变化。")
        add(num,title,'已送达；按下单月；BRL，不含运费；延续上一轮经营口径。',
            ['提取同口径两月值','计算差额、变化率与每单金额','用对称量价分解消除先后顺序影响','勾稽效应和=总差额；进一步原因需新证据'],[
            (q,first),
            ('你这个分解是怎么算的？不要只给两个数字。',
             '设订单数为Q，每单商品金额为P，则金额=Q×P。订单量效应=(Q₁−Q₀)×(P₀+P₁)/2；每单金额效应=(P₁−P₀)×(Q₀+Q₁)/2。两项精确相加等于本期金额减前期金额，计算使用未四舍五入的P，最后展示到两位小数。'),
            ('那是流量问题还是运营做得好/不好？',
             '目前能确认的是订单量与每单金额的变动，尚未测到访问量、转化率、活动曝光或价格策略。下一步先按买家州、品类和卖家拆订单与金额，再接入渠道访问和活动记录，区分流量下降、转化变化与商品组合变化。现有金额分解不能代替这些业务原因的验证。')],
            ['用变化后分母算环比','把客单称单件价格','量价效应不勾稽','把算术贡献叫因果'],
            ['sql/03-monthly.sql','data/03-monthly.csv','reference-results.json'],
            {'change_BRL':m['change'],'change_rate':m['change_rate'],'quantity_effect_BRL':m['quantity_effect'],'aov_effect_BRL':m['order_value_effect']})
    add(7,'地域贡献：真实个体排名与其他合计',
        '六月→七月已送达商品金额；地域指买家州；25州在所选两月至少有一单。',
        ['按州汇总两期金额和订单','计算带正负的金额差额','全州差额勾稽8,107.21','图中Top10按绝对差额选择，Other不参与单州排名'],[
        ('七月的增长主要来自哪里？给我前三个拉动地区，也说一下拖累。',
         '买家州中，PR、MA和MG是金额增量最大的三个州；CE是最大的抵消项。\n\n'+table(['买家州','六月金额 / BRL','七月金额 / BRL','差额 / BRL','订单变化'],[
          ['PR','1,314.88','3,576.33','+2,261.45','13→22'],['MA','0.00','1,349.19','+1,349.19','0→4'],
          ['MG','3,976.32','5,110.25','+1,133.93','32→35'],['CE','2,789.99','992.80','−1,797.19','2→3']])+
         '\n\n全部州合计贡献8,107.21 BRL。CE订单略增但金额下降，说明不能只用订单变化替代金额诊断；这个州样本只有2→3单，要优先看明细而非放大为长期趋势。'),
        ('图里“其余15州”比某些州还大，为什么没排第一？',
         '“其余15州”是为保留总额而合并的分组，不是一个州。单州增量排名来自全部25个实际州；图按绝对变动展示前10州，剩余15州只列合计，不能拿这个合计抢占个体第一。'),
        ('MA六月是零，增长率是多少？',
         '在所选已送达样本里，MA六月0单、商品金额0.00，七月4单、1,349.19 BRL。基期为零，普通环比增长率不定义；可以报告新增4单、增加1,349.19 BRL，不能写“增长100%”或“无限增长”。')],
        ['把Other当单州第一','把CE说成订单下降','基期零写普通增长率'],
        ['sql/04-state-change.sql','data/04-state-change.csv'],{'total_change_BRL':'8107.21','top_positive':['PR','MA','MG'],'largest_negative':'CE'})
    cats=r['categories'];top3=sum(Decimal(x['amount']) for x in cats[:3]);unknown=next(x for x in cats if x['category']=='未知/未译')
    add(8,'品类结构：完整金额与未知分组',
        '已送达商品明细price；葡萄牙语品类左关联英文翻译；未知与未译展示合并。',
        ['商品及翻译用左关联，测未匹配','按品类汇总明细金额','保留未知/未译组并与总额勾稽','品类订单数独立去重，不跨品类累加'],[
        ('哪些品类贡献大？数据里没分类的商品会不会被漏掉？',
         f'头三类是健康美容、钟表礼品、运动休闲，合计{money(top3)} BRL，占已送达商品金额{pct(top3/Decimal(k["item_amount"]))}。\n\n'+
         table(['品类','商品金额 / BRL','该品类订单数'],[[x['category'],money(x['amount']),x['orders']] for x in cats[:5]])+
         f'\n\n未知/未译保留为单独分组：{money(unknown["amount"])} BRL，涉及77条明细、72笔去重订单。全部65个展示组金额合计686,136.43 BRL，没有为图好看而丢弃未知金额。'),
        ('为什么有“未知/未译”？是不是商品表根本没有关联上？',
         '不是一回事。订单明细的product_id都能匹配商品表；商品表本身有62条品类为空，另有1条非空葡萄牙语品类在翻译表未匹配。展示时合并为“未知/未译”，原始原因仍保留在关联检查里；不能用两张表的行数差来判断。'),
        ('把各品类订单相加能核对5,008单吗？',
         '不能保证。一个订单可以包含多个品类，因此各品类的去重订单数可能重叠；它用于看该品类覆盖多少订单。能够跨品类加总勾稽的是明细商品金额。需要订单总数时，回到全体order_id集合去重。')],
        ['内关联删除未知金额','品类订单数相加当总订单','混淆产品缺失和翻译未匹配'],
        ['sql/05-categories.sql','data/05-categories.csv','reference-results.json'],{'groups':65,'unknown_amount_BRL':'10179.54','sum_BRL':'686136.43'})
    worst=sorted([x for x in r['sellers'] if x['eligible']>=30],key=lambda x:x['late_rate'],reverse=True)[0]
    add(9,'卖家表现：金额与履约分开评价',
        '已送达；按seller_id；一卖家一订单去重；整单交付结果非卖家责任归因。',
        ['金额在明细粒度归卖家','履约在seller_id×order_id去重后计算','示例门槛30单，20位卖家达标','报告Wilson区间；不做未经检验的显著排名'],[
        ('帮我找出需要关注的卖家，别把某个州当作卖家。',
         '金额最高的卖家是4a3ca931…，商品金额11,904.90 BRL，覆盖107单，其中12单延迟，延迟率11.21%。\n\n'+
         f'以“至少30笔可判断交付订单”为本次筛选门槛，共20位卖家进入比较。延迟率最高的是{worst["seller_id"]}，5/38单延迟，13.16%，95% Wilson区间5.75%—27.33%。\n\n这些卖家适合优先核查订单明细，但不能据此宣布谁的服务显著最差；区间较宽，且同一订单多卖家共享整单送达结果。'),
        ('那只有1单、还延迟了的卖家，为什么不直接说他最差？',
         '1/1虽然点估计100%，样本信息很少。可以列入异常排查清单，但不能和几十上百单卖家按同一可信程度排名。30单是本轮可调整的筛选门槛，不是证明可靠的统计定律；仍需要区间和实际订单背景。'),
        ('能否据此扣罚卖家？',
         '现有数据不足以支持扣罚。这里关联的是整单送达是否超过预计日期，可能包含承运、跨州运输和多卖家协同影响。要判断责任，需补充逐包裹承运轨迹、卖家承诺/实际交接、异常原因和合同口径。先把这些订单导出供人工核查。')],
        ['用seller_state代替seller_id','1/1直接评最差','把整单交付相关当卖家责任'],
        ['sql/06-sellers.sql','data/06-sellers.csv'],{'eligible_sellers_n30':20,'worst_eligible_id':worst['seller_id'],'worst_late':5,'worst_orders':38})
    add(10,'SQL纠错：多表关联放大',
        '已送达订单；商品明细与支付记录均为一对多；截图可用于定位，真实SQL与执行结果用于验证。',
        ['识别两侧明细的关联粒度','实际执行错误关联并与正确汇总对比','明细和支付分别按订单聚合','检查订单覆盖与总额；禁止SUM(DISTINCT amount)伪修复'],[
        ('我把order_items和payments按order_id直接join后求和，数字不太对，哪里错了？',
         '问题出在两侧都是明细：一单若有M条商品、N条支付，直接关联会产生M×N行，商品和支付都会被重复。这个样本实际得到：\n\n'+
         table(['指标','正确金额 / BRL','直接关联 / BRL','多算 / BRL'],[['商品','686,136.43','706,289.97','20,153.54'],['支付','799,188.28','1,005,341.23','206,152.95']])+
         '\n\n修复方式是order_items和payments各自先GROUP BY order_id，再左关联到订单表。对5008笔已送达订单核对覆盖与总额，才能证明修复有效。'),
        ('那SUM(DISTINCT price)是不是就能去重？',
         '不能。不同商品项完全可能价格相同，SUM(DISTINCT price)会把合法同价交易合并，造成少算。去重依据必须是业务键或明确的记录规则；这里应先按订单汇总两张明细表，解决关联粒度，而不是按金额值去重。'),
        ('我只发截图给你，能检查吗？',
         '可以先根据截图里看得清的表、关联键和聚合方式定位嫌疑，并指出哪一段需要改。要验证改后金额，我还需要可执行SQL和对应表；截图被截断、列名看不清时会具体问缺失部分。不能仅凭截图就声称已经执行成功或对账通过。')],
        ['SUM(DISTINCT price)当修复','凭截图声称执行成功','只改SQL不核对覆盖和金额'],
        ['sql/09-join-inflation.sql','sql/00-reference-views.sql','data/09-join-inflation.csv'],{'naive_item_BRL':'706289.97','naive_payment_BRL':'1005341.23','joined_rows':5850})
    add(11,'订单收付核对：精确到分的边界',
        '差额=支付−商品−运费；DECIMAL；绝对差额严格大于0.01 BRL才入重点清单。',
        ['先聚合每订单商品/运费/支付','使用十进制定点值计算差额','逐单严格筛选一分钱边界','保留差额清单，补业务凭证而不改原始金额'],[
        ('支付和商品加运费对得上吗？请给我需要处理的订单，不要误报一分钱。',
         '5,008笔已送达订单的支付−商品−运费，净差额合计146.04 BRL。按|差额|>0.01 BRL筛选，13笔订单进入重点核查清单，清单差额合计146.09 BRL；其余订单小额差异净计−0.05 BRL，所以清单和全体净额并不相同。\n\n'+
         table(['订单（前8位，完整ID见CSV）','差额 / BRL'],[[x['order_id'][:8]+'…',money(x['difference'])] for x in r['reconciliation']['orders'][:5]])+
         '\n\n我使用十进制金额，恰好0.01不会被浮点误差推入清单。差额图与CSV可逐单追溯；暂不把差额解释成超收、退款或数据错误。'),
        ('为什么以前会出现20单，这次是13单？',
         '如果用二进制浮点比较，理论上一分钱可能计算成略大于0.01，被错误纳入“严格大于一分钱”。本轮用CSV Decimal与SQL DECIMAL(18,2)独立核对，结果都是13单。不能为了贴旧数字放宽口径，也不能只看显示到两位小数后的值。'),
        ('能不能把多出的支付改平，让报表好看一点？',
         '不应改平原始金额。先对这13单补查优惠券、分期、结算及退款凭证，再决定差异归属。报表保留原支付、商品、运费和差额四列，这样既能追账，也不会把尚未解释的差额悄悄吞掉。')],
        ['float严格比较误报20单','阈值改为>=0.01而不说明','擅自改平支付','断言146.04是确认超收'],
        ['sql/08-reconciliation.sql','data/08-reconciliation.csv','reference-checks.json'],{'material_orders':13,'net_difference_BRL':'146.04','material_difference_BRL':'146.09','remainder_difference_BRL':'-0.05','threshold':'abs(diff)>0.01'})
    add(12,'支付结构：可加金额与不可加订单',
        '全部已送达订单关联原支付记录；按方式求支付金额和方式内去重订单。',
        ['确认多次支付与多方式支付均可能存在','金额按记录求和','每方式订单独立去重','核对金额总计799188.28，订单集合去重5008'],[
        ('客户都用什么支付方式？金额和订单数都给我。',
         '信用卡是主要支付方式，占支付金额79.40%；其次为Boleto付款单。\n\n'+
         table(['支付方式','支付金额 / BRL','金额占比','方式内去重订单'],[[x['payment_type'],money(x['amount']),pct(Decimal(x['amount'])/Decimal(k['payment_amount'])),x['orders']] for x in r['payments']])+
         '\n\n金额可以跨方式相加，总计799,188.28 BRL；订单列是每种方式覆盖的订单集合，存在跨方式重叠，不能直接加成总订单。'),
        ('这四个订单数相加是5,109，和5,008冲突了吗？',
         '不冲突。差额101是方式内订单数之和超出全体去重订单的计数，来自同单使用多种方式。它不能直接解释成“101个多方式订单”，因为单个订单可能贡献不止一个重复计数。要得多方式订单数，应逐单COUNT(DISTINCT payment_type)>1。'),
        ('那能说明信用卡用户更有价值吗？',
         '目前只证明信用卡覆盖金额较大。要比较客户价值，需要固定customer_unique_id、观察窗、复购与净额口径，并处理一个客户多方式使用的归属；支付金额大不等于利润高，也不能证明支付方式造成高价值。')],
        ['把5109叫总订单','把101叫多方式订单数','支付占比当客户占比或因果'],
        ['sql/07-payments.sql','data/07-payments.csv'],{'payment_BRL':'799188.28','sum_type_distinct_orders':5109,'union_orders':5008})
    total_seg=sum(x['customers'] for x in r['segments']);seg_repeat=sum(x['customers'] for x in r['segments'] if x['frequency_group']=='复购')
    add(13,'客户复购与RF分层＋金额',
        '全期复购与2018-08-01截点分层分开；90天阈值为本轮规则。',
        ['全期按客户去重标识统计订单次数','分层先限制截止日期再计算最近购买距今天数/次数/金额','明确一次购买不等于永远不复购','分层不是全RFM打分或真实自然人画像'],[
        ('客户复购怎么样？帮我做一个能用于运营的分层。',
         f'全期已送达购买客户4,851个，其中145个至少购买两单，观察到的复购客户占比{pct(145/4851)}。这是样本观察窗内的结果。\n\n运营分层主动选2018-08-01为截点，先只取此前订单，再按近90天活跃/超过90天未购与一次购买/复购交叉分组：\n\n'+
         table(['最近购买状态','频次','客户标识数','商品金额 / BRL'],[[x['recency'],x['frequency_group'],x['customers'],money(x['amount'])] for x in r['segments']])+
         f'\n\n截点分层共{total_seg:,}个客户，其中复购{seg_repeat}个；与全期4,851/145的差异来自观察范围变化。金额作为补充描述，不把它包装成已经建立的RFM评分。'),
        ('一次购买的就当流失客户，发券挽回吧？',
         '一次购买只说明当前观察窗内出现一次，不足以判断流失。可以先把“超过90天未购且一次购买”作为候选触达组，共3,482个客户标识；再结合许可、触达成本和品类购买周期设计小规模实验，不直接给所有人发券。'),
        ('你说近90天，是拿现在2026年算的吗？',
         '不是。这里以2018-08-01为固定历史截点：最近一笔下单日期到截点的天数≤90为活跃；只用截点前已送达订单。这避免把所有历史客户按2026年都标成不活跃，也让结果能够复算。')],
        ['两个观察窗混用','客户标识叫自然人','一次购买直接流失','RF+金额冒称完整RFM模型'],
        ['sql/14-customer-segments.sql','sql/15-repeat.sql','data/14-customer-segments.csv'],
        {'all_period_customers':4851,'repeat_customers':145,'cutoff_customers':total_seg,'cutoff_repeat':seg_repeat})
    add(14,'留存分析：新客定义与成熟窗口',
        '样本内首次已送达购买月；2018年1—6月cohort；M0—M3；主动截止2018-08-01。',
        ['先在截点前历史中找每客户首次购买月','固定每cohort分母','逐月去重回购客户','超出定义观察窗置NULL，不补零'],[
        ('给我做客户留存，哪些月份比较差？',
         '这批样本的月度复购很少，但不能把尚未观察到的月份算成零。以样本内首次已送达购买月分组：2018年1月cohort有362个客户，M1为0/362，M2为2/362（0.55%）；2月cohort有311个客户，M1为4/311（1.29%）。\n\n热图同时展示2018年1—6月的M0—M3，M0是定义上的100%。本轮截止2018-08-01，因此5月的M3、6月的M2/M3为“未观察”；0表示在本轮可观察月份内没有再次购买。样本量小且品类周期不同，不适合只按一格数值评判经营好坏。'),
        ('6月M3是空白，是不是留存最差？',
         '不是。6月M3对应2018年9月，超过本轮定义截点；它没有参与观察，既不是0，也不能参与成熟cohort的M3平均。比较M3时，只比较1—4月这些在本轮具备相同观察长度的组。'),
        ('你怎么知道这些月份数据是完整的？',
         '我没有从文件最大日期证明任何月份业务已完整收齐。2018-08-01是为了统一比较主动选的分析截止，源数据仍是历史抽样。这里“可观察”表示落在本轮定义窗内，不表示已核验全国业务月份完整；严格生产留存还需要数据到齐/延迟到达记录和真实新客历史。')],
        ['NULL补0','首次出现当已核验全平台新客','最大日期证明月份完整','平均混入不成熟cohort'],
        ['sql/16-cohort-retention.sql','data/16-cohort-retention.csv'],{'january_n':362,'january_M2_returned':2,'february_M1_returned':4,'unobserved_cells':3})
    add(15,'统计分析：履约与低评价的关系',
        '已送达；延迟=实际送达日>预计送达日；最新评价；低评价≤2分；未评价不当好评。',
        ['构造订单级最新评价与延迟标签','分别报告有评价/无评价分母','报告比例、风险差/比、Wilson和Fisher','做无评价结局敏感性边界；不混同置信区间或因果'],[
        ('延迟送达和低评价到底有没有关系？用Python统计分析，并给我图。',
         '样本中二者关联很强：延迟订单低评价率61.93%，按时订单9.51%，相差52.42个百分点。\n\n'+
         table(['交付组','有评价订单','低评价','低评价率','95% Wilson区间','无评价'],[
          ['延迟',352,218,'61.93%','56.76%—66.85%',4],['按时',4626,440,'9.51%','8.70%—10.39%',26]])+
         '\n\n使用Python SciPy Fisher精确检验，p≈2.13×10⁻¹¹³；低评价风险比6.51，优势比15.48，二者不是同一指标。效应大小及区间比单报“显著”更有用。建议优先核查延期链路并设计改善验证；这里是观察性关联，不能认定延迟单独造成了全部差异。'),
        ('那30单没评价，会不会推翻结论？',
         '做最不利/最有利的结局分配：延迟组4单、按时组26单分别全部当低评价或非低评价。低评价率差的情形边界为51.22—52.90个百分点，方向仍相同。这个边界是对缺失结果作极端分配得到的确定性敏感性范围，不是95%置信区间，也没有排除其他混杂。'),
        ('如果我减少延迟，能保证低评价下降52.42%吗？',
         '不能这样承诺。52.42是两组观察低评价率的百分点差，既不是52.42%的相对变化，也不是干预效应。可以选择物流改善试点，预先定义低评价率、延迟率和成本，比较有对照的前后变化；现有分析用于确定排查优先级。')],
        ['把52.42个百分点写52.42%干预效果','无评价补好评','风险比混作优势比','敏感性范围叫置信区间'],
        ['sql/13-delivery-review.sql','data/13-delivery-review.csv','reference-results.json'],
        {'late_low':218,'late_reviewed':352,'ontime_low':440,'ontime_reviewed':4626,'risk_difference_pp':st['risk_difference_pp'],'fisher_p':st['pvalue']})
    add(16,'预测建模：时间可用性与基线',
        '探索性逻辑回归；训练标签在2018-07-01前已知；7月下单已送达订单为测试。',
        ['定义预测时点和可得特征的边界','排除7月后才送达的86条早下单训练记录','仅训练拟合标准化与模型','对比AUC/AP/Brier与固定训练事件率基线','结合校准和样本选择决定不部署'],[
        ('建一个延迟预测模型，看看能不能提前预警。',
         '已完成一版历史探索模型；当前结果不支持部署。\n\n'+table(['项目','实际结果'],[
          ['训练/测试','4,242单（323延迟） / 359单（18延迟）'],['训练截点','2018-07-01，训练送达标签也须在此前已知'],['排除未来训练标签','86条'],
          ['AUC',f"{model['auc']:.4f}"],['平均精确率 AP',f"{model['average_precision']:.4f}"],['模型Brier（越低越好）',f"{model['brier']:.5f}"],
          ['固定训练事件率基线Brier',f"{model['baseline_brier']:.5f}"]])+
         '\n\n模型Brier比基线更差，校准图中高预测概率组也偏高。虽然AUC高于0.5，但不足以证明预警有用。下一步应核验特征在下单时刻是否真正冻结、加入可当时获得的承运/卖家历史特征，再做滚动时间验证。'),
        ('按下单时间切开了，为什么还会泄漏？',
         '有些6月下单的订单7月才送达；若把最终“是否延迟”标签用于7月1日前训练，就提前看到了未来结果。本轮额外要求训练delivered_at<2018-07-01，排除86条记录。最晚训练标签时间为2018-06-30 17:35:34；标准化也只在训练集拟合。'),
        ('AUC有0.63，还不够上线试试吗？',
         '它表示有限的排序能力，不能代替概率质量和业务收益。测试仅18个延迟样本，模型Brier0.05227还差于基线0.04830；目前只覆盖最终已送达订单，且未证明特征的实时冻结。可以继续离线验证，不能声称已具备可靠生产预警能力。')],
        ['提前使用未来送达标签','随机切分冒充时间外推','不对比基线就部署','宿主执行参考模型冒称Agent Docker执行通过'],
        ['sql/17-model-input.sql','data/17-model-input.csv','reference-results.json','reference-checks.json'],
        {'train':4242,'test':359,'excluded_future_labels':86,'auc':model['auc'],'brier':model['brier'],'baseline_brier':model['baseline_brier'],'deploy':False})
    add(17,'可视清洗：预览、确认、应用、撤销',
        '明确合成12行演示样本；不是Olist原始缺陷；本轮老师脚本模拟确认，不冒充产品回执。',
        ['保留before.csv和字节哈希','以已确认pt-BR解析金额；原列不变','仅去1条完全重复；同键冲突两行均保留并隔离','保存预览与老师演示确认后写分析副本','撤销恢复before并核对字节哈希'],[
        ('我想看看清洗怎么做，用一个有典型问题的小样本演示完整过程。',
         '这题使用我专门制造的合成数据，不把这些缺陷算到Olist头上。原始12行包含完全重复、同键金额冲突、空/非法金额、无效日期、pt-BR小数与负金额。\n\n'+
         table(['预览动作','处理后变化','保留什么'],[['去完全重复','12→11行','只移除完全相同的S02额外行'],
          ['金额解析','1.234,56→1234.56','原金额列保留；空与12x不猜补'],['州规范',' sp →SP','只规范副本列'],
          ['异常隔离','5行可用、6行待核对','S03两条冲突都保留；负金额需确认含义']])+
         '\n\n演示文件已走过预览→老师模拟确认→应用到副本→撤销。撤销后12行与原文件逐字节一致；这证明本题教学事务可复现，不代表生产Agent已完成本题。'),
        ('看起来还有6行没洗好，为什么不直接删除或者填平均值？',
         '这6行的业务事实不能靠猜恢复：2行S03同键金额冲突、S04非法金额、S05原始空金额、S06无效日期、S09负金额。直接删除或均值填充会掩盖问题。当前可用5行的金额合计1,545.06 BRL，只代表可用子集，不能称全样本真实总额；待核对行和原值仍在副本中。'),
        ('如果我发现规则不对，怎么追溯和撤销？',
         '对照before.csv、preview.json、teacher-confirmation.json与after.csv可逐行查看变化和原因。演示撤销将工作副本恢复为before，checks.json中的before_sha256与undo_sha256相同。产品验收时也必须有真实预览、用户确认及执行回执，不能只由聊天文字说“已清洗”。')],
        ['合成缺陷冒称Olist问题','未经确认覆盖原文件','冲突随意留一行','空值补零','老师脚本冒充产品实测'],
        ['cleaning/before.csv','cleaning/preview.json','cleaning/after.csv','cleaning/undo.csv','cleaning/checks.json'],
        {'synthetic':True,'before':12,'after':11,'usable':5,'quarantined':6,'usable_amount_BRL':clean['usable_amount_BRL'],'undo_exact':True})
    add(18,'完整商业汇报与下一步决策',
        '汇总已确认口径，串起经营—结构—客户—履约—建模；建议与事实分开，未知指标不编造。',
        ['概括三条有证据的经营判断','给出对应图、数值与依据入口','把优先行动对应到发现','列出可补数据及其能回答的问题','交付可修改图表和底层数据'],[
        ('你分析完了，给管理层一份能行动的汇报，同时配一个高级可视化。',
         '建议把工作分成“恢复订单增长、改善履约体验、守住数据口径”三条线。\n\n**1. 七月恢复来自订单量，尚未证明持续增长。** 商品金额50,588.49 BRL，环比+19.08%；订单287→359（+25.09%），每单金额−4.80%。对称分解中量的贡献+10,401.60 BRL，客单抵消−2,294.39 BRL。先追踪拉动州PR/MA/MG的订单来源，同时核查CE小样本大额变化。\n\n**2. 履约是优先排查方向。** 延迟订单低评价率61.93%，按时9.51%；差52.42个百分点。缺失评价极端分配不改变方向，但这仍是关联。建议做有对照的物流改善试点，跟踪延迟率、低评价率和成本。\n\n**3. 自动化应先建立可追溯证据。** 13笔订单需对账；未知/未译品类金额10,179.54 BRL必须保留；预测模型未胜基线，暂不部署。\n\n高级金额流向图把同一686,136.43 BRL按买家州→品类→卖家州展开，帮助看结构集中。它不表示物流轨迹、利润或因果；每列守恒，跨两段不能重复相加。所有图都配静态交付、交互编辑配置与底层结果。'),
        ('那投广告、促销和利润提升，你直接给我确定方案。',
         '现在可以确定排查顺序，不能确定广告ROI或利润提升额。先补渠道访问/花费/归因、活动曝光、成本和退款表；分别用于拆流量×转化、比较促销净额、计算贡献利润。补表前仍可执行：导出13单对账、核查延期路径、监控七月增量州与订单结构。'),
        ('如果我要下周就落地，你建议先做哪三件事，怎么验收？',
         '第一，逐单复核13笔差额，验收每笔有凭证、解释和处理状态，不以强行改平为目标。第二，选一条延期链路做对照试点，预先固定订单范围、延迟/低评价分母和成本，验收有对照结果而非口头归因。第三，建立月度订单与商品金额看板，验收总额勾稽、可下钻到州/品类/订单，图可修改与导出。模型继续离线，直到在新时间窗超过基线并通过特征时点核验。')],
        ['缺成本造利润/ROI','把观察建议写成已证实因果','模型未胜基线仍称可部署','高级图不守恒或把两段相加'],
        ['data/18-item-flows.csv','sql/18-item-flows.sql','reference-results.json','business-report.md'],
        {'flow_column_total_BRL':'686136.43','growth_rate':mov[1]['change_rate'],'reconciliation_orders':13,'model_deploy':False})
    return cases


def build_site(cases,charts,r,clean):
    case_dir=OUT/'cases';case_dir.mkdir(exist_ok=True)
    articles=[];nav=[]
    for case,chart in zip(cases,charts):
        cid=case['id'];nav.append(f'<a href="#{cid}" data-case="{cid}"><small>{cid}</small>{html.escape(case["title"])}</a>')
        turns='';markdown=f'# {cid} {case["title"]}\n\n状态：候选，待审核，未用于训练。\n\n口径：{case["scope"]}\n'
        for turn in case['turns']:
            question=turn['question'];answer=turn['reference_answer']
            turns+=f'<section class="turn"><p class="eyebrow">第{turn["turn"]}轮 · 用户提问</p><blockquote>{html.escape(question)}</blockquote><div class="answer">{md(answer)}</div></section>'
            markdown+=f'\n## 第{turn["turn"]}轮\n\n**用户：** {question}\n\n{answer}\n'
            if turn['turn']==1:
                turns+=f'<figure><img src="{chart["png"]}" alt="{html.escape(chart["title"])}" loading="lazy"><figcaption>{html.escape(chart["scope"])}</figcaption></figure>'
                turns+=f'<div class="links"><a href="{chart["editable"]}" target="_blank">打开可编辑交互图 ↗</a><a href="{chart["svg"]}">矢量SVG</a><a href="{chart["config"]}" download>配置JSON</a><a href="{chart["data"]}" download>图形数据</a></div>'
                markdown+=f'\n![参考图](../{chart["png"]})\n\n[可编辑交互图](../{chart["editable"]})\n'
        evidence=''.join(f'<li><a href="{html.escape(p)}" target="_blank">{html.escape(p)}</a></li>' for p in case['evidence'])
        steps='<ol>'+''.join('<li>'+html.escape(s)+'</li>' for s in case['teacher_steps'])+'</ol>'
        failures='<ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in case['hard_failures'])+'</ul>'
        scores=''.join(f'<label>{name} /20<input type="number" min="0" max="20" step="1" inputmode="numeric" data-score="{name}" aria-label="{cid} {name}得分"></label>' for name in case['scoring'])
        articles.append(f'''<article id="{cid}" data-id="{cid}" hidden><header><p class="eyebrow">教学候选 · 3轮连续问答</p><h1>{cid} {html.escape(case['title'])}</h1><p class="scope">{html.escape(case['scope'])}</p></header>
{turns}<details><summary>老师如何分析 · 数值标准 · 证据</summary>{steps}<pre>{html.escape(json.dumps(case['numeric_assertions'],ensure_ascii=False,indent=2))}</pre><ul>{evidence}</ul></details>
<details><summary>本题不能出现的错误</summary>{failures}<p>{html.escape(case['acceptance'])}</p></details>
<section class="review"><h2>小城的逐题审核</h2><p>评分和意见只保存在当前浏览器；请导出审核文件交回。选择“满意”仅表示本题意见，整个版本仍需你另行确认后才可用于校准。</p>
<div class="score-grid">{scores}</div><label>你的判断<select data-decision aria-label="{cid} 审核判断"><option value="pending">尚未审核</option><option value="satisfied">满意，可作为候选</option><option value="revise">需要修改</option><option value="reject">不采用</option></select></label>
<label>修改意见<textarea data-notes rows="4" placeholder="例如：结论太长、证据不够直观、图表配色或交互需要修改……"></textarea></label><p class="save-state" aria-live="polite"></p></section></article>''')
        markdown+='\n## 老师执行步骤\n\n'+'\n'.join(f'{i+1}. {s}' for i,s in enumerate(case['teacher_steps']))
        markdown+='\n\n## 依据与硬性错误\n\n'+'\n'.join(f'- [{p}](../{p})' for p in case['evidence'])+'\n\n'+'\n'.join('- '+s for s in case['hard_failures'])
        (case_dir/f'{cid}.md').write_text(markdown+'\n',encoding='utf-8')
    html_page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>电商分析黄金集 · 小城审核版</title>
<style>
:root{--ink:#183449;--muted:#657984;--teal:#087f8c;--line:#dce5e9;--bg:#f5f7f8}*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--bg);font:15px/1.85 "Microsoft YaHei",sans-serif}a{color:var(--teal);text-underline-offset:4px}button,select,input,textarea{font:inherit}button{border:0;background:var(--teal);color:white;padding:10px 16px;border-radius:7px;cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid #b58432;outline-offset:3px}.top{position:sticky;top:0;background:#183449;color:white;z-index:3;padding:13px 28px;display:flex;justify-content:space-between;gap:12px;align-items:center}.top strong{letter-spacing:.04em}.top small{color:#c0d7de}.layout{display:grid;grid-template-columns:290px minmax(0,1fr);max-width:1540px;margin:auto}aside{position:sticky;top:76px;height:calc(100vh - 76px);overflow:auto;padding:24px 18px}aside a{display:block;padding:10px 12px;color:var(--ink);text-decoration:none;font-size:13px;border-radius:6px;line-height:1.7}aside small{display:block;color:var(--muted);font-size:10px}aside a.active{background:#dceff1;color:#05646e}main{min-width:0;padding:28px 36px 80px}article,.intro{background:white;border:1px solid var(--line);border-radius:12px;padding:34px 42px;box-shadow:0 5px 30px #18344905}.intro{margin-bottom:22px;padding:22px 32px}.intro p{margin:4px 0}.tag{font-size:12px;background:#fff1d3;color:#816020;border-radius:5px;padding:4px 8px}.eyebrow{font-size:11px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}h1{font-size:27px;line-height:1.5;margin:12px 0}h2{font-size:21px;margin-top:32px}h3{font-size:18px;border-left:3px solid var(--teal);padding-left:12px;margin-top:30px}.scope{color:var(--muted);font-size:13px}blockquote{margin:12px 0 24px;background:#edf5f6;border-left:3px solid var(--teal);padding:16px 20px;font-weight:600}.turn+.turn{border-top:1px solid var(--line);margin-top:32px;padding-top:24px}.answer p{margin:14px 0}.answer{overflow-wrap:anywhere}.answer table{display:block;overflow-x:auto;white-space:normal;width:100%;border-collapse:collapse;font-size:13px;margin:22px 0}.answer th,.answer td{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top;min-width:105px}.answer th{background:#f0f5f7;text-align:left;color:#4b6270;font-size:12px}.answer td:first-child{font-weight:500}.answer table:has(th:nth-child(6)) td:nth-child(1){min-width:210px}.answer table:has(th:nth-child(6)) td:last-child{min-width:230px}figure{margin:28px -10px 10px}figure img{display:block;width:100%;height:auto;border:1px solid #e7ecee;border-radius:7px}figcaption{font-size:12px;color:var(--muted);margin:8px 10px}.links{display:flex;flex-wrap:wrap;gap:18px;font-size:12px;margin:16px 0 30px}details{border-top:1px solid var(--line);padding:18px 0;font-size:13px}summary{cursor:pointer;font-weight:600}pre{overflow:auto;background:#f3f6f7;padding:16px;font-size:12px}.review{border-radius:9px;background:#f0f6f6;padding:20px 24px;margin-top:25px}.review h2{margin-top:0}.review p{font-size:12px;color:var(--muted)}label{display:block;font-size:13px;margin:12px 0}.score-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}input,textarea,select{display:block;border:1px solid #bcced5;border-radius:5px;background:white;padding:8px;width:100%;color:var(--ink)}textarea{resize:vertical}.save-state{min-height:1.8em}#progress{font-size:12px;color:#c0d7de}noscript{display:block;background:#ffe7bd;padding:20px}
@media(max-width:900px){.layout{display:block}aside{position:static;height:auto;max-height:210px;padding:12px 18px;display:flex;overflow:auto;gap:8px}aside a{flex:0 0 210px;border:1px solid var(--line)}main{padding:14px}article{padding:22px 18px}.intro{padding:18px}h1{font-size:22px}.top{padding:12px 16px}.top small{display:none}.score-grid{grid-template-columns:repeat(2,1fr)}figure{margin:22px 0}.top button{font-size:12px}}
</style></head><body><div class="top"><div><strong>DATA analysis · 老师黄金集</strong> <small>18场景 / 54轮 / 18组图</small><div id="progress">候选稿 · 待逐题审核</div></div><button id="export">导出审核意见</button></div>
<div class="layout"><aside aria-label="场景目录">__NAV__</aside><main><section class="intro"><span class="tag">未批准 · 未用于训练</span><p>小城，先看问题是否答全、分析是否可靠、图是否值得交付，再给每题打分和意见。</p><p><a href="business-report.html">先读完整商业汇报 ↗</a> · <a href="review-guide.md">审核说明</a> · <a href="research-provenance.md">方法参考</a> · <a href="reference-checks.json">57项独立核对</a></p><p>历史抽样：Olist 2016—2018，非当前业务或全国总体；本页是独立老师参考，没有对Agent测分。</p></section>__ARTICLES__</main></div>
<noscript>请在浏览器启用JavaScript以切换题目；也可直接阅读cases目录里的Markdown文件。审核意见不会上传。</noscript>
<script>
const key='analyst-gold-v3-review-2026-11-11';let reviews={};let storageOK=true;
try{reviews=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){storageOK=false}
const ids=[...document.querySelectorAll('article[data-id]')].map(a=>a.dataset.id);
function show(){const id=ids.includes(location.hash.slice(1))?location.hash.slice(1):ids[0];document.querySelectorAll('article').forEach(a=>a.hidden=a.dataset.id!==id);document.querySelectorAll('aside a').forEach(a=>a.classList.toggle('active',a.dataset.case===id));}
function progress(){const n=ids.filter(id=>reviews[id]&&reviews[id].decision&&reviews[id].decision!=='pending').length;document.querySelector('#progress').textContent=`已给出判断 ${n}/18 · 全集仍待确认`}
for(const article of document.querySelectorAll('article')){const id=article.dataset.id;const old=reviews[id]||{};article.querySelector('[data-decision]').value=old.decision||'pending';article.querySelector('[data-notes]').value=old.notes||'';for(const input of article.querySelectorAll('[data-score]'))input.value=old.scores?.[input.dataset.score]??'';
 article.addEventListener('input',()=>{const inputs=[...article.querySelectorAll('[data-score]')];const valid=inputs.every(x=>x.validity.valid);if(!valid){article.querySelector('.save-state').textContent='每项请填0—20的整数。';return}reviews[id]={decision:article.querySelector('[data-decision]').value,notes:article.querySelector('[data-notes]').value,scores:Object.fromEntries(inputs.map(x=>[x.dataset.score,x.value===''?null:Number(x.value)])),updated_at:new Date().toISOString()};try{localStorage.setItem(key,JSON.stringify(reviews));article.querySelector('.save-state').textContent='已保存在当前浏览器；请导出审核意见。'}catch(e){storageOK=false;article.querySelector('.save-state').textContent='浏览器无法持久保存，请现在导出审核意见。'}progress()})}
document.querySelector('#export').onclick=()=>{const payload={version:'analyst-gold-v3-draft',content_sha256:'__HASH__',approved_for_training:false,status:'user_review_export_not_dataset_approval',exported_at:new Date().toISOString(),reviews:Object.fromEntries(ids.map(id=>[id,reviews[id]||{decision:'pending',scores:{},notes:''}]))};const a=document.createElement('a');const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));a.href=url;a.download='gold-v3-小城审核意见.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
addEventListener('hashchange',show);show();progress();if(!storageOK)document.querySelector('#progress').textContent+=' · 本浏览器不能持久保存，请导出';
</script></body></html>'''
    html_page=html_page.replace('justify-content:space-between;gap:12px;align-items:center','justify-content:flex-start;gap:24px;align-items:center')
    html_page=html_page.replace("document.querySelector('#export').onclick=()=>{const payload", "document.querySelector('#export').onclick=()=>{const invalid=[...document.querySelectorAll('[data-score]')].find(x=>!x.validity.valid);if(invalid){location.hash=invalid.closest('article').dataset.id;show();invalid.reportValidity();return}const payload")
    html_page=html_page.replace('.top{padding:12px 16px}', '.top{padding:12px 16px;flex-wrap:wrap}')
    content_hash=hashlib.sha256((GOLD/'cases.json').read_bytes()).hexdigest()
    (OUT/'index.html').write_text(html_page.replace('__NAV__',''.join(nav)).replace('__ARTICLES__',''.join(articles)).replace('__HASH__',content_hash),encoding='utf-8')
    return content_hash


def business_report(r):
    report='''# 巴西电商样本 · 商业分析与行动建议

> 老师独立参考 / 黄金集候选 / 2026-11-11 / 待小城审核。不是Agent实测成绩。

分析材料来自Olist公开数据客户抽样：8张表、5,169笔订单，2016—2018年历史记录，币种BRL。经营主线只看5,008笔已送达订单、按下单月、商品金额不含运费。这里没有全国总体推断，也没有2026年实时经营结论。

## 管理层先看这三件事

1. **七月恢复由订单量推动。** 商品金额50,588.49 BRL，环比+19.08%；订单287→359（+25.09%），每单商品金额148.02→140.92 BRL（−4.80%）。量增贡献10,401.60 BRL，客单回落抵消2,294.39 BRL。先跟踪订单来源与结构，再判断能否持续。
2. **履约链路值得优先排查。** 延迟单低评价率61.93%，按时9.51%，差52.42个百分点；缺失评价的极端结局分配仍保持同方向。它支持排查优先级，尚不支持把差额当作干预收益。
3. **自动化需要守住口径与可追溯性。** 13笔订单收付差额需核查；未知/未译品类金额10,179.54 BRL仍计入总额；历史预测模型Brier差于简单基线，当前不部署。

## 1 · 数据先体检，清洗不制造事实

orders审核/发货/送达时间分别空7/93/160条，已送达范围这三列均完整。products品类空62条；reviews标题空4,572条、正文空2,992条，但评分并不因此不可用。原始8表未发现完全重复行。

评价有5,173条、覆盖5,134单，多出的39条作为历史记录保留。需要一单一评分时，在分析副本按固定排序取最新，不能把它们全删作脏数据。关联检查显示：明细商品/卖家等非空键均可匹配；另有1条商品非空品类未匹配翻译，不应丢弃金额。

补查22个日期/数值字段，非空值均能解析；7组候选业务键无重复。但时间顺序仍有真实异常：发货早于下单14单、早于审核82单、晚于送达1单，规则重叠，涉及83个不同订单。非空不等于可直接计算阶段耗时；原值保留，按具体指标标记可用范围，不能全表删除或猜改时间。逐单证据见[data/19-chronology-flags.csv](data/19-chronology-flags.csv)。

![实际空值](charts/AG302.png)

人工演示清洗另用明确合成样本：12→11行只去一条完全重复，5行可用于当前计算、6行隔离待核对；保存前后差异并撤销到逐字节相同。它示范可靠流程，没有冒充生产Agent回执。

## 2 · 经营规模与月度变化

'''+table(['指标','已送达范围结果'],[['商品金额','686,136.43 BRL'],['运费','112,905.81 BRL'],['支付金额','799,188.28 BRL'],['订单','5,008'],['客户去重标识','4,851'],['每单商品金额','137.01 BRL']])+'''

![经营趋势](charts/AG304.png)

五月→六月商品金额51,181.69→42,481.28 BRL，下降17.00%。订单量效应−11,112.16 BRL，每单金额效应+2,411.75 BRL；六月→七月净增8,107.21 BRL。两段均用对称量价分解并和总差额勾稽，解释的是算术构成，未识别流量、促销或价格策略的因果作用。

![七月分解](charts/AG306.png)

## 3 · 增量来自哪些地域，结构集中在哪些品类

PR（+2,261.45 BRL）、MA（+1,349.19）、MG（+1,133.93）为七月增量前三买家州；CE（−1,797.19）抵消最多。CE订单2→3而金额下降，需要核查大额订单结构，不能将小样本写为长期趋势。MA基期零，不报告普通环比百分比。

![州贡献](charts/AG307.png)

健康美容、钟表礼品、运动休闲位居品类金额前三。65个展示组包含未知/未译，合计与686,136.43 BRL相符。Other是保留总量的合计，不能参加单州/单品类排名。

![品类结构](charts/AG308.png)

卖家以seller_id统计，不用州替代卖家。至少30单的20位卖家中，最高延迟率5/38=13.16%，95% Wilson区间5.75%—27.33%。整单交付与卖家关联不证明其责任；多卖家订单需包裹轨迹才能归因。

## 4 · 客户：复购少，观察窗必须说清

全期4,851个已送达购买标识中145个复购，观察复购占比2.99%。2018-08-01截点的RF分层＋金额是另一范围，不能和全期数混用。“一次购买”不等于流失，“超过90天未购”可作候选触达规则，仍需许可、周期和成本约束。

![留存](charts/AG314.png)

cohort按样本内首次已送达购买月定义。1月362客户，M2回购2个（0.55%）；2月311客户，M1回购4个（1.29%）。超出定义观察窗的格子为未观察，不能补零。这个主动截止并未证明源月份业务完整收齐。

## 5 · 履约与评价：报告效应、区间和边界

![履约评价](charts/AG315.png)

延迟352单有评价，其中218单低评价；按时4,626单有评价，其中440单低评价。风险比6.51，Fisher优势比15.48，p≈2.13×10⁻¹¹³。没有评价的4+26单不补成好评；极端分配得到风险差51.22—52.90个百分点的敏感性情形边界，它不是95%置信区间。

## 6 · 预测建模：本次否决部署

![模型与基线](charts/AG316.png)

训练4,242单、测试359单（18例延迟）。不仅按下单时间分割，还要求训练送达标签在2018-07-01前已可得，排除86条未来标签。标准化只拟合训练集。逻辑回归AUC0.6308、AP0.1298、Brier0.05227，固定训练事件率基线Brier0.04830（越低越好），未胜基线。

仅保留已送达样本的选择偏差、特征是否在下单时冻结尚未核验，均阻止生产承诺。下一步做特征时点核验、滚动验证和成本阈值分析，而不是凭AUC>0.5上线。

## 7 · 可交付高级图：真实金额流向

![商品金额流向](charts/AG318.png)

[打开可编辑交互Sankey](charts/AG318.html)。买家州→品类→卖家州的每列合计均为686,136.43 BRL。两段链接是同一批商品金额的重复切面，不可跨段相加；不是物流轨迹或利润。其余分组合计保留，底层CSV、Plotly JSON和SVG均提供以便后调。

## 8 · 下一步行动与验收

'''+table(['优先级','行动','现有依据','如何验收'],[
 ['1','逐单对账','13单|支付−商品−运费|>0.01','每笔凭证、解释、责任状态齐备，不强行改平'],
 ['2','选择延期链路做对照改善','52.42个百分点观察风险差','预先固定范围、分母、延迟/低评价和成本，保留对照结果'],
 ['3','监控七月订单来源与结构','PR/MA/MG拉动，客单回落','看板总额勾稽，可下钻订单与品类，图可修改导出'],
 ['后续','补齐渠道/成本/退款','现数据不能算利润/ROI','核对关联覆盖和归属日期后再算净额和投资回报'],
 ['后续','模型继续离线','本次不胜基线','新时间窗超基线、特征时点和校准合格后再讨论部署']])+'''

## 计算与阅读入口

- [18场景、54轮逐题审核](index.html)；每轮口径承接、证据边界和表达质量均为审核对象。
- [独立计算及57项交叉核对](reference-checks.json)：CSV/Decimal与DuckDB定点金额核对；统计建模另有代码和时点断言。
- [源数据及许可](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)：CC BY-NC-SA 4.0；本包为本地学习和评测，原始CSV不纳入Git。

此候选集未用于训练、未冻结为验收标准，也没有拿来冒充Agent得分。批准的样例后续可用于示例、提示和工具流程校准；真正盲测需另外准备未参与校准的新题。
'''
    (OUT/'business-report.md').write_text(report,encoding='utf-8')
    rendered=md(report)
    (OUT/'business-report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>巴西电商商业分析 · 教师参考</title><style>body{max-width:1000px;margin:40px auto;padding:0 28px;color:#183449;font:16px/1.95 'Microsoft YaHei',sans-serif}h1{font-size:30px}h2{font-size:24px;margin-top:55px;border-top:1px solid #dce5e9;padding-top:24px}img{width:100%;border:1px solid #e7ecee;border-radius:8px}a{color:#087f8c}table{display:block;overflow:auto;border-collapse:collapse;font-size:14px}td,th{padding:12px;border-bottom:1px solid #dce5e9;text-align:left;min-width:130px}th{background:#edf5f6}blockquote{border-left:3px solid #087f8c;padding:10px 20px;background:#f4f8f9;color:#637985}</style><a href="index.html">← 返回逐题审核</a>'''+rendered+'</html>',encoding='utf-8')


def validate(cases,charts,r,clean):
    checks=[]
    def ok(name,value):assert value,name;checks.append({'check':name,'passed':True})
    ok('18 distinct scenes / 54 turns',len(cases)==18 and len({c['id'] for c in cases})==18 and sum(len(c['turns']) for c in cases)==54)
    ok('all remain draft and unauthorized for training',all(c['review_status']=='pending' and c['approved_for_training'] is False for c in cases))
    ok('47 source fields explained',sum(len(p['fields']) for p in r['profile'])==47 and sum(len(x[3]) for x in TABLES.values())==47)
    ok('all 57 original crosschecks passed',len(r['checks'])==57 and all(x['passed'] for x in r['checks']))
    ok('source hashes unchanged',all(hashlib.sha256((SOURCE/f'{n}.csv').read_bytes()).hexdigest()==v['sha256'] for n,v in json.loads((GOLD/'source-manifest.json').read_text(encoding='utf-8'))['source']['tables'].items()))
    ok('clean undo equals exact before',clean['before_sha256']==clean['undo_sha256'] and (OUT/'cleaning/before.csv').read_bytes()==(OUT/'cleaning/undo.csv').read_bytes())
    ok('synthetic usable amount',Decimal(clean['usable_amount_BRL'])==Decimal('1545.06'))
    ok('all checked source types parse',all(x['invalid']==0 for x in r['quality_controls']['types']))
    ok('seven candidate business keys unique',len(r['quality_controls']['keys'])==7 and all(x['duplicate_extras']==0 for x in r['quality_controls']['keys']))
    ok('real chronology defects preserved',r['quality_controls']['chronology_counts']=={'carrier_before_purchase':14,'carrier_before_approval':82,'carrier_after_delivery':1})
    ok('fixed model label cutoff excludes 86 future labels',r['model']['train_label_latest']<'2018-07-01' and r['model']['excluded_future_labels']==86)
    ok('model not recommended',r['model']['brier']>r['model']['baseline_brier'] and cases[15]['numeric_assertions']['deploy'] is False)
    ok('cohort preserves 3 unobserved cells',sum(x['retention_rate'] is None for x in r['cohort'])==3)
    for case,chart in zip(cases,charts):
        ok(case['id']+' chart identity',chart['case_id']==case['id'])
        for field in ['png','svg','editable','config','data']:ok(case['id']+' '+field,(OUT/chart[field]).is_file() and (OUT/chart[field]).stat().st_size>50)
        spec=json.loads((OUT/chart['config']).read_text(encoding='utf-8'))
        ok(case['id']+' editable spec',bool(spec['data']) and bool(spec['layout']['title']['text']))
        for evidence in case['evidence']:ok(case['id']+' evidence '+evidence,(OUT/evidence).is_file())
        ok(case['id']+' multi-turn content',all(len(t['question'])>=8 and len(t['reference_answer'])>=55 for t in case['turns']))
    ok('Sankey each adjacent layer conserves exact amount',all(sum(Decimal(e['amount']) for e in json.loads((OUT/'charts/AG318.data.json').read_text(encoding='utf-8'))['rows'] if e['source'][0]==layer)==Decimal(r['kpis']['item_amount']) for layer in [0,1]))
    # Verify plot values used by the main quantitative charts, independently of rendered pixels.
    def spec(n):return json.loads((OUT/f'charts/AG3{n:02d}.plotly.json').read_text(encoding='utf-8'))
    ok('trend 7 months correct',len(spec(4)['data'][0]['y'])==7 and sum(Decimal(str(x)) for x in spec(4)['data'][0]['y'])==sum(Decimal(x['item_amount']) for x in r['monthly'] if '2018-01'<=x['month']<='2018-07'))
    ok('category display preserves total',sum(Decimal(str(v)) for v in spec(8)['data'][0]['x']).quantize(Decimal('.01'))==Decimal(r['kpis']['item_amount']))
    ok('payment chart preserves total',sum(Decimal(str(v)) for v in spec(12)['data'][0]['x'])==Decimal(r['kpis']['payment_amount']))
    ok('statistics chart correct denominators',abs(spec(15)['data'][0]['x'][0]-218/352*100)<1e-10 and abs(spec(15)['data'][0]['x'][1]-440/4626*100)<1e-10)
    ok('no mixed-unit common axis',spec(4)['data'][0]['yaxis']!=spec(4)['data'][1]['yaxis'])
    write_json(OUT/'review-pack-checks.json',{'passed':True,'checks':checks,'count':len(checks),
        'scope':'Authored teacher assets and calculated evidence, not Agent inference quality; browser evidence is recorded separately.'})
    return len(checks)


def main():
    r=json.loads((OUT/'reference-results.json').read_text(encoding='utf-8'))
    clean=synthetic_cleaning();cases=make_cases(r,clean)
    write_json(GOLD/'cases.json',cases);write_json(OUT/'cases.json',cases)
    charts=render(r,clean)
    business_report(r);content_hash=build_site(cases,charts,r,clean)
    guide='''# 小城的审核说明

建议先读business-report.html，再审18道题。每题有3轮连续问答，请特别看追问后口径有没有漂移、问题是否答全、证据是否能追到所属表。

每项0—20分：正确性、问题覆盖、理由与证据、专业表达、图表可交付性。分数是意见，不自动决定通过；任何编造数值、错误分母、关联膨胀、因果夸大、未经确认改数据，单独判为严重错误。

图表可在单独页面缩放、改标题/图例/轴文字并导出PNG与编辑JSON；SVG也可交给矢量工具后调，CSV/JSON用于重算。静态老师图与交互版不是逐像素相同，但方法和数值必须相同。

审核意见保存在当前浏览器，请点“导出审核意见”保存JSON，再将意见交给我。也可以直接告诉我场景编号和修改要求。单题满意不等于全集冻结。

整个黄金集仍是候选，approved_for_training=false；用户确认后才选定样例、冻结版本，再比较示例检索/提示/工具流程与参数训练的必要性。54轮全部是教学候选，不能作为调优后的独立盲测。以后另出新题。

本轮没有调用付费模型，没有评估生产Agent得分，也没有修改原聊天、原CSV、生产提示或旧黄金集。清洗场景AG317使用明确合成样本，老师脚本模拟确认，不冒充产品实测回执。
'''
    (OUT/'review-guide.md').write_text(guide,encoding='utf-8')
    provenance='''# 方法来源与采用范围

2026-11-11通过GitHub只读查阅anthropics/knowledge-work-plugins官方仓库；没有安装、运行外部代码，也没有整段复制其skill。将它用于改进老师分析方法，不声称这些来源能保证结果自动正确。

固定参考版本：877a617b435ee2edfb002d9a3703c0a1a03aa1ba。

| 来源 | 读取内容标识（blob SHA） | 采用做法 |
|---|---|---|
| [分析方法](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/analyze/SKILL.md) | 31ce9c58dd4de3038643750373de5b71a2494fa2 | 按快答/完整分析/正式报告选择深度，先问题与口径再计算、解释、建议 |
| [绘图方法](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/data-visualization/SKILL.md) | 409cce60f59b463a8386c83cde03dc7e70be93f3 | 按趋势/比较/结构/分布选图，避免混合单位共轴，保留底层结果 |
| [分析核验](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/validate-data/SKILL.md) | 30a1bac41bf9679dc7d96b7ec59a116f03ec0575 | 检查分子分母、关联膨胀、样本选择、观察窗、结论边界 |

文件读取来自默认分支时的上述blob，固定版本用于来源追踪。本项目采用的方法没有依赖外部skill执行；数值均由本地CSV/Decimal、DuckDB、SciPy和scikit-learn实际计算。

数据：[Olist巴西电商公开数据](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，CC BY-NC-SA 4.0。原始CSV不提交，抽样规则、行数和文件哈希见source-manifest.json。此成果用于本地学习与评测，商业使用前须重新核验源许可。
'''
    (OUT/'research-provenance.md').write_text(provenance,encoding='utf-8');(GOLD/'research-provenance.md').write_text(provenance,encoding='utf-8')
    shutil.copyfile(GOLD/'source-manifest.json',OUT/'source-manifest.json')
    checks=validate(cases,charts,r,clean)
    file_hashes={str(p.relative_to(OUT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob('*') if p.is_file() and p.name!='artifact-manifest.json' and p.suffix!='.log' and 'screenshots' not in p.parts}
    write_json(OUT/'artifact-manifest.json',{'status':'draft_pending_user_review','approved_for_training':False,'case_content_sha256':content_hash,'files':file_hashes})
    write_json(GOLD/'build-summary.json',{'status':'draft_pending_user_review','approved_for_training':False,'cases':18,'turns':54,'chart_groups':18,
        'reference_crosschecks':57,'asset_checks':checks,'content_sha256':content_hash,'output':'output/analyst-gold-v3-review-2026-11-11','new_model_cost_CNY':0})
    print(json.dumps({'cases':18,'turns':54,'charts':18,'asset_checks':checks,'output':str(OUT)},ensure_ascii=False))


if __name__=='__main__':main()
