from types import SimpleNamespace

from test_local_api import local_app  # noqa: F401


async def test_compound_overview_keeps_measured_missingness_and_answers_impacts(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/complete-overview/upload', files={'files':
        ('sales.csv', b'item,amount\na,10\nb,\n', 'text/csv')})
    from app.agent import nodes
    captured = []
    def reply(messages):
        captured.extend(messages)
        return nodes.OverviewExplanation(impacts={'sales.amount':'金额汇总仅覆盖已知金额，暂不将未知金额计为零。',
            'invented.price':'这个字段实际上没有缺失，不能出现在最终回复。'},tables=[nodes.TableExplanation(
                table_name='sales',title='商品销售表',description='逐条商品记录及其金额。',fields=[
                    nodes.FieldExplanation(column='item',label='商品',meaning='原始商品标识。'),
                    nodes.FieldExplanation(column='amount',label='商品金额',meaning='每条记录的商品金额，币种需另行确认。'),
                    nodes.FieldExplanation(column='invented',label='不应出现',meaning='不存在的列。')])])
    monkeypatch.setattr(nodes, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=reply))
    result = nodes.chat_node({'question':'介绍当前表，哪些字段缺失，有什么影响',
        'session_id':'complete-overview', 'show_data_preview':True,
        'table_descriptions':{'sales':'一行一个商品记录'},
        'schema':'Table: sales\n  - amount: DOUBLE (missing: 1)'})
    assert captured, '概览不能提前结束、跳过对影响和其他子问题的回答'
    assert 'missing: 1' in captured[-1].content
    assert '当前导入了 1 张表' in result['answer']
    assert 'amount' in result['answer'] and '仅覆盖已知金额' in result['answer']
    assert 'invented.price' not in result['answer']
    table=result['data_dictionary'][0]
    assert table['table_name']=='sales' and table['title']=='商品销售表'
    assert [field['column'] for field in table['fields']]==['item','amount']
    assert table['fields'][1]['missing']==1 and table['fields'][1]['missing_percent']==50
    assert table['fields'][1]['label']=='商品金额' and table['fields'][1]['sample']==10
    assert '实际缺失字段及影响' not in result['answer']
