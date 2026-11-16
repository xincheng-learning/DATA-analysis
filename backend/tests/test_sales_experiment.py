"""Literal sales references verify the controller's observed numbers and paths."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from evals.sales_experiment import (
    GOAL,
    check_reference,
    controlled_models,
    fixed_workflow,
    sales_csv,
)

from app.agent.investigation import run_investigation
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import validate_and_convert


@pytest.mark.parametrize("case", ["volume_drop", "price_drop", "data_gap"])
def test_sales_cases_have_verified_values_and_distinct_paths(case, monkeypatch, tmp_path):
    manager = DatasetManager()
    dataset = manager.get_or_create("sales-case", tmp_path)
    manager.add_file("sales-case", validate_and_convert("sales.csv", io.BytesIO(sales_csv(case)), tmp_path))
    monkeypatch.setattr("app.tools.dataset_manager.get_dataset_manager", lambda: manager)
    try:
        with controlled_models():
            result = run_investigation(GOAL, "sales-case")
        assert check_reference(case, result)["passed"]
        baseline = fixed_workflow(dataset)
        assert baseline["status"] == result["status"]
        if case == "data_gap":
            assert baseline["rows"] == [{"invalid": 1}]
            assert len(result["steps"]) == 1
        else:
            assert baseline["monthly"][0]["revenue"] == 4000
            assert baseline["monthly"][1]["revenue"] == 3000
            assert len(result["steps"]) == 3
    finally:
        dataset.close()


def test_wrong_number_cannot_pass_reference_check():
    result = {"status": "completed", "steps": [
        {"step_id": "monthly_metrics", "rows": [{"revenue": 4000, "quantity": 400}, {"revenue": 2999, "quantity": 300}]},
        {"step_id": "volume_by_channel", "rows": []},
    ]}
    assert check_reference("volume_drop", result)["passed"] is False


@pytest.mark.parametrize("case,quantity,last_step,rows", [
    ("volume_drop", 300, "volume_by_channel", [
        {"channel": "online", "month": "2026-01", "quantity": 200},
        {"channel": "online", "month": "2026-02", "quantity": 99},
    ]),
    ("price_drop", 400, "price_by_product", [
        {"product": "A", "month": "2026-01", "unit_price": 10},
        {"product": "A", "month": "2026-02", "unit_price": 7},
    ]),
])
def test_wrong_breakdown_cannot_pass_reference_check(case, quantity, last_step, rows):
    result = {"status": "completed", "steps": [
        {"step_id": "monthly_metrics", "rows": [
            {"revenue": 4000, "quantity": 400}, {"revenue": 3000, "quantity": quantity},
        ]},
        {"step_id": last_step, "rows": rows},
    ]}
    assert check_reference(case, result)["passed"] is False


def test_command_runs_controlled_cases_without_api_configuration():
    root = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_sales_experiment.py")],
        cwd=root,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    summary = json.loads(process.stdout)
    assert summary["mode"] == "controlled"
    assert len(summary["cases"]) == 3
    assert all(case["reference_passed"] for case in summary["cases"])
