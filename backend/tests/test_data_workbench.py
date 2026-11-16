import hashlib
import json

import pandas as pd
import pytest

from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import store_dataframe
from app.tools.data_workbench import (CleaningRequest, WorkbenchConflict, preview_cleaning,
                                      apply_cleaning, undo_cleaning, workbench_snapshot,
                                      restore_workbench, describe_workbench)


@pytest.fixture
def workbench(tmp_path):
    manager = DatasetManager()
    ds = manager.get_or_create('clean-test', tmp_path)
    manager.add_file(ds.session_id, store_dataframe('sales.csv', pd.DataFrame({
        'id': ['0007', '0008', '0008'], 'region': [' East ', None, 'NA'],
        'amount': [100., None, 20.], 'date': ['2018/01/02', 'bad', None],
    }), tmp_path))
    yield ds, manager
    ds.close()


def request(ds, rules):
    return CleaningRequest(table='sales', snapshot=workbench_snapshot(ds), rules=rules)


def test_preview_apply_undo_preserves_original_and_rejects_stale(workbench):
    ds, manager = workbench
    original = ds.files[0].parquet_path
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    imported_at = ds.files[0].source['imported_at']
    initial = workbench_snapshot(ds)
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    assert preview['affected_rows'] == 1 and preview['before_rows'] == preview['after_rows'] == 3
    assert preview['examples'][0]['after']['region'] == 'East'
    assert workbench_snapshot(ds) == initial
    apply_cleaning(ds, preview['preview_id'], manager)
    assert ds.files[0].source['imported_at'] == imported_at
    assert ds.files[0].parquet_path != original
    assert ds.get_connection().execute('select region from sales limit 1').fetchone()[0] == 'East'
    with pytest.raises(WorkbenchConflict):
        apply_cleaning(ds, preview['preview_id'], manager)
    restored = type(ds)(ds.session_id, files=[store_original(original)], parquet_dir=ds.parquet_dir)
    restore_workbench(restored)
    assert workbench_snapshot(restored) == workbench_snapshot(ds)
    assert len(restored.files) == 1
    restored.close()
    undo_cleaning(ds, 'sales', workbench_snapshot(ds), manager)
    assert workbench_snapshot(ds) == initial
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    assert describe_workbench(ds)['tables'][0]['can_undo'] is False


def store_original(path):
    from app.tools.data_workbench import read_version
    return read_version(path, 'sales')


def test_explicit_nulls_dates_and_dedup_conflicts(workbench):
    ds, _ = workbench
    preview = preview_cleaning(ds, request(ds, [
        {'operation': 'null_tokens', 'column': 'region', 'tokens': ['NA']},
        {'operation': 'fill', 'column': 'region', 'value': '未知', 'confirmed': True},
        {'operation': 'parse_date', 'column': 'date', 'format': '%Y/%m/%d'},
    ]))
    assert preview['conversion_failures'] == 1
    assert preview['after_null_counts']['amount'] == 1
    assert preview['after_null_counts']['region'] == 0
    assert {'column':'date','before':'string','after':'datetime64'} in preview['type_changes']
    with pytest.raises(ValueError, match='同键不同'):
        preview_cleaning(ds, request(ds, [{'operation': 'deduplicate', 'keys': ['id'], 'keep': 'first'}]))
    preview = preview_cleaning(ds, request(ds, [
        {'operation': 'deduplicate', 'keys': ['id'], 'keep': 'last', 'confirmed': True}]))
    assert preview['after_rows'] == 2 and preview['conflicting_keys'] == 1


def test_boundary_and_save_failure_do_not_change_active_version(workbench, monkeypatch):
    ds, manager = workbench
    with pytest.raises(ValueError):
        CleaningRequest(table='../escape', snapshot='x', rules=[{'operation': 'sql'}])
    with pytest.raises(ValueError):
        request(ds, [{'operation': 'fill', 'column': 'amount', 'value': 0}])
    with pytest.raises(ValueError, match='字段'):
        preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'absent'}]))
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    initial = workbench_snapshot(ds)
    from app.tools import data_workbench
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(data_workbench, '_write_state', fail)
    with pytest.raises(OSError):
        apply_cleaning(ds, preview['preview_id'], manager)
    assert workbench_snapshot(ds) == initial
    assert not (ds.parquet_dir / '.workbench' / 'state.json').exists()


def test_preview_from_other_dataset_cannot_be_applied(workbench, tmp_path):
    ds, manager = workbench
    other = manager.get_or_create('other', tmp_path / 'other')
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    with pytest.raises((ValueError, FileNotFoundError)):
        apply_cleaning(other, preview['preview_id'], manager)


