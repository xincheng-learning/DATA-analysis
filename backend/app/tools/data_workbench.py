"""Local, preview-first cleaning over immutable Parquet versions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.commerce import commerce_facts
from app.tools.ingest import IngestedFile, store_dataframe


class WorkbenchConflict(ValueError):
    pass


class CleaningRule(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    operation: Literal['trim', 'null_tokens', 'fill', 'parse_date', 'deduplicate', 'parse_number', 'map_values']
    column: str = ''
    tokens: list[str] = Field(default_factory=list, max_length=30)
    value: str | float | None = None
    format: str = Field(default='', max_length=100)
    keys: list[str] = Field(default_factory=list, max_length=20)
    keep: Literal['first', 'last'] = 'first'
    confirmed: bool = False
    decimal_separator: Literal['.', ','] = '.'
    thousands_separator: Literal['', '.', ',', ' '] = ''
    currency_token: str = Field(default='', max_length=12)
    failure_policy: Literal['reject', 'null'] = 'reject'
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode='after')
    def valid_operation(self):
        if self.operation == 'deduplicate':
            if not self.keys or len(set(self.keys)) != len(self.keys):
                raise ValueError('去重需要明确且不重复的业务键。')
        elif not self.column:
            raise ValueError('请选择字段。')
        if self.operation == 'null_tokens' and not self.tokens:
            raise ValueError('请明确哪些文本表示空值。')
        if self.operation == 'fill' and (self.value is None or not self.confirmed):
            raise ValueError('填充需要明确的值及业务确认，不默认补零。')
        if self.operation == 'parse_date' and not self.format:
            raise ValueError('请指定原始日期格式。')
        if self.operation == 'parse_number' and self.decimal_separator == self.thousands_separator:
            raise ValueError('小数点和千分位符号不能相同。')
        if self.operation == 'map_values' and not self.mapping:
            raise ValueError('请指定原值与目标类别的对应关系。')
        return self


class CleaningRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    table: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=100)
    snapshot: str = Field(min_length=1, max_length=64)
    rules: list[CleaningRule] = Field(min_length=1, max_length=20)


class TemporalRowsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    table: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=100)
    snapshot: str = Field(min_length=1,max_length=64)
    check_id: str = Field(min_length=1,max_length=250)


def version_of(file):
    return file.data_version or 'legacy-' + hashlib.sha256(file.parquet_path.read_bytes()).hexdigest()


def workbench_snapshot(dataset, replacement=None) -> str:
    versions = sorted((file.table_name, version_of(replacement) if replacement and file.table_name == replacement.table_name else version_of(file)) for file in dataset.files)
    return hashlib.sha256(json.dumps(versions).encode()).hexdigest()


def table_rows(dataset,table,snapshot,offset):
    _check_snapshot(dataset,snapshot)
    file=next((file for file in dataset.files if file.table_name==table),None)
    if file is None:raise ValueError('所选数据表不存在。')
    with pq.ParquetFile(file.parquet_path) as stored:
        batch=next((batch for index,batch in enumerate(stored.iter_batches(batch_size=50)) if index==offset//50),None)
    from app.agent.nodes import _sanitize_records
    return {'snapshot':snapshot,'table_name':table,'columns':file.columns,'row_count':file.row_count,
        'offset':offset,'rows':_sanitize_records(batch.to_pylist() if batch is not None else [])}


def _directory(dataset, child='') -> Path:
    root = dataset.parquet_dir / '.workbench'
    path = root / child
    if path.resolve() != path.absolute():
        raise ValueError('数据版本目录不能重定向到其他位置。')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path, data):
    temporary = path.with_name(uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state(dataset):
    path = _directory(dataset) / 'state.json'
    if path.is_symlink():
        raise ValueError('数据版本状态不能重定向。')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'tables': {}}


def _write_state(dataset, state):
    _write_json(_directory(dataset) / 'state.json', state)


def read_version(path: Path, table: str) -> IngestedFile:
    schema = pq.read_schema(path)
    data = json.loads((schema.metadata or {}).get(b'datapilot', b'{}'))
    return IngestedFile(data.get('source', {}).get('original_filename', data.get('filename', path.name)), table, path, schema.names,
                        {field.name: str(field.type) for field in schema}, pq.read_metadata(path).num_rows,
                        path.stat().st_size, data.get('source', {'kind': 'file'}), data.get('data_version', ''))


def _stored_path(dataset, relative):
    path = dataset.parquet_dir / relative
    valid = (path.resolve() == path.absolute() and path.suffix == '.parquet' and
             path.parent in (dataset.parquet_dir, _directory(dataset, 'versions')))
    if not valid:
        raise ValueError('无效的数据版本路径。')
    return path


def restore_workbench(dataset):
    state = _state(dataset)
    for index, file in enumerate(dataset.files):
        history = state['tables'].get(file.table_name, [])
        if history:
            dataset.files[index] = read_version(_stored_path(dataset, history[-1]['path']), file.table_name)


def describe_workbench(dataset):
    state = _state(dataset)
    tables = dataset.preview()
    for table, file in zip(tables, dataset.files):
        history = state['tables'].get(file.table_name, [])
        table.update(data_version=version_of(file), can_undo=len(history) > 1,
                     history=[{'version': h['version'], 'rules': h.get('rules', [])} for h in history],
                     original_available=bool(file.source.get('raw_file')))
        if history and history[-1].get('analysis_impact'):
            table['analysis_impact']=history[-1]['analysis_impact']
        event_columns=[c for c in file.columns if c in {'order_id','order_purchase_timestamp','order_approved_at','order_delivered_carrier_date','order_delivered_customer_date'}]
        table.update(temporal_report(pd.read_parquet(file.parquet_path,columns=event_columns)))
    from app.tools.relationships import commerce_confirmed
    commerce = commerce_facts(dataset)
    commerce['confirmed'] = commerce_confirmed(dataset)
    return {'snapshot': workbench_snapshot(dataset), 'tables': tables, 'commerce': commerce}


def _temporal_flags(frame):
    pairs=[('order_purchase_timestamp','order_approved_at'),('order_purchase_timestamp','order_delivered_carrier_date'),
        ('order_purchase_timestamp','order_delivered_customer_date'),('order_approved_at','order_delivered_carrier_date'),
        ('order_delivered_carrier_date','order_delivered_customer_date')]
    pairs=[pair for pair in pairs if set(pair)<=set(frame.columns)]
    parsed={}
    for column in {column for pair in pairs for column in pair}:
        text=frame[column].astype('string')
        iso=text.str.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?',na=False)
        parsed[column]=pd.to_datetime(text.where(iso),format='ISO8601',errors='coerce')
    flags=[]
    for earlier,later in pairs:
        eligible=parsed[earlier].notna()&parsed[later].notna()
        reversed_rows=eligible&(parsed[later]<parsed[earlier])
        flags.append((earlier,later,eligible,reversed_rows))
    return flags


def temporal_report(frame):
    """Measure row sets on explicit event fields; do not infer orders or repair dates."""
    checks=[];hits={}
    for earlier,later,eligible,reversed_rows in _temporal_flags(frame):
        identifier=earlier+'__'+later
        hits[identifier]=reversed_rows
        missing=frame[[earlier,later]].isna().any(axis=1)
        positions=[i for i,bad in enumerate(reversed_rows) if bad][:3]
        examples=[{'active_row':i+1,'earlier':str(frame.iloc[i][earlier]),'later':str(frame.iloc[i][later]),
            **({'order_id':None if pd.isna(frame.iloc[i]['order_id']) else str(frame.iloc[i]['order_id'])} if 'order_id' in frame else {})}
            for i in positions]
        checks.append({'id':identifier,'earlier_column':earlier,'later_column':later,'eligible_rows':int(eligible.sum()),
            'missing_rows':int(missing.sum()),'invalid_rows':int((~missing&~eligible).sum()),
            'unavailable_rows':int((~eligible).sum()),'reversed_rows':int(reversed_rows.sum()),
            'examples':examples,
            'scope':'按明确事件字段名和无时区ISO原值作候选检查；空值、非ISO或时区不明值不参与。需确认业务含义，不自动改时间；规则可重叠。'})
    counts=pd.DataFrame(hits,index=frame.index).sum(axis=1)
    identifiers=list(hits)
    overlaps=[{'check_ids':[first,second],'rows':int((hits[first]&hits[second]).sum())}
        for i,first in enumerate(identifiers) for second in identifiers[i+1:] if (hits[first]&hits[second]).any()]
    return {'temporal_checks':checks,'temporal_summary':{'unit':'rows','affected_rows':int((counts>0).sum()),
        'multiple_rule_rows':int((counts>1).sum()),'overlaps':overlaps}}


def temporal_checks(frame):
    return temporal_report(frame)['temporal_checks']


def temporal_rows(dataset,request:TemporalRowsRequest):
    from app.tools.query_results import save_result,read_result
    with dataset.query_lock:
        _check_snapshot(dataset,request.snapshot)
        file=next((f for f in dataset.files if f.table_name==request.table),None)
        if file is None:raise ValueError('数据表不存在。')
        frame=pd.read_parquet(file.parquet_path)
        flags={earlier+'__'+later:hit for earlier,later,eligible,hit in _temporal_flags(frame)}
        if request.check_id=='any':hit=pd.DataFrame(flags,index=frame.index).any(axis=1)
        elif request.check_id in flags:hit=flags[request.check_id]
        else:raise ValueError('此版本没有对应的时间检查规则。')
        matched=frame.loc[hit].copy()
        if 'active_row' in matched:raise ValueError('原表已有active_row字段，不能覆盖原值生成行定位。')
        matched.insert(0,'active_row',[i+1 for i,bad in enumerate(hit) if bad])
        result_id=save_result(dataset.session_id,matched)
        return {**read_result(dataset.session_id,result_id,0),'result_id':result_id,
            'snapshot':request.snapshot,'data_version':version_of(file),'table':request.table,'check_id':request.check_id}


def diagnose_data(dataset):
    from app.execution import checkpoint
    from app.tools.dataset_manager import is_sensitive_column
    tables = []
    with dataset.query_lock:
        for file in dataset.files:
            checkpoint('diagnose_data')
            frame = pd.read_parquet(file.parquet_path)
            fields = []
            for column in frame:
                values = frame[column]
                text = values[values.map(lambda value: isinstance(value, str))].astype('string')
                sensitive = is_sensitive_column(column)
                candidates = text[text.str.strip().str.casefold().isin(['', 'na', 'null', 'none', 'n/a', 'nan', '未填写', '未知', '-'])]
                field = {'column': column, 'dtype': str(values.dtype), 'null_count': int(values.isna().sum()),
                               'null_percent': round(float(values.isna().mean() * 100), 2) if len(values) else 0,
                               'distinct_count': int(values.nunique()), 'trim_count': int(text.ne(text.str.strip()).sum()),
                               'null_candidates': [] if sensitive else [{'value': str(k), 'count': int(v)} for k, v in candidates.value_counts().head(10).items()],
                               'frequent_values': [] if sensitive else [{'value': str(k), 'count': int(v)} for k, v in values.value_counts().head(5).items()]}
                if not sensitive and len(text) and re.search(r'price|amount|cost|revenue|金额|单价|成本', column, re.I):
                    markers = text.str.strip().str.extract(r'^(R\$|US\$|USD|BRL|CNY|¥|￥|\$|€)',expand=False).dropna().unique().tolist()
                    field['currency_markers'] = markers
                    field['number_candidates'] = []
                    if len(markers) <= 1:
                        for decimal, thousands in [('.', ','), (',', '.')]:
                            rule = CleaningRule(operation='parse_number', column=column, decimal_separator=decimal,
                                                thousands_separator=thousands, currency_token=markers[0] if markers else '', failure_policy='null')
                            try:
                                parsed, failures = _parse_numbers(values, rule)
                            except ValueError:
                                continue
                            if parsed.notna().any():
                                field['number_candidates'].append({'decimal_separator':decimal,'thousands_separator':thousands,
                                    'currency_token':rule.currency_token,'success_count':int(parsed.notna().sum()),'failure_count':failures,
                                    'failure_examples':values[values.notna() & parsed.isna()].astype(str).head(5).tolist(),
                                    'scope':'按此明确候选格式的整列试算，不表示格式已由业务确认。'})
                fields.append(field)
            tables.append({'table': file.table_name, 'rows': len(frame), 'data_version':version_of(file), 'duplicate_rows': int(frame.duplicated().sum()),
                           'fields': fields,**temporal_report(frame), 'scope': '当前活动版本整表统计；重复和空值标记为候选问题，不代表应删除。'})
        return {'snapshot': workbench_snapshot(dataset), 'tables': tables}


def _parse_numbers(values, rule):
    if re.search(r'(^|_)(id|code|key|zip|postal)($|_)|编号|编码|邮编', rule.column, re.I):
        raise ValueError('标识或编码字段保留文本，不转换前导零。')
    decimal = re.escape(rule.decimal_separator)
    group = re.escape(rule.thousands_separator)
    integer = rf'(?:\d{{1,3}}(?:{group}\d{{3}})+|\d+)' if group else r'\d+'
    pattern = rf'[+-]?{integer}(?:{decimal}\d+)?'
    failures = 0
    def parse(value):
        nonlocal failures
        if pd.isna(value):
            return None
        text = str(value).strip()
        if rule.currency_token:
            if text.startswith(rule.currency_token):
                text = text[len(rule.currency_token):].strip()
            elif text.endswith(rule.currency_token):
                text = text[:-len(rule.currency_token)].strip()
        if re.match(r'[+-]?0\d', text):
            raise ValueError('发现前导零，请先确认这不是编码字段；当前未转换。')
        if re.fullmatch(pattern, text):
            normalized = text.replace(rule.thousands_separator, '') if rule.thousands_separator else text
            number = Decimal(normalized.replace(rule.decimal_separator, '.'))
            if len(number.as_tuple().digits) <= 38:
                return number
        failures += 1
        return None
    result = values.map(parse)
    if failures and rule.failure_policy == 'reject':
        raise ValueError(f'有 {failures} 条数值格式无法转换；请调整分隔符，或明确确认失败值转为空值后再预览。')
    return result, failures


def _transform(frame, rules):
    converted = conflicts = 0
    for rule in rules:
        names = rule.keys if rule.operation == 'deduplicate' else [rule.column]
        if any(name not in frame.columns for name in names):
            raise ValueError('所选字段不存在，请重新读取数据。')
        if rule.operation == 'deduplicate':
            distinct = frame.drop_duplicates()
            conflicts += int((distinct.groupby(rule.keys, dropna=False).size() > 1).sum())
            if conflicts and not rule.confirmed:
                raise ValueError('存在同键不同值的记录；请确认业务键与保留策略后再预览。')
            frame = frame.drop_duplicates(subset=rule.keys, keep=rule.keep)
        elif rule.operation == 'trim':
            frame[rule.column] = frame[rule.column].map(lambda value: value.strip() if isinstance(value, str) else value)
        elif rule.operation == 'null_tokens':
            frame[rule.column] = frame[rule.column].mask(frame[rule.column].isin(rule.tokens))
        elif rule.operation == 'map_values':
            frame[rule.column] = frame[rule.column].map(lambda value: rule.mapping.get(value, value) if isinstance(value, str) else value)
        elif rule.operation == 'parse_number':
            frame[rule.column], failures = _parse_numbers(frame[rule.column], rule)
            converted += failures
        elif rule.operation == 'fill':
            values = frame[rule.column]
            decimal = pd.api.types.infer_dtype(values, skipna=True) == 'decimal'
            if pd.api.types.is_numeric_dtype(values.dtype) or decimal:
                if isinstance(rule.value, str):
                    raise ValueError('数值字段只能填入明确数值，不能填入文本。')
                if pd.api.types.is_integer_dtype(values.dtype) and rule.value % 1:
                    raise ValueError('整数数值字段不能填入小数。')
            if pd.api.types.is_datetime64_any_dtype(values.dtype):
                raise ValueError('日期缺失请保留未知；日期标准化请使用日期格式转换。')
            frame[rule.column] = values.fillna(Decimal(str(rule.value)) if decimal else rule.value)
        elif rule.operation == 'parse_date':
            values = pd.to_datetime(frame[rule.column], format=rule.format, errors='coerce')
            converted += int((frame[rule.column].notna() & values.isna()).sum())
            frame[rule.column] = values
    return frame, converted, conflicts


def _records(frame):
    exact = frame.map(lambda value: str(value) if isinstance(value, Decimal) else value)
    return json.loads(exact.to_json(orient='records', date_format='iso'))


def _check_snapshot(dataset, expected):
    if workbench_snapshot(dataset) != expected:
        raise WorkbenchConflict('数据版本已变化，请重新预览后再应用。')


def preview_cleaning(dataset, request: CleaningRequest, *, analysis_state=None) -> dict:
    with dataset.query_lock:
        _check_snapshot(dataset, request.snapshot)
        file = next((file for file in dataset.files if file.table_name == request.table), None)
        if file is None:
            raise ValueError('数据表不存在。')
        before = pd.read_parquet(file.parquet_path)
        after, failures, conflicts = _transform(before.copy(), request.rules)
        aligned = after.reindex(before.index)
        same = (before.eq(aligned) | (before.isna() & aligned.isna())).fillna(False).all(axis=1)
        changed = before.index[~same | ~before.index.isin(after.index)]
        examples = [{'before': _records(before.loc[[index]])[0],
                     'after': _records(after.loc[[index]])[0] if index in after.index else None}
                    for index in changed[:50]]
        original_facts = commerce_facts(dataset)
        new_facts = commerce_facts(dataset, {request.table: after})
        type_changes = [{'column': name, 'before': pd.api.types.infer_dtype(before[name], skipna=True),
                         'after': pd.api.types.infer_dtype(after[name], skipna=True)} for name in before.columns]
        preview_id = uuid.uuid4().hex
        report = {'preview_id': preview_id, 'snapshot': request.snapshot, 'table': request.table,
                  'before_rows': len(before), 'after_rows': len(after), 'affected_rows': len(changed),
                  'conversion_failures': failures, 'conflicting_keys': conflicts, 'examples': examples,
                  'type_changes': [change for change in type_changes if change['before'] != change['after']],
                  'rules': request.model_dump()['rules'],
                  'before_null_counts': {k: int(v) for k, v in before.isna().sum().items()},
                  'after_null_counts': {k: int(v) for k, v in after.isna().sum().items()},
                  'metrics_before': original_facts, 'metrics_after': new_facts}
        from app.cleaning_impact import analysis_reference,compare_analysis
        reference=analysis_reference(dataset,request.table,analysis_state)
        report['analysis_impact']=compare_analysis(dataset,request.table,reference,frame=after)
        _write_json(_directory(dataset, 'previews') / (preview_id + '.json'),
                    {'request': request.model_dump(), 'created_at': time.time(), 'report': report,'analysis_reference':reference})
        return report


def apply_cleaning(dataset, preview_id: str, manager, *, plan_id=None, approval_text=None):
    if not re.fullmatch(r'[a-f0-9]{32}', preview_id):
        raise ValueError('无效的预览编号。')
    if (dataset.parquet_dir / '.workbench' / 'plans' / (preview_id+'.json')).exists() and plan_id != preview_id:
        raise WorkbenchConflict('清洗方案请通过当前聊天或方案卡片确认，不能从旧预览直接应用。')
    path = _directory(dataset, 'previews') / (preview_id + '.json')
    if path.is_symlink():
        raise ValueError('无效的预览文件。')
    saved = json.loads(path.read_text(encoding='utf-8'))
    request = CleaningRequest.model_validate(saved['request'])
    with dataset.query_lock:
        _check_snapshot(dataset, request.snapshot)
        if time.time() - saved['created_at'] > 1800:
            raise WorkbenchConflict('预览已过期，请重新预览。')
        file = next(file for file in dataset.files if file.table_name == request.table)
        frame, _, _ = _transform(pd.read_parquet(file.parquet_path), request.rules)
        candidate = store_dataframe(uuid.uuid4().hex + '.csv', frame, _directory(dataset, 'versions'),
                                    source={**file.source, 'original_filename': file.original_filename})
        candidate.table_name, candidate.original_filename = file.table_name, file.original_filename
        state = _state(dataset)
        history = state['tables'].setdefault(file.table_name, [
            {'path': file.parquet_path.relative_to(dataset.parquet_dir).as_posix(), 'version': version_of(file)}])
        history.append({'path': candidate.parquet_path.relative_to(dataset.parquet_dir).as_posix(),
                        'version': candidate.data_version, 'rules': request.model_dump()['rules'], 'plan_id': plan_id})
        if plan_id:
            state.setdefault('plan_receipts', {})[plan_id] = {'status': 'applied', 'before_snapshot': request.snapshot,
                'after_snapshot': workbench_snapshot(dataset, candidate), 'table': request.table,
                'rules': request.model_dump()['rules'], 'affected_rows': saved['report']['affected_rows']}
            if approval_text is not None:
                state['plan_receipts'][plan_id]['approval_text'] = approval_text
        try:
            from app.cleaning_impact import compare_analysis
            impact=compare_analysis(dataset,request.table,saved.get('analysis_reference'),replacement=candidate,
                previous=saved['report'].get('analysis_impact'))
            expected=(saved['report'].get('analysis_impact') or {}).get('after',{})
            if expected.get('status')=='computed' and impact.get('preview_agrees') is False:
                raise WorkbenchConflict('实际新版本的分析结果与预览不一致，未切换当前数据；请重新预览后确认。')
            impact.update(before_snapshot=request.snapshot,after_snapshot=workbench_snapshot(dataset,candidate))
            history[-1]['analysis_impact']=impact
            if plan_id:state['plan_receipts'][plan_id]['analysis_impact']=impact
            manager.replace_file(dataset.session_id, candidate, lambda: _write_state(dataset, state))
        except Exception:
            candidate.parquet_path.unlink(missing_ok=True)
            raise
        return describe_workbench(dataset)


def undo_cleaning(dataset, table: str, expected_snapshot: str, manager):
    with dataset.query_lock:
        _check_snapshot(dataset, expected_snapshot)
        state = _state(dataset)
        history = state['tables'].get(table, [])
        if len(history) < 2:
            raise ValueError('当前数据没有可撤销的清洗步骤。')
        removed = history.pop()
        candidate = read_version(_stored_path(dataset, history[-1]['path']), table)
        if removed.get('plan_id'):
            receipt = state['plan_receipts'][removed['plan_id']]
            receipt.update(status='undone', undo_snapshot=workbench_snapshot(dataset, candidate))
        manager.replace_file(dataset.session_id, candidate, lambda: _write_state(dataset, state))
        return describe_workbench(dataset)
