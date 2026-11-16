import asyncio
import pytest
from app import main, session
from app.execution import TaskRegistry
from app.tools.dataset_manager import get_dataset_manager
from app.tools.data_workbench import workbench_snapshot
from test_chat_storage import storage_app  # noqa: F401


async def test_ambiguous_sql_blocks_are_not_silently_chosen(storage_app):
    from app.sql_proposals import proposal_from_reply
    client, _, _ = storage_app
    await saved_proposal(client)
    data = get_dataset_manager().get('proposal')
    answer = "原查询：\n```sql\nSELECT SUM(amount) FROM orders\n```\n修正：\n```sql\nSELECT SUM(amount) FROM orders WHERE region='East'\n```"
    assert proposal_from_reply(answer, data) is None


async def test_image_recommendation_uses_structured_sql_instead_of_first_code_block(storage_app, monkeypatch):
    from app import attachments
    from app.schemas import AskRequest
    from types import SimpleNamespace
    client, _, _ = storage_app
    await saved_proposal(client)
    sql = "SELECT SUM(amount) AS total FROM orders WHERE region='East'"
    reply = attachments.ImageAnswer(answer='原查询：\n```sql\nSELECT SUM(amount) FROM orders\n```\n修正建议见推荐SQL卡片。', recommended_sql=sql)
    monkeypatch.setattr(attachments, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=lambda messages: reply))
    req = AskRequest(question='检查截图', session_id='proposal', images=[{'id':'a'*64+'.png','filename':'sql.png'}])
    req._attachments = {'images': []}
    final = attachments.analyse_board(req)
    response = main._complete_analysis(req, final, 'proposal', {})
    assert response.sql_proposal['sql'] == sql
    from app.sql_proposals import execute_proposal
    assert execute_proposal('proposal', 1, response.sql_proposal)['rows'] == [{'total': 140}]
    reply.recommended_sql = None
    final = attachments.analyse_board(req)
    assert main._complete_analysis(req, final, 'proposal', {}).sql_proposal is None


async def saved_proposal(client,sql='SELECT region,SUM(amount) AS total FROM orders GROUP BY region'):
    await client.post('/sessions/proposal/upload',files={'files':('orders.csv',b'region,amount\nEast,100\nWest,60\nEast,40\n','text/csv')})
    version=workbench_snapshot(get_dataset_manager().get('proposal'))
    backend=session.get_session_store()._backend
    backend.save_message('proposal','assistant','推荐 SQL，尚未执行',{'sql_proposal':{'sql':sql,'data_version':version}})
    return backend.get_messages('proposal')[-1]['id']


async def test_saved_exact_sql_runs_through_task_without_model_and_keeps_source(storage_app,monkeypatch):
    client,_,_=storage_app
    mid=await saved_proposal(client)
    monkeypatch.setattr(main,'get_chat_llm',lambda:(_ for _ in ()).throw(AssertionError('No model required')))
    registry=TaskRegistry();monkeypatch.setattr(main,'tasks',registry)
    try:
        response=await client.post(f'/sessions/proposal/messages/{mid}/execute-sql',json={})
        assert response.status_code==200,response.text
        for _ in range(100):
            task=(await client.get('/tasks/'+response.json()['task_id'])).json()
            if task['status'] in {'succeeded','failed'}:break
            await asyncio.sleep(.02)
        assert task['status']=='succeeded',task
        result=task['result']
        assert sorted((r['region'],r['total']) for r in result['rows'])==[('East',140),('West',60)]
        assert task['model_calls']==0 and result['sql_source']['message_id']==mid
        assert result['checks'][0]['status']=='passed'
    finally:registry.close()


@pytest.mark.parametrize('sql',['DELETE FROM orders',"SELECT * FROM read_csv('C:/secret.csv')"])
async def test_unsafe_saved_queries_are_rejected(storage_app,sql):
    client,_,_=storage_app
    mid=await saved_proposal(client,sql)
    response=await client.post(f'/sessions/proposal/messages/{mid}/execute-sql',json={})
    assert response.status_code==400,response.text


async def test_proposal_refuses_stale_version_and_wrong_chat(storage_app):
    client,_,_=storage_app
    mid=await saved_proposal(client)
    await client.post('/sessions/proposal/upload',files={'files':('extra.csv',b'x\n1\n','text/csv')})
    assert (await client.post(f'/sessions/proposal/messages/{mid}/execute-sql',json={})).status_code==409
    await client.post('/sessions/other/upload',files={'files':('extra.csv',b'x\n1\n','text/csv')})
    assert (await client.post(f'/sessions/other/messages/{mid}/execute-sql',json={})).status_code==404