def test_numeric_fill_rejects_text_and_expired_preview(workbench):
    ds, manager = workbench
    with pytest.raises(ValueError, match='数值'):
        preview_cleaning(ds, request(ds, [{'operation': 'fill', 'column': 'amount', 'value': 'unknown', 'confirmed': True}]))
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    path = ds.parquet_dir / '.workbench' / 'previews' / (preview['preview_id'] + '.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['created_at'] = 0
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(WorkbenchConflict, match='过期'):
        apply_cleaning(ds, preview['preview_id'], manager)


def test_recovered_version_preserves_original_filename(workbench):
    ds, manager = workbench
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    apply_cleaning(ds, preview['preview_id'], manager)
    restored = type(ds)(ds.session_id, files=[store_original(ds.parquet_dir / 'sales.parquet')], parquet_dir=ds.parquet_dir)
    restore_workbench(restored)
    assert restored.files[0].original_filename == 'sales.csv'
    restored.close()


def test_explicit_number_conversion_and_mapping_preserve_raw_and_reject_ambiguity(tmp_path):
    manager = DatasetManager()
    ds = manager.get_or_create('number-clean', tmp_path)
    manager.add_file(ds.session_id, store_dataframe('sales.csv', pd.DataFrame({
        'order_id': ['0007', '0008', '0009'], 'amount': ['R$ 1.234,50', 'R$ 20,00', None],
        'region': ['east', 'East', None],
    }), tmp_path))
    original = ds.files[0].parquet_path.read_bytes()
    preview = preview_cleaning(ds, request(ds, [
        {'operation': 'parse_number', 'column': 'amount', 'decimal_separator': ',', 'thousands_separator': '.', 'currency_token': 'R$'},
        {'operation': 'map_values', 'column': 'region', 'mapping': {'east': 'East'}},
    ]))
    assert preview['examples'][0]['after']['amount'] == '1234.50'
    assert preview['after_null_counts']['amount'] == 1
    assert preview['rules'][1]['mapping'] == {'east': 'East'}
    apply_cleaning(ds, preview['preview_id'], manager)
    assert ds.get_connection().execute('select sum(amount) from sales').fetchone()[0] == 1254.5
    assert ds.get_connection().execute('select order_id from sales limit 1').fetchone()[0] == '0007'
    assert (tmp_path / 'sales.parquet').read_bytes() == original
    with pytest.raises(ValueError, match='编码|前导零'):
        preview_cleaning(ds, request(ds, [{'operation': 'parse_number', 'column': 'order_id'}]))
    ds.close()


def test_decimal_preview_keeps_exact_money_digits(tmp_path):
    manager = DatasetManager(); ds = manager.get_or_create('precise-price',tmp_path)
    manager.add_file(ds.session_id,store_dataframe('sales.csv',pd.DataFrame({'price':['R$ 12345678901234567.89']}),tmp_path))
    preview = preview_cleaning(ds,request(ds,[{'operation':'parse_number','column':'price','currency_token':'R$'}]))
    assert preview['examples'][0]['after']['price'] == '12345678901234567.89'
    ds.close()


def test_number_conversion_reports_failures_and_requires_explicit_loss_policy(workbench):
    ds, _ = workbench
    with pytest.raises(ValueError, match='转换|格式'):
        preview_cleaning(ds, request(ds, [{'operation': 'parse_number', 'column': 'region'}]))
    preview = preview_cleaning(ds, request(ds, [
        {'operation': 'parse_number', 'column': 'region', 'failure_policy': 'null'}]))
    assert preview['conversion_failures'] == 2
    assert preview['after_null_counts']['region'] == 3


def test_diagnosis_measures_whole_table_without_leaking_sensitive_samples(tmp_path):
    from app.tools.data_workbench import diagnose_data
    manager = DatasetManager()
    ds = manager.get_or_create('profile', tmp_path)
    frame = pd.DataFrame({'order_id': ['0007'] * 60, 'region': [' East '] * 58 + ['NA', None],
                          'email': ['private@example.com'] * 60})
    manager.add_file(ds.session_id, store_dataframe('sales.csv', frame, tmp_path))
    report = diagnose_data(ds)
    table = report['tables'][0]
    assert table['rows'] == 60 and table['duplicate_rows'] == 57
    region = next(col for col in table['fields'] if col['column'] == 'region')
    assert region['null_count'] == 1 and region['trim_count'] == 58
    assert region['null_candidates'] == [{'value': 'NA', 'count': 1}]
    assert 'private@example.com' not in json.dumps(report)
    assert workbench_snapshot(ds) == report['snapshot']
    preview = preview_cleaning(ds, request(ds, [{'operation': 'trim', 'column': 'region'}]))
    assert preview['affected_rows'] == 58 and len(preview['examples']) == 50
    ds.close()


def test_diagnosis_finds_rare_number_failure_outside_frequent_samples(tmp_path):
    from app.tools.data_workbench import diagnose_data
    manager = DatasetManager(); ds = manager.get_or_create('rare-format',tmp_path)
    manager.add_file(ds.session_id,store_dataframe('sales.csv',pd.DataFrame({'price':['R$ 10.50']*70+['待核对',None]}),tmp_path))
    field = diagnose_data(ds)['tables'][0]['fields'][0]
    candidate = next(c for c in field['number_candidates'] if c['decimal_separator']=='.')
    assert candidate['success_count'] == 70 and candidate['failure_count'] == 1
    assert candidate['failure_examples'] == ['待核对']
    ds.close()
