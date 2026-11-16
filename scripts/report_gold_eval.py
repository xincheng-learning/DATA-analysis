"""Build this experiment's report offline; no model or network calls."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evals/runs/gold-v1"
ROUNDS = ["baseline", "improved", "validation", "validation-v2", "histogram-check", "improved-final", "validation-final", "final"]


def report():
    ledger = json.loads((BASE / "budget.json").read_text(encoding="utf-8"))
    assert not ledger["blocked"] and all(c["status"] == "settled" for c in ledger["calls"])
    assert [c["id"] for c in ledger["calls"]] == list(range(1, len(ledger["calls"])+1))
    assert sum(c["charged_micro_cny"] for c in ledger["calls"]) == ledger["accounted_micro_cny"] <= 5_000_000
    for call in ledger["calls"]:
        audit = json.loads((BASE / call["audit_file"]).read_text(encoding="utf-8"))
        raw = json.dumps(audit["request"], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        assert hashlib.sha256(raw).hexdigest() == call["request_sha256"], call["id"]
    scores = {n: json.loads((BASE / n / "score.json").read_text(encoding="utf-8")) for n in ROUNDS}
    assert (len(ledger["calls"]), ledger["accounted_micro_cny"]) == (469, 2_873_440), "Use a new report version for another experiment"
    assert [scores[n]["metrics"]["outcomes"].get("pass", 0) for n in ("baseline", "improved-final", "validation-final", "final")] == [33, 39, 19, 16]
    lines = ["""# DataPilot 黄金集真实模型实测报告 · 2026-10-21

本轮调用已结束。469次真实模型请求，费用按官方核对日最高峰时、输入全部未命中缓存保守估算为**2.873440元**，低于授权5元。没有查询账号实际账单，实际扣款以服务商为准。

## 结果

| 集合/轮次 | 案例 | 提问轮次 | 严格通过 | 费用上界/元 |
|---|---:|---:|---:|---:|"""]
    for name in ("baseline", "improved-final", "validation-final", "final"):
        m = scores[name]["metrics"]
        lines.append(f"| [{name}](../evals/runs/gold-v1/{name}/score.json) | {m['cases']} | {m['turns']} | {m['outcomes'].get('pass',0)}/{m['turns']} | {m['cost_upper_cny']:.6f} |")
    lines.append("""
最终留出16/20轮（80%），按整案例所有轮通过为14/18（77.78%）；两条多轮链均通过。开发、已用于调优的验证及最终留出不能混加成通用准确率。

同一开发题群27轮SQL/制图查询，首次SQL执行从20/27（74.07%）提升到27/27（100%）；最终SQL执行两轮都为27/27。执行成功不是业务答案正确率。真实请求108→86，费用保守估算0.942846→0.421626元，下降55.28%。

基线3道Python题未执行，最终补齐；基线已实际执行的36轮中33通过，最终同36轮36通过。另3轮是环境补齐，不能全算作模型能力提升。耗时只为单轮观测，受网络、思考与WSL启动影响，不作统计显著性或SLA承诺。

## 方法与配置

黄金标准1.0.1：72案例、78轮、12方向，36开发/18验证/18最终留出，按模板族隔离；6张商业表与独立质量/统计/调查夹具。真实CSV/Excel→Parquet→会话DuckDB，targets实际用xlsx；调用现有Graph/调查控制器，独立会话、链内顺序，保留调用/步骤/SQL尝试预算。本轮没有走浏览器或PostgreSQL连接入口。

沿用用户加密保存的https://api.deepseek.com、deepseek-flash、auto/low设置。auto在强制工具调用时关闭思考，自由文本沿用服务商默认；low仅在显式enabled时传入。未为提分更改模型配置。评测专用输出上限4096 Token，SDK自动重试0；实际参数见每次请求。模型别名不代表可固定服务商权重版本。

