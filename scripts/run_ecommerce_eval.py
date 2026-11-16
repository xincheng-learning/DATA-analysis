"""Actual provider calls through the application's request handler, with the existing shared budget."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'),str(ROOT)]

from evals.live_budget import BudgetTransport, BudgetStop, write_json
from evals.ecommerce_reference import build_reference
from evals.ecommerce_scoring import score_case


def run(name, split, selected=None, max_output_tokens=8192):
    import httpx
    import pandas as pd
    from app import execution, llm, model_settings, session, main
    from app.config import settings
    from app.schemas import AskRequest
    from app.tools import dataset_manager
    from app.tools.ingest import validate_and_convert
    from app.tools.data_workbench import workbench_snapshot, _state, _write_state
    from app.tools.relationships import relationship_candidates, RelationshipRequest, confirm_relationship

    if not name or any(character not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for character in name):
        raise ValueError('轮次名只接受小写字母、数字、减号和下划线。')
    config = model_settings.load_saved_settings()
    status = model_settings.get_model_status()
    if not config or config.base_url.rstrip('/') not in {'https://api.deepseek.com','https://api.deepseek.com/v1'} or config.model != 'deepseek-flash':
        raise BudgetStop('当前模型不在已核价范围，没有更改模型设置或发出请求。')
    directory = ROOT/'evals/runs/ecommerce-v1'/name
    directory.mkdir(parents=True,exist_ok=True)
    case_file = ROOT/'evals/ecommerce_v1'/('expansion.json' if split == 'expansion' else 'cases.json')
    cases = [case for case in json.loads(case_file.read_text(encoding='utf-8'))['cases'] if case['split'] == split]
    if selected:
        if not set(selected) <= {case['id'] for case in cases}:
            raise ValueError('题目必须属于选定分组。')
        cases = [case for case in cases if case['id'] in selected]
    demo = ROOT/'.local/ecommerce/demo'
    manifest = json.loads((demo/'manifest.json').read_text(encoding='utf-8'))
    for table,item in manifest['tables'].items():
        if hashlib.sha256((demo/(table+'.csv')).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('公开数据哈希已变化，未发出请求。')
    tables = {path.stem:pd.read_csv(path,dtype=str,keep_default_na=False) for path in demo.glob('*.csv')}
    reference = build_reference(tables)
    sources = {path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
               for path in [*sorted((ROOT/'backend/app').rglob('*.py')),Path(__file__),ROOT/'evals/ecommerce_scoring.py',ROOT/'evals/ecommerce_reference.py',case_file]}
    snapshot = {'model':status,'max_output_tokens':max_output_tokens,'sources':sources,'manifest_sha256':hashlib.sha256((demo/'manifest.json').read_bytes()).hexdigest(),
                'price_verification':{'date':'2026-10-30','url':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
                                      'upper_input_cny_per_million':2,'upper_output_cny_per_million':8},
                'scope':'真实模型API + 原应用_analyse/Graph/调查/文件导入；HTTP接收层与浏览器另外验收；Docker部署暂缓'}
    snapshot_file = directory/'configuration.json'
    if snapshot_file.exists() and json.loads(snapshot_file.read_text(encoding='utf-8')) != snapshot:
        raise ValueError('源码或配置已变化，请另建轮次，不能覆盖首次结果。')
    write_json(snapshot_file,snapshot)
    for relative,fingerprint in sources.items():
        original = ROOT/relative
        if hashlib.sha256(original.read_bytes()).hexdigest() != fingerprint:
            raise ValueError('快照期间源码发生变化，未发出请求。')
        saved = directory/'source'/relative
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,saved)
    write_json(directory/'reference.json',reference)
    app_dir = directory/'app-data'
    settings.app_mode = 'local'
    settings.docker_wsl_distro = ''  # Never boot a WSL distribution during this memory-constrained local evaluation.
    settings.local_data_dir = app_dir.resolve()
    model_settings.load_saved_settings = lambda: config
    session._store = session.SessionStore(backend=session.SQLiteBackend(app_dir/'datapilot.sqlite3'))
    dataset_manager._manager = dataset_manager.DatasetManager()
    results = []
    budget = BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=max_output_tokens)
    initial_cost = budget.ledger['accounted_micro_cny']
    with httpx.Client(transport=budget,follow_redirects=False,timeout=30) as http:
        llm._chat_llm = llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
             thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=max_output_tokens,http_client=http)
        llm._structured_llms.clear()
        for case in cases:
            result_path = directory/'cases'/(case['id']+'.json')
            if result_path.exists():
                results.append(json.loads(result_path.read_text(encoding='utf-8')))
                continue
            sid = 'live-'+name+'-'+case['id'].lower()
            if len(sid)>64:
                raise ValueError('轮次名称过长。')
            manager = dataset_manager.get_dataset_manager()
            data = manager.get_or_create(sid,app_dir/'uploads'/sid)
            try:
                if not data.files:
                    for path in sorted(demo.glob('*.csv')):
                        with path.open('rb') as stream:
                            manager.add_file(sid,validate_and_convert(path.name,stream,data.parquet_dir,data.table_names))
                version = workbench_snapshot(data)
                for candidate in relationship_candidates(data):
                    if candidate['source'].startswith('Olist'):
                        candidate.pop('source')
                        confirm_relationship(data,RelationshipRequest(**candidate,snapshot=version,confirmed=True))
                state = _state(data)
                state['commerce_snapshot'] = version
                _write_state(data,state)
                session.get_session_store().get_or_create(sid,user_id='local-user')
                session.get_session_store()._backend.set_title(sid,f'真实 API · L{case["level"]} · {case["id"]}')
                turn_records = []
                if list((directory/'turns').glob(case['id']+'-t*.json')):
                    raise ValueError('存在未完成多轮记录；请审阅后另建轮次，禁止隐式重复付费。')
                for turn_index,turn in enumerate(case.get('turns',[case]),1):
                    turn_case = {**case,**turn}
                    budget.label = f'ecommerce/{name}/{case["id"]}/t{turn_index}'
                    task = execution.AnalysisTask(owner='ecommerce-eval',session_id=sid)
                    token = execution._current.set(task)
                    first_request = len(budget.ledger['calls'])
                    started = time.monotonic()
                    record = {'id':case['id'],'level':case['level'],'split':split,'question':turn_case['question'],
                              'session_id':sid,'utc':datetime.now(timezone.utc).isoformat()}
                    try:
                        response = main._analyse(AskRequest(question=turn_case['question'],session_id=sid,mode=case['mode']),'local-user')
                        record.update(status='ran',final=response.model_dump(mode='json'))
                    except Exception as exc:
                        record.update(status='budget_stop' if isinstance(exc,BudgetStop) else 'error',error_type=type(exc).__name__)
                    finally:
                        record.update(seconds=round(time.monotonic()-started,3),model_calls=task.model_calls,trace=task.trace,
                                      request_ids=[call['id'] for call in budget.ledger['calls'][first_request:]],
                                      upper_cost_cny=sum(call['charged_micro_cny'] for call in budget.ledger['calls'][first_request:])/1e6)
                        execution._current.reset(token)
                    record['score'] = score_case(turn_case,record,reference)
                    turn_records.append(record)
                    write_json(directory/'turns'/f'{case["id"]}-t{turn_index}.json',record)
                    if budget.ledger['blocked'] or record['status'] == 'budget_stop':
                        break
                record = {**record,'turns':turn_records,'model_calls':sum(r['model_calls'] for r in turn_records),
                          'request_ids':[i for r in turn_records for i in r['request_ids']],
                          'upper_cost_cny':sum(r['upper_cost_cny'] for r in turn_records)}
                if len(turn_records)>1:
                    checks = {f'turn_{i+1}':r['score']['passed'] for i,r in enumerate(turn_records)}
                    record['score'] = {'passed':all(checks.values()),'checks':checks,'failed':[k for k,v in checks.items() if not v]}
                write_json(result_path,record)
                results.append(record)
                print(json.dumps({key:record[key] for key in ['id','level','status','model_calls','request_ids','upper_cost_cny','score']},ensure_ascii=False),flush=True)
                write_json(directory/'summary.json',{'results':[{'id':r['id'],'score':r['score'],'requests':r['request_ids'],'cost_cny':r['upper_cost_cny']} for r in results],
                    'run_upper_cost_cny':(budget.ledger['accounted_micro_cny']-initial_cost)/1e6,
                    'shared_total_upper_cny':budget.ledger['accounted_micro_cny']/1e6,'blocked':budget.ledger['blocked']})
                if budget.ledger['blocked'] or record['status'] == 'budget_stop':
                    raise BudgetStop('已保存结果，费用状态不确定或预算不足，停止后续请求。')
            finally:
                manager.close_session(sid)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--name',required=True)
    parser.add_argument('--split',choices=['development','holdout','expansion'],required=True)
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--max-output-tokens',type=int,default=8192,choices=range(1,16385),metavar='1..16384')
    args = parser.parse_args()
    if not args.live:
        parser.error('必须显式 --live；真实请求计入已有20元累计预算。')
    try:
        run(args.name,args.split,args.cases,args.max_output_tokens)
    except BudgetStop as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(2) from None
