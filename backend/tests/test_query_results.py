import pandas as pd
import pytest

from app.config import settings
from app.tools.query_results import read_result, save_result


def test_result_pages_keep_exact_order_and_survive_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    frame = pd.DataFrame({"id": range(123), "amount": [0.123456789012] * 123})
    result_id = save_result("first", frame)
    frame.loc[:, "id"] = -1
    pages = [read_result("first", result_id, offset) for offset in (0, 50, 100, 150)]
    assert [len(page["rows"]) for page in pages] == [50, 50, 23, 0]
    assert [row["id"] for page in pages for row in page["rows"]] == list(range(123))
    assert pages[1]["rows"][0]["amount"] == 0.123456789012
    assert all(page["row_count"] == 123 for page in pages)
    with pytest.raises(FileNotFoundError):
        read_result("other", result_id, 50)
    with pytest.raises(ValueError):
        read_result("first", "../outside", 50)
    with pytest.raises(ValueError):
        read_result("first", result_id, 51)


def test_arrow_pages_keep_nullable_large_ids_and_decimal_digits(tmp_path,monkeypatch):
    from decimal import Decimal
    monkeypatch.setattr(settings,'local_data_dir',tmp_path)
    frame=pd.DataFrame({'id':pd.array([2**53+1,None],dtype='Int64'),
        'amount':[Decimal('9007199254740993.01'),Decimal('0.01')]})
    result_id=save_result('precision',frame)
    assert read_result('precision',result_id,0)['rows']==[
        {'id':'9007199254740993','amount':'9007199254740993.01'},{'id':None,'amount':'0.01'}]


def test_chart_uses_all_typed_arrow_values_instead_of_the_json_display_page(tmp_path,monkeypatch):
    from decimal import Decimal
    from app.agent.nodes import make_chart_node
    from app.tools import viz
    monkeypatch.setattr(settings,'local_data_dir',tmp_path)
    frame=pd.DataFrame({'amount':[Decimal('0.01')]*61})
    result_id=save_result('chart-typed',frame)
    def choose(**kwargs):
        assert len(kwargs['sample_rows'])==61 and all(isinstance(row['amount'],Decimal) for row in kwargs['sample_rows'])
        return viz.ChartSpec(chart_type='histogram',x_column='amount',title='分布',x_label='金额',y_label='频数')
    monkeypatch.setattr(viz,'choose_chart',choose)
    state={'session_id':'chart-typed','question':'分布','row_count':61,'rows':[{'amount':'0.01'}]*50,'columns':['amount'],'result_id':result_id}
    result=make_chart_node(state)
    assert not result.get('chart_error') and len(result['chart_spec']['data'][0]['x'])==61
    assert result['checks'][-1]['source_rows']==61
    assert make_chart_node({**state,'row_count':62})['chart_error']


def test_chart_limit_also_applies_when_all_rows_are_already_in_memory(monkeypatch):
    from app.agent.nodes import make_chart_node
    from app.tools import viz
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:pytest.fail('Oversized chart must stop before a model request'))
    rows=[{'amount':i} for i in range(5001)]
    result=make_chart_node({'rows':rows,'columns':['amount'],'row_count':len(rows),'question':'画图'})
    assert '5000' in result['chart_error'] and not result['chart_spec']


def test_result_directory_cannot_redirect_outside_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    result_id = save_result("first", pd.DataFrame({"id": range(60)}))
    directory = tmp_path / "uploads" / "first" / ".results"
    outside = tmp_path / "outside"
    directory.rename(outside)
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows account cannot create directory symlinks")
    with pytest.raises(ValueError):
        read_result("first", result_id, 50)


def test_deleting_session_removes_its_result_snapshot_and_releases_handles(tmp_path, monkeypatch):
    from app.tools.file_storage import delete_session_files
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    frame = pd.DataFrame({"id": range(60)})
    first, other = save_result("first", frame), save_result("other", frame)
    assert len(read_result("first", first, 50)["rows"]) == 10
    delete_session_files("first")
    assert not (tmp_path / "uploads" / "first").exists()
    assert len(read_result("other", other, 50)["rows"]) == 10


async def test_pagination_endpoint_reads_saved_results_without_sql_or_model(local_app, monkeypatch):
    from app import main
    from app.agent import nodes
    client, restart, directory = local_app
    sid = "pagination"
    response = await client.post(f"/sessions/{sid}/upload", files={"files": ("sales.csv", b"value\n" + b"1\n" * 123, "text/csv")})
    assert response.status_code == 200, response.text
    result = nodes.execute_sql_node({"session_id": sid, "sql": "SELECT value FROM sales ORDER BY value"})
    assert result["row_count"] == 123 and len(result["rows"]) == 50
    assert result["result_id"]
    restart()
    monkeypatch.setattr(main, "get_chat_llm", lambda: (_ for _ in ()).throw(AssertionError("No model call allowed")))
    monkeypatch.setattr(main, "get_dataset_manager", lambda: (_ for _ in ()).throw(AssertionError("No SQL rerun allowed")))
    page = await client.get(f'/sessions/{sid}/results/{result["result_id"]}?offset=50')
    assert page.status_code == 200, page.text
    assert len(page.json()["rows"]) == 50 and page.headers["cache-control"] == "no-store"
    assert (await client.get(f'/sessions/{sid}/results/{result["result_id"]}?offset=100')).json()["rows"] == [{"value": 1}] * 23
    assert (await client.get(f'/sessions/{sid}/results/{result["result_id"]}?offset=1')).status_code == 422
    assert (await client.get(f'/sessions/missing/results/{result["result_id"]}?offset=50')).status_code == 404


from test_local_api import local_app  # noqa: E402,F401


async def test_csv_export_keeps_all_rows_and_escapes_formula_text_only_in_copy(local_app):
    import csv
    import io
    client,_,_=local_app
    await client.post('/sessions/export/upload',files={'files':('sales.csv',b'id\n1\n','text/csv')})
    frame=pd.DataFrame({'id':['0007']*123,'text':['=1+1',' @SUM(1)',*['normal']*121],'amount':[-2]*123})
    result_id=save_result('export',frame)
    response=await client.get(f'/sessions/export/results/{result_id}/export')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows)==123 and rows[0]['id']=='0007' and rows[0]['amount']=='-2'
    assert rows[0]['text']=="'=1+1" and rows[1]['text']=="' @SUM(1)"
    assert read_result('export',result_id,0)['rows'][0]['text']=='=1+1'
    assert (await client.get(f'/sessions/other/results/{result_id}/export')).status_code==404


async def test_small_actual_query_keeps_a_downloadable_snapshot_after_restart(local_app,monkeypatch):
    import csv
    import io
    from app import main
    from app.agent.nodes import execute_sql_node
    client,restart,_=local_app
    await client.post('/sessions/small-export/upload',files={'files':('sales.csv',b'group,amount\nA,0\nA,100\nB,\n','text/csv')})
    result=execute_sql_node({'session_id':'small-export','sql':'SELECT * FROM sales'})
    assert result['result_id'] and result['row_count']==3
    restart()
    monkeypatch.setattr(main,'get_chat_llm',lambda:(_ for _ in ()).throw(AssertionError('No model request')))
    response=await client.get(f'/sessions/small-export/results/{result["result_id"]}/export')
    assert response.status_code==200
    assert list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))==[
        {'group':'A','amount':'0.0'},{'group':'A','amount':'100.0'},{'group':'B','amount':''}]
