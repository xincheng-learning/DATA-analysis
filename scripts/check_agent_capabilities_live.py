"""Sequential real-model capability checks with independent CSV/Decimal answers."""
import argparse
import asyncio
import csv
import hashlib
import json
import re
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]


async def run(name,selected=None):
    import httpx
    from app import execution,llm,main,model_settings,session,superset
    from app.config import settings
    from app.tools import dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.query_results import read_result
    from evals.live_budget import BudgetTransport,BudgetStop,write_json
    from evals.ecommerce_scoring import match_rows

    assert re.fullmatch(r'[a-z0-9-]{1,32}',name)
    output=ROOT/'evals/runs/capabilities'/name
    assert not output.exists(),'Use a new name; paid results must not be overwritten.'
    config=model_settings.load_saved_settings()
    assert config and config.model=='deepseek-flash' and config.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'}
    original=settings.local_data_dir
    settings.app_mode='local';settings.session_backend='sqlite';settings.local_data_dir=output/'app-data'
    model_settings.load_saved_settings=lambda:config
    superset.runtime_dir=lambda:original/'superset'
    session._store=session.SessionStore(backend=session.SQLiteBackend(settings.local_data_dir/'datapilot.sqlite3'))
    dataset_manager._manager=dataset_manager.DatasetManager()
    main.limiter.enabled=False  # Isolated acceptance traffic; rate-limit behavior has separate HTTP tests.
    output.mkdir(parents=True,exist_ok=True)
    sources={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT/'backend/app').rglob('*.py')}
    demo=ROOT/'.local/ecommerce/demo'
    tables={p.stem:list(csv.DictReader(p.open(encoding='utf-8-sig',newline=''))) for p in demo.glob('*.csv')}
    delivered={r['order_id']:r for r in tables['orders'] if r['order_status']=='delivered'}
    customers={r['customer_id']:r for r in tables['customers']};sellers={r['seller_id']:r for r in tables['sellers']}
    monthly=defaultdict(Decimal);flows=defaultdict(Decimal)
    all_amount=sum((Decimal(r['price']) for r in tables['order_items']),Decimal(0))
    for item in tables['order_items']:
        order=delivered.get(item['order_id'])
        if order:
            monthly[order['order_purchase_timestamp'][:7]]+=Decimal(item['price'])
            flows[('卖家：'+sellers[item['seller_id']]['seller_state'],'买家：'+customers[order['customer_id']]['customer_state'])]+=Decimal(item['price'])
    write_json(output/'reference.json',{'monthly':monthly,'flows':[{ 'seller_state':a,'buyer_state':b,'item_amount':v} for (a,b),v in flows.items()],
        'all_item_amount':all_amount,'sources':sources,'scope':'Original CSV, Python dictionaries and Decimal; no Agent SQL used for these answers.'})
    results=[];sid='cap-'+name
    budget=BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=16384)
    initial=budget.ledger['accounted_micro_cny'];first=len(budget.ledger['calls'])

    def summary():
        write_json(output/'summary.json',{'results':results,'new_cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,
            'shared_total_cny':budget.ledger['accounted_micro_cny']/1e6,'blocked':budget.ledger['blocked'],
            'request_ids':[c['id'] for c in budget.ledger['calls'][first:]]})

    def full(result,session_id=sid):
        if result.get('result_id'):
            return [r for offset in range(0,result['row_count'],50) for r in read_result(session_id,result['result_id'],offset)['rows']]
        return result.get('rows',[])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,client=('127.0.0.1',12345)),base_url='http://localhost:8000',timeout=180) as client:
        async def upload(session_id,files):
            response=await client.post(f'/sessions/{session_id}/upload',files=[('files',(title,content,mime)) for title,content,mime in files])
            assert response.status_code==200 and not response.json()['errors'],response.text
            return response.json()

        async def turn(label,question,session_id=sid,**extra):
            budget.label=f'capabilities/{name}/{label}';start=len(budget.ledger['calls'])
            task=execution.AnalysisTask(owner='local-user',session_id=session_id);token=execution._current.set(task)
            try:
                response=await client.post('/ask',json={'question':question,'session_id':session_id,**extra})
                result=response.json()
                write_json(output/(label+'.json'),{'question':question,'http_status':response.status_code,'final':result,
                    'calls':task.model_calls,'request_ids':[c['id'] for c in budget.ledger['calls'][start:]]})
                print(json.dumps({'turn':label,'http_status':response.status_code,'route':result.get('route'),'calls':task.model_calls},ensure_ascii=False),flush=True)
                if budget.ledger['blocked']:raise BudgetStop('Uncertain fee; further paid calls stopped.')
                assert response.status_code==200 and not result.get('error'),result.get('error') or result.get('detail')
                return result
            finally:
                execution._current.reset(token);summary()

        async def check(label,fn):
            if selected and label not in selected:return
            try:
                await fn();record={'id':label,'passed':True}
            except BudgetStop:
                raise
            except Exception as exc:
                record={'id':label,'passed':False,'error':str(exc),'error_type':type(exc).__name__}
            results.append(record);summary();print(json.dumps(record,ensure_ascii=False),flush=True)

        def last_assistant(session_id=sid):
            return next(m for m in reversed(session.get_session_store()._backend.get_messages(session_id)) if m['role']=='assistant')

        try:
            with httpx.Client(transport=budget,timeout=90,follow_redirects=False) as upstream:
                llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
                    thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=16384,http_client=upstream)
                llm._structured_llms.clear()
                files=sorted(demo.glob('*.csv'))
                for index in range(0,len(files),5):await upload(sid,[(p.name,p.read_bytes(),'text/csv') for p in files[index:index+5]])
                session.get_session_store()._backend.set_title(sid,'能力实测 · 真实电商与多轮工具闭环')

                async def no_data():
                    session.get_session_store().get_or_create('empty-'+name,user_id='local-user')
                    result=await turn('01-no-data','我还没有导入数据，能直接给我本月销售额并画图吗？',session_id='empty-'+name)
                    assert not result.get('rows') and not result.get('sql') and not result.get('chart_spec')
                    assert any(word in result['answer'] for word in ['上传','导入','数据'])
                await check('no-data-does-not-invent-results',no_data)

                async def overview():
                    result=await turn('02-overview','请逐表列出当前八张表的行数与业务粒度，说明实际缺失字段的影响和多表关联风险；每张表都要回答。')
                    previews={r['table_name']:r['rows'] for r in result['table_previews']}
                    assert previews=={table:len(rows) for table,rows in tables.items()}
                await check('all-eight-table-overview',overview)

                async def causality_boundary():
                    result=await turn('13-causality-boundary','当前这些数据能否证明2018年6月商品金额下滑是广告投放失效造成的？还能算出净利润吗？直接回答这两个问题，并列出判断所缺的依据。')
                    text=result['answer']
                    assert any(word in text for word in ['不能','无法','不足']) and any(word in text for word in ['广告','投放','营销'])
                    assert any(word in text for word in ['成本','费用'])
                    assert not any(re.search(r'profit|利润',column,re.I) for column in result.get('columns',[]))
                await check('causality-and-profit-evidence-boundary',causality_boundary)

                trend_id=None
                async def multi_turn():
                    nonlocal trend_id
                    result=await turn('03-clarification','想看每个月净销售额的趋势。口径不确定，你先问我最关键的问题，不要猜。')
                    assert result.get('clarification')
                    mid=last_assistant()['id']
                    result=await turn('04-clarification','这里改为已送达订单商品金额，不含运费，不扣退款，按下单日期。币种先不要猜，接着问清楚。',clarification_reply={'message_id':mid})
                    assert result.get('clarification')
                    mid=last_assistant()['id']
                    result=await turn('05-trend','确认币种BRL，使用所有观察月份，不排除最后一个月，画折线图并说明仅限当前导入样本。',clarification_reply={'message_id':mid})
                    rows=full(result);measure=next(c for c in result['columns'] if c!='month')
                    assert match_rows([{'month':r['month'],'item_amount':r[measure]} for r in rows],[{'month':k,'item_amount':str(v)} for k,v in monthly.items()])
                    assert result.get('chart_spec') and not result.get('clarification');trend_id=last_assistant()['id']
                await check('multi-turn-confirmation-and-complete-trend',multi_turn)

                async def python_stats():
                    result=await turn('06-python','请明确使用Python计算order_items.price的数量、均值、中位数和P95，包含全部订单状态；P95采用线性插值。result用字典，键为count、mean、median、p95，保留数值。币种BRL。')
                    assert result['route']=='python' and result.get('python_output') and not result.get('error'),'Python execution receipt is missing code/output or reports a failure.'
                    import ast
                    data=ast.literal_eval(result['python_output'].strip())
                    values=sorted(Decimal(r['price']) for r in tables['order_items']);position=Decimal(len(values)-1)*Decimal('.95');lower=int(position)
                    expected={'count':len(values),'mean':sum(values)/len(values),'median':Decimal(str(statistics.median(values))),
                              'p95':values[lower]+(values[lower+1]-values[lower])*(position-lower)}
                    assert all(abs(Decimal(str(data[k]))-Decimal(str(v)))<Decimal('.000001') for k,v in expected.items()),data
                await check('real-python-statistics-in-container',python_stats)

                async def advanced_workflow():
                    result=await turn('07-flow-source','按已送达订单汇总卖家州至买家州的商品金额，不含运费，币种BRL，所有观察月份保留。同州保留，分别给卖家州加“卖家：”、买家州加“买家：”前缀。返回seller_state、buyer_state、item_amount三列完整结果，先不要绘图。')
                    measured={(r['seller_state'],r['buyer_state']):Decimal(str(r['item_amount'])) for r in full(result)}
                    assert measured.keys()==flows.keys() and all(abs(measured[k]-v)<Decimal('.005') for k,v in flows.items())
                    mid=last_assistant()['id']
                    result=await turn('08-advanced-workflow',f'使用当前聊天第{mid}条助手回复保存的完整201行结果，创建名为“能力复测·州际流向”的Superset桑基图，再回读核对该高级视图，最后导出完整CSV供下载。执行这三项，不重新查询或聚合，不用文字代替工具回执。')
                    workflow=result.get('workflow') or {};steps=workflow.get('steps',[])
                    assert workflow.get('status')=='completed' and len(steps)>=3 and all(s['status']=='succeeded' for s in steps),workflow
                    download=next(s['result']['download'] for s in steps if s.get('result',{}).get('download'))
                    csv_response=await client.get(f"/sessions/{sid}/results/{download['result_id']}/export")
                    assert csv_response.status_code==200 and len(csv_response.text.strip().splitlines())==202
                await check('superset-create-readback-full-export-workflow',advanced_workflow)

                async def board_tool():
                    assert trend_id is not None
                    result=await turn('09-board',f'创建一个新的分析面板，名称为“能力复测·月度经营”，并加入当前聊天第{trend_id}条助手回复中已生成的月度趋势图。请实际保存。')
                    boards_data=(await client.get('/boards')).json()
                    saved=next(b for b in boards_data if b['title']=='能力复测·月度经营')
                    board=(await client.get('/boards/'+saved['id'])).json()
                    assert any(item['kind']=='chart' and item.get('spec',{}).get('data') for item in board['items'])
                    assert result.get('workflow') and not result.get('error')
                await check('agent-saves-real-board-with-chart',board_tool)

                async def image_diagnosis():
                    image=ROOT/'.local/qa-chat-first-20261110/uploads/qa-flow-qa20261110/_images/4bd7bc6030277335a569d225a12aecff3fc837ed0694ad62763fa2e8efeaef7f.png'
                    uploaded=await upload(sid,[('sql-error.png',image.read_bytes(),'image/png')])
                    reference=uploaded['uploaded'][0]['image']
                    result=await turn('10-image','截图中的SQL错在哪里？目标是当前所有订单明细的商品金额合计，不限制订单状态，币种BRL。请给一条最推荐的修正SQL，先不执行。',images=[{'id':reference['id'],'filename':'sql-error.png'}])
                    assert not result.get('sql') and result.get('sql_proposal')
                    proposal=result['sql_proposal']['sql'].lower();assert 'order_items' in proposal and 'price' in proposal
                    mid=last_assistant()['id'];calls=len(budget.ledger['calls'])
                    response=await client.post(f'/sessions/{sid}/messages/{mid}/execute-sql',json={});assert response.status_code==200,response.text
                    task=response.json()
                    for _ in range(300):
                        task=(await client.get('/tasks/'+task['task_id'])).json()
                        if task['status'] in {'succeeded','failed','cancelled'}:break
                        await asyncio.sleep(.1)
                    assert task['status']=='succeeded' and task.get('result'),task
                    measured=list(task['result']['rows'][0].values());assert any(abs(Decimal(str(v))-all_amount)<Decimal('.005') for v in measured if isinstance(v,(int,float,str)))
                    assert len(budget.ledger['calls'])==calls,'Explicit SQL validation must not add an unrecorded model request.'
                    write_json(output/'image-sql-validation.json',task)
                await check('image-diagnosis-and-explicit-sql-execution',image_diagnosis)

                async def cleaning():
                    clean_sid='clean-'+name
                    raw='order_id,region,price\n0001, SP ,"R$ 1.234,50"\n0002,RJ,"R$ 10,00"\n0003, SP ,bad\n0004,,\n0005,MG,"R$ 0,00"\n0006,NA,"R$ 20,00"\n'.encode()
                    await upload(clean_sid,[('cleaning.csv',raw,'text/csv')])
                    data=dataset_manager.get_dataset_manager().get(clean_sid);before=workbench_snapshot(data);original_file=data.files[0].parquet_path;digest=hashlib.sha256(original_file.read_bytes()).hexdigest()
                    result=await turn('11-cleaning-preview','请给出清洗预览，先不要执行：只去掉region首尾空白，并将该字段NA明确设为空值；price已有币种BRL，小数逗号、千分位点，去掉R$标记转成精确数值，格式失败明确转为空值。已有缺失不填0，不去重，保留全部订单号的前导零。',session_id=clean_sid)
                    plan=result.get('cleaning') or {};assert plan.get('status') in {'proposed','pending'} and plan.get('preview'),'No applicable cleaning preview returned.'
                    actual_plan=(await client.get(f"/sessions/{clean_sid}/cleaning/plans/{plan['plan_id']}")).json()
                    assert actual_plan['status']=='pending','The saved plan is not awaiting confirmation.'
                    assert workbench_snapshot(data)==before
                    calls=len(budget.ledger['calls'])
                    result=await turn('12-cleaning-apply','执行刚才的清洗方案',session_id=clean_sid)
                    assert result.get('cleaning_event') and len(budget.ledger['calls'])==calls
                    rows=data.execute('SELECT order_id,region,price FROM cleaning ORDER BY order_id',as_frame=True).to_dict(orient='records')
                    assert [r['order_id'] for r in rows]==[f'{i:04d}' for i in range(1,7)]
                    assert Decimal(str(rows[0]['price']))==Decimal('1234.50') and Decimal(str(rows[4]['price']))==0
                    assert data.execute('SELECT COUNT(*) AS n FROM cleaning WHERE price IS NULL',as_frame=True).iloc[0,0]==2
                    assert Decimal(str(data.execute('SELECT SUM(price) AS total FROM cleaning',as_frame=True).iloc[0,0]))==Decimal('1264.50')
                    response=await client.post(f"/sessions/{clean_sid}/cleaning/plans/{plan['plan_id']}/undo",json={});assert response.status_code==200,response.text
                    assert workbench_snapshot(data)==before and hashlib.sha256(original_file.read_bytes()).hexdigest()==digest
                await check('chat-cleaning-preview-apply-undo-and-identifiers',cleaning)
        finally:
            summary();main.tasks.close()
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true');parser.add_argument('--name',required=True)
    parser.add_argument('--checks',nargs='+',choices=['no-data-does-not-invent-results','all-eight-table-overview','causality-and-profit-evidence-boundary','multi-turn-confirmation-and-complete-trend','real-python-statistics-in-container','superset-create-readback-full-export-workflow','agent-saves-real-board-with-chart','image-diagnosis-and-explicit-sql-execution','chat-cleaning-preview-apply-undo-and-identifiers'])
    args=parser.parse_args()
    if not args.live:parser.error('Requires --live; requests count towards the existing shared RMB20 ceiling.')
    results=asyncio.run(run(args.name,args.checks))
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)
