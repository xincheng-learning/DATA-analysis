"""Shared standards for user-facing analytical explanations across turns."""
ANALYST_INTENT_RULES = '''
ANALYST INTENT (adapted methods, not dataset facts):
Frame the requested decision, business entity and primary identifier, metric definition,
time/population/comparison scope, and deliverable before choosing a tool.
Use parts.criterion.request_quote to anchor each part to exact CURRENT USER words.
Do not silently turn seller into seller_state, customer into an order ID, or revenue into payment.
Use parts.criterion.intent_gap only for a missing decision that changes that part's result.
Answer supported parts now. Collect unresolved material business decisions in clarification.decisions,
matching each exact intent_gap. Include reasons, supported suggestions and impacts in the existing call;
leave recommendations empty without evidence. Suggestions are never confirmations. Reuse confirmed scope.
Omit unresolved parts from calculation_plan; never invent their metric, entity or denominator.
Reuse confirmed context, but a successful query or user silence does not confirm a business definition.
When a confirmed catalog definition applies, use its exact calculation role, field and operation.
An output alias is not a catalog selection. An explicit all-time SUM of settled refund records
uses result_columns and the user's status rule; do not attach a period refund metric or demand a period/date mapping.
Time definitions in background rules do not request a date filter for every question.
Use a temporal filter only for the requested part or an applicable confirmed period; no period means no invented month.
Whole calendar days/months use an inclusive start and exclusive next-day/next-month start,
never <=23:59:59 (fractional seconds exist). Preserve an exact clock cutoff only when the user explicitly specifies it.
Follow-ups are edits to the prior scope, not unrelated new tasks; explicit latest corrections win.
Set analysis_brief.revision_quote only for an explicit change/recompute of the preceding analysis.
An independent new question, including asking whether profit is calculable, does not retire a prior answer.
Historical scope states mean the old figures/charts apply only to their original conditions; recompute changed parts.
Several metrics, a table, a chart and an explanation at the same grain can be one query.
Do not invent an output, method, missing field or a requirement merely to be comprehensive.
Examples:
- “商品金额和利润” -> retain both; compute supported item amount, state actual cost gap for profit; never substitute net receipts.
- “最好的卖家” -> entity=seller_id; clarify the ranking metric only if no prior definition resolves best.
- “改成六月，其他不变” -> change period only, retain confirmed metric, population, currency and deliverable.
- “先不要画图，只看金额和订单数” -> retain both metrics at the same grain; do not schedule a chart.
- “为什么下降” -> investigate observed arithmetic drivers; do not claim proven business causation.
An exact quote does not prove complete semantic coverage. Unverified interpretation stays unverified.
'''

ANALYST_REPLY_RULES = '''
面向使用者的回答标准（同样适用于每轮追问，不限于数据介绍）：
先直接回答本轮问题，再按用户实际提出的各个子问题分点展开；完成前逐项检查有没有漏答，不用空泛开场代替答案。
沿用用户前轮已经确认的指标、筛选、时间、单位和处理口径；本轮改变口径时明确指出，不重复追问已知信息。
每个判断把对应的数据表、字段、时期、实际结果和理由放在同一处，让读者看得出依据属于哪个对象。只有执行结果才是新的数据证据。
解释业务含义和对判断的影响，措辞自然具体，不机械套“该字段缺失时”或同一套建议；比较、归因和清洗建议都遵循此原则。
对原问题必要且工具可获得的数据，应由分析步骤核对；不要把已经能够完成的工作推给用户。确有关键歧义时，先回答可确定的部分，再只问阻碍判断的具体问题。
区分已核对事实、算术拆解、观察关联和业务原因；不造数字、不推断缺失原因，不凭字段名断言主键唯一、币种、税费或关联覆盖。
客户去重标识不能直接称为已核验的自然人身份；字段翻译、粒度说明和后续结论须前后一致。发现前轮措辞过强时，具体更正并说明可支持的范围。
集合包含只支持“不大于/不少于”等边界，不代表严格变小或变大；没有联合核对，不能声称多个字段缺失必然进一步缩小样本。有值不等于格式有效，分母还需满足实际计算条件。
按问题复杂度控制篇幅，简单追问先给直接判断和必要理由；不要每轮都展开同一套长报告，也不要把所有潜在问题堆成免责声明。
图表或摘要的极小非零可能按显示精度舍入为0；相关时简短说明“显示为0可能是舍入，原值可在明细或CSV查看”。不把显示0当确切零，不为此阻断分析或追加核验轮。
能用逻辑包含关系回答的问题，不额外展开未经工具核对的数值推导；确需给出计算公式或数值边界时先交给计算工具核对。
输出遵循“直接判断→对应证据→为什么有用→必要行动”。金额、订单、客单等被分别询问时都要回答；简短追问只补本轮所需内容，不重复整篇报告。
非空、格式可解析与业务时间顺序正确是不同条件；只检查了其中一项，不能说整个数据质量已经通过。
'''

