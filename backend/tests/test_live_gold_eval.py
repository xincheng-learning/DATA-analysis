"""Budget and audit checks use a fake HTTP server, never a paid model."""
import json

import httpx
import pytest

from evals.live_budget import BudgetStop, BudgetTransport, LIMIT


def test_user_approved_total_limit_is_forty_yuan():
    assert LIMIT == 40_000_000


def test_approved_thirty_to_forty_amendment_preserves_prior_spending_and_calls(tmp_path):
    from copy import deepcopy
    from evals.live_budget import PRICE
    old = {'limit_micro_cny': 30_000_000, 'accounted_micro_cny': 29_466_982,
        'blocked': False, 'price': PRICE, 'calls': [{'id': 1, 'status': 'settled', 'charged_micro_cny': 29_466_982}]}
    (tmp_path/'budget.json').write_text(json.dumps(old), encoding='utf-8')
    with BudgetTransport(tmp_path, httpx.MockTransport(success)) as transport:
        updated = deepcopy(transport.ledger)
    assert updated['limit_micro_cny'] == 40_000_000
    assert updated['accounted_micro_cny'] == old['accounted_micro_cny'] and updated['calls'] == old['calls']
    assert updated['budget_amendments'][-1]['previous_limit_micro_cny'] == 30_000_000
    with BudgetTransport(tmp_path, httpx.MockTransport(success)) as transport:
        assert transport.ledger == updated


def test_old_limit_cannot_be_silently_reset_or_raised(tmp_path):
    from evals.live_budget import PRICE
    (tmp_path/'budget.json').write_text(json.dumps({'limit_micro_cny':20_000_000,'accounted_micro_cny':0,
        'blocked':False,'price':PRICE,'calls':[]}),encoding='utf-8')
    with pytest.raises(BudgetStop,match='预算或价格'):
        BudgetTransport(tmp_path,httpx.MockTransport(success))
    assert json.loads((tmp_path/'budget.json').read_text())['limit_micro_cny']==20_000_000


def test_larger_output_cap_is_explicit_and_still_reserves_the_shared_budget(tmp_path):
    with httpx.Client(transport=BudgetTransport(tmp_path,httpx.MockTransport(success),max_output_tokens=8192)) as client:
        response = client.post('https://api.deepseek.com/chat/completions',json={
            'model':'deepseek-flash','max_tokens':8192,'messages':[{'role':'user','content':'complex task'}]})
        assert response.status_code == 200
    ledger = json.loads((tmp_path/'budget.json').read_text())
    assert ledger['calls'][0]['reserved_micro_cny'] >= 8192*8
    assert ledger['accounted_micro_cny'] == 360


