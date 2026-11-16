"""Score saved results offline; never calls an LLM or modifies raw evidence."""
import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.gold_scoring import score_turn  # noqa: E402
from evals.live_budget import write_json  # noqa: E402


def score(name):
    base = ROOT / "evals/runs/gold-v1"
    directory = base / name
    meta = json.loads((directory / "configuration.json").read_text(encoding="utf-8"))
    gold_relative = meta.get("gold_directory", "evals/gold_v1")
    if gold_relative not in ("evals/gold_v1", "evals/gold_v2", "evals/reliability_v1"):
        raise ValueError("未知的冻结标准目录")
    gold = ROOT / gold_relative
    if hashlib.sha256((gold / "manifest.json").read_bytes()).hexdigest() != meta["gold_manifest_sha256"]:
        raise ValueError("标准与运行时冻结快照不一致")
    split = {"baseline": "development", "improved": "development", "validation": "validation", "final": "test"}[meta["stage"]]
    public = json.loads((gold / "cases.json").read_text(encoding="utf-8"))["cases"]
    cases = [c for c in public if c["split"] == split]
    answers = json.loads((gold / "answers.json").read_text(encoding="utf-8"))["answers"]
    review_path = directory / "reviews.json"
    reviews = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    ledger = json.loads((base / "budget.json").read_text(encoding="utf-8"))
    scored, durations, requests, sql_attempts = [], [], [], []
    for case in cases:
        path = directory / "cases" / f"{case['id']}.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"turns": []}
        for i, turn in enumerate(case["turns"]):
            key = f"{case['id']}:{i+1}"
            if i >= len(result["turns"]):
                scored.append({"key": key, "outcome": "not_run", "reason": "尚未执行"})
                continue
            record = result["turns"][i]
            grade = score_turn(record, answers[case["id"]][i], reviews.get(key, {}))
            final = record.get("final", {})
            coverage = final.get('analysis_process', {}).get('coverage', [])
            delivery = 'complete' if coverage and all(c['status'] == 'answered' for c in coverage) else 'partial' if coverage else 'absent'
            calculation_checked = any(c.get('id') == 'calculation_contract' and c.get('status') == 'passed' for c in final.get('checks', []))
            route_ok = final.get("route") in turn["allowed_routes"] if case["route_scored"] and record["status"] == "ran" else None
            scored.append({"key": key, **grade, "route_ok": route_ok, "direction": case["direction"],
                           "status": record["status"], "delivery_coverage": delivery, 'calculation_checked': calculation_checked,
                           'declared_plan': bool((final.get('analysis_brief') or {}).get('calculation_plan')),
                           "evidence": f"cases/{case['id']}.json#/turns/{i}", "request_ids": record["request_ids"]})
            if record["status"] == "ran":
                durations.append(record["seconds"])
            requests.extend(record["request_ids"])
            executions = [t for t in record["trace"] if t["node"] == "execute_sql"]
            if executions and case["mode"] != "investigate":
                sql_attempts.append({"key": key, "first_execution_ok": not bool(executions[0].get("error")),
                                     "final_execution_ok": not bool(executions[-1].get("error")), "attempts": len(executions)})
    calls = [c for c in ledger["calls"] if c["id"] in requests]
    chains = [[s for s in scored if s["key"].startswith(c["id"] + ":")] for c in cases if len(c["turns"]) > 1]
    metrics = {"cases": len(cases), "turns": len(scored), "outcomes": dict(Counter(s["outcome"] for s in scored)),
               'grade_scope': 'numeric/table/chart or explicitly reviewed rubric; report coverage is separate and not independent method certification',
               'delivery_coverage': dict(Counter(s['delivery_coverage'] for s in scored if 'delivery_coverage' in s)),
               'passed_with_partial_delivery': sum(s['outcome'] == 'pass' and s.get('delivery_coverage') == 'partial' for s in scored),
               'declared_plan_tasks': sum(s.get('declared_plan', False) for s in scored),
               'calculation_checked_tasks': sum(s.get('calculation_checked', False) for s in scored),
               "environment_not_run": sum(s.get("status") == "environment_not_run" for s in scored),
               "routes_correct": sum(s.get("route_ok") is True for s in scored), "route_denominator": sum(s.get("route_ok") is not None for s in scored),
               "chains_passed": sum(all(s["outcome"] == "pass" for s in chain) for chain in chains), "chains_total": len(chains),
               "sql_turns": len(sql_attempts), "first_execution_ok": sum(s["first_execution_ok"] for s in sql_attempts),
               "final_execution_ok": sum(s["final_execution_ok"] for s in sql_attempts),
               "http_requests": len(calls), "input_tokens": sum(c.get("usage", {}).get("prompt_tokens", 0) for c in calls),
               "output_tokens": sum(c.get("usage", {}).get("completion_tokens", 0) for c in calls),
               "cost_upper_cny": sum(c["charged_micro_cny"] for c in calls) / 1e6,
               "total_accounted_upper_cny": ledger["accounted_micro_cny"] / 1e6,
               "p50_seconds": statistics.median(durations) if durations else None,
               "p95_seconds": sorted(durations)[max(0, int(len(durations)*0.95 + 0.999999)-1)] if durations else None}
    write_json(directory / "score.json", {"gold_version": json.loads((gold / "answers.json").read_text(encoding="utf-8"))["version"],
               "scored_at_utc": datetime.now(timezone.utc).isoformat(),
               "scorer_sha256": hashlib.sha256((ROOT / "evals/gold_scoring.py").read_bytes()).hexdigest(),
               "reviews_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest() if review_path.exists() else None,
               "metrics": metrics, "turns": scored, "sql_attempts": sql_attempts})
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    score(parser.parse_args().name)
