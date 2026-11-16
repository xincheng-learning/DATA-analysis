"""Business-task gold authoring, isolated from Agent code and model calls."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import duckdb
import pandas as pd

from evals.gold_dataset import compare_rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'evals/gold_v1/fixtures.json'
VERSION = '1.0.0'

DICTIONARIES = {
    'commerce': '四张合成表：orders一行一笔订单；items一行一条商品明细，quantity为件数、unit_price为成交单价，金额单位人民币元；refunds一行一笔退款事件，amount为退款金额；targets一行一个自然月×地区的销售目标。关联键分别为order_id和月份×地区。order_date为本地下单日期，无时区换算；refunds没有结算日期，只能归属到原订单期间，不能回答现金退款发生月份。数据覆盖2026年1—4月，含取消单；未提供成本、税费和经营费用。所有订单的商品明细已完整提供。订单取消状态为canceled，已完成为completed。',
    'dirty': '新增批次orders一行一笔订单，order_id唯一。order_date是未经修复的日期文本；status为订单状态文本；amount是整笔商品金额，单位人民币元。原批次可能存在状态空格、不可解析日期及未知金额；0是有效金额。没有额外资料可恢复未知值，不可推断该批次覆盖业务全量。',
    'presentation': 'daily_sales一行一天×地区×渠道，amount为该单元已汇总商品金额，单位人民币元。报告窗口为2026-02-01至2026-02-05（含首尾）。这是未齐全的日报摘录，空单元和没有记录都不能视为0，只有显式0代表已记录的零销售。online为线上，store为门店；渠道互斥。没有更细的订单可补齐缺口。',
}
CONFIRMED = ('本任务已确认：只统计completed订单，销售额为quantity×unit_price合计，不含运费；'
             '退款单列，仅扣settled退款，归属于原订单日期，不是退款现金发生期。'
             '订单按order_id计数，客单价为商品销售额/已完成订单数。自然月含月首至次月月首之前；'
             '未知保持未知，不擅自填零或改原表。原始商品明细完整，订单无退款记录表示该范围内无已结退款。')


def profiles():
    source = json.loads(SOURCE.read_text(encoding='utf-8'))['profiles']['commerce']
    commerce = {name: deepcopy(source[name]) for name in ('orders', 'items', 'refunds', 'targets')}
    for row in commerce['items']:
        del row['unit_cost']
    for row in commerce['targets']:
        if row['month'] == '2026-04' and row['region'] == '华东':
            row['target'] = 0
    dirty = [
        ('D01', '2026-02-01', 'completed', 100),
        ('D02', '2026-02-02', ' completed ', 50),
        ('D03', 'bad-date', 'completed', 200),
        ('D04', '2026-02-03', 'completed', None),
        ('D05', '2026-02-04', 'canceled', 900),
        ('D06', '2026-03-01', 'completed', 70),
        ('D07', '2026-02-05', 'completed', 0),
    ]
    east, north = '华东地区—直营网点及线上业务联合经营组', '华北地区—直营网点及线上业务联合经营组'
    daily = [(day, region, channel, amount) for day, a, b in [
        ('2026-02-01', 100, 60), ('2026-02-02', None, 80),
        ('2026-02-04', 0, 40), ('2026-02-05', 120, None)]
        for region, channel, amount in [(east, 'online', a), (north, 'store', b)]]
    return {'commerce': commerce,
        'dirty': {'orders': [dict(zip(('order_id', 'order_date', 'status', 'amount'), row)) for row in dirty]},
        'presentation': {'daily_sales': [dict(zip(('sale_date', 'region', 'channel', 'amount'), row)) for row in daily]}}


def case(identifier, title, profile, briefing, turns, facts, required, *, cleaning=False, decisions=False):
    return {'id': identifier, 'title': title, 'profile': profile, 'split': 'workflow_regression_not_blind',
        'public_input': {'data_dictionary': DICTIONARIES[profile], 'briefing': briefing},
        'turns': [{'id': f'T{i+1}', 'kind': kind, 'message': message, 'expected_fact_ids': keys}
                  for i, (kind, message, keys) in enumerate(turns)],
        'required_evidence': required, 'expected_fact_ids': facts,
        'applicability': {'business_decision': decisions, 'cleaning': cleaning},
        'pass_rule': '所有适用验收项有实际证据；无关键错误。待人工判断不能计为通过；不要求固定路由、SQL、列别名或非必要的图型。'}


def build_cases():
    return [
        case('A01', '月度经营简报与渠道追问', 'commerce',
            '经营负责人要复核2026年2月表现。销售额是否含运费、是否扣退款尚未确认。允许先只读了解数据，正式金额计算须先给出建议、理由和影响并由分析师确认。', [
            ('request', '整理2月份经营简报：销售额、订单数、客单价和每日走势，给我可核对的表和图。', []),
            ('decision', '确认：只看已完成订单；销售额按商品成交金额，不含运费，退款单列；订单去重，客单价用销售额除订单数。按下单自然月，未知不补零。请继续。', ['feb_overview', 'feb_daily']),
            ('followup', '只看线上渠道，其他条件不变，更新这份简报。', ['feb_online'])],
            ['feb_overview', 'feb_daily', 'feb_online'],
            ['首轮给建议及理由，不默认选定关键口径或提前正式计算；只读准备可以做。',
             '确认后覆盖指标和逐日图；沿用确认，不再重复追问。', '线上追问重算；旧图保留原条件且不混入新结论。'], decisions=True),
        case('A02', '经营环比与贡献拆解', 'commerce', CONFIRMED, [
            ('request', '比较2026年2月与1月销售额，差了多少、变化百分比是多少？从订单量和客单价解释数字变化，先不猜经营原因。', ['month_compare', 'volume_aov']),
            ('followup', '再按渠道拆开两月销售变化，哪些增加、哪些减少？它们与整体差额能否对上？', ['channel_changes'])],
            ['month_compare', 'volume_aov', 'channel_changes'],
            ['不能因题目问变化就预设下降原因。', '量价分解说明方法，渠道贡献正负相加须等于总差额。',
             '基期、现期和分母一致；算术贡献不当经营因果，不要求相同解释措辞。']),
        case('A03', '地区目标复盘', 'commerce', CONFIRMED + ' 目标表缺行表示未提供目标，不能补零；目标为0时不定义完成率。', [
            ('request', '整理2026年4月各地区销售额、目标、实际减目标的差额和完成率，按销售额排名作图，方便开会跟进。', ['apr_targets']),
            ('followup', '哪些地区可以判断目标完成情况，哪些不能？给出对应依据，保留未能判断的地区。', ['apr_targets'])],
            ['apr_targets'], ['目标按月地区仅取一次，不能随订单明细倍增。',
                '缺目标和目标零分开；缺目标的差额与比率未知，零目标的比率不定义，不能写0%。',
                '地区实际销售、排序、图表与表格一致；无需编造跟进优先级的唯一建议。']),
        case('A04', '季度客户复购与渠道归属', 'commerce', CONFIRMED +
            ' 本题复购指2026年1—3月内至少2笔已完成订单。购买客户只含该期间有已完成订单者。首轮按订单渠道各自独立去重；同一客户可进入不同渠道，因此渠道人数不要求可相加。', [
            ('request', '复盘一季度购买客户和复购客户：整体有多少、复购率是多少？各渠道分别怎样？说明渠道人数能不能相加。', ['quarter_repeat', 'channel_repeat']),
            ('followup', '现在每位客户只归属一个渠道，按该客户在当前导入数据中首笔已完成订单的渠道归属。重算渠道复购，其他条件不变。', ['acquisition_repeat'])],
            ['quarter_repeat', 'channel_repeat', 'acquisition_repeat'],
            ['复购先按客户订单数计算，不能用明细数当订单数。',
             '首轮独立渠道与次轮首购归属是两套范围，均有明确分母。',
             '只能称导入数据内首购，不能扩大为客户终身首次购买。']),
        case('A05', '退款对账与净利润边界', 'commerce', CONFIRMED, [
            ('request', '给我2026年2月商品销售、已结退款和退款后金额，顺便判断净利润能否算；资料不足的部分列清楚，其他部分继续。', ['feb_finance']),
            ('followup', '成本和费用暂时补不了，先沿用上述口径，按地区列出这三项金额，不再等待利润资料。', ['feb_region_finance'])],
            ['feb_finance', 'feb_region_finance'], ['一单多退款先汇总再关联，排除pending和rejected。',
                '没有成本费用，只能给退款后商品金额，不能称净利润。',
                '可算部分应交付；缺利润资料不应导致整项拒绝或反复追问。']),
        case('A06', '新增脏数据的分析准备与清洗影响', 'dirty',
            '本轮只分析这个新增批次2026年2月已完成订单，金额只用已知值；日期无法解析的订单暂不能归入任何月份。状态必须精确为completed才纳入，除非分析师授权统一空格。当前没有任何清洗授权；不删除行、不猜日期、不填金额。', [
            ('request', '先判断这批订单能否支持2月份销售分析，有哪些影响结果的问题？不要修改数据。给我处理建议和影响。', ['dirty_quality']),
            ('decision', '同意把状态两端空格作为待处理方案，其他内容保持原样；先只预览，不应用。按可识别二月且状态精确匹配的同一口径，对照处理前后的金额与样本；未知不要补零。', ['dirty_before', 'dirty_after']),
            ('apply', '确认应用刚才预览的状态去空格方案，别改日期或金额。复核实际新版本并保留原数据。', ['dirty_after']),
            ('undo', '撤销刚才的清洗，回到原批次版本，告诉我哪些数字和图需要重新计算。', ['dirty_before'])],
            ['dirty_quality', 'dirty_before', 'dirty_after'],
            ['首轮自动查相关数据，说明有限范围，不能因局部异常全盘拒绝。',
             '预览不能切换当前版本；前后同一口径，只因状态空格处理改变样本。',
             '若原计划不支持试算，允许明确未核验且不给虚构差额；这仅通过诚实边界项，完整清洗影响能力仍未通过。',
             '应用须有实际版本和复核证据；撤销恢复原版本，旧清洗结果保持历史身份。'], cleaning=True, decisions=True),
        case('A07', '会议中的连续条件变更', 'commerce', CONFIRMED, [
            ('request', '先列2026年2月各地区商品销售额，给我比较图。', ['feb_regions']),
            ('followup', '改看1月，其他条件不变。', ['jan_regions']),
            ('followup', '仍看1月，改成扣掉已结退款后的金额，更新表和图。', ['jan_region_finance'])],
            ['feb_regions', 'jan_regions', 'jan_region_finance'],
            ['每轮结果的月份、指标和数据版本有据可查。', '不能沿用前一轮数字或图冒充新条件的结果。',
             '历史结果保留并标适用条件；本题不人工制造系统故障，环境故障另记未完成，不能算成功。']),
        case('A08', '不完整日报的图表与导出交付', 'presentation',
            '需要2026年2月1—5日日报材料。只汇报摘录内已知金额，始终标明数据不完整，不能称业务全量销售额；不修改原数据。', [
            ('request', '整理这五天销售日报：每天已知金额和地区表现都要有表和图。缺失保留，长地区名称可读，坐标注明日期和元，方便我核对。', ['daily_known', 'region_known']),
            ('followup', '现在只看线上，其他条件不变。更新逐日图，并导出与这张图同范围的五天明细，未知不要写成0。', ['online_daily'])],
            ['daily_known', 'region_known', 'online_daily'], ['缺记录、未知金额和显式0分开；2月3日不是零销售日。',
                '日期粒度可辨、单位明确、长标签能读；允许合理图型与换行。',
                'CSV须实际下载并核对完整五行及未知值；提供链接文字不等于导出通过。',
                '第二轮线上表、图、导出与条件一致，原全渠道结果保持历史身份。']),
    ]


def references(data=None):
    """Decimal teachers over records, plus separately executed SQL references."""
    data = data or profiles()
    tables = data['commerce']
    refunds = defaultdict(Decimal)
    for row in tables['refunds']:
        if row['status'] == 'settled':
            refunds[row['order_id']] += Decimal(str(row['amount']))
    orders = []
    for row in tables['orders']:
        if row['status'] != 'completed':
            continue
        amount = sum((Decimal(str(line['quantity'])) * Decimal(str(line['unit_price']))
                      for line in tables['items'] if line['order_id'] == row['order_id']), Decimal(0))
        orders.append({**row, 'amount': amount, 'refund': refunds[row['order_id']]})
    base = """WITH line_totals AS (SELECT order_id,SUM(quantity*unit_price) amount FROM items GROUP BY order_id),
        refund_totals AS (SELECT order_id,SUM(amount) refund FROM refunds WHERE status='settled' GROUP BY order_id),
        c AS (SELECT o.*,l.amount,COALESCE(r.refund,0) refund FROM orders o JOIN line_totals l USING(order_id)
        LEFT JOIN refund_totals r USING(order_id) WHERE o.status='completed') """
    out = {}

    def add(key, profile, rows, sql):
        out[key] = {'profile': profile, 'rows': rows, 'columns': list(rows[0]), 'reference_sql': sql,
            'comparison': {'ordered': False, 'abs_tol': 0.000001, 'rel_tol': 0.00000001}}

    def selected(month):
        return [r for r in orders if r['order_date'][:7] == month]

    def sum_amount(rows):
        return sum((r['amount'] for r in rows), Decimal(0))

    jan, feb = selected('2026-01'), selected('2026-02')
    total_jan, total_feb = sum_amount(jan), sum_amount(feb)
    for key, rows, where in [('feb_overview', feb, ''), ('feb_online', [r for r in feb if r['channel'] == 'online'], " AND channel='online'")]:
        add(key, 'commerce', [{'sales': float(sum_amount(rows)), 'orders': len(rows), 'aov': float(sum_amount(rows)/len(rows))}],
            base + "SELECT SUM(amount) sales,COUNT(*) orders,AVG(amount) aov FROM c WHERE order_date LIKE '2026-02%'" + where)
    daily = defaultdict(Decimal)
    for row in feb:
        daily[row['order_date']] += row['amount']
    add('feb_daily', 'commerce', [{'date': k, 'sales': float(v)} for k, v in sorted(daily.items())],
        base + "SELECT order_date date,SUM(amount) sales FROM c WHERE order_date LIKE '2026-02%' GROUP BY order_date")
    add('month_compare', 'commerce', [{'jan_sales': float(total_jan), 'feb_sales': float(total_feb),
        'delta': float(total_feb-total_jan), 'change_pct': float((total_feb-total_jan)/total_jan*100)}],
        base + "SELECT SUM(amount) FILTER(WHERE order_date LIKE '2026-01%') jan_sales,SUM(amount) FILTER(WHERE order_date LIKE '2026-02%') feb_sales,feb_sales-jan_sales delta,delta/jan_sales*100 change_pct FROM c")
    n0, n1 = Decimal(len(jan)), Decimal(len(feb))
    a0, a1 = total_jan/n0, total_feb/n1
    add('volume_aov', 'commerce', [{'jan_orders': int(n0), 'feb_orders': int(n1), 'jan_aov': float(a0), 'feb_aov': float(a1),
        'volume_effect': float((n1-n0)*a0), 'aov_effect': float(n1*(a1-a0))}],
        base + "SELECT COUNT(*) FILTER(WHERE order_date LIKE '2026-01%') jan_orders,COUNT(*) FILTER(WHERE order_date LIKE '2026-02%') feb_orders,AVG(amount) FILTER(WHERE order_date LIKE '2026-01%') jan_aov,AVG(amount) FILTER(WHERE order_date LIKE '2026-02%') feb_aov,(feb_orders-jan_orders)*jan_aov volume_effect,feb_orders*(feb_aov-jan_aov) aov_effect FROM c")
    channels = sorted({r['channel'] for r in jan+feb})
    add('channel_changes', 'commerce', [{'channel': ch, 'jan_sales': float(sum_amount([r for r in jan if r['channel'] == ch])),
        'feb_sales': float(sum_amount([r for r in feb if r['channel'] == ch])),
        'delta': float(sum_amount([r for r in feb if r['channel'] == ch])-sum_amount([r for r in jan if r['channel'] == ch]))} for ch in channels],
        base + "SELECT channel,SUM(amount) FILTER(WHERE order_date LIKE '2026-01%') jan_sales,SUM(amount) FILTER(WHERE order_date LIKE '2026-02%') feb_sales,feb_sales-jan_sales delta FROM c WHERE order_date<'2026-03-01' GROUP BY channel")
    for month, prefix in [('2026-01', 'jan'), ('2026-02', 'feb')]:
        rows = selected(month)
        region_rows = [{'region': region, 'sales': float(sum_amount([r for r in rows if r['region'] == region])),
            'refund': float(sum((r['refund'] for r in rows if r['region'] == region), Decimal(0)))} for region in sorted({r['region'] for r in rows})]
        add(prefix+'_regions', 'commerce', [{k: r[k] for k in ('region', 'sales')} for r in region_rows],
            base + f"SELECT region,SUM(amount) sales FROM c WHERE order_date LIKE '{month}%' GROUP BY region")
        add(prefix+'_region_finance', 'commerce', [{**r, 'net_sales': r['sales']-r['refund']} for r in region_rows],
            base + f"SELECT region,SUM(amount) sales,SUM(refund) refund,SUM(amount)-SUM(refund) net_sales FROM c WHERE order_date LIKE '{month}%' GROUP BY region")
    add('feb_finance', 'commerce', [{'sales': float(total_feb), 'refund': float(sum((r['refund'] for r in feb), Decimal(0))),
        'net_sales': float(sum((r['amount']-r['refund'] for r in feb), Decimal(0)))}],
        base + "SELECT SUM(amount) sales,SUM(refund) refund,SUM(amount)-SUM(refund) net_sales FROM c WHERE order_date LIKE '2026-02%'")
    targets = {r['region']: r['target'] for r in tables['targets'] if r['month'] == '2026-04'}
    apr = selected('2026-04')
    target_rows = []
    for region in sorted({r['region'] for r in apr}):
        amount, target = sum_amount([r for r in apr if r['region'] == region]), targets.get(region)
        target_rows.append({'region': region, 'sales': float(amount), 'target': target,
            'difference': float(amount-target) if target is not None else None,
            'attainment_pct': float(amount/target*100) if target else None})
    add('apr_targets', 'commerce', target_rows,
        base + "SELECT c.region,SUM(amount) sales,t.target,sales-t.target difference,sales/NULLIF(t.target,0)*100 attainment_pct FROM c LEFT JOIN targets t ON t.region=c.region AND t.month='2026-04' WHERE order_date LIKE '2026-04%' GROUP BY c.region,t.target")
    quarter = [r for r in orders if r['order_date'] < '2026-04-01']

    def repeat(rows):
        counts = Counter(r['customer_id'] for r in rows)
        n, returning = len(counts), sum(count >= 2 for count in counts.values())
        return {'buyers': n, 'repeat_buyers': returning, 'repeat_pct': returning/n*100}

    add('quarter_repeat', 'commerce', [repeat(quarter)], base + "SELECT COUNT(*) buyers,COUNT(*) FILTER(WHERE n>=2) repeat_buyers,repeat_buyers*100.0/buyers repeat_pct FROM (SELECT customer_id,COUNT(*) n FROM c WHERE order_date<'2026-04-01' GROUP BY customer_id)")
    first = {customer: min((r for r in orders if r['customer_id'] == customer), key=lambda r: (r['order_date'], r['order_id']))['channel'] for customer in {r['customer_id'] for r in quarter}}
    add('channel_repeat', 'commerce', [{'channel': ch, **repeat([r for r in quarter if r['channel'] == ch])} for ch in channels],
        base + "SELECT channel,COUNT(*) buyers,COUNT(*) FILTER(WHERE n>=2) repeat_buyers,repeat_buyers*100.0/buyers repeat_pct FROM (SELECT channel,customer_id,COUNT(*) n FROM c WHERE order_date<'2026-04-01' GROUP BY channel,customer_id) GROUP BY channel")
    add('acquisition_repeat', 'commerce', [{'channel': ch, **repeat([r for r in quarter if first[r['customer_id']] == ch])} for ch in channels],
        base + ", first_channel AS (SELECT customer_id,FIRST(channel ORDER BY order_date,order_id) channel FROM c GROUP BY customer_id) SELECT f.channel,COUNT(*) buyers,COUNT(*) FILTER(WHERE n>=2) repeat_buyers,repeat_buyers*100.0/buyers repeat_pct FROM (SELECT customer_id,COUNT(*) n FROM c WHERE order_date<'2026-04-01' GROUP BY customer_id) q JOIN first_channel f USING(customer_id) GROUP BY f.channel")
    dirty = data['dirty']['orders']
    invalid = 0
    for row in dirty:
        try:
            date.fromisoformat(row['order_date'])
        except ValueError:
            invalid += 1
    add('dirty_quality', 'dirty', [{'rows': len(dirty), 'invalid_dates': invalid,
        'status_whitespace': sum(r['status'] != r['status'].strip() for r in dirty),
        'missing_amount': sum(r['amount'] is None for r in dirty)}],
        'SELECT COUNT(*) "rows",COUNT(*) FILTER(WHERE TRY_CAST(order_date AS DATE) IS NULL) invalid_dates,COUNT(*) FILTER(WHERE status<>TRIM(status)) status_whitespace,COUNT(*) FILTER(WHERE amount IS NULL) missing_amount FROM orders')
    for key, trim in [('dirty_before', False), ('dirty_after', True)]:
        rows = [r for r in dirty if (r['status'].strip() if trim else r['status']) == 'completed' and r['order_date'].startswith('2026-02')]
        add(key, 'dirty', [{'orders': len(rows), 'known_amount': sum(r['amount'] for r in rows if r['amount'] is not None),
            'missing_amount': sum(r['amount'] is None for r in rows)}],
            "SELECT COUNT(*) orders,SUM(amount) known_amount,COUNT(*) FILTER(WHERE amount IS NULL) missing_amount FROM orders WHERE " +
            ("TRIM(status)" if trim else 'status') + "='completed' AND TRY_CAST(order_date AS DATE)>=DATE '2026-02-01' AND TRY_CAST(order_date AS DATE)<DATE '2026-03-01'")
    daily_rows = data['presentation']['daily_sales']
    for key, online in [('daily_known', False), ('online_daily', True)]:
        rows = [r for r in daily_rows if not online or r['channel'] == 'online']
        result = []
        for day in range(1, 6):
            stamp = f'2026-02-{day:02d}'
            cells = [r for r in rows if r['sale_date'] == stamp]
            known = [r['amount'] for r in cells if r['amount'] is not None]
            result.append({'date': stamp, 'known_amount': sum(known) if known else None,
                'unknown_cells': sum(r['amount'] is None for r in cells), 'recorded_cells': len(cells)})
        add(key, 'presentation', result,
            "WITH days AS (SELECT CAST(d AS VARCHAR) date FROM generate_series(DATE '2026-02-01',DATE '2026-02-05',INTERVAL 1 DAY) t(d)),s AS (SELECT * FROM daily_sales" +
            (" WHERE channel='online'" if online else '') + ") SELECT SUBSTR(days.date,1,10) date,SUM(s.amount) known_amount,COUNT(*) FILTER(WHERE s.sale_date IS NOT NULL AND s.amount IS NULL) unknown_cells,COUNT(s.sale_date) recorded_cells FROM days LEFT JOIN s ON s.sale_date=SUBSTR(days.date,1,10) GROUP BY days.date")
    add('region_known', 'presentation', [{'region': region, 'known_amount': sum(r['amount'] for r in daily_rows if r['region'] == region and r['amount'] is not None),
        'unknown_cells': sum(r['amount'] is None for r in daily_rows if r['region'] == region), 'recorded_cells': sum(r['region'] == region for r in daily_rows)} for region in sorted({r['region'] for r in daily_rows})],
        "SELECT region,SUM(amount) known_amount,COUNT(*) FILTER(WHERE amount IS NULL) unknown_cells,COUNT(*) recorded_cells FROM daily_sales GROUP BY region")
    return out


def verify_references(data=None):
    data = data or profiles()
    expected = references(data)
    for profile, tables in data.items():
        with duckdb.connect() as con:
            for name, rows in tables.items():
                con.register(name, pd.DataFrame(rows))
            for key, fact in expected.items():
                if fact['profile'] != profile:
                    continue
                result = con.execute(fact['reference_sql']).fetchdf()
                actual = result.astype(object).where(pd.notna(result), None).to_dict('records')
                assert compare_rows(actual, fact['rows'], **fact['comparison']), key
    return expected


def public_input(scenario, turn):
    """Only business data/briefing and the current message enter model input."""
    return {**scenario['public_input'], 'message': turn['message']}
