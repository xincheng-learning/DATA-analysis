"""One recorded teacher turn against the real Agent, isolated from original chats."""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]


async def run(label,question):
    import httpx
    from app import execution,llm,main,model_settings,session,superset
    from app.config import settings
    from app.tools import dataset_manager
    from evals.live_budget import BudgetTransport,write_json

    assert re.fullmatch(r'[a-z0-9-]{1,64}',label)
    output=ROOT/'evals/runs/olist-teacher-20261111'
    record=output/(label+'.json')
    assert not record.exists(),'Preserve earlier attempts; use a new turn label.'
    config=model_settings.load_saved_settings()
    assert config and config.model=='deepseek-flash' and config.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'}
    original=settings.local_data_dir
    settings.local_data_dir=output/'app-data';settings.app_mode='local';settings.session_backend='sqlite'
    model_settings.load_saved_settings=lambda:config
    superset.runtime_dir=lambda:original/'superset'
    session._store=session.SessionStore(backend=session.SQLiteBackend(settings.local_data_dir/'datapilot.sqlite3'))
    dataset_manager._manager=dataset_manager.DatasetManager()
    main.limiter.enabled=False
    sid='olist-teacher-20261111'
    output.mkdir(parents=True,exist_ok=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,client=('127.0.0.1',12345)),base_url='http://localhost:8000',timeout=180) as client:
        if not session.get_session_store()._backend.get(sid):
            files=sorted((ROOT/'.local/ecommerce/demo').glob('*.csv'))
            for index in range(0,len(files),5):
                uploaded=await client.post('/sessions/'+sid+'/upload',files=[('files',(p.name,p.read_bytes(),'text/csv')) for p in files[index:index+5]])
                assert uploaded.status_code==200 and not uploaded.json()['errors'],uploaded.text
            session.get_session_store()._backend.set_title(sid,'教学验收｜Olist完整商业分析')
        budget=BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=16384)
        initial=budget.ledger['accounted_micro_cny'];first=len(budget.ledger['calls']);budget.label='olist-teacher/'+label
        try:
            with httpx.Client(transport=budget,timeout=90,follow_redirects=False) as upstream:
                llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
                    thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=16384,http_client=upstream)
                llm._structured_llms.clear()
                task=execution.AnalysisTask(owner='local-user',session_id=sid);token=execution._current.set(task)
                try:
                    response=await client.post('/ask',json={'session_id':sid,'question':question})
                    result=response.json()
                    write_json(record,{'question':question,'http_status':response.status_code,'final':result,
                        'model_calls':task.model_calls,'request_ids':[c['id'] for c in budget.ledger['calls'][first:]],
                        'cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,
                        'shared_total_cny':budget.ledger['accounted_micro_cny']/1e6,'blocked':budget.ledger['blocked']})
                    print(json.dumps({'turn':label,'http_status':response.status_code,'route':result.get('route'),
                        'error':result.get('error'),'rows':result.get('row_count'),'has_chart':bool(result.get('chart_spec')),
                        'cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,'shared_total_cny':budget.ledger['accounted_micro_cny']/1e6},ensure_ascii=False),flush=True)
                    assert response.status_code==200,response.text
                    assert not budget.ledger['blocked'],'Unknown fees: stop further paid calls.'
                finally:
                    execution._current.reset(token)
        finally:
            main.tasks.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true');parser.add_argument('--label',required=True);parser.add_argument('--question-file',required=True,type=Path)
    args=parser.parse_args()
    if not args.live:parser.error('Use --live; charges share the original cumulative RMB20 ceiling.')
    asyncio.run(run(args.label,args.question_file.read_text(encoding='utf-8-sig')))
