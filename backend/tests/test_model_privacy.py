import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_chat_storage import storage_app  # noqa: F401

from app.context_memory import project_messages
from app.execution import invoke_model
from app.tools.dataset_manager import is_sensitive_column, model_safe_rows


@pytest.mark.parametrize('column', ['收货人', '收件人', '收货地址', '联系手机', 'customerName', 'recipientName'])
def test_common_commerce_personal_columns_are_hidden(column):
    assert is_sensitive_column(column)
    assert model_safe_rows([{column: '张三', 'amount': 100}])[0] == {column: '[个人字段样例已隐藏]', 'amount': 100}


@pytest.mark.parametrize('column', ['product_name', 'product_category_name', 'category_name', 'customer_count'])
def test_business_labels_and_counts_are_not_personal_fields(column):
    assert not is_sensitive_column(column)


def test_history_redacts_personal_values_in_rows_text_and_charts_without_mutating_storage():
    messages = [{'id': 1, 'role': 'assistant', 'content': '张三消费100元，邮箱fake@example.invalid',
                 'metadata': {'rows': [{'收货人': '张三', 'email': 'fake@example.invalid', 'amount': 100}],
                              'columns': ['收货人', 'email', 'amount'],
                              'chart_spec': {'data': [{'type': 'bar', 'x': ['张三'], 'y': [100]}],
                                             'layout': {'xaxis': {'title': '收货人'}}}}}]
    before = deepcopy(messages)
    projected = project_messages(messages)
    serialized = json.dumps(projected, ensure_ascii=False)
    assert '张三' not in serialized and 'fake@example.invalid' not in serialized
    assert projected[0]['chart_snapshot']['spec']['data'][0]['y'] == [100]
    assert messages == before


def test_every_model_request_redacts_email_and_phone_in_free_text():
    captured = []
    class Model:
        def invoke(self, messages):
            captured.extend(messages)
            return AIMessage(content='ok')
    message = HumanMessage(content='联系邮箱fake@example.invalid，电话13800138000；销售额10000元')
    invoke_model(Model(), [message])
    assert 'fake@example.invalid' not in captured[0].content
    assert '13800138000' not in captured[0].content
    assert '销售额10000元' in captured[0].content
    assert 'fake@example.invalid' in message.content


def test_board_chart_and_notes_are_redacted_before_model_serialization(monkeypatch):
    from app import attachments
    from app.schemas import AskRequest
    captured = []
    class Model:
        def invoke(self, messages):
            captured.extend(messages)
            return AIMessage(content='ok')
    monkeypatch.setattr(attachments, 'get_chat_llm', lambda: Model())
    req = AskRequest(question='总结面板', session_id='privacy-example')
    req._attachments = {'board': {'items': [
        {'kind': 'chart', 'spec': {'data': [{'type': 'bar', 'x': ['示例路1号'], 'y': [100]}], 'layout': {'xaxis': {'title': '收货地址'}}}},
        {'kind': 'text', 'text': '联系fake@example.invalid，销售额100元'},
    ]}}
    before = deepcopy(req._attachments)
    attachments.analyse_board(req)
    assert '示例路1号' not in captured[-1].content
    assert 'fake@example.invalid' not in captured[-1].content
    assert req._attachments == before


def test_legacy_query_memory_redacts_known_personal_values_in_question_and_sql():
    from app.session import QueryMemory, Session, render_session_context
    memory = QueryMemory(question='张三的金额', sql="SELECT amount FROM orders WHERE customer_name='张三'",
                         columns=['customer_name', 'amount'], row_count=1,
                         sample_rows=[{'customer_name': '张三', 'amount': 100}])
    source = Session(session_id='private-history', created_at=0, last_accessed_at=0, recent_queries=[memory])
    assert '张三' not in render_session_context(source)
    assert '张三' in memory.sql


async def test_quote_uses_source_rows_to_redact_names_but_keeps_local_selection(storage_app):  # noqa: F811
    from app import attachments, session
    from app.schemas import AskRequest
    client, _, _ = storage_app
    await client.post('/sessions/private-quote/upload', files={'files': ('a.csv', b'amount\n100\n', 'text/csv')})
    store = session.get_session_store()._backend
    store.save_message('private-quote', 'assistant', '张三消费100元', {'rows': [{'收货人': '张三', 'amount': 100}]})
    source = store.get_messages('private-quote')[-1]
    req = AskRequest(question='解释这条', session_id='private-quote', quote={'message_id': source['id'], 'text': '张三消费100元'})
    attachments.prepare_context(req)
    assert req._attachments['quote']['text'] == '张三消费100元'
    assert '张三' not in attachments.quoted_question(req)
