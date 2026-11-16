import hashlib

import pandas as pd
import pytest

from app.tools.data_workbench import temporal_report, temporal_rows, TemporalRowsRequest, workbench_snapshot, WorkbenchConflict, preview_cleaning, apply_cleaning, CleaningRequest
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import store_dataframe


@pytest.mark.parametrize('first,second',[
    ({1,3},{2,4}),({1,3},{1,3}),({1,3},{3,4}),(set(),set()),
])
def test_temporal_counts_are_row_unions_and_real_intersections(first,second):
    frame=pd.DataFrame({'order_id':['same']*5,'order_purchase_timestamp':['2018-05-03']*5,
        'order_approved_at':['2018-05-02' if i in first else '2018-05-04' for i in range(5)],
        'order_delivered_customer_date':['2018-05-02' if i in second else '2018-05-04' for i in range(5)]})
    report=temporal_report(frame);checks=report['temporal_checks'];summary=report['temporal_summary']
    assert summary['affected_rows']==len(first|second)
    assert summary['multiple_rule_rows']==len(first&second) and summary['unit']=='rows'
    assert checks[0]['reversed_rows']==len(first) and checks[1]['reversed_rows']==len(second)
    assert sum(item['rows'] for item in summary['overlaps'])==len(first&second)
    assert [e['active_row'] for e in checks[0]['examples']]==[i+1 for i in sorted(first)[:3]]
    if first:assert checks[0]['examples'][0]['order_id']=='same'


def test_temporal_missing_invalid_and_true_zero_are_distinct():
    frame=pd.DataFrame({'order_purchase_timestamp':['2018-05-03',None,'bad','2018-05-03T01:00:00+03:00','2018-02-30'],
        'order_approved_at':['2018-05-02']*5})
    check=temporal_report(frame)['temporal_checks'][0]
    assert (check['eligible_rows'],check['missing_rows'],check['invalid_rows'],check['unavailable_rows'])==(1,1,3,4)
    assert check['examples'][0]['earlier']=='2018-05-03'
    assert temporal_report(frame.iloc[:0])['temporal_summary']['affected_rows']==0


def test_full_hits_are_immutable_pageable_and_snapshot_bound(tmp_path,monkeypatch):
    from app.tools import query_results
    monkeypatch.setattr(query_results,'local_session_dir',lambda sid:tmp_path/sid)
    manager=DatasetManager();ds=manager.get_or_create('time-hits',tmp_path/'time-hits')
    frame=pd.DataFrame({'order_id':[f'{i:04d}' for i in range(61)],'region':[' East ']*61,
        'order_purchase_timestamp':['2018-05-03']*61,
        'order_approved_at':['2018-05-04']*6+['2018-05-02']*55})
    manager.add_file(ds.session_id,store_dataframe('orders.csv',frame,ds.parquet_dir))
    source=ds.files[0].parquet_path;digest=hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot=workbench_snapshot(ds);check=temporal_report(frame)['temporal_checks'][0]
    request=TemporalRowsRequest(table='orders',snapshot=snapshot,check_id=check['id'])
    result=temporal_rows(ds,request)
    assert result['row_count']==55 and len(result['rows'])==50
    assert result['rows'][0]['active_row']==7 and result['rows'][0]['order_id']=='0006'
    last=query_results.read_result(ds.session_id,result['result_id'],50)
    assert last['rows'][-1]['active_row']==61 and last['rows'][-1]['order_id']=='0060'
    assert '0060' in ''.join(query_results.result_csv(ds.session_id,result['result_id']))
    preview=preview_cleaning(ds,CleaningRequest(table='orders',snapshot=snapshot,rules=[{'operation':'trim','column':'region'}]))
    apply_cleaning(ds,preview['preview_id'],manager)
    with pytest.raises(WorkbenchConflict):temporal_rows(ds,request)
    assert query_results.read_result(ds.session_id,result['result_id'],0)['rows'][0]['region']==' East '
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    ds.close()