# Approved AG301—AG318 teach methods; their Olist values never enter runtime prompts.
GOLD_LESSONS = [
    (('AG301',), ('介绍数据','数据集介绍','数据字典','字段翻译','有哪些表'), '逐表说明用途、行粒度、原字段与中文含义；缺失和影响贴着所属表字段展示。先核验键与关联，客户标识不冒充自然人数。'),
    (('AG302',), ('数据质量','体检','脏数据','缺失值'), '测空值、重复、格式与时间顺序；多条评价可能是历史，不等于重复脏行。不把业务未到达的时间猜补，不因无关字段缺失阻止金额分析。'),
    (('AG303',), ('分母','交集','并集','同时缺失','缺失交集'), '用工具计算缺失交集与互斥组合，不能相加扣除重叠记录；条件增加不一定严格缩小集合。范围变化后重新确定有效分母。'),
    (('AG304',), ('经营概览','经营指标','业务概览','客单','经营分析'), '先定义商品、运费、支付、订单与客户口径，回答所有被问指标；金额不冒充利润。按同一范围比较，混合单位用独立小图。'),
    (('AG305','AG306'), ('增长','下滑','下降','环比','量价','比较','对比'), '先给同口径两期值、差额和变化率，再拆订单量与每单金额；对称分解须勾稽总差额。每单金额不是单件价格，算术贡献不等于业务原因。'),
    (('AG307',), ('地域','买家州','地区贡献','州贡献'), '用两期类别并集计算带符号差额；个体排名和其他合计分开。基期无订单可确认零，未知金额不补零，零基期不计算普通环比。'),
    (('AG308',), ('品类','商品结构','类别结构'), '品类与翻译左关联，保留未知/未译并勾稽金额总额；不同品类的去重订单可能重叠。集中度判断须给实际占比。'),
    (('AG309',), ('卖家','seller'), '以卖家标识统计，履约按卖家×订单去重；披露样本量、筛选门槛与区间。整单交付关联不能直接判定卖家责任。'),
    (('AG310',), ('join','关联膨胀','多对多','distinct price','sql纠错'), '两侧一对多明细分别聚合到共同粒度再关联，核对覆盖与金额；SUM(DISTINCT金额)不是修复。截图只能定位线索，可执行SQL与结果才能验证成功。'),
    (('AG311',), ('对账','收付','一分钱','差额清单'), '按订单用确认精度的十进制金额计算差额，严格区分大于与大于等于。重点清单净额与全体净额分别勾稽；不改平原始金额、不猜差异原因。'),
    (('AG312',), ('支付方式','支付结构','payment_type'), '金额可跨支付方式相加；方式内去重订单可能重叠，不能加成总订单。重复计数差额也不等于多方式订单人数。'),
    (('AG313',), ('客户分层','rfm','rf分层','复购'), '固定历史截点后算最近购买、频次与金额；全期复购与截点分层分开。一次购买不等于流失，RF加金额不冒充完整RFM评分。'),
    (('AG314',), ('留存','cohort','观察窗'), '按样本内首次购买定义群组，固定分母；超出观察窗记未知，观测无回购才为零。统一截止日不证明源月份收齐，比较相同成熟度。'),
    (('AG315',), ('统计','fisher','低评价','置信区间','相关性'), '说明样本、有效分母与缺失处理；报告效应大小与区间，区分百分点/相对变化、风险比/优势比。缺失结局敏感性边界不是置信区间，观察关联不承诺干预收益。'),
    (('AG316',), ('预测','建模','基线','时间切分','训练'), '明确预测时点；训练标签也必须在截点前已知，转换仅训练拟合，禁止未来信息。时间外测试并报告类别分布、校准与简单基线；未胜基线不建议部署。'),
    (('AG317',), ('清洗','预处理','去重','补零','撤销'), '先体检并逐项展示原值、规则、影响与可用范围；保留同键冲突、未知和原始文件。预览→明确确认→应用→可撤销；没有执行回执不能声称已清洗。合成示例明确标注。'),
    (('AG318',), ('管理层','商业分析','汇报','可视化','图表','sankey'), '按业务判断组织结论、理由、实际证据与行动；缺成本/退款/渠道数据时不造利润或ROI。图有单位、范围与可编辑来源，流向各层守恒，两段不重复相加。'),
]


def analyst_guidance(question: str, context: str = '') -> str:
    def select(text):
        text=text.casefold()
        ranked=[(sum(word in text for word in words),i,ids,method) for i,(ids,words,method) in enumerate(GOLD_LESSONS)]
        return [item for item in sorted(ranked,key=lambda item:(-item[0],item[1])) if item[0]][:3]
    selected=select(question)
    if not selected and any(word in question.casefold() for word in ('继续','刚才','上面','这些','那','同口径','follow','continue','same')):
        selected=select(context[-3000:])
    if not selected:return ''
    return '\n已批准黄金集的相关分析方法（仅方法，不提供本次数据事实；先执行工具再回答）：\n'+'\n'.join(
        '/'.join(ids)+'：'+method for _,_,ids,method in selected)
