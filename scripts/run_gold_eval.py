"""Run the real application graph against public gold inputs with one shared budget.

Requires --live; artifacts and the shared ledger stay in git-ignored evals/runs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from evals.live_budget import BudgetStop, BudgetTransport, write_json  # noqa: E402

GOLD = ROOT / "evals/gold_v1"
RUNS = ROOT / "evals/runs/gold-v1"


def load_gold(name):
    if name == "gold_v1":
        from scripts.build_gold_set import check
    elif name == "gold_v2":
        from scripts.build_optimization_set import check
    elif name == 'reliability_v1':
        from scripts.build_reliability_gold import check
    else:
        raise ValueError("只能选择已冻结的gold_v1、gold_v2或reliability_v1")
    check()
    return ROOT / "evals" / name


def public_question(case, turn):
    # Whitelist input fields: no ID, fixture name, trap, route label or answer.
    return f"业务口径：{case['business_rules']}\n\n当前问题：{turn['question']}"


def snapshot(directory, status, stage, gold=GOLD):
    sources = {}
    for path in [*sorted((ROOT / "backend/app").rglob("*.py")), ROOT / "scripts/run_gold_eval.py", ROOT / "scripts/score_gold_eval.py",
                 ROOT / "evals/live_budget.py", ROOT / "evals/gold_scoring.py", ROOT / "evals/gold_dataset.py",
                 *([ROOT / 'evals/reliability_dataset.py'] if gold.name == 'reliability_v1' else []), ROOT / "docker/sandbox.Dockerfile"]:
        relative = path.relative_to(ROOT).as_posix()
        sources[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {"stage": stage, "model": status, "max_output_tokens": 4096,
                "application_sources": sources, "gold_directory": gold.relative_to(ROOT).as_posix(),
                "gold_manifest_sha256": hashlib.sha256((gold / "manifest.json").read_bytes()).hexdigest(),
                "dependencies": {name: importlib.metadata.version(name) for name in
                                 ("langchain-openai", "openai", "httpx", "duckdb", "pandas", "langgraph")}}
    path = directory / "configuration.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != metadata:
            raise BudgetStop("本轮源码或参数已改变，请另建轮次，不能覆盖原始基线。")
        return
    write_json(path, metadata)
    for relative in sources:
        destination = directory / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def run(stage, name, selected=None, max_cases=None, gold_name="gold_v1"):
    import httpx
    from app import execution, llm, model_settings, session
    from app.agent.graph import record_query_after_run, run_agent
    from app.agent.investigation import run_investigation
    from app.tools import dataset_manager
    from app.tools.ingest import validate_and_convert
    from app.tools.python_tool import docker_command

    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name):
        raise ValueError("轮次名称只接受小写英文字母、数字、减号、下划线")
    gold = load_gold(gold_name)
    config = model_settings.load_saved_settings()
    status = model_settings.get_model_status()
    if not config or config.base_url.rstrip("/") not in ("https://api.deepseek.com", "https://api.deepseek.com/v1") or config.model != "deepseek-flash":
        raise BudgetStop("当前模型不在本次已核价范围；未改动你的配置。")
    split = {"baseline": "development", "improved": "development", "validation": "validation", "final": "test"}[stage]
    cases = [c for c in json.loads((gold / "cases.json").read_text(encoding="utf-8"))["cases"] if c["split"] == split]
    if selected:
        if not set(selected) <= {c["id"] for c in cases}:
            raise ValueError("不能跨集合选择题目")
        cases = [c for c in cases if c["id"] in selected]
    directory = RUNS / name
    directory.mkdir(parents=True, exist_ok=True)
    snapshot(directory, status, stage, gold)
    session._store = session.SessionStore(backend=session.SQLiteBackend(directory / 'sessions.sqlite3'))
    dataset_manager._manager = dataset_manager.DatasetManager()
    try:
        docker_ready = subprocess.run(docker_command("image", "inspect", "datapilot-sandbox"), capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        docker_ready = False
    write_json(directory / "environment.json", {"utc": datetime.now(timezone.utc).isoformat(), "docker_ready": docker_ready,
               "docker_command": docker_command("version"),
               "scope": "真实应用Graph/调查控制器与CSV/Excel导入；本轮不经过浏览器或PostgreSQL连接接口"})
    completed = 0
    budget = BudgetTransport(RUNS)
    with httpx.Client(transport=budget, follow_redirects=False, timeout=30) as http:
        llm._chat_llm = llm.build_compatible_client(config.base_url, config.api_key.get_secret_value(), config.model,
                      thinking_mode=config.thinking_mode, reasoning_effort=config.reasoning_effort, max_tokens=4096, http_client=http)
        # The audited budget transport settles one complete response including usage.
        llm._chat_llm.disable_streaming = True
        llm._structured_llms.clear()
        for case in cases:
            path = directory / "cases" / f"{case['id']}.json"
            result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"id": case["id"], "direction": case["direction"], "turns": []}
            if len(result["turns"]) == len(case["turns"]):
                continue
            if max_cases and completed >= max_cases:
                break
            with tempfile.TemporaryDirectory(prefix="datapilot-gold-") as temporary:
                sid = session.get_session_store().get_or_create(None).session_id
                manager = dataset_manager.get_dataset_manager()
                manager.get_or_create(sid, Path(temporary))
                files = sorted((gold / "data" / case["profile"]).glob("*.csv"))
                try:
                    for file in files:
                        if file.name == "targets.csv" and file.with_suffix('.xlsx').exists():
                            file = file.with_suffix(".xlsx")
                        with file.open("rb") as stream:
                            manager.add_file(sid, validate_and_convert(file.name, stream, Path(temporary)))
                    # Use the application's actual cached schema, including measured missing counts.
                    for previous in result["turns"]:
                        if previous.get("final"):
                            record_query_after_run(sid, previous["final"])
                    for i, turn in enumerate(case["turns"]):
                        if i < len(result["turns"]):
                            continue
                        if budget.ledger["blocked"]:
                            raise BudgetStop("账本已拦截后续调用")
                        budget.label = f"{name}/{case['id']}/t{i+1}"
                        task = execution.AnalysisTask(owner="gold-eval", session_id=sid)
                        token = execution._current.set(task)
                        first = len(budget.ledger["calls"])
                        started = time.monotonic()
                        record = {"question": turn["question"], "utc": datetime.now(timezone.utc).isoformat()}
                        try:
                            if turn["allowed_routes"] == ["python"] and not docker_ready:
                                record.update(status="environment_not_run", error="Docker隔离环境不可用；未调用模型生成代码，不在宿主回退")
                            elif case["mode"] == "investigate":
                                record.update(status="ran", final=run_investigation(public_question(case, turn), sid))
                            else:
                                final, _ = run_agent(public_question(case, turn), sid)
                                record_query_after_run(sid, final)
                                record.update(status="ran", final=final)
                        except Exception as exc:
                            # Never persist exception bodies that may contain provider credentials.
                            record.update(status="budget_stop" if isinstance(exc, BudgetStop) else "error", error_type=type(exc).__name__)
                        finally:
                            record.update(seconds=round(time.monotonic() - started, 3), trace=task.trace, model_calls=task.model_calls,
                                          request_ids=[c["id"] for c in budget.ledger["calls"][first:]])
                            execution._current.reset(token)
                        result["turns"].append(record)
                        write_json(path, result)
                        print(json.dumps({"run": name, "case": case["id"], "turn": i+1, "status": record["status"],
                                          "route": record.get("final", {}).get("route"), "seconds": record["seconds"],
                                          "requests": record["request_ids"], "total_upper_cny": budget.ledger["accounted_micro_cny"] / 1e6}, ensure_ascii=False), flush=True)
                        if budget.ledger["blocked"] or record["status"] == "budget_stop":
                            raise BudgetStop("已保存本题，停止后续调用")
                finally:
                    manager.close_session(sid)
            completed += 1
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["baseline", "improved", "validation", "final"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--gold", choices=["gold_v1", "gold_v2", "reliability_v1"], default="gold_v1")
    args = parser.parse_args()
    if not args.live:
        parser.error("真实模型评测必须显式加 --live；会计入共享40元总预算，历史费用不重置")
    try:
        run(args.stage, args.name, args.cases, args.max_cases, args.gold)
    except BudgetStop as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
