"""Sequential real-model Olist acceptance in an isolated store, with independent Decimal answers."""
import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]


async def run(directory, name, resume=False):
    import httpx
    from app import execution, llm, main, model_settings
    from app.config import settings
    from app.schemas import AskRequest
    from evals.live_budget import BudgetTransport, write_json

    directory=directory.resolve()
    assert directory.is_relative_to(ROOT/'evals/runs') and name.replace('-','').isalnum()
    output=ROOT/'evals/runs/closed-loop'/name
    assert not output.exists() or resume, 'Do not overwrite a paid run; use --resume to reuse saved turns'
    config=model_settings.load_saved_settings()
    assert config.model=='deepseek-flash' and config.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'}
    settings.local_data_dir=directory
    output.mkdir(parents=True,exist_ok=resume)
    sid='qa-flow-'+name
    demo=ROOT/'.local/ecommerce/demo'
    tables={path.stem:list(csv.DictReader(path.open(encoding='utf-8-sig',newline=''))) for path in demo.glob('*.csv')}
    delivered={row['order_id']:row for row in tables['orders'] if row['order_status']=='delivered'}
    customers={row['customer_id']:row for row in tables['customers']}
    sellers={row['seller_id']:row for row in tables['sellers']}
    flows=defaultdict(Decimal)
    monthly=defaultdict(Decimal)
    for item in tables['order_items']:
        order=delivered.get(item['order_id'])
        if order:
            flows[('卖家：'+sellers[item['seller_id']]['seller_state'],'买家：'+customers[order['customer_id']]['customer_state'])]+=Decimal(item['price'])
            monthly[order['order_purchase_timestamp'][:7]]+=Decimal(item['price'])
    reference={'flows':[{'seller_state':key[0],'buyer_state':key[1],'item_amount':str(value)} for key,value in sorted(flows.items())],
               'monthly':{key:str(value) for key,value in sorted(monthly.items())}}
    assert sum(flows.values())==sum(monthly.values())
    write_json(output/'reference.json',reference)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,client=('127.0.0.1',12345)),base_url='http://localhost:8000') as client:
        files=sorted(demo.glob('*.csv'))
        for offset in range(0,0 if resume else len(files),5):
            response=await client.post(f'/sessions/{sid}/upload',files=[('files',(path.name,path.read_bytes(),'text/csv')) for path in files[offset:offset+5]])
            assert response.status_code==200 and not response.json()['errors'], response.text
        main.get_session_store()._backend.set_title(sid,'深测闭环 · Olist沟通与流向')
        budget=BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=16384)
        initial=budget.ledger['accounted_micro_cny']
        http=None
        try:
            with httpx.Client(transport=budget,follow_redirects=False,timeout=90) as http:
                llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
                    thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=16384,http_client=http)
                llm._structured_llms.clear()

                def ask(question,label):
                    if (output/(label+'.json')).exists():
                        result=json.loads((output/(label+'.json')).read_text(encoding='utf-8'))
                        assert result['question']==question
                        return result
                    budget.label=f'closed-loop/{name}/{label}'
                    task=execution.AnalysisTask(owner='qa',session_id=sid)
                    token=execution._current.set(task)
                    try:
                        result=main._analyse(AskRequest(question=question,session_id=sid),'local-user').model_dump(mode='json')
                        write_json(output/(label+'.json'),result)
                        assert not result.get('error'), result.get('error')
                        print(json.dumps({'turn':label,'route':result['route'],'rows':result['row_count']},ensure_ascii=False),flush=True)
                        return result
                    finally:
                        execution._current.reset(token)

                overview=ask('请逐表告诉我导入的八张表各有多少行、各代表什么业务粒度，再说明实际缺失字段的分析影响以及关联时最重要的风险。不要漏掉任何一张表。','01-overview')
                assert len(overview['table_previews'])==8
                first=ask('想看每个月的净销售额趋势，帮我画折线图。你先问我需要确认的口径，不要自行猜测。','02-question')
                assert first.get('clarification'), first
                second=ask('净销售额在这里改为已送达订单的商品金额，不包含运费，不扣退款，按下单日期。币种这一步我还没有确认，请接着问清楚。','03-question')
                assert second.get('clarification'), second
                trend=ask('币种是BRL，使用当前数据的所有月份，保留最后一个月并说明只是当前样本的观察结果。','04-trend')
                assert trend.get('chart_spec') and not trend.get('clarification')
                from app.tools.dataset_manager import get_dataset_manager
                from app.tools.query_results import read_result
                def full(result):
                    if not result.get('result_id'):
                        assert len(result['rows'])==result['row_count']
                        return result['rows']
                    return [row for offset in range(0,result['row_count'],50) for row in read_result(sid,result['result_id'],offset)['rows']]
                from evals.ecommerce_scoring import match_rows
                assert len(trend['columns'])==2 and 'month' in trend['columns']
                amount=next(column for column in trend['columns'] if column!='month')
                assert match_rows([{'month':row['month'],'item_amount':row[amount]} for row in full(trend)],
                                  [{'month':k,'item_amount':v} for k,v in reference['monthly'].items()])
                flow=ask('请查询所有已送达订单，按卖家州和买家州汇总商品金额，不含运费，币种BRL；所有观察月份均保留。通过sellers和customers关联，卖家标签加“卖家：”前缀，买家加“买家：”前缀，同州也保留。只输出seller_state、buyer_state、item_amount三列，保留全部流向，先查询不要创建视图或绘图。','05-flows')
                measured={(row['seller_state'],row['buyer_state']):Decimal(str(row['item_amount'])) for row in full(flow)}
                assert set(measured)==set(flows)
                assert all(abs(measured[key]-value)<Decimal('.005') for key,value in flows.items())
                messages=main.get_session_store()._backend.get_messages(sid)
                write_json(output/'result.json',{'passed':True,'session_id':sid,'flow_message_id':messages[-1]['id'],
                    'flow_rows':len(flows),'flow_total':str(sum(flows.values())),'new_cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,
                    'total_cost_cny':budget.ledger['accounted_micro_cny']/1e6})
        finally:
            if http is None:
                budget.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--name',required=True)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    asyncio.run(run(args.directory,args.name,args.resume))
