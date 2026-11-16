# 第二轮补充检查（2.0.0-supplement）

12个单轮案例，覆盖新表名的客户间隔、多表Python、Excel目标联表、合法空表、零分母、具体缺失澄清、已确认质量口径、因果边界与图表。

使用独立合成的tickets/accounts/targets、ledger、lines数据，数值与v1不同；目标表实际读取Excel。每题隔离新会话，标准在第二轮第一次真实请求之前冻结，数值经手算及DuckDB交叉核验。模型只看到业务口径、问题、真实schema和实际历史/工具结果，不读取答案或题号。

这是同领域的补充检查，部分题型与已修复缺陷相近，不能冒充外部盲测或跨行业泛化证明。冻结后只运行一次；不按补充结果继续改应用。新增README不属于已冻结题目/数据/答案清单，不改变标准。

离线核验（无API费用）：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/build_optimization_set.py --check
```

真实评测复用`scripts/run_gold_eval.py --gold gold_v2 --stage final`，仍使用原`evals/runs/gold-v1/budget.json`，总预算不重置。

首轮补充检查已完成：10/12通过；X002结构化route字段格式错误、X007未先查目标表，原始失败保留。用户随后将累计预算提高至10元继续优化，这份题集由此转为回归材料；后续复测不能再称首次新题结果。
