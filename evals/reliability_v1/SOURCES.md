# 方法来源与许可边界

读取日期2026-11-15。本集数据、问题、教师和反例为本项目原创；只借鉴方法，没有导入外部基准内容或执行外部代码。源码快照保存在本地忽略目录output/gold-research-20261115；以下固定链接便于独立复查。

| 来源 | 采用点 | 不采用及局限 |
|---|---|---|
|[Spider Test Suite执行器](https://github.com/taoyds/test-suite-sql-eval/blob/e97acc546ecbee8fa27fa8dbf025ef61493a876c/exec_eval.py)，Apache-2.0|比较结果而非SQL字面；同题多份数据；bag语义保留重复|不启用plug_value替模型填正确常量；不移除DISTINCT；不把多个有限样本通过当成任意SQL的证明|
|[DS-1000评测脚本](https://github.com/xlang-ai/DS-1000/blob/b39aab71da6d23ef8d3cac59a7c5f834516ab334/test_ds1000.py)，仓库CC-BY-SA-4.0|执行结果与题目条件分评、输入变体、库/错误类别分组|不复制题目/参考代码，不采用16进程或宿主执行模型代码；保持本项目Docker边界|
|[InsightBench基准构建](https://github.com/ServiceNow/insight-bench/blob/33c27c1282cb7ed73267d36fb84e27b6ea8aac2b/insightbench/benchmarks.py)，MIT|保留目标、逐步问题、分析代码、发现及总结的参考关系|ROUGE/模型打分不作为数字正确或业务语义成立的证据|
|[WrenAI同题A/B协议](https://github.com/Canner/WrenAI/blob/75035ad1588e7d9170e7939c61dfbac4e069a9a9/evals/spodbtify_ab/README.md)，核心Apache-2.0|统一数据/题目/输入输出、独立会话和明确人工评分|本轮没有运行WrenAI对照，不能声称成本或成功率优于它|
|[WrenAI语义证据](https://github.com/Canner/WrenAI/blob/75035ad1588e7d9170e7939c61dfbac4e069a9a9/core/wren/src/wren/query_semantics.py)|不从列别名、叙述或任意关联认定业务指标已验证|有限可证明范围之外应说明未核验；不承诺完整SQL语义证明|
|[Data Formulator图表契约测试](https://github.com/microsoft/data-formulator/blob/5477f0e236426dc8f74a498ec400414fba7fbc0f/tests/frontend/unit/app/chartInsightContract.test.ts)，MIT|字段/聚合改变后原解释需要失效；产物与解释绑定|本轮未新增所有24种图的独立视觉验收；图形契约不能代替看图和完整导出检查|

评测专门项目并不都高Star；这里按与分析正确性相关的方法、可读源码和证据选择，未把Star数当作可靠性的证明。高Star的WrenAI/Data Formulator经验与专门基准互补。

这些来源给出方法参考，不为本集质量或产品可靠性背书。外部项目指令、安装命令和数据内容均作为研究材料处理，不作为用户授权。