模型仅得到问题、业务口径、schema、真实历史与工具结果，不发送case ID、预设路线、目录名、参考SQL或答案。数值核对列语义/行集合/精度，图表额外核对类型/轴/点数。别名、日期、明确比率单位在reviews显式映射；额外正确背景列人工核对后才能排除。Python文本人工逐字转录并绑定输出SHA256，再程序比较；调查/澄清等按量表逐项人工审阅，不能称全部自动评分。

## 改动及对照证据

| 共用问题 | 改动 | 对照记录 |
|---|---|---|
| 合法CTE、EXTRACT、除法被误拦截 | DuckDB原生解析、单语句与表边界、修正跨引号匹配 | [基线G008](../evals/runs/gold-v1/baseline/cases/G008.json)、[最终开发G008](../evals/runs/gold-v1/improved-final/cases/G008.json) |
| 季度整数CAST、比例过早舍入 | 原生日期函数提示；证据12位、文字6位 | [G009](../evals/runs/gold-v1/improved-final/cases/G009.json)、[G026](../evals/runs/gold-v1/improved-final/cases/G026.json) |
| 缺价格后继续分解 | 请求关键输入，避免对不完整总额确定归因 | [原G057](../evals/runs/gold-v1/baseline/cases/G057.json)、[最终G057](../evals/runs/gold-v1/improved-final/cases/G057.json) |
| 直方图丢频次或换图型 | 保留y+histfunc=sum；保留原始重复样本，优先指定图型 | [失败](../evals/runs/gold-v1/validation/cases/G040.json)、[复验](../evals/runs/gold-v1/validation-final/cases/G040.json) |
| Windows无法调用已有WSL Docker | 显式WSL发行版、只读挂载转换、UTF8、启动上限、单线程库 | [Docker说明](Docker隔离环境与评测日志.md)、[实跑](../evals/runs/gold-v1/checks/docker-smoke-retry.txt) |

Ubuntu-24.04原有Docker Engine 29.8.0已复用，构建datapilot-sandbox。在线构建遇旧代理超时，Windows下载官方镜像和固定依赖后离线构建。首次容器检查启动异常，重试成功，失败日志保留。模型代码始终只在非root、无网络、只读、256MB/1CPU、15秒容器限时内执行，无宿主回退。

## 留出未通过项及下一轮方向

留出只运行一次，以下问题未再用于本轮修改程序，没有用更好一次覆盖失败。

| 题号 | 实际问题 | 下一轮方向 |
|---|---|---|
| [G024](../evals/runs/gold-v1/final/cases/G024.json) | Python默认df是customers，却读取status；报错后又误说全数据缺状态，实际orders有该列 | 显式表名→DataFrame映射；错误解释以实际schema为据 |
| [G036](../evals/runs/gold-v1/final/cases/G036.json) | SQL和最终文字已正确返回空结果，但验证状态仍失败，触发3次SQL尝试 | 用户明确允许空表时正确终止；口径中的sum不应让所有问题都视作非空聚合 |
| [G065](../evals/runs/gold-v1/final/cases/G065.json) | 正确返回销售430、目标/达成率NULL，未捏造，但没有请求补目标 | 说明缺项、已知结果及具体补充请求 |
| [G066](../evals/runs/gold-v1/final/cases/G066.json) | 确实要求确认重复、负数、缺失规则，未算假总额；但没明确点名影响收入的缺价格字段，严格完整性量表未过 | 澄清具体到关键字段；这是回复完整性问题，不是捏造金额 |

## 全部收费轮次

| 轮次 | 请求数 | 输入Token | 输出Token | 费用上界/元 |
|---|---:|---:|---:|---:|""")
    for name in ROUNDS:
        m = scores[name]["metrics"]
        lines.append(f"| [{name}](../evals/runs/gold-v1/{name}/score.json) | {m['http_requests']} | {m['input_tokens']} | {m['output_tokens']} | {m['cost_upper_cny']:.6f} |")
    lines.append("""
