"""Tests for the rule-based validator.

The validator is pure (no LLM, no I/O), so we exercise it directly with
crafted state dicts.
"""
from __future__ import annotations

from app.agent.validator import validator_node


def test_chinese_summary_requires_rows_but_filtered_empty_result_is_valid():
    assert validator_node(_ok_state("销售总额最高的前5个区域", [], ["region"]))["validation_failure"]
    assert not validator_node(_ok_state("列出销售额小于0的订单", [], ["order"]))["validation_failure"]


def _ok_state(question: str, rows: list, cols: list) -> dict:
    return {
        "question": question,
        "rows": rows,
        "columns": cols,
        "row_count": len(rows),
    }


# --- pass-through cases -----------------------------------------------------


def test_passes_when_sql_already_failed() -> None:
    state = {"error": "syntax error", "question": "top 5 customers"}
    out = validator_node(state)
    assert out["validation_failure"] == ""


def test_passes_with_normal_result() -> None:
    state = _ok_state(
        question="Which product category has the highest revenue?",
        rows=[{"category": "x", "revenue": 100.0}],
        cols=["category", "revenue"],
    )
    out = validator_node(state)
    assert out["validation_failure"] == ""


def test_passes_zero_rows_when_question_does_not_imply_rows() -> None:
    """Zero rows are perfectly fine for some questions."""
    state = _ok_state(
        question="List orders cancelled before they shipped",
        rows=[],
        cols=["order_id"],
    )
    out = validator_node(state)
    assert out["validation_failure"] == ""


# --- failure cases ----------------------------------------------------------


def test_flags_zero_rows_when_question_implies_top_n() -> None:
    state = _ok_state(
        question="Top 5 customers by total spend",
        rows=[],
        cols=["customer", "spend"],
    )
    out = validator_node(state)
    assert out["validation_failure"]
    assert "zero rows" in out["validation_failure"].lower()


def test_flags_zero_rows_when_question_says_highest() -> None:
    state = _ok_state(
        question="Which category has the highest revenue?",
        rows=[],
        cols=["category"],
    )
    out = validator_node(state)
    assert out["validation_failure"]


def test_all_null_result_remains_unknown_without_an_unjustified_retry() -> None:
    state = _ok_state(
        question="What is the average review score?",
        rows=[{"avg": None}],
        cols=["avg"],
    )
    out = validator_node(state)
    assert out["validation_failure"] == ''
    assert not out.get('previous_attempts')
    assert any(c['status'] == 'warning' and 'NULL' in c['detail'] for c in out['checks'])


def test_business_formula_does_not_make_filtered_empty_result_invalid():
    state = _ok_state("业务口径：销售额=sum(quantity*unit_price)，按月求平均。\n\n当前问题：列出2026年5月的订单。", [], ["order_id"])
    assert validator_node(state)["validation_failure"] == ""


def test_explicit_empty_result_is_valid_even_for_top_n():
    state = _ok_state("Top 5 orders in May; an empty result is valid. Do not widen the dates.", [], ["order_id"])
    result = validator_node(state)
    assert result["validation_failure"] == ""
    assert not result.get("previous_attempts")


def test_chinese_explicit_empty_result_is_valid():
    state = _ok_state("列出五月销售额最高的订单；若无匹配，返回空表，不能放宽日期。", [], ["order_id"])
    assert validator_node(state)["validation_failure"] == ""


def test_forbidden_empty_result_is_not_treated_as_permission():
    state = _ok_state("找出最高销售额的地区，不能返回空表。", [], ["region"])
    assert validator_node(state)["validation_failure"]


def test_ranking_cannot_silently_omit_categories_with_unknown_change():
    rows=[{'state':'SC','change_amount':-1785.46,'decline_rank':1},
          {'state':'MA','change_amount':None,'decline_rank':None}]
    result=validator_node(_ok_state('商品金额下降最多的3个州，保留两月并集',rows,list(rows[0])))
    assert result['validation_failure'] and 'NULL' in result['validation_failure']
    assert len(result['previous_attempts'])==1
    rows[1].update(change_amount=-1349.19,decline_rank=2)
    assert not validator_node(_ok_state('商品金额下降最多的3个州',rows,list(rows[0])))['validation_failure']
