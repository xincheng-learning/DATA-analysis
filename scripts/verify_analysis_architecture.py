"""One budgeted real-model regression turn; sequential, isolated, immutable attempts."""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]


async def run(label,attempt):
    import httpx
    from app import execution,llm,main,model_settings,session,superset
    from app.config import settings
    from app.tools import dataset_manager
    from evals.live_budget import BudgetTransport,write_json
    scenarios=json.loads((ROOT/'evals/analyst_gold_v3/calibration-scenarios.json').read_text(encoding='utf-8'))
    case=next(c for c in scenarios['cases'] if c['label']==label)
    output=ROOT/'output/gold-architecture-2026-11-11';output.mkdir(parents=True,exist_ok=True)
    record=output/f'{label}-{attempt}.json';assert not record.exists(),'Keep the original attempt; choose a new attempt label.'
    config=model_settings.load_saved_settings()
    assert config and config.model=='deepseek-flash' and config.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'}
    original=settings.local_data_dir
    storage=ROOT/'evals/runs/architecture-gold-20261111/app-data'
    settings.local_data_dir=storage;settings.app_mode='local';settings.session_backend='sqlite'
    model_settings.load_saved_settings=lambda:config
    superset.runtime_dir=lambda:original/'superset'
    session._store=session.SessionStore(backend=session.SQLiteBackend(storage/'datapilot.sqlite3'))
    dataset_manager._manager=dataset_manager.DatasetManager();main.limiter.enabled=False
    sid='architecture-'+case['dataset']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,client=('127.0.0.1',12345)),base_url='http://localhost:8000',timeout=180) as client:
        if not session.get_session_store()._backend.get(sid):
            if case['dataset']=='olist':
                files=sorted((ROOT/'.local/ecommerce/demo').glob('*.csv'))
                for index in range(0,len(files),5):
                    uploaded=await client.post('/sessions/'+sid+'/upload',files=[('files',(p.name,p.read_bytes(),'text/csv')) for p in files[index:index+5]])
                    assert uploaded.status_code==200 and not uploaded.json()['errors'],uploaded.text
            else:
                data=b'month,order_id,amount_brl,status\n2024-02,f1,40,delivered\n2024-02,f2,40,delivered\n2024-02,f3,40,delivered\n2024-03,m1,30,delivered\n2024-03,m2,30,delivered\n2024-03,m3,30,delivered\n2024-03,m4,30,delivered\n2024-03,m5,30,delivered\n2024-03,x1,9999,canceled\n'
                uploaded=await client.post('/sessions/'+sid+'/upload',files={'files':('sales.csv',data,'text/csv')})
                assert uploaded.status_code==200,uploaded.text
            session.get_session_store()._backend.set_title(sid,'新架构验收｜'+('Olist电商分析' if case['dataset']=='olist' else '合成新数值回归'))
        budget=BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=8192)
        initial=budget.ledger['accounted_micro_cny'];first=len(budget.ledger['calls']);budget.label=f'architecture-v3/{label}/{attempt}'
        try:
            with httpx.Client(transport=budget,timeout=90,follow_redirects=False) as upstream:
                # Real model semantics use non-stream requests so every response has a durable billable usage record.
                llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
                    thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=8192,http_client=upstream,disable_streaming=True)
                llm._structured_llms.clear()
                task=execution.AnalysisTask('local-user',sid);token=execution._current.set(task)
                try:
                    response=await client.post('/ask',json={'session_id':sid,'question':case['question']})
                    final=response.json()
                    summary={'label':label,'attempt':attempt,'http_status':response.status_code,'route':final.get('route'),
                        'error':final.get('error'),'rows':final.get('row_count'),'has_chart':bool(final.get('chart_spec')),
                        'process':final.get('analysis_process'),'model_calls':task.model_calls,
                        'cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,'cumulative_cny':budget.ledger['accounted_micro_cny']/1e6,
                        'blocked':budget.ledger['blocked'],'request_ids':[c['id'] for c in budget.ledger['calls'][first:]]}
                    write_json(record,{'case':case,'summary':summary,'final':final})
                    print(json.dumps({k:v for k,v in summary.items() if k!='process'},ensure_ascii=False),flush=True)
                    assert response.status_code==200,response.text
                    assert not budget.ledger['blocked'],'Unknown charge: do not send another request.'
                finally:execution._current.reset(token)
        finally:main.tasks.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    parser.add_argument('--label',required=True);parser.add_argument('--attempt',default='first')
    args=parser.parse_args()
    if not args.live:parser.error('Use --live; shared accumulated maximum is RMB30, never reset.')
    assert re.fullmatch(r'[a-z0-9-]{1,40}',args.attempt)
    asyncio.run(run(args.label,args.attempt))
