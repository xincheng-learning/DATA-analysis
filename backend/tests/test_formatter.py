"""Tests for the answer formatter helpers."""
from __future__ import annotations

import pandas as pd

from app.agent.nodes import _clean_dataframe, _fmt_cell, _sanitize_records


# --- _fmt_cell --------------------------------------------------------------


def test_fmt_int_with_thousands_separator() -> None:
    assert _fmt_cell(99441) == "99,441"


def test_fmt_strips_float_noise() -> None:
    assert _fmt_cell(1149781.8199999975) == "1,149,781.82"


def test_fmt_drops_trailing_zeros() -> None:
    assert _fmt_cell(354.75) == "354.75"
    assert _fmt_cell(354.700) == "354.7"


def test_fmt_treats_whole_floats_as_int() -> None:
    assert _fmt_cell(1000.0) == "1,000"


def test_fmt_handles_none_and_nan() -> None:
    assert _fmt_cell(None) == ""
    assert _fmt_cell(float("nan")) == ""


def test_fmt_strings_pass_through() -> None:
    assert _fmt_cell("health_beauty") == "health_beauty"


def test_small_nonzero_statistics_are_never_displayed_as_zero():
    from decimal import Decimal
    for value in (8.276505321e-20,-8.276505321e-20,1e-7,Decimal('8.276505321e-20'),Decimal('-8.276505321e-20')):
        assert _fmt_cell(value)!='0' and float(_fmt_cell(value))!=0
        assert abs(float(_fmt_cell(value))/float(value)-1)<1e-5
    assert _fmt_cell(0.)=='0' and _fmt_cell(Decimal('0'))=='0'
    assert _fmt_cell(float('inf'))==_fmt_cell(float('-inf'))==''


def test_large_whole_floats_do_not_expand_binary_rounding_into_fictitious_integer_digits():
    from decimal import Decimal
    for value in (1e30,-1e30,1.2345678901234567e19):
        assert Decimal(_fmt_cell(value))==Decimal(str(value))
    assert '19,884,624' not in _fmt_cell(1e30)


# --- _clean_dataframe -------------------------------------------------------


def test_clean_preserves_evidence_precision() -> None:
    df = pd.DataFrame({"x": [1.123456789, 2.0]})
    out = _clean_dataframe(df)
    assert out["x"].tolist() == [1.123456789, 2.0]


def test_clean_handles_empty_frame() -> None:
    df = pd.DataFrame({"x": []})
    out = _clean_dataframe(df)
    assert out.empty


def test_ratio_evidence_keeps_precision_when_converted_to_percent() -> None:
    ratio = 4 / 15
    cleaned = _clean_dataframe(pd.DataFrame({"refunded_share": [ratio]}))
    assert abs(cleaned.iloc[0, 0] * 100 - ratio * 100) < 1e-6
    assert abs(float(_fmt_cell(ratio)) * 100 - ratio * 100) < 0.005


# --- _sanitize_records ------------------------------------------------------


def test_sanitize_replaces_nan_with_none() -> None:
    records = [{"a": 1.0, "b": float("nan")}, {"a": float("nan"), "b": 2.0}]
    out = _sanitize_records(records)
    assert out == [{"a": 1.0, "b": None}, {"a": None, "b": 2.0}]


def test_sanitize_leaves_other_values_alone() -> None:
    records = [{"a": "x", "b": 5, "c": None}]
    out = _sanitize_records(records)
    assert out == [{"a": "x", "b": 5, "c": None}]


def test_infinite_values_are_unknown_in_json_results():
    assert _sanitize_records([{'x': float('inf'), 'y': float('-inf')}]) == [{'x': None, 'y': None}]


def test_json_preserves_decimal_digits_and_unsafe_integers_as_exact_text():
    from decimal import Decimal
    assert _sanitize_records([{'amount':Decimal('9007199254740993.01'),'id':2**53+1,'zero':0,'small':.01,'null':None}])==[
        {'amount':'9007199254740993.01','id':'9007199254740993','zero':0,'small':.01,'null':None}]
