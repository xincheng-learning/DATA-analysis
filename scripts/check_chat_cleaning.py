"""Small real-provider acceptance with public Olist data and the shared RMB20 ledger."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT)]


async def run(round_name):
    import httpx
    import pandas as pd
    from evals.live_budget import BudgetStop, BudgetTransport, write_json

    from app import execution, llm, main, model_settings, session
    from app.schemas import AskRequest
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.dataset_manager import get_dataset_manager

    if not round_name.replace('-','').isalnum():
        raise ValueError('Invalid round name')
    output = ROOT/'evals/runs/chat-cleaning'/round_name
    if output.exists():
        raise ValueError('Round exists; inspect its record before another paid run')
    config = model_settings.load_saved_settings()
    if not config or config.base_url.rstrip('/') not in {'https://api.deepseek.com','https://api.deepseek.com/v1'} or config.model != 'deepseek-flash':
        raise BudgetStop('Current model is outside the existing verified budget configuration')
    output.mkdir(parents=True)
    demo = ROOT/'.local/ecommerce/demo'
    items = pd.read_csv(demo/'order_items.csv', dtype=str).head(80)
    orders = pd.read_csv(demo/'orders.csv', dtype=str)
    customers = pd.read_csv(demo/'customers.csv', dtype=str)
    frame = items.merge(orders[['order_id','customer_id']],on='order_id',validate='many_to_one').merge(
        customers[['customer_id','customer_state']],on='customer_id',validate='many_to_one')
    frame = frame[['order_id','order_item_id','price','customer_state']].copy()
    frame['external_code'] = [f'{i:04d}' for i in range(80)]
    frame['channel'] = ['marketplace' if i<60 else 'Marketplace' for i in range(80)]
    expected_sum = sum(Decimal(value) for i,value in enumerate(frame.price) if i not in {1,3,5,7})
    frame['price'] = frame.price.map(lambda v:'R$ '+format(Decimal(v),',.2f'))
    frame.loc[[1,3,5],'price'] = None
    frame.loc[7,'price'] = '待核对'
    for i in range(0,80,2):
        frame.loc[i,'customer_state'] = ' '+frame.loc[i,'customer_state']+' '
    raw = frame.to_csv(index=False).encode('utf-8')
    sid = 'cleaning-live-'+round_name
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=('127.0.0.1',12345)),base_url='http://localhost:8000')
    response = await client.post(f'/sessions/{sid}/upload',files={'files':('ecommerce_cleaning.csv',raw,'text/csv')})
    assert response.status_code == 200, response.text
    store = session.get_session_store()._backend
    store.set_title(sid,'验收 · 聊天清洗 · 公开电商样本')
    dataset = get_dataset_manager().get(sid)
    original = dataset.files[0].parquet_path
    initial_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    before = workbench_snapshot(dataset)
    reference = {'rows':80,'missing_after_conversion':4,'known_price_subtotal':str(expected_sum),
                 'state_trim_count':40,'channel_mapping_count':60,'original_hash':initial_hash,'before_snapshot':before,'session_id':sid}
    write_json(output/'reference.json',reference)
    budget = BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=8192)
    initial_cost = budget.ledger['accounted_micro_cny']
    with httpx.Client(transport=budget,follow_redirects=False,timeout=90) as http:
        llm._chat_llm = llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
            thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=8192,http_client=http)
        llm._structured_llms.clear()
        def ask(question,turn):
            budget.label = f'chat-cleaning/{round_name}/t{turn}'
            task = execution.AnalysisTask(owner='cleaning-eval',session_id=sid)
            token = execution._current.set(task)
            try:
                result = main._analyse(AskRequest(question=question,session_id=sid),'local-user').model_dump(mode='json')
                write_json(output/f'turn-{turn}.json',result)
                return result
            finally:
                execution._current.reset(token)
        try:
            first = ask('这份电商订单明细看起来有些格式问题。先检查整表，告诉我最适合的清洗策略以及哪些东西应该保留，不要擅自清洗或把未知金额当成零。',1)
            assert first.get('cleaning') and not first.get('sql')
            assert workbench_snapshot(dataset) == before
            second = ask('我调整一下方案：这一轮仅去掉customer_state首尾空格，把channel中的marketplace统一为Marketplace，并将price文本转为数值（小数点.、千分位逗号、币种标记R$）。无法解析的待核对金额我同意转为空值；原本缺失的金额继续未知。保留所有订单明细、订单号和external_code前导零。给我可确认的处理预览，先不要应用。',2)
            plan_id = second['cleaning']['plan_id']
            assert workbench_snapshot(dataset) == before
            rules = second['cleaning']['preview']['rules']
            assert {r['operation'] for r in rules} == {'trim','map_values','parse_number'}, rules
            applied = await client.post(f'/sessions/{sid}/cleaning/plans/{plan_id}/apply',json={})
            assert applied.status_code == 200, applied.text
            saved = applied.json()
            assert saved['status'] == 'applied'
            again = (await client.post(f'/sessions/{sid}/cleaning/plans/{plan_id}/apply',json={})).json()
            assert again['current_snapshot'] == saved['current_snapshot']
            con = dataset.get_connection()
            row = con.execute('select count(*), count(*)-count(price), sum(price), count(*) filter(where channel=\'Marketplace\') from ecommerce_cleaning').fetchone()
            assert row == (80,4,expected_sum,80), row
            assert con.execute('select external_code from ecommerce_cleaning limit 1').fetchone()[0] == '0000'
            assert hashlib.sha256(original.read_bytes()).hexdigest() == initial_hash
            third = ask('请基于当前清洗后的数据，给出总明细行数、price未知行数和已知商品金额小计。这里是BRL，小计不能称作包含未知金额的完整收入。',3)
            assert third.get('sql') and third.get('rows'), third
            write_json(output/'result.json',{'status':'passed-awaiting-browser-undo','session_id':sid,'plan_id':plan_id,
                'measured':list(row),'request_ids':[c['id'] for c in budget.ledger['calls'] if c['label'].startswith(f'chat-cleaning/{round_name}/')],
                'new_cost_cny':(budget.ledger['accounted_micro_cny']-initial_cost)/1e6,'total_cost_cny':budget.ledger['accounted_micro_cny']/1e6,
                'reference':reference})
            print(json.dumps({'status':'passed-awaiting-browser-undo','session_id':sid,'plan_id':plan_id,
                'total_cost_cny':budget.ledger['accounted_micro_cny']/1e6},ensure_ascii=False))
        except Exception as exc:
            write_json(output/'failure.json',{'error_type':type(exc).__name__,'error':str(exc),'total_cost_cny':budget.ledger['accounted_micro_cny']/1e6})
            raise
    await client.aclose()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--round',default='v1')
    asyncio.run(run(parser.parse_args().round))
