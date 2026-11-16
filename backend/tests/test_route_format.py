import json

import httpx
import pytest
from pydantic import ValidationError

from app.agent.router import RouteDecision
from app.llm import build_compatible_client

BRIEF={'goal':'统计','requirements':['统计'],'acceptance':[{'requirement_index':0,'answer_type':'value','result_columns':['n']}]}


def test_official_router_uses_the_existing_json_protocol_without_an_extra_request(monkeypatch):
    from app import llm
    from app.agent import router
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload.get('response_format') == {'type': 'json_object'} and not payload.get('tools')
        assert 'JSON Schema' in payload['messages'][0]['content']
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0,
            'model': 'deepseek-flash', 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps({'route': 'sql', 'reason': '统计', 'analysis_brief': BRIEF})}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        base = build_compatible_client('https://api.deepseek.com', 'offline-only', 'deepseek-flash', http_client=http)
        monkeypatch.setattr(llm, '_chat_llm', base)
        monkeypatch.setattr(llm, '_structured_llms', {})
        decision = router.router_node({'question': '统计数量', 'schema': 'records(id)'})
    assert decision['route'] == 'sql' and len(requests) == 1


@pytest.mark.parametrize("value", ['route: "python"', "route: 'sql'", '  route: "viz"  '])
def test_redundant_field_label_preserves_known_route(value):
    expected = value.strip().split(":", 1)[1].strip().strip("\"'")
    assert RouteDecision(route=value, reason="格式兼容检查", analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}).route == expected


@pytest.mark.parametrize("value", ['route: "delete"', 'route: "python"; delete()', 'route: "sql\'', {"route": "sql"}])
def test_format_repair_cannot_introduce_unknown_routes_or_executable_text(value):
    with pytest.raises(ValidationError):
        RouteDecision(route=value, reason="保持枚举边界", analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})


def test_sdk_parses_labeled_route_without_an_extra_http_request():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "", "tool_calls": [{"id": "offline-call", "type": "function", "function": {
            "name": "RouteDecision", "arguments": json.dumps({"route": 'route: "python"', "reason": "统计相关系数",'analysis_brief':BRIEF})}}]}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = build_compatible_client("https://api.deepseek.com", "offline-only", "deepseek-flash", http_client=http)
        decision = client.with_structured_output(RouteDecision).invoke("选择统计路线")
    assert decision.route == "python" and len(requests) == 1


def test_redundant_decision_object_is_unwrapped_without_changing_its_meaning():
    result = RouteDecision.model_validate({"route": {"route": "sql", "reason": "统计缺失行数",'analysis_brief':BRIEF}})
    assert result.route == "sql" and result.reason == "统计缺失行数"


def test_nested_wrapper_cannot_override_an_explicit_outer_reason():
    with pytest.raises(ValidationError):
        RouteDecision.model_validate({"route": {"route": "sql", "reason": "内层"}, "reason": "冲突外层"})
def test_router_can_request_a_data_overview_without_executing_analysis_sql():
    decision=RouteDecision(route='chat',reason='介绍导入表和空值',show_data_preview=True, analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})
    assert decision.show_data_preview is True


@pytest.mark.parametrize('brief',[None,{'goal':'利润可以算吗','requirements':['可计算性']},
    {'goal':'定义及表','requirements':['定义','表'],'acceptance':[{'requirement_index':0,'answer_type':'definition','metric_ids':['profit']}]},
    {'goal':'定义及表','requirements':['定义','表'],'acceptance':[{'requirement_index':0,'answer_type':'definition'},{'requirement_index':0,'answer_type':'table'}]}])
def test_incomplete_routing_contract_cannot_fall_through_to_untracked_chat(brief):
    raw={'route':'chat','reason':'现有数据缺成本，说明可计算性'}
    if brief is not None:raw['analysis_brief']=brief
    with pytest.raises(ValidationError):RouteDecision.model_validate(raw)


@pytest.mark.parametrize('kind',['definition','feasibility','value','comparison','table','chart'])
def test_typed_but_targetless_contract_is_rejected_at_model_boundary(kind):
    raw={'route':'chat','reason':'真实失败返回的结构回放','analysis_brief':{
        'goal':'利润及变化是否可以算','requirements':['判断利润能否计算'],
        'acceptance':[{'requirement_index':0,'answer_type':kind,'metric_ids':[],'result_columns':[]}]}}
    with pytest.raises(ValidationError):RouteDecision.model_validate(raw)



def test_targetless_real_route_reply_stops_before_free_chat_or_execution(monkeypatch):
    from types import SimpleNamespace
    from app.agent import router,graph,nodes
    raw={'route':'chat','reason':'可行性判断与缺失项说明','analysis_brief':{
        'goal':'两期利润及利润变化可以算吗','requirements':['判断两期利润及变化是否可计算','逐项列出缺失项','商品金额不能替代利润'],
        'acceptance':[{'requirement_index':0,'answer_type':'feasibility','metric_ids':[],'result_columns':[]},
            {'requirement_index':1,'answer_type':'explanation','metric_ids':[],'result_columns':[]},
            {'requirement_index':2,'answer_type':'explanation','metric_ids':[],'result_columns':[]}]}}
    calls=[]
    def invoke(messages):
        calls.append(messages)
        assert '"id": "profit"' in messages[1].content and '实际成本' in messages[1].content
        return RouteDecision.model_validate(raw)
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(nodes,'_get_llm',lambda:pytest.fail('no untracked free chat'))
    monkeypatch.setattr(graph,'write_sql_node',lambda state:pytest.fail('do not execute the protocol text'))
    final=graph.build_graph().invoke({'question':'两期利润及利润变化可以算吗','schema':'sales.amount_brl'})
    assert len(calls)==1 and final['error'] and '未执行查询' in final['answer']
    assert final['checks'][0]['id']=='analysis_contract'
