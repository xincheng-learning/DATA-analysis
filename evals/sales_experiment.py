"""Offline fixtures and controlled model boundaries; never production reasoning."""

from __future__ import annotations

import csv
import io
import json
import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.investigation import InvestigationDecision

GOAL = (
    "Compare 2026-01 and 2026-02 sales revenue (SUM(quantity * unit_price)). "
    "Rows aggregate quantity by month, channel and product, not individual orders. "
    "Investigate arithmetic contributors to a decline, check missing data first, "
    "and do not claim proven causation."
)
QUALITY = "Count records with missing or negative quantity or unit_price"
MONTHLY = "Compare monthly revenue SUM(quantity * unit_price) and total quantity"
VOLUME = "Compare total quantity by channel and month"
PRICE = "Compare quantity-weighted unit price by product and month"

QUERIES = {
    QUALITY: "SELECT COUNT(*) AS invalid FROM sales WHERE quantity IS NULL OR unit_price IS NULL OR quantity < 0 OR unit_price < 0",
    MONTHLY: "SELECT month, SUM(quantity * unit_price) AS revenue, SUM(quantity) AS quantity FROM sales GROUP BY month ORDER BY month",
    VOLUME: "SELECT channel, month, SUM(quantity) AS quantity FROM sales GROUP BY channel, month ORDER BY channel, month",
    PRICE: "SELECT product, month, SUM(quantity * unit_price) / SUM(quantity) AS unit_price FROM sales GROUP BY product, month ORDER BY product, month",
}

# Literal reference values are independent of the code being evaluated.
REFERENCES = {
    "volume_drop": {"before": 4000, "after": 3000, "quantity_before": 400, "quantity_after": 300, "online_before": 200, "online_after": 100, "status": "completed", "last_step": "volume_by_channel"},
    "price_drop": {"before": 4000, "after": 3000, "quantity_before": 400, "quantity_after": 400, "product_a_price_before": 10, "product_a_price_after": 7.5, "status": "completed", "last_step": "price_by_product"},
    "data_gap": {"invalid": 1, "status": "clarify", "last_step": "data_quality"},
}


def sales_csv(case: str) -> bytes:
    if case not in REFERENCES:
        raise ValueError(f"Unknown sales fixture: {case}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["month", "channel", "product", "quantity", "unit_price"])
    for month in ("2026-01", "2026-02"):
        for channel in ("online", "store"):
            for product in ("A", "B"):
                quantity, price = 100, 10
                if month == "2026-02" and channel == "online":
                    if case == "volume_drop":
                        quantity = 50
                    elif case == "price_drop":
                        price = 5
                if case == "data_gap" and (month, channel, product) == ("2026-02", "store", "B"):
                    price = None
                writer.writerow([month, channel, product, quantity, price])
    return output.getvalue().encode("utf-8")


@contextmanager
def controlled_models():
    """Read real observations, but use explicit fixture rules, not a real LLM."""
    def decide(messages):
        context = json.loads(messages[-1].content)
        steps = context["steps"]
        if not steps:
            return InvestigationDecision(action="analyze", step_id="data_quality", question=QUALITY, reason="Missing inputs would invalidate a revenue comparison")
        if steps[0]["rows"][0]["invalid"]:
            return InvestigationDecision(action="clarify", reason="Missing unit_price prevents a complete revenue comparison; request the missing values")
        if len(steps) == 1:
            return InvestigationDecision(action="analyze", step_id="monthly_metrics", question=MONTHLY, reason="Measure revenue and quantity before selecting a breakdown")
        if len(steps) == 2:
            before, after = steps[1]["rows"]
            if after["quantity"] < before["quantity"]:
                return InvestigationDecision(action="analyze", step_id="volume_by_channel", question=VOLUME, reason="Quantity fell; locate the channel contributing to that change")
            return InvestigationDecision(action="analyze", step_id="price_by_product", question=PRICE, reason="Quantity stayed stable; inspect the weighted-price change")
        return InvestigationDecision(action="finish", reason="Recorded arithmetic comparisons are sufficient for this controlled case; they do not establish causation")

    def write(messages):
        match = re.search(r"^(?:Current question|Question): ([^\n]+)", messages[-1].content, re.MULTILINE)
        if match is None or match.group(1) not in QUERIES:
            raise ValueError("Unexpected question in controlled SQL writer")
        return SimpleNamespace(content=QUERIES[match.group(1)])

    with patch("app.agent.investigation.get_structured_llm", lambda schema: SimpleNamespace(invoke=decide)), patch("app.agent.nodes._get_llm", lambda: SimpleNamespace(invoke=write)):
        yield


def fixed_workflow(dataset) -> dict:
    """Honest baseline: these known contributors require no autonomous planning."""
    quality = dataset.execute(QUERIES[QUALITY]).fetchdf().to_dict("records")
    if quality[0]["invalid"]:
        return {"status": "clarify", "query_count": 1, "rows": quality}
    monthly = dataset.execute(QUERIES[MONTHLY]).fetchdf().to_dict("records")
    before, after = monthly
    query = QUERIES[VOLUME if after["quantity"] < before["quantity"] else PRICE]
    breakdown = dataset.execute(query).fetchdf().to_dict("records")
    return {"status": "completed", "query_count": 3, "monthly": monthly, "rows": breakdown}


def check_reference(case: str, result: dict) -> dict:
    """Score actual observations, not merely SQL execution success."""
    reference = REFERENCES[case]
    steps = result["steps"]
    observed = {"status": result["status"], "last_step": steps[-1]["step_id"] if steps else None}
    quality = next((s for s in steps if s["step_id"] == "data_quality"), None)
    if quality and quality["rows"]:
        observed["invalid"] = quality["rows"][0]["invalid"]
    monthly = next((s for s in steps if s["step_id"] == "monthly_metrics"), None)
    if monthly and len(monthly["rows"]) == 2:
        before, after = monthly["rows"]
        observed.update(before=before["revenue"], after=after["revenue"], quantity_before=before["quantity"], quantity_after=after["quantity"])
    for step in steps:
        for row in step.get("rows", []):
            suffix = {"2026-01": "before", "2026-02": "after"}.get(row.get("month"))
            if suffix and step["step_id"] == "volume_by_channel" and row.get("channel") == "online":
                observed[f"online_{suffix}"] = row.get("quantity")
            if suffix and step["step_id"] == "price_by_product" and row.get("product") == "A":
                observed[f"product_a_price_{suffix}"] = row.get("unit_price")
    return {"passed": all(observed.get(key) == value for key, value in reference.items()), "reference": reference, "observed": observed}