5元由本轮脚本共享，含基线/调试/复测/留出，不是网页手动调用的总费用限制。发送前预留、返回usage后保守结算，未知费用停止，重启不清零。实际账单未核对。

## 逐题追溯

点击题号进入case，再按request_ids打开requests/同编号.json；budget.json同ID可核对Token和费用。configuration/source保存应用源码、依赖与黄金集哈希；reviews保存人工判断；score保存评分器/reviews哈希。evidence-manifest.json逐文件校验，ZIP携带完整材料。密钥、认证头、隐藏思考文本不入日志。下表采用最终开发、最终验证、最终留出同源轮次。

| 题号/轮次 | 方向 | 结果 | 原因 | 请求ID |
|---|---|---|---|---|""")
    public = json.loads((ROOT / "evals/gold_v1/cases.json").read_text(encoding="utf-8"))["cases"]
    index = {c["id"]: c for c in public}
    csv_rows = []
    for name in ("improved-final", "validation-final", "final"):
        for t in scores[name]["turns"]:
            key, number = t["key"].split(":")
            reason = t["reason"].replace("|", "/").replace("\n", " ")[:140]
            ids = ",".join(map(str, t["request_ids"]))
            lines.append(f"| [{key}:{number}](../evals/runs/gold-v1/{name}/cases/{key}.json) | {index[key]['direction']} | {t['outcome']} | {reason} | {ids} |")
            csv_rows.append({"case": key, "turn": number, "split": index[key]["split"], "run": name, "direction": index[key]["direction"], "outcome": t["outcome"], "request_ids": ids, "reason": t["reason"]})
    assert len(csv_rows) == 78 and {r["case"] for r in csv_rows} == set(index)
    lines.append("""
## 复核、勘误与边界

后端216 passed（4项原有弃用警告），Ruff仅F/E9指定检查通过，黄金集冻结校验通过。SQL越界/多语句、容器不回退、超时/停止/预算/持久化等回归仍通过。未做本轮浏览器点击/视觉验收、PostgreSQL实连入口验收、系统重启后恢复或生产负载测试。

1.0.0标准G052图点数写成3，实际参考SQL/表格一直只有2行。完整保留旧标准与原评分，1.0.1仅修元数据，题目、原始数据和数值标准不变。初次验证16/19含2次标准误判；勘误后18/19，剩余直方图是真缺陷。验证集已用于调优，最终test候选冻结后才首次调用。见[勘误](../evals/gold_v1/CHANGELOG.md)。

本集为自建合成场景、单服务商单轮运行，有人工量表及同域数据；不代表第三方盲测、跨行业泛化或对成熟Agent的胜率。简历原件未改。可审批的量化口径：建立12方向72案例/78轮可溯源黄金集；固定27轮SQL首次执行20→27；同题开发费用下降55.28%。不能写72题全部通过或通用准确率100%。

离线重算（不调模型）：.venv/Scripts/python.exe -X utf8 scripts/score_gold_eval.py final
生成本报告（不调模型）：.venv/Scripts/python.exe -X utf8 scripts/report_gold_eval.py
日志默认Git忽略，分享报告时按需携带证据包，不自动上传原始记录。

依据：[DeepSeek人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[DuckDB解析接口](https://duckdb.org/docs/stable/clients/python/reference/)、[Plotly直方图聚合](https://plotly.com/python/histograms/)、[Docker/WSL说明](https://docs.docker.com/desktop/setup/install/windows-install/)。
""")
    text = "\n".join(lines)
    (ROOT / "docs/黄金集实测报告-2026-10-21.md").write_text(text, encoding="utf-8")
    (BASE / "评测报告.md").write_text(text.replace("../evals/runs/gold-v1/", "./").replace("../evals/gold_v1/", "./standards-v1.0.1/"), encoding="utf-8")
    with (BASE / "逐题成绩.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"Report saved; {len(csv_rows)} scored turns; {ledger['accounted_micro_cny']/1e6:.6f} CNY upper estimate.")


if __name__ == "__main__":
    report()
