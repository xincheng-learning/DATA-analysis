"""Sequential full-Olist evaluation via real /ask; isolated sessions, no answer injection."""
import argparse
import asyncio
import hashlib
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
SUITE = ROOT / "evals/olist_business_v1"
RAW = ROOT.parent / "数据分析/data/raw"
CORE = "B01 B03 B05 B06 B08 B11 B12 B16 B21 B23 B26 B27 B31 B36 B38 B46 B51 B56 B57 B61".split()


def save(path, value):
    from evals.live_budget import write_json
    write_json(path, value)


def load_questions():
    text = (SUITE / "仅题目.md").read_text(encoding="utf-8-sig")
    common = text.split("## 共同约定", 1)[1].split("## B01", 1)[0].strip()
    cases = []
    for match in re.finditer(r"(?ms)^## (B\d{2}) ([^\n]+)\n\n(.*?)(?=^## B|\Z)", text):
        ident, title, question = match.groups()
        turns = re.split(r"第[一二三]轮(?:（[^）]*）)?：", question.strip())
        turns = [question.strip()] if len(turns) == 1 else [part.strip() for part in turns[1:]]
        cases.append({"id": ident, "title": title, "turns": turns})
    assert len(cases) == 64 and len({c['id'] for c in cases}) == 64
    assert all(len(c['turns']) == (3 if int(c['id'][1:]) >= 61 else 1) for c in cases)
    assert not any(word in common for word in ("reference_summary", "最低合格", "本题硬伤"))
    return common, cases


