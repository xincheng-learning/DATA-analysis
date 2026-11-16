"""Run controlled sales experiments; --live explicitly enables the configured model."""

from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from evals.sales_experiment import (  # noqa: E402
    GOAL,
    REFERENCES,
    check_reference,
    controlled_models,
    fixed_workflow,
    sales_csv,
)

from app.agent.investigation import run_investigation  # noqa: E402
from app.tools.dataset_manager import get_dataset_manager  # noqa: E402
from app.tools.ingest import validate_and_convert  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the model configured in .env; default is controlled and offline")
    args = parser.parse_args()
    mode = "live" if args.live else "controlled"
    manager = get_dataset_manager()
    cases = []

    for case in REFERENCES:
        session_id = uuid.uuid4().hex
        output_dir = ROOT / "tmp" / "experiments" / "sales-inputs" / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_data = sales_csv(case)
        (output_dir / "sales.csv").write_bytes(csv_data)
        dataset = manager.get_or_create(session_id, output_dir)
        manager.add_file(session_id, validate_and_convert("sales.csv", io.BytesIO(csv_data), output_dir))
        try:
            with nullcontext() if args.live else controlled_models():
                result = run_investigation(GOAL, session_id)
            baseline = fixed_workflow(dataset)
            verification = check_reference(case, result) if not args.live else {
                "passed": None,
                "note": "Live step identities and queries are unrestricted; independently review the recorded evidence against the reference values.",
                "reference": REFERENCES[case],
            }
            cases.append({"case": case, "result": result, "verification": verification, "fixed_workflow": baseline})
        finally:
            manager.close_session(session_id)

    report = {
        "mode": mode,
        "real_model_called": args.live,
        "interpretation": "Controlled responses verify execution and memory, not LLM judgment. The fixed workflow also solves these known cases.",
        "cases": cases,
    }
    output_path = ROOT / "evals" / "results" / f"sales-{mode}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "mode": mode,
        "report": str(output_path),
        "cases": [{"case": c["case"], "status": c["result"]["status"], "steps": [s["step_id"] for s in c["result"]["steps"]], "reference_passed": c["verification"]["passed"], "fixed_workflow_status": c["fixed_workflow"]["status"]} for c in cases],
    }, ensure_ascii=False, indent=2))
    if args.live:
        return 1 if any(c["result"]["status"] == "planner_error" for c in cases) else 0
    return 0 if all(c["verification"]["passed"] for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