def test_supplementary_gold_is_frozen_and_uses_new_inputs():
    from scripts.run_gold_eval import load_gold
    path = load_gold("gold_v2")
    cases = json.loads((path / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 12 and all(c["id"].startswith("X") for c in cases)
    assert "tickets.csv" in {p.name for p in (path / "data/trade").iterdir()}


def test_gold_selection_rejects_arbitrary_directories():
    from scripts.run_gold_eval import load_gold
    with pytest.raises(ValueError):
        load_gold("../.local")


def request(client):
    return client.post("https://api.deepseek.com/chat/completions", headers={"Authorization": "Bearer PRIVATE"},
                       json={"model": "deepseek-flash", "max_tokens": 4096, "messages": [{"role": "user", "content": "你好"}]})


def success(req):
    return httpx.Response(200, json={"model": "deepseek-flash", "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                                    "choices": [{"finish_reason": "stop", "message": {"content": "ok", "reasoning_content": "private reasoning"}}]})


def test_budget_reserves_before_network_and_audits_without_secrets(tmp_path):
    def handler(req):
        ledger = json.loads((tmp_path / "budget.json").read_text())
        assert ledger["calls"][-1]["status"] == "reserved"
        assert ledger["accounted_micro_cny"] > 0
        return success(req)
    with httpx.Client(transport=BudgetTransport(tmp_path, httpx.MockTransport(handler))) as client:
        assert request(client).status_code == 200
    ledger = json.loads((tmp_path / "budget.json").read_text())
    assert ledger["accounted_micro_cny"] == 100 * 2 + 20 * 8
    audit = json.loads((tmp_path / ledger["calls"][0]["audit_file"]).read_text())
    assert audit["request"]["messages"][0]["content"] == "你好"
    assert audit["response"]["choices"][0]["message"]["content"] == "ok"
    assert "PRIVATE" not in str(audit) and "private reasoning" not in str(audit)
    # Restarting does not reset the user's budget.
    with httpx.Client(transport=BudgetTransport(tmp_path, httpx.MockTransport(success))) as client:
        request(client)
    assert json.loads((tmp_path / "budget.json").read_text())["accounted_micro_cny"] == 720


def test_budget_stops_before_network_and_cannot_run_concurrently(tmp_path):
    transport = BudgetTransport(tmp_path, httpx.MockTransport(success))
    with pytest.raises(BudgetStop):
        BudgetTransport(tmp_path, httpx.MockTransport(success))
    ledger = json.loads((tmp_path / "budget.json").read_text())
    ledger["accounted_micro_cny"] = LIMIT - 1
    transport.ledger = ledger
    with httpx.Client(transport=transport) as client:
        with pytest.raises(BudgetStop):
            request(client)
    assert not list((tmp_path / "requests").glob("*.json"))


@pytest.mark.parametrize("failure", ["timeout", "no_usage", "http_error"])
def test_uncertain_bill_keeps_reservation_and_stops_following_calls(tmp_path, failure):
    calls = []
    def handler(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("unknown outcome", request=req)
        if failure == "http_error":
            return httpx.Response(401, json={"error": "PRIVATE"})
        return httpx.Response(200, json={"choices": []})
    with httpx.Client(transport=BudgetTransport(tmp_path, httpx.MockTransport(handler))) as client:
        with pytest.raises(BudgetStop):
            request(client)
        with pytest.raises(BudgetStop):
            request(client)
    ledger = json.loads((tmp_path / "budget.json").read_text())
    assert len(calls) == 1 and ledger["accounted_micro_cny"] > 0 and ledger["blocked"]
    assert "PRIVATE" not in (tmp_path / "budget.json").read_text()
    with httpx.Client(transport=BudgetTransport(tmp_path, httpx.MockTransport(success))) as client:
        with pytest.raises(BudgetStop):
            request(client)


def test_unapproved_model_endpoint_or_output_limit_never_reaches_network(tmp_path):
    def forbidden(req):
        pytest.fail("Network must not be reached")
    with httpx.Client(transport=BudgetTransport(tmp_path, httpx.MockTransport(forbidden))) as client:
        for url, body in [("https://other.invalid/chat/completions", {"model": "deepseek-flash", "max_tokens": 4096}),
                          ("https://api.deepseek.com/chat/completions", {"model": "other", "max_tokens": 4096}),
                          ("https://api.deepseek.com/chat/completions", {"model": "deepseek-flash"})]:
            with pytest.raises(BudgetStop):
                client.post(url, json=body)


def test_gold_runner_whitelists_model_input_and_freezes_source(tmp_path):
    from scripts.run_gold_eval import public_question, snapshot
    case = {"business_rules": "RULES", "id": "SECRET_ID", "profile": "ANSWER_PROFILE", "failure_trap": "SECRET_ANSWER"}
    assert public_question(case, {"question": "QUESTION", "allowed_routes": ["sql"]}) == "业务口径：RULES\n\n当前问题：QUESTION"
    snapshot(tmp_path, {"model": "deepseek-flash"}, "baseline")
    snapshot(tmp_path, {"model": "deepseek-flash"}, "baseline")
    assert (tmp_path / "source/backend/app/agent/nodes.py").exists()
    assert not (tmp_path / "source/.env").exists()
    with pytest.raises(BudgetStop):
        snapshot(tmp_path, {"model": "other"}, "baseline")


def test_offline_scorer_checks_data_chart_and_explicit_equivalences():
    from evals.gold_dataset import verify_references
    from evals.gold_scoring import score_turn
    expected = verify_references()["G037"][0]
    rows = [{"month": r["month"] + "-01T00:00:00", "sales": r["revenue"]} for r in expected["rows"]]
    final = {"rows": rows, "columns": ["month", "sales"], "chart_spec": {"data": [{"type": "scatter", "mode": "lines+markers", "x": [r["month"] for r in rows], "y": [r["sales"] for r in rows]}]}}
    record = {"status": "ran", "final": final}
    review = {"column_map": {"sales": "revenue"}, "normalize": {"month": "month"}, "note": "销售额别名；月初时间与自然月等价"}
    assert score_turn(record, expected, review)["outcome"] == "pass"
    final["chart_spec"]["data"][0]["y"][0] += 1
    assert score_turn(record, expected, review)["outcome"] == "fail"


def test_python_numeric_review_requires_matching_output_and_execution():
    import hashlib
    from evals.gold_dataset import verify_references
    from evals.gold_scoring import score_turn
    output = "1.000000"
    record = {"status": "ran", "final": {"route": "python", "python_output": output}, "trace": [{"node": "execute_python"}]}
    review = {"python_rows": [{"r": 1.0}], "python_output_sha256": hashlib.sha256(output.encode()).hexdigest(), "note": "逐字核对真实容器输出"}
    expected = verify_references()["G045"][0]
    assert score_turn(record, expected, review)["outcome"] == "pass"
    record["final"]["python_output"] = "0.500000"
    assert score_turn(record, expected, review)["outcome"] == "uncertain"
    assert score_turn(record, expected, {})["outcome"] != "pass"
    record["status"] = "environment_not_run"
    assert score_turn(record, expected, review)["outcome"] == "fail"


def test_frequency_histogram_scores_equivalent_observations_but_rejects_lost_weights():
    from app.tools.viz import ChartSpec, build_plotly_spec
    from evals.gold_dataset import verify_references
    from evals.gold_scoring import score_turn
    rows = [{"unit_price": p, "n": n} for p,n in [(25,3),(40,1),(45,1),(50,4),(90,1),(100,6),(180,1),(200,3)]]
    spec = ChartSpec(chart_type="histogram", x_column="unit_price", y_column="n", title="单价", x_label="单价", y_label="明细条数")
    record = {"status": "ran", "final": {"columns": ["unit_price", "n"], "rows": rows, "chart_spec": build_plotly_spec(spec, rows)}}
    expected = verify_references()["G040"][0]
    review = {"frequency_column": "n", "note": "COUNT(*)为明细频次，合计20，图必须按相同频次求和"}
    assert score_turn(record, expected, review)["outcome"] == "pass"
    del record["final"]["chart_spec"]["data"][0]["y"]
    assert score_turn(record, expected, review)["outcome"] == "fail"