async def run(args):
    import httpx
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app import execution, llm, main, model_settings, session
    from app.config import settings
    from app.tools import dataset_manager
    from app.tools.file_storage import local_session_dir
    from app.tools.ingest import IngestedFile, IngestionError, _is_identifier, store_dataframe, validate_and_convert

    common, cases = load_questions()
    parts_dir = ROOT / 'output/olist-upload-parts-20261116'
    parts_manifest = None
    if args.sharded:
        parts_manifest = json.loads((parts_dir / 'manifest.json').read_text(encoding='utf-8'))
        mapping = '\n'.join(t['logical_table'] + ' = ' + ', '.join(Path(p['file']).stem for p in t['parts'])
                            for t in parts_manifest['tables'])
        common += '\n物理上传为同源分片，下面每组列结构相同，合计代表一张完整逻辑表。没有抽样或去重。查询时用 UNION ALL 合并该组全部分片，不能只用第一片：\n' + mapping
    selected = args.cases or CORE + [c['id'] for c in cases if c['id'] not in CORE]
    assert len(selected) == len(set(selected)) and set(selected) <= {c['id'] for c in cases}
    cases = sorted([c for c in cases if c['id'] in selected], key=lambda c: selected.index(c['id']))
    output = ROOT / 'evals/runs/olist-business-v1' / args.name
    output.mkdir(parents=True, exist_ok=True)
    config = model_settings.load_saved_settings()
    if not config:
        raise RuntimeError('No saved model configuration; original settings unchanged.')
    status = model_settings.get_model_status()
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (ROOT / 'backend/app').rglob('*.py')}
    meta = {'model': status, 'application_sha256': sources,
            'question_sha256': hashlib.sha256((SUITE / '仅题目.md').read_bytes()).hexdigest(),
            'scope': ('Real ASGI multipart uploads of 18 CSV parts then /ask, isolated SQLite sessions; no injection or raised upload limits. Browser untested.' if args.sharded else
                      'Real ASGI /ask, isolated SQLite sessions; seeded full-table analytical capability. Not browser or full-upload acceptance.'),
            'selected': selected, 'single_process_sequential': True}
    metadata_path = output / 'configuration.json'
    if metadata_path.exists():
        assert json.loads(metadata_path.read_text(encoding='utf-8')) == meta, 'Use a new run after code/config changes.'
    else:
        save(metadata_path, meta)
    original_dir = settings.local_data_dir
    settings.local_data_dir = output / 'app-data'
    settings.app_mode = 'local'
    settings.session_backend = 'sqlite'
    model_settings.load_saved_settings = lambda: config
    session._store = session.SessionStore(backend=session.SQLiteBackend(settings.local_data_dir / 'datapilot.sqlite3'))
    dataset_manager._manager = dataset_manager.DatasetManager()
    main.limiter.enabled = False
    files = {
        'orders': 'olist_orders_dataset.csv', 'order_items': 'olist_order_items_dataset.csv',
        'order_payments': 'olist_order_payments_dataset.csv', 'reviews': 'olist_order_reviews_dataset.csv',
        'customers': 'olist_customers_dataset.csv', 'products': 'olist_products_dataset.csv',
        'sellers': 'olist_sellers_dataset.csv', 'category_translation': 'product_category_name_translation.csv',
        'geolocation': 'olist_geolocation_dataset.csv',
    }
    if args.sharded:
        files = {}
    fixture_dir = output / 'fixtures'
    fixture_dir.mkdir(exist_ok=True)
    fixture_records = []
    for table, filename in files.items():
        path = RAW / filename
        stored = fixture_dir / f'{table}.parquet'
        if not stored.exists():
            with path.open('rb') as source:
                try:
                    ingested = validate_and_convert(f'{table}.csv', source, fixture_dir)
                    admission = {'accepted': True}
                except IngestionError as exc:
                    admission = {'accepted': False, 'error': str(exc)}
                    raw = pd.read_csv(path, dtype='string', keep_default_na=False, na_values=[''])
                    ids = {name: 'string' for name in raw if _is_identifier(name)
                           or raw[name].str.fullmatch(r'[+-]?(?:0\d+|\d{16,})').any()}
                    del raw
                    frame = pd.read_csv(path, dtype=ids, keep_default_na=False, na_values=[''])
                    if len(frame) <= 500_000:
                        store_dataframe(f'{table}.csv', frame, fixture_dir, source={'kind': 'evaluation_fixture', 'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                    else:
                        # shortcut: explicit oversized test fixture, never counted as successful product ingestion.
                        arrow = pa.Table.from_pandas(frame, preserve_index=False)
                        arrow = arrow.replace_schema_metadata({b'datapilot': json.dumps({'filename': f'{table}.csv',
                            'source': {'kind': 'evaluation_fixture'}, 'data_version': hashlib.sha256(path.read_bytes()).hexdigest()}).encode()})
                        pq.write_table(arrow, stored)
                    del frame
            save(fixture_dir / f'{table}.admission.json', admission)
        admission = json.loads((fixture_dir / f'{table}.admission.json').read_text(encoding='utf-8'))
        schema = pq.read_schema(stored)
        fixture_records.append({'table': table, 'rows': pq.ParquetFile(stored).metadata.num_rows,
            'columns': schema.names, 'dtypes': {f.name: str(f.type) for f in schema},
            'file': filename, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'admission': admission})
    assert len(fixture_records) == (0 if args.sharded else 9)
    reference = json.loads((SUITE / 'reference_summary.json').read_text(encoding='utf-8'))
    for record in fixture_records:
        assert record['sha256'] == reference['source_sha256'][record['file']]
    save(output / 'data-admission.json', fixture_records)
    print(json.dumps({'stage': 'ready', 'native_accepted': sum(r['admission']['accepted'] for r in fixture_records),
                      'full_tables': 9, 'model': config.model, 'original_data_dir_unchanged': str(original_dir)}, ensure_ascii=False), flush=True)

    # Record provider token usage without storing keys, request headers or hidden reasoning.
    class AuditTransport(httpx.BaseTransport):
        def __init__(self):
            self.upstream = httpx.HTTPTransport(retries=0)
            self.label = ''
            self.calls = 0

        def handle_request(self, request):
            self.calls += 1
            record = {'case_turn': self.label, 'ordinal': self.calls, 'started': time.time()}
            try:
                response = self.upstream.handle_request(request)
                response.read()
                record['http_status'] = response.status_code
                if response.status_code == 200:
                    body = response.json()
                    record.update(model=body.get('model'), usage=body.get('usage'))
                save(output / 'requests' / f'{self.label}-{self.calls:04d}.json', record)
                return response
            except Exception as exc:
                record['error_type'] = type(exc).__name__
                save(output / 'requests' / f'{self.label}-{self.calls:04d}.json', record)
                raise

        def close(self):
            self.upstream.close()

    transport = AuditTransport()
    manager = dataset_manager.get_dataset_manager()
    with httpx.Client(transport=transport, timeout=90, follow_redirects=False) as upstream:
        llm._chat_llm = llm.build_compatible_client(config.base_url, config.api_key.get_secret_value(), config.model,
            thinking_mode=config.thinking_mode, reasoning_effort=config.reasoning_effort, http_client=upstream)
        llm._chat_llm.disable_streaming = True
        llm._structured_llms.clear()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=('127.0.0.1', 12345)),
                                         base_url='http://localhost:8000', timeout=600) as client:
                for case in cases:
                    destination = output / 'cases' / f"{case['id']}.json"
                    if destination.exists():
                        continue  # Preserve first attempts, including failures.
                    sid = f"ob-{args.name}-{case['id'].lower()}"
                    session.get_session_store().get_or_create(sid, user_id='local-user')
                    directory = local_session_dir(sid)
                    directory.mkdir(parents=True, exist_ok=True)
                    manager.get_or_create(sid, directory)
                    if args.sharded:
                        upload_files = [parts_dir / p['file'] for t in parts_manifest['tables'] for p in t['parts']]
                        admission = []
                        for offset in range(0, len(upload_files), 5):
                            batch = upload_files[offset:offset + 5]
                            response = await client.post(f'/sessions/{sid}/upload',
                                files=[('files', (path.name, path.read_bytes(), 'text/csv')) for path in batch])
                            admission.append({'http_status': response.status_code, 'response': response.json()})
                            save(output / 'uploads' / f"{case['id']}.json", admission)
                            assert response.status_code == 200 and not response.json().get('errors'), 'Native upload failed; inspect admission record.'
                        data = manager.get(sid)
                        expected = {Path(p['file']).stem: p['rows'] for t in parts_manifest['tables'] for p in t['parts']}
                        assert {file.table_name: file.row_count for file in data.files} == expected
                        print(json.dumps({'case': case['id'], 'stage': 'native_upload_verified', 'parts': len(expected),
                                          'rows': sum(expected.values())}, ensure_ascii=False), flush=True)
                    for record in fixture_records:
                        path = directory / f"{record['table']}.parquet"
                        shutil.copyfile(fixture_dir / path.name, path)
                        manager.add_file(sid, IngestedFile(original_filename=f"{record['table']}.csv", table_name=record['table'],
                            parquet_path=path, columns=record['columns'], dtypes=record['dtypes'], row_count=record['rows'],
                            size_bytes=path.stat().st_size, source={'kind': 'evaluation_fixture'}, data_version=record['sha256']))
                    result = {'id': case['id'], 'title': case['title'], 'session_id': sid, 'turns': [], 'status': 'running'}
                    save(destination, result)
                    for index, question in enumerate(case['turns'], 1):
                        prompt = f'共同约定：\n{common}\n\n当前问题：{question}' if index == 1 else question
                        assert len(prompt) <= 2000
                        transport.label = f"{case['id']}-t{index}"
                        task = execution.AnalysisTask(owner='local-user', session_id=sid)
                        token = execution._current.set(task)
                        started = time.monotonic()
                        record = {'turn': index, 'question': prompt}
                        print(json.dumps({'case': case['id'], 'turn': index, 'stage': 'started'}, ensure_ascii=False), flush=True)
                        try:
                            response = await client.post('/ask', json={'session_id': sid, 'question': prompt})
                            record.update(http_status=response.status_code, final=response.json())
                        except Exception as exc:
                            record['error_type'] = type(exc).__name__
                        finally:
                            record.update(seconds=round(time.monotonic() - started, 2), trace=task.trace,
                                          model_calls=task.model_calls, usage=task.usage_records)
                            execution._current.reset(token)
                        result['turns'].append(record)
                        save(destination, result)
                        print(json.dumps({'case': case['id'], 'turn': index, 'seconds': record['seconds'],
                            'http_status': record.get('http_status'), 'route': record.get('final', {}).get('route'),
                            'calls': record['model_calls']}, ensure_ascii=False), flush=True)
                    result['status'] = 'recorded'
                    save(destination, result)
                    manager.close_session(sid)
        finally:
            main.tasks.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--name', required=True)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--sharded', action='store_true', help='Upload record-preserving CSV parts through the real HTTP upload endpoint.')
    arguments = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9-]{1,30}', arguments.name)
    if arguments.check:
        common, cases = load_questions()
        assert max(len(common) + len(c['turns'][0]) + 25 for c in cases) <= 2000
        print('64 cases / 72 turns parsed; rubric and answers excluded; input sizes valid')
    else:
        if not arguments.live:
            parser.error('Real model calls require --live.')
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(run(arguments))
