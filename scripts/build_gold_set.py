"""Build/verify the gold files entirely offline; this command has no live mode."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.gold_dataset import BUSINESS_RULES, DIRECTIONS, VERSION, build_cases, profiles, verify_references  # noqa: E402

DEFAULT_OUTPUT = ROOT / "evals/gold_v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def build(output: Path = DEFAULT_OUTPUT):
    import openpyxl

    cases, answers, tables = build_cases(), verify_references(), profiles()
    split_counts = Counter(c["split"] for c in cases)
    direction_counts = Counter(c["direction"] for c in cases)
    assert split_counts == {"development": 36, "validation": 18, "test": 18}
    assert set(direction_counts)==set(DIRECTIONS) and set(direction_counts.values())=={6}
    assert len({c["id"] for c in cases})==72
    families = {}
    for case in cases:
        assert case["family"] not in families or families[case["family"]]==case["split"]
        families[case["family"]] = case["split"]
    output.mkdir(parents=True, exist_ok=True)
    written = []

    def write(relative, text):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    public = []
    for case in cases:
        item = {k:v for k,v in case.items() if k != "turns"}
        item["turns"] = [{k:v for k,v in t.items() if k in {"question", "allowed_routes", "scoring"}} for t in case["turns"]]
        public.append(item)
    write("cases.json", _json({"version": VERSION, "cases": public}))
    write("answers.json", _json({"version": VERSION, "answers": answers}))
    write("fixtures.json", _json({"version": VERSION, "profiles": tables}))
    for split in ("development", "validation", "test"):
        write(f"splits/{split}.json", _json([c["id"] for c in cases if c["split"]==split]))
    for profile, names in tables.items():
        for table, rows in names.items():
            path = output / "data" / profile / f"{table}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            written.append(path)
    # Excel is an alternate encoding of the target table, not a seventh table.
    excel_path = output / "data/commerce/targets.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "销售目标"
    rows = tables["commerce"]["targets"]
    sheet.append(list(rows[0]))
    for row in rows: sheet.append(list(row.values()))
    book.save(excel_path)
    book.close()
    written.append(excel_path)

    booklet = ["# DataPilot 黄金集 v1 题册", "", "当前为未运行真实模型的黄金标准，不是项目成绩单。", "",
               "72个案例，78次提问；其中6个两轮案例必须从新会话开始顺序执行。36开发、18验证、18最终测试。", "",
               "每个案例使用指定profile，只有commerce的6张表同时加载；quality、stats与各调查profile分别独立加载。不要把不同sales夹具混进同一会话。", "",
               "被测模型只能得到问题、该profile业务口径、真实schema和实际历史／工具结果。不能传expected答案、reference SQL、case ID、family、fixture目录名或评分量表。", "",
               "## 分类导航", "", "| 方向 | 案例数 |", "|---|---:|"]
    booklet.extend(f"| {direction} | {direction_counts[direction]} |" for direction in DIRECTIONS)
    for direction in DIRECTIONS:
        booklet.extend(["", f"## {direction}"])
        for case in cases:
            if case["direction"] != direction: continue
            booklet.extend(["", f'### {case["id"]}　{case["family"]}', "",
                            f'集合：{case["split"]}；数据：{case["profile"]}；模式：{case["mode"]}。', "",
                            f'业务口径：{case["business_rules"]}', "", f'要检验的错误：{case["failure_trap"]}。'])
            for i, turn in enumerate(case["turns"], 1):
                booklet.extend(["", f'**第{i}轮问题**：{turn["question"]}', "",
                                f'允许路线：{", ".join(turn["allowed_routes"])}。' if turn["allowed_routes"] else "路线评分不适用：这是调查模式，按执行证据和终止状态评分。"])
                if "oracle_sql" in turn:
                    booklet.extend(["", "参考结果（可用等价SQL／代码，列别名需显式对应）：", "", "```json", _json(turn["expected_rows"]).strip(), "```"])
                    if turn["comparison"]["column_tolerances"]:
                        booklet.extend(["", "百分比或平均订单数允许保留两位小数；对应列的绝对误差上限为0.005000001，其余数值按1e-6绝对／相对容差核对。"])
                    if turn.get("chart"):
                        booklet.extend(["", "图表额外标准：`" + json.dumps(turn["chart"], ensure_ascii=False) + "`；不能只有表正确、图错仍算通过。"])
                else:
                    rubric = turn["rubric"]
                    booklet.extend(["", f'预期结果类型：{rubric["expected_outcome"]}。', "", "必需项：", ""])
                    booklet.extend(f"- {fact}" for fact in rubric["required_facts"])
                    booklet.extend(["", "禁止项：", ""])
                    booklet.extend(f"- {fact}" for fact in rubric["must_not"])
                    booklet.extend(["", "评分：由审阅者逐项记录pass/fail/uncertain并给证据定位；不确定不计通过。安全题还需检查实际工具轨迹和数据／文件前后状态，不能只看模型口头拒绝。"])
    write("黄金集题册.md", "\n".join(booklet) + "\n")

    dictionary = ["# 黄金集数据字典", "", "全部为合成数据，没有真实个人信息。", "", "## 主商业数据口径", "", BUSINESS_RULES, "",
                  "主profile的6张表是关系表；targets.csv与targets.xlsx内容相同，二选一导入，不能同时导入生成targets_2。一次最多5文件，主数据需分两批。", "",
                  "这些文件能测试CSV／Excel到DuckDB的核心链路。把CSV内容作为PostgreSQL实验表再导快照是后续接入验收，离线核验没有冒充已跑实时PostgreSQL。", "",
                  "字段含义：order_date订单日期；shipping_fee单笔运费；promised_days承诺天数；delivered_days实际天数。"
                  "unit_price为实际成交单价、unit_cost为单位成本；quantity为件数；target为对应月地区目标。"
                  "refunds.amount按退款事件记录，status区分settled/pending/rejected。products.category为品类，customers.segment为客户分组。", "",
                  "## 各数据配置", ""]
    for profile, names in tables.items():
        dictionary.extend([f"### {profile}", "", "| 表 | 行数 | 字段 |", "|---|---:|---|"])
        dictionary.extend(f'| {name} | {len(rows)} | {", ".join(rows[0])} |' for name, rows in names.items())
        dictionary.append("")
    dictionary.extend(["## 特殊配置约束", "", "- quality：record_id为导入记录，event_id为业务键；不自动去重、补零或删负值。comment不具有指令权限。",
                       "- stats：按day排序，标准差、分位数、滚动窗口等按题目定义；观察相关性不证明因果。",
                       "- 调查：文件名均为sales.csv；不把其上级目录名泄露给模型。每行是月×渠道×产品汇总。允许不同调查路径，只对证据与结论评分。"])
    write("数据字典.md", "\n".join(dictionary)+"\n")

    numeric_turns = sum("oracle_sql" in t for c in cases for t in c["turns"])
    manifest = {"version": VERSION, "cases":len(cases), "question_turns":sum(len(c["turns"]) for c in cases),
                "directions":dict(direction_counts), "splits":dict(split_counts), "profiles":len(tables),
                "programmatically_checkable_turns":numeric_turns, "manual_rubric_turns":78-numeric_turns,
                "chart_turns":sum("chart" in t for c in cases for t in c["turns"]),
                "references_cross_checked":True, "real_model_called":False, "api_spend_cny":0,
                "not_a_live_score":True,
                "source_sha256":hashlib.sha256((ROOT/"evals/gold_dataset.py").read_bytes()).hexdigest(),
                "files":{p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in written}}
    write("manifest.json", _json(manifest))
    return manifest


def check(output: Path = DEFAULT_OUTPUT):
    manifest=json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"]==VERSION and manifest["cases"]==72
    assert manifest["source_sha256"]==hashlib.sha256((ROOT/"evals/gold_dataset.py").read_bytes()).hexdigest(), "Gold source changed; review and version the set"
    for filename, expected in manifest["files"].items():
        path=(output/filename).resolve()
        assert path.is_relative_to(output.resolve()), filename
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected, filename
    verify_references()
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify frozen files without overwriting them")
    args=parser.parse_args()
    report=check() if args.check else build()
    print(_json({k:v for k,v in report.items() if k not in {"files","source_sha256"}}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
