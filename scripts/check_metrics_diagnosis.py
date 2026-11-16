"""Three real-provider checks, frozen independent references, shared RMB20 ledger."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]


async def run(round_name, only=None):
    import logging

    import httpx
    import pandas as pd
    from evals.live_budget import BudgetStop, BudgetTransport, write_json

    from app import llm, main, model_settings, session
    from app.metrics import metric_context
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.dataset_manager import get_dataset_manager
    logging.getLogger('httpx').setLevel(logging.WARNING)

    if not round_name.replace('-', '').isalnum():
        raise ValueError('Invalid round name')
    output = ROOT / 'evals/runs/metrics-diagnosis' / round_name
    if output.exists():
        raise ValueError('Round already exists; do not overwrite first results')
    config = model_settings.load_saved_settings()
    if not config or config.base_url.rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'} or config.model != 'deepseek-flash':
        raise BudgetStop('Model is outside the existing verified budget configuration')
    output.mkdir(parents=True)
    demo = ROOT / '.local/ecommerce/demo'
    frames = {path.stem: pd.read_csv(path, dtype=str, keep_default_na=False) for path in sorted(demo.glob('*.csv'))}
    orders = frames['orders']
    delivered = orders[orders.order_status.eq('delivered')].copy()
    delivered['month'] = delivered.order_purchase_timestamp.str[:7]
    items = frames['order_items'].copy()
    for column in ['price', 'freight_value']:
        items[column] = items[column].map(Decimal)
    money = items.groupby('order_id', as_index=False)[['price', 'freight_value']].sum()
    facts = delivered.merge(money, on='order_id', how='left', validate='one_to_one').merge(
        frames['customers'][['customer_id', 'customer_state']], on='customer_id', validate='one_to_one')
    months = {}
    for month in ['2018-06', '2018-07']:
        selected = facts[facts.month.eq(month)]
        assert selected.price.notna().all()
        amount = sum(selected.price, Decimal(0))
        payment = frames['payments'][frames['payments'].order_id.isin(selected.order_id)].payment_value.map(Decimal).sum()
        months[month] = {'item_amount': str(amount), 'freight_amount': str(sum(selected.freight_value, Decimal(0))),
                         'payment_amount': str(payment), 'orders': len(selected), 'aov': str(amount / len(selected))}
    by_state = facts[facts.month.isin(months)].groupby(['month', 'customer_state']).price.sum()
    changes = {state: by_state.get(('2018-07', state), Decimal(0)) - by_state.get(('2018-06', state), Decimal(0))
               for state in facts.customer_state.unique()}
    reference = {'months': months, 'net_change': str(Decimal(months['2018-07']['item_amount']) - Decimal(months['2018-06']['item_amount'])),
                 'state_changes': {key: str(value) for key, value in sorted(changes.items(), key=lambda item: abs(item[1]), reverse=True)},
                 'scope': 'Public Olist customer-complete subset; delivered orders; BRL; source references not sent to model'}
    write_json(output / 'reference-before-live.json', reference)
    chats = {'diagnosis': 'metric-diagnosis-' + round_name, 'metrics': 'metric-definition-' + round_name}
    cases = [
        {'id': 'diagnosis', 'chat': 'diagnosis', 'question': '帮我看看2018年7月比6月的商品金额变化，先弄清订单量和客单价，再看主要是哪些州在变，配张图。'},
        {'id': 'metrics', 'chat': 'metrics', 'question': '2018年7月已送达订单的商品金额、运费和支付额分别是多少，客单商品金额也给我。'},
        {'id': 'net', 'chat': 'metrics', 'question': '那按月给我退款后的净额。'},
    ]
    if only:
        cases = [case for case in cases if case['id'] == only]
    write_json(output / 'cases-before-live.json', cases)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=('127.0.0.1', 12345)), base_url='http://localhost:8000')
    for label, sid in chats.items():
        if session.get_session_store()._backend.get(sid):
            raise ValueError('Test session already exists')
        paths = sorted(demo.glob('*.csv'))
        for offset in range(0, len(paths), 5):
            files = [('files', (path.name, path.read_bytes(), 'text/csv')) for path in paths[offset:offset + 5]]
            response = await client.post(f'/sessions/{sid}/upload', files=files)
            response.raise_for_status()
        dataset = get_dataset_manager().get(sid)
        response = await client.post(f'/sessions/{sid}/commerce/confirm', json={'confirmed': True, 'profile': 'olist_v1', 'currency': 'BRL', 'snapshot': workbench_snapshot(dataset)})
        response.raise_for_status()
        session.get_session_store()._backend.set_title(sid, '验收 · ' + ('新版经营诊断' if label == 'diagnosis' else '指标口径对照'))
    left, right = [metric_context(get_dataset_manager().get(sid)) for sid in chats.values()]
    assert left == right, 'Both chats must use the same reference definitions'
    budget = BudgetTransport(ROOT / 'evals/runs/gold-v1', max_output_tokens=16384)
    start_cost = budget.ledger['accounted_micro_cny']
    try:
        with httpx.Client(transport=budget, follow_redirects=False, timeout=90) as http:
            llm._chat_llm = llm.build_compatible_client(config.base_url, config.api_key.get_secret_value(), config.model,
                thinking_mode=config.thinking_mode, reasoning_effort=config.reasoning_effort, max_tokens=16384, http_client=http)
            llm._structured_llms.clear()
            for case in cases:
                budget.label = f'metrics-diagnosis/{round_name}/{case["id"]}'
                payload = {'question': case['question'], 'session_id': chats[case['chat']], 'mode': 'quick'}
                response = await client.post('/tasks', json=payload)
                response.raise_for_status()
                task_id = response.json()['task_id']
                while True:
                    task = (await client.get('/tasks/' + task_id)).json()
                    if task['status'] in {'succeeded', 'failed', 'cancelled'}:
                        break
                    await asyncio.sleep(.2)
                write_json(output / (case['id'] + '.json'), {'payload': payload, 'task': task})
                print(json.dumps({'case': case['id'], 'status': task['status'], 'model_calls': task['model_calls'],
                                  'route': (task.get('result') or {}).get('route'), 'total_cny': budget.ledger['accounted_micro_cny'] / 1e6}, ensure_ascii=False), flush=True)
                if budget.ledger['blocked']:
                    raise BudgetStop('Unsettled request; stopped without a follow-up request')
            write_json(output / 'run.json', {'sessions': chats, 'increment_cny': (budget.ledger['accounted_micro_cny'] - start_cost) / 1e6,
                'total_cny': budget.ledger['accounted_micro_cny'] / 1e6,
                'request_ids': [call['id'] for call in budget.ledger['calls'] if call['label'].startswith(f'metrics-diagnosis/{round_name}/')]})
    finally:
        await client.aclose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--round', required=True)
    parser.add_argument('--only', choices=['diagnosis', 'metrics', 'net'])
    args = parser.parse_args()
    asyncio.run(run(args.round, args.only))
