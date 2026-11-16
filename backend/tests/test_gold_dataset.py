"""Gold standards must reject plausible wrong answers without a live model."""
from collections import Counter

import pytest

from evals.gold_dataset import build_cases, compare_rows, profiles, verify_references


def test_gold_coverage_and_grouped_splits():
    cases = build_cases()
    assert len(cases) == 72
    assert len({case["id"] for case in cases}) == 72
    assert len(Counter(case["direction"] for case in cases)) == 12
    assert all(n == 6 for n in Counter(case["direction"] for case in cases).values())
    assert Counter(case["split"] for case in cases) == {"development": 36, "validation": 18, "test": 18}
    families = {}
    for case in cases:
        assert case["family"] not in families or families[case["family"]] == case["split"]
        families[case["family"]] = case["split"]
        assert case["business_rules"] and case["failure_trap"]
        assert case["turns"] and case["profile"] in profiles()
        for turn in case["turns"]:
            assert turn["question"]
            assert bool(turn["allowed_routes"]) == case["route_scored"]
            assert set(turn["allowed_routes"]) <= {"sql", "viz", "python", "chat", "clarify", "refuse"}
            assert bool(turn.get("oracle_sql")) != bool(turn.get("rubric"))
    assert sum(len(c["turns"]) for c in cases) == 78
    assert sum(len(c["turns"]) == 2 for c in cases) == 6


def test_chart_observation_counts_agree_with_reference_rows():
    for case in build_cases():
        for turn in case["turns"]:
            if turn.get("chart"):
                assert turn["chart"]["points"] == len(turn["expected_rows"]), case["id"]


def test_reference_values_have_independent_literal_checks():
    answers = verify_references()
    assert answers["G001"][0]["rows"] == [{"order_count": 15}]
    assert answers["G003"][0]["rows"] == [{"revenue": 2815}]
    assert answers["G014"][0]["rows"] == [{"revenue": 715, "target": 1000, "attainment_pct": 71.5}]
    assert answers["G016"][0]["rows"] == [{"net_revenue": 2610}]
    assert answers["G017"][0]["rows"] == [{"shipping_fee": 72}]
    assert answers["G036"][0]["rows"] == []
    assert answers["G046"][0]["rows"][-1]["rolling_mean"] == pytest.approx(131 / 3)
    assert len(answers) == 72


def test_numeric_grader_rejects_wrong_values_duplicate_rows_and_null_substitution():
    correct = [{"region": "华东", "revenue": 250.0}, {"region": "华南", "revenue": 240.0}]
    assert compare_rows(list(reversed(correct)), correct)
    assert not compare_rows(correct[:1] * 2, correct)
    assert not compare_rows([{"region": "华东", "revenue": 251}, correct[1]], correct)
    assert not compare_rows([{"rate": 0}], [{"rate": None}])
    assert not compare_rows([{"rate": True}], [{"rate": 1}])
    assert not compare_rows([{"rate": 1, "extra": 2}], [{"rate": 1}])
    assert not compare_rows([{"a": 1, "b": 2}], [{"rate": 2}], column_map={"a":"rate", "b":"rate"})
    assert compare_rows([{"收入": 250.00000001}], [{"revenue": 250}], column_map={"收入": "revenue"})
    assert not compare_rows(list(reversed(correct)), correct, ordered=True)
    tolerance = {"repeat_pct":{"abs_tol":0.005000001,"rel_tol":0}}
    assert compare_rows([{"repeat_pct":85.71}], [{"repeat_pct":600/7}], column_tolerances=tolerance)
    assert not compare_rows([{"repeat_pct":85.7}], [{"repeat_pct":600/7}], column_tolerances=tolerance)


def test_every_numeric_reference_rejects_a_changed_business_value():
    from copy import deepcopy
    from numbers import Real

    checked = 0
    for case in build_cases():
        for turn in case["turns"]:
            if "expected_rows" not in turn or not turn["expected_rows"]: continue
            reference = turn["expected_rows"]
            wrong = deepcopy(reference)
            changed = False
            for row in wrong:
                for key, value in row.items():
                    if isinstance(value, Real) and not isinstance(value, bool):
                        row[key] = value + 1
                        changed = True
                        break
                if changed: break
            if changed:
                assert not compare_rows(wrong, reference, **turn["comparison"]), case["id"]
                checked += 1
    assert checked >= 50


def test_exported_gold_can_be_imported_by_real_csv_excel_pipeline(tmp_path):
    import json
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import validate_and_convert
    from scripts.build_gold_set import build, check

    output = tmp_path / "gold"
    manifest = build(output)
    assert manifest["programmatically_checkable_turns"] == 58
    assert manifest["manual_rubric_turns"] == 20
    assert manifest["real_model_called"] is False
    assert check(output)["cases"] == 72
    assert json.loads((output/"cases.json").read_text(encoding="utf-8"))["version"] == "1.0.1"
    manager = DatasetManager()
    for profile, tables in profiles().items():
        directory = tmp_path / "ingested" / profile
        dataset = manager.get_or_create(profile, directory)
        try:
            for name, expected in tables.items():
                suffix = ".xlsx" if profile == "commerce" and name == "targets" else ".csv"
                path = output / "data" / profile / (name+suffix)
                with path.open("rb") as stream:
                    ingested = validate_and_convert(path.name, stream, directory)
                manager.add_file(profile, ingested)
                assert dataset.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == len(expected)
            if profile == "commerce":
                assert dataset.execute("SELECT target FROM targets WHERE month='2026-05'").fetchone()[0] == 0
            if profile == "quality":
                assert dataset.execute("SELECT count(*) FROM quality WHERE unit_price IS NULL").fetchone()[0] == 1
                assert dataset.execute("SELECT region FROM quality WHERE record_id='Q07'").fetchone()[0] == "华南 "
        finally:
            manager.close_session(profile)
    (output/"cases.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="cases.json"):
        check(output)


def test_investigation_gold_is_evidence_based_not_fixed_step_names():
    cases = [c for c in build_cases() if c["direction"] == "多步调查"]
    assert len(cases) == 6
    for case in cases:
        rubric = case["turns"][0]["rubric"]
        assert rubric["required_facts"] and rubric["must_not"]
        assert "step_id" not in rubric
    gap = next(c for c in cases if c["profile"] == "investigation_gap")
    assert gap["turns"][0]["rubric"]["expected_outcome"] == "clarify"
