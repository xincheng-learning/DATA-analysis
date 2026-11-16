"""Sequential real /ask workflow evaluation; no answers or authored plans injected."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
PACK = ROOT / 'evals/analyst_workflow_gold_v1'
from evals.live_budget import write_json as save


def prompt_for(case, index):
    message = case['turns'][index]['message']
    if index == 0:
        context = case['public_input']
        message = context['data_dictionary'] + '\n\n业务背景：' + context['briefing'] + '\n\n当前问题：' + message
    assert 0 < len(message) <= 2000
    return message


def evidence_leaves(value, path='root'):
    """Keep exact paths; artifacts from different steps never substitute each other."""
    yield path, value
    for flow in ('workflow', 'investigation'):
        for index, step in enumerate((value.get(flow) or {}).get('steps', [])):
            yield from evidence_leaves(step.get('result', step), f'{path}-{flow}-{index}')


async def run(args):
    import httpx
    from app import execution, llm, main, model_settings, session
    from app.config import settings
    from app.tools import dataset_manager

    cases = json.loads((PACK / 'cases.json').read_text(encoding='utf-8'))['cases']
    selected = args.cases or [c['id'] for c in cases]
    assert len(selected) == len(set(selected)) and set(selected) <= {c['id'] for c in cases}
    config = model_settings.load_saved_settings()
    if not config:
        raise RuntimeError('No saved model configuration; no settings changed.')
    status = model_settings.get_model_status()
    output = ROOT / 'evals/runs/analyst-workflow-v1' / args.name
    metadata = {'model': status, 'pack_sha256': hashlib.sha256((PACK / 'manifest.json').read_bytes()).hexdigest(),
        'sources': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [*sorted((ROOT / 'backend/app').rglob('*.py')), Path(__file__).resolve()]},
        'scope': 'Real ASGI upload and /ask; saved provider configuration, no auto retries; browser and rendered appearance not certified.',
        'base_client_max_output_tokens': 16384,
        'application_token_policy': 'Per-stage output limits in backend/app/execution.py; source hash captured above.',
        'sequential': True, 'answers_provided_to_agent': False}
    metadata_path = output / 'configuration.json'
    if metadata_path.exists():
        assert json.loads(metadata_path.read_text(encoding='utf-8')) == metadata, 'Code or model changed: use a new run name.'
    else:
        save(metadata_path, metadata)
    settings.local_data_dir = output / 'app-data'
    settings.app_mode = 'local'
    settings.session_backend = 'sqlite'
    model_settings.load_saved_settings = lambda: config
    session._store = session.SessionStore(backend=session.SQLiteBackend(settings.local_data_dir / 'datapilot.sqlite3'))
    dataset_manager._manager = dataset_manager.DatasetManager()
    main.limiter.enabled = False

    class AuditTransport(httpx.BaseTransport):
        def __init__(self):
            self.upstream = httpx.HTTPTransport(retries=0)
            self.label = ''
            self.calls = 0
            self.connection_failed = False

        def handle_request(self, request):
            self.calls += 1
            record = {'turn': self.label, 'ordinal': self.calls, 'started': time.time()}
            try:
                response = self.upstream.handle_request(request)
                response.read()
                record['http_status'] = response.status_code
                if response.status_code == 200:
                    body = response.json()
                    record.update(model=body.get('model'), usage=body.get('usage'))
                else:
                    self.connection_failed = True
                return response
            except httpx.TransportError as exc:
                record['error_type'] = type(exc).__name__
                self.connection_failed = True
                raise
            finally:
                save(output / 'requests' / f'{self.label}-{self.calls:04}.json', record)

        def close(self):
            self.upstream.close()

    transport = AuditTransport()
    with httpx.Client(transport=transport, timeout=90, follow_redirects=False) as upstream:
        llm._chat_llm = llm.build_compatible_client(config.base_url, config.api_key.get_secret_value(), config.model,
            thinking_mode=config.thinking_mode, reasoning_effort=config.reasoning_effort, http_client=upstream)
        llm._chat_llm.disable_streaming = True
        llm._structured_llms.clear()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=('127.0.0.1', 12345)), base_url='http://localhost:8000', timeout=600) as client:
                for case in [c for c in cases if c['id'] in selected]:
                    destination = output / 'cases' / f"{case['id']}.json"
                    if destination.exists():
                        print(json.dumps({'case': case['id'], 'stage': 'first_attempt_preserved'}), flush=True)
                        continue
                    sid = f"awg-{args.name}-{case['id'].lower()}"
                    result = {'id': case['id'], 'session_id': sid, 'title': case['title'], 'status': 'running', 'turns': []}
                    save(destination, result)
                    files = sorted((PACK / 'data' / case['profile']).glob('*.csv'))
                    response = await client.post(f'/sessions/{sid}/upload', files=[('files', (p.name, p.read_bytes(), 'text/csv')) for p in files])
                    save(output / 'uploads' / f"{case['id']}.json", {'http_status': response.status_code, 'response': response.json()})
                    if response.status_code != 200 or response.json().get('errors'):
                        result['status'] = 'upload_failed'
                        save(destination, result)
                        continue
                    for index, turn in enumerate(case['turns']):
                        prompt = prompt_for(case, index)
                        transport.label = f"{case['id']}-{turn['id']}"
                        task = execution.AnalysisTask(owner='local-user', session_id=sid)
                        token = execution._current.set(task)
                        record = {'turn': turn['id'], 'question': prompt}
                        print(json.dumps({'case': case['id'], 'turn': turn['id'], 'stage': 'started'}), flush=True)
                        started = time.monotonic()
                        try:
                            response = await client.post('/ask', json={'session_id': sid, 'question': prompt})
                            record.update(http_status=response.status_code, final=response.json())
                        except Exception as exc:
                            record['error_type'] = type(exc).__name__
                        finally:
                            record.update(seconds=round(time.monotonic()-started, 2), trace=task.trace, model_calls=task.model_calls, usage=task.usage_records)
                            execution._current.reset(token)
                        result['turns'].append(record)
                        save(destination, result)
                        for path, leaf in evidence_leaves(record.get('final', {})):
                            if leaf.get('chart_spec'):
                                save(output / 'charts' / f"{case['id']}-{turn['id']}-{path}.json", leaf['chart_spec'])
                            if leaf.get('result_id'):
                                download = await client.get(f"/sessions/{sid}/results/{leaf['result_id']}/export")
                                if download.status_code == 200:
                                    export = output / 'exports' / f"{case['id']}-{turn['id']}-{path}.csv"
                                    export.parent.mkdir(parents=True, exist_ok=True)
                                    export.write_bytes(download.content)
                                save(output / 'exports' / f"{case['id']}-{turn['id']}-{path}.json", {'http_status': download.status_code, 'result_id': leaf['result_id']})
                        history = await client.get(f'/sessions/{sid}')
                        save(output / 'history' / f"{case['id']}-{turn['id']}.json", history.json())
                        print(json.dumps({'case': case['id'], 'turn': turn['id'], 'seconds': record['seconds'], 'http_status': record.get('http_status'), 'route': record.get('final', {}).get('route'), 'calls': record['model_calls']}, ensure_ascii=False), flush=True)
                        if transport.connection_failed:
                            result['status'] = 'provider_stopped'
                            save(destination, result)
                            return
                    result['status'] = 'recorded_not_graded'
                    save(destination, result)
                    dataset_manager._manager.close_session(sid)
        finally:
            main.tasks.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--name', required=True)
    parser.add_argument('--cases', nargs='+')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,25}', args.name):
        parser.error('Use a short lowercase run name.')
    if args.check:
        cases = json.loads((PACK / 'cases.json').read_text(encoding='utf-8'))['cases']
        assert len(cases) == 8
        for case in cases:
            for index in range(len(case['turns'])):
                prompt_for(case, index)
        print('8 scenarios / 20 messages; public inputs only; no live model calls.')
    elif not args.live:
        parser.error('Real provider calls require --live.')
    else:
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(run(args))
