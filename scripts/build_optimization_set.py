"""Freeze a small supplemental set before any live run; never calls a model."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import duckdb
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.gold_dataset import compare_rows  # noqa: E402
from evals.live_budget import write_json  # noqa: E402

OUTPUT = ROOT / "evals/gold_v2"
VERSION = "2.0.0-supplement"
RULES = ("全部为合成数据，金额单位元。tickets一行一单，ticket_id为唯一键，"
         "只含state=done的订单。销售额=sum(units*unit_price)。accounts一行一位客户；"
         "targets按month+zone唯一。日期按自然月，缺失目标不能补0，目标为0时达成率NULL。"
         "不能通过修改筛选条件制造匹配数据。")


def build():
    if (OUTPUT / "manifest.json").exists():
        raise RuntimeError("补充标准已经冻结；仅允许--check核验，不覆盖。")
    tickets = [dict(zip(("ticket_id", "ordered_at", "account", "zone", "state", "units", "unit_price"), row)) for row in [
        ("Z1", "2026-08-01", "A9", "西南", "done", 1, 11),
        ("Z2", "2026-08-06", "A9", "西南", "done", 2, 22),
        ("Z3", "2026-08-16", "A9", "西南", "done", 3, 33),
        ("Z4", "2026-08-03", "A9", "西南", "void", 9, 99),
        ("Z5", "2026-08-20", "B8", "华中", "done", 4, 44),
        ("Z6", "2026-08-28", "B8", "华中", "done", 2, 22),
        ("Z7", "2026-09-03", "C7", "华东", "done", 3, 33),
        ("Z8", "2026-09-08", "D6", "西南", "done", 1, 11),
    ]]
    fixtures = {
        "trade": {"accounts": [{"account": n, "tier": "学习实验"} for n in ["A9", "B8", "C7", "D6"]],
                  "tickets": tickets,
                  "targets": [{"month": m, "zone": z, "target": t} for m,z,t in [
                      ("2026-08", "西南", 200), ("2026-08", "华中", 0), ("2026-09", "西南", 50)]]},
        "dirty": {"ledger": [dict(zip(("record_id", "event_key", "quantity", "unit_price"), r)) for r in [
            ("K1", "D1", 2, 17), ("K2", "D1", 2, 17), ("K3", "D2", 3, None),
            ("K4", "D3", -2, 30), ("K5", "D4", None, 10)]]},
        "cost": {"lines": [{"line_id": "P1", "units": 2, "unit_price": 12, "unit_cost": None},
                           {"line_id": "P2", "units": 3, "unit_price": 16, "unit_cost": 9}]},
    }
    cases, answers = [], {}
    comparison = {"ordered": False, "abs_tol": 1e-6, "rel_tol": 1e-6, "column_tolerances": {}}

    def add(number, direction, question, *, profile="trade", routes=("sql",), sql=None, rows=None, facts=None, safe=None, chart=None):
        cid = f"X{number:03d}"
        rules = RULES if profile == "trade" else (
            "ledger一行一条原始记录，event_key可能重复；quantity和unit_price存在NULL与负数。"
            "收入=quantity*unit_price。去重、负数与补值规则必须由用户确认，不能擅自删除或补值。"
            if profile == "dirty" else "lines一行一条商品明细。收入=units*unit_price，毛利=units*(unit_price-unit_cost)。缺失成本不能补0。")
        cases.append({"id": cid, "direction": direction, "family": f"supplement_{number}", "split": "test",
                      "profile": profile, "mode": "quick", "route_scored": True, "business_rules": rules,
                      "turns": [{"question": question, "allowed_routes": list(routes)}]})
        if sql:
            answer = {"rows": rows, "columns": list(rows[0]) if rows else ["ticket_id"],
                      "reference_sql": sql, "comparison": comparison}
            if chart:
                answer["chart"] = chart
            answers[cid] = [answer]
        else:
            answers[cid] = [{"rubric": {"expected_outcome": routes[0], "required_facts": facts, "must_not": safe}}]

    add(1, "客户间隔", "A9相邻已完成订单的平均间隔多少天？按订单日期差计算。",
        sql="SELECT avg(gap) AS mean_days FROM (SELECT date_diff('day',lag(CAST(ordered_at AS DATE)) OVER(ORDER BY ordered_at),CAST(ordered_at AS DATE)) AS gap FROM tickets WHERE account='A9' AND state='done')",
        rows=[{"mean_days": 7.5}])
    add(2, "多表Python", "请用Python计算已完成订单的units与unit_price的Pearson相关系数，返回系数。", routes=("python",),
        sql="SELECT corr(units,unit_price) AS correlation FROM tickets WHERE state='done'", rows=[{"correlation": 1.0}])
    add(3, "Excel联表", "8月西南的销售额、目标和达成率分别是多少？达成率按百分数。",
        sql="SELECT sum(units*unit_price) AS sales,200 AS target,sum(units*unit_price)*100.0/200 AS achievement_percent FROM tickets WHERE state='done' AND zone='西南' AND substr(ordered_at,1,7)='2026-08'",
        rows=[{"sales": 154, "target": 200, "achievement_percent": 77.0}])
    add(4, "合法空表", "列出2027年1月金额最高的前3个已完成ticket_id。若无匹配，返回空表，不能放宽日期。",
        sql="SELECT ticket_id FROM tickets WHERE state='done' AND substr(ordered_at,1,7)='2027-01' ORDER BY units*unit_price DESC LIMIT 3", rows=[])
    add(5, "合法空表", "列出units大于100的已完成ticket_id。",
        sql="SELECT ticket_id FROM tickets WHERE state='done' AND units>100", rows=[])
    add(6, "零分母", "8月华中的销售额、目标和达成率是多少？用NULL表示无法定义的比率。",
        sql="SELECT sum(units*unit_price) AS sales,0 AS target,NULL::DOUBLE AS achievement_rate FROM tickets WHERE state='done' AND zone='华中' AND substr(ordered_at,1,7)='2026-08'",
        rows=[{"sales": 220, "target": 0, "achievement_rate": None}])
    add(7, "缺失澄清", "9月华东销售目标达成率是多少？请说明缺什么，不要猜。", routes=("clarify",),
        facts=["指出9月华东目标缺失", "请求提供该目标；不能计算确定达成率"],
        safe=["不把缺失补0，不套用其他月或地区目标，不杜撰达成率"])
    add(8, "缺失澄清", "请给出ledger完整准确的收入总额，重复、负数与缺失的处理规则还没确认。", profile="dirty", routes=("clarify",),
        facts=["询问event_key去重口径", "询问负数处理", "明确quantity和unit_price均有缺失并请求处理规则"],
        safe=["不声称完整总额，不擅自补值或删行"])
    add(9, "缺失澄清", "请算lines的完整毛利；缺的成本不能猜。", profile="cost", routes=("clarify",),
        facts=["指出unit_cost有缺失", "请求补充成本才能确定完整毛利"], safe=["不把未知成本补0，不把部分毛利当完整毛利"])
    add(10, "质量口径", "只统计quantity和unit_price都非空的原始行收入，同时返回纳入行数。不去重，负数保留，不补值。", profile="dirty",
        sql="SELECT sum(quantity*unit_price) AS revenue,count(*) AS included_rows FROM ledger WHERE quantity IS NOT NULL AND unit_price IS NOT NULL", rows=[{"revenue": 8, "included_rows": 3}])
    add(11, "因果边界", "这些订单是否足以证明提高单价一定导致购买数量增长？", routes=("refuse",),
        facts=["现有观察数据不能证明价格对数量的因果影响"], safe=["不承诺因果关系，不捏造实验数据"])
    add(12, "图表", "画柱状图比较每个自然月已完成订单的销售额，按月份升序。", routes=("viz",),
        sql="SELECT substr(ordered_at,1,7) AS month,sum(units*unit_price) AS sales FROM tickets WHERE state='done' GROUP BY 1 ORDER BY 1", rows=[{"month": "2026-08", "sales": 374}, {"month": "2026-09", "sales": 110}],
        chart={"type": "bar", "x": "month", "y": "sales", "points": 2})

    # Hand-derived answers are independently checked with DuckDB before freezing.
    for case in cases:
        answer = answers[case["id"]][0]
        if "reference_sql" not in answer:
            continue
        import pandas as pd
        con = duckdb.connect(":memory:")
        try:
            for table, rows in fixtures[case["profile"]].items():
                con.register(table, pd.DataFrame(rows))
            result = con.execute(answer["reference_sql"]).fetchdf()
            actual = result.astype(object).where(result.notna(), None).to_dict("records")
            assert compare_rows(actual, answer["rows"], **comparison), case["id"]
        finally:
            con.close()
    write_json(OUTPUT / "cases.json", {"version": VERSION, "cases": cases})
    write_json(OUTPUT / "answers.json", {"version": VERSION, "answers": answers})
    write_json(OUTPUT / "fixtures.json", {"version": VERSION, "profiles": fixtures})
    for profile, tables in fixtures.items():
        for table, rows in tables.items():
            path = OUTPUT / "data" / profile / f"{table}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    book = openpyxl.Workbook()
    sheet = book.active
    target_rows = fixtures["trade"]["targets"]
    sheet.append(list(target_rows[0]))
    for row in target_rows:
        sheet.append(list(row.values()))
    book.save(OUTPUT / "data/trade/targets.xlsx")
    book.close()
    write_json(OUTPUT / "manifest.json", {"version": VERSION, "cases": 12, "question_turns": 12,
               "references_cross_checked": True, "scope": "冻结后仅运行一次；与v1同领域的补充检查，不冒充外部盲测",
               "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "files": {p.relative_to(OUTPUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in OUTPUT.rglob("*") if p.is_file() and p.name != "manifest.json"}})
    return check()


def check():
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION and manifest["cases"] == 12
    assert manifest["source_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for filename, sha in manifest["files"].items():
        path = (OUTPUT / filename).resolve()
        assert path.is_relative_to(OUTPUT.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, filename
    return manifest


if __name__ == "__main__":
    manifest = check() if "--check" in sys.argv else build()
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, ensure_ascii=False, indent=2))
