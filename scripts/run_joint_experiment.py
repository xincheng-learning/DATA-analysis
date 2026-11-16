"""Real PostgreSQL + Excel + task API + DuckDB, with explicitly controlled LLM responses."""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import main  # noqa: E402
from app.agent import investigation, nodes, router  # noqa: E402
from app.config import settings  # noqa: E402
from app.execution import TaskRegistry  # noqa: E402

QUERY = """SELECT a.month, SUM(a.revenue) AS actual, SUM(t.target) AS target,
 SUM(a.revenue)-SUM(t.target) AS gap, ROUND(100*SUM(a.revenue)/SUM(t.target),2) AS attainment_pct
 FROM sales_actuals a JOIN sales_targets t USING(month,region) GROUP BY a.month ORDER BY a.month"""


async def verify():
    if settings.app_mode != "local" or settings.session_backend != "sqlite":
        raise RuntimeError("此实验只允许在本地 SQLite 模式运行。")
    registry = TaskRegistry()
    sid = "joint-check-" + uuid.uuid4().hex[:12]
    decisions = iter([
        investigation.InvestigationDecision(action="analyze", step_id="attainment", question="按月份对比销售额、目标和缺口", reason="先用月份与区域完整关联，避免重复累加"),
        investigation.InvestigationDecision(action="finish", reason="2月销售900、目标1300、缺口400；未提供因果实验，不能证明业务原因。"),
    ])
    report = {"mode": "controlled_model_real_postgresql_excel_sql", "no_live_model_calls": True}
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 12345))
    with patch.object(main, "tasks", registry), patch.object(main, "get_chat_llm", lambda: object()), \
         patch.object(nodes, "_get_llm", lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=QUERY))), \
         patch.object(router, "_get_router_llm", lambda: SimpleNamespace(invoke=lambda messages: router.RouteDecision(route="sql", reason="跨来源联合查询"))), \
         patch.object(investigation, "get_structured_llm", lambda schema: SimpleNamespace(invoke=lambda messages: next(decisions))):
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            try:
                response = await client.post(f"/sessions/{sid}/database/import", json={"tables": [{"schema_name": "public", "table_name": "sales_actuals"}]})
                assert response.status_code == 200, response.text
                response = await client.post(f"/sessions/{sid}/upload", files={"files": ("sales_targets.xlsx", (ROOT / ".local/examples/sales_targets.xlsx").read_bytes())})
                assert response.status_code == 200 and response.json()["status"] == "ok", response.text
                tables = (await client.get(f"/sessions/{sid}/tables")).json()["tables"]
                assert {table["source"]["kind"] for table in tables} == {"file", "postgresql"}
                for mode in ("quick", "investigate"):
                    response = await client.post("/tasks", json={"question": "受控实验：核对每月销售目标达成率", "session_id": sid, "mode": mode})
                    assert response.status_code == 200, response.text
                    task_id = response.json()["task_id"]
                    for _ in range(300):
                        task = (await client.get(f"/tasks/{task_id}")).json()
                        if task["status"] in {"succeeded", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(0.02)
                    assert task["status"] == "succeeded", task
                    rows = task["result"]["rows"]
                    assert rows == [
                        {"month": "2026-01", "actual": 1200, "target": 1200, "gap": 0, "attainment_pct": 100},
                        {"month": "2026-02", "actual": 900, "target": 1300, "gap": -400, "attainment_pct": 69.23},
                    ], rows
                    saved = (await client.get(f"/sessions/{sid}")).json()["messages"][-1]["metadata"]
                    assert saved["rows"] == rows and saved["sql"] == QUERY
                    assert len(saved["data_sources"]) == 2
                    report[mode] = {"rows": rows, "model_calls": task["model_calls"], "evidence_saved": True}
            finally:
                registry.close()
                response = await client.delete(f"/sessions/{sid}")
                assert response.status_code == 200, response.text
    destination = ROOT / "evals/results/joint-controlled.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(verify())
