# Semantic Reliability Gold / 语义可靠性黄金补充集

2026-11-15续验见[40元报告](../../docs/40元续验与24图形复验-2026-11-15.md)及[公开摘要](BUDGET40_20261115.json)：两次完整轮各34/36数值通过（严格列对应28/36，6项透明审阅）；最终仍2题未交付，模型计划回放36错误全拦、1正确误拦，严格门禁仍失败。旧基线与声明计划报告保持原样。

后续修复与完整失败记录见[声明计算核对验收](../../docs/声明计算核对与黄金集改进-2026-11-15.md)及[机器摘要](DECLARED_GATES_20261115.json)。人工正确计划条件下36/36拦截，不代表模型自动规划或整体可靠性已通过；旧BASELINE保持历史原样。

**版本1.0.0，36案例、12问题族，每族3组确定性合成数据。全部用于开发校准，不是隐藏测试集。** 本集检查“SQL能运行但分析错误”、合法未知结果，以及产品是否明确说明核验边界。

它是原有gold_v1/gold_v2/analyst_gold_v3的增量补充，不覆盖它们的标签、数据、成绩或留出集合。黄金集自检通过不代表Agent通过；生产反例门禁仍有已知失败，见[基线与限制](BASELINE.md)。

## 快速复现

在项目根目录、现有项目Python环境运行，无需新增依赖。Windows使用PowerShell7；本地`.venv`可将`python`替换为`& '.venv/Scripts/python.exe' -X utf8`。

```powershell
python scripts/build_reliability_gold.py --check
python -m pytest backend/tests/test_reliability_gold.py -q
python scripts/check_reliability_canaries.py --output output/my-canaries.json
```

第三条真实执行CSV→Parquet→会话DuckDB、关联校验、结果校验和报告交付。它没有调用模型，也没有操作浏览器。输出路径已存在时拒绝覆盖。

发布前的严格门禁另加`--require-product-gates`：任意已知错误答案未拦截，或正确答案被误拦，命令退出码为1。**当前版本此门禁预期失败，不能当已通过能力对外宣传。** 普通诊断模式退出0只代表检查跑完。

可选真实模型测试沿用现有runner：

```powershell
python scripts/run_gold_eval.py --live --gold reliability_v1 --stage improved --name my-semantic-run --cases RG001 RG004 RG007 RG008 RG009 RG012
python scripts/score_gold_eval.py my-semantic-run
```

收费运行受原共享累计40元账本约束（用户2026-11-15明确上调，保留此前全部费用）；只支持已核价模型，未配置时停止，不自动改模型或重置费用。Python题必须实际Docker执行。评测使用单独SQLite记录及非流式客户端核对usage，不更改产品设置。独立会话无跨题答案或历史共享。

## 文件与信息隔离

| 文件 | 用途 |
|---|---|
|cases.json|题目、公开口径、数据配置与分组；runner仅向模型发送business_rules及当前question|
|data/、fixtures.json|15份CSV及其原始合成记录，无真实个人资料|
|answers.json|独立教师数值、参考SQL与数值容差，仅供评分|
|adversarial.json|36个可执行但错误的SQL变体及错误类别，仅供评测器|
|reference-check.json|教师/参考SQL/错误变体的确定性核对，不是模型成绩|
|manifest.json|生成源与上述文件SHA-256；日常只用--check|
|SOURCES.md|外部经验、固定源码、许可与采用范围|
|BASELINE.md|实测前后结果与未解决缺口；不挑成功记录|

题号、profile目录名、family、failure_trap、参考答案、参考SQL和反例均不得进入模型提示。`public_question`采用白名单；同题的三个数据变体一律属于development，不跨split制造虚假的泛化成绩。

## 数据卡与范围

数据由项目原创生成，不含第三方基准题目或答案。`evals/reliability_dataset.py`定义全部数据与教师；使用标准库Decimal、Counter、statistics独立计算答案，DuckDB参考SQL只做交叉核验，没有调用产品的指标计算来充当教师。

| 表 | 粒度与字段 |
|---|---|
|orders|一笔订单：order_id、customer_id、region、status、ordered_at；时间为无时区本地示意时间|
|lines|一条明细：line_id、order_id、amount；amount为该条金额，不是单价|
|targets|一个month+region：month、region、target；缺地区目标与目标0不同|
|refunds|一笔退款：refund_id、status、amount；仅settled计入|
|observations|一条观测：record_id、value、tiny；NULL、0及重复观测含义不同|

每份数据有5张小表；同题变体改变金额尺度和部分观测数值，保留相同结构，方便定位算法错误。它们没有覆盖不同关联拓扑、大表、并发、跨行业或脏日期格式；**不能据此声称跨领域稳健性。** 后续扩展拓扑或口径应另建版本，不改原失败的参考。

## 十二类反例与判分

| 类别 | 正确要求 | 错误变体 |
|---|---|---|
|join_target|目标只按月地区取一次|把目标重复SUM|
|order_grain|按唯一订单计数|按关联后明细计数|
|order_denominator|总订单金额的均值|使用明细金额均值|
|missing_target|缺目标保留NULL|补成0|
|zero_denominator|零目标时比率为NULL|返回0%|
|refund_status|仅settled退款|计入pending|
|repeat_denominator|同月已完成购买客户作分母|含取消订单客户|
|month_endpoint|自然月含月末全天、不含次月起点|BETWEEN截止月末零点|
|null_mean|忽略NULL但保留0|先将NULL补0|
|duplicate_observation|保留每条观测和顺序|DISTINCT删除相同值|
|legitimate_empty|无匹配返回合法空表|返回其他地区订单|
|tiny_sample_statistics|非零极小数、样本ddof=1|总体ddof=0；评分另拒绝把小数置0|

默认数值容差为绝对/相对1e-9；极小数题仅相对1e-8、绝对0，防止0冒充非零。NULL不能与0互换，重复数必须一致；按record_id输出的题要求顺序，其余允许行顺序不同。列别名只能经明确审阅映射，不能按列位置猜。

数值、执行、业务方法、文字报告、视觉、交互、完整导出分开。数值评分pass不证明模型选的方法适用、报告全部正确或所有用户要求完成。评分器优先真实结构化Python结果及成功执行回执；历史文本转录仅用于没有结构化表的旧记录，不能覆盖实际表。

## 贡献与版本规则

新增问题请同时提交：业务目的/口径、原始合成数据、独立教师、可运行错误变体、允许的等价答案、容差与未覆盖边界。先证明错误变体被评分器识别，再测Agent；人工规则需逐项证据，uncertain不算通过。

冻结后构建入口拒绝覆盖。标准错误须记录原因、受影响ID和新版本，保留旧标准与旧成绩。禁止为通过放宽容差、忽略重复数，或只公布修复后成功。公开结果须给源码/数据/评分器版本、模型参数、样本范围、完整失败、费用和耗时。

本集源文件及冻结资产使用Git强制LF换行，避免Windows/Linux检出改变哈希。实际测试的Python和依赖版本见BASELINE.json的tested_runtime；当前未做多版本兼容矩阵。

原创数据与代码遵循项目[MIT许可](../../LICENSE)；现有版权声明保留。参考项目的代码、数据、题目和答案未被复制进本集，来源及各自许可见SOURCES.md。
