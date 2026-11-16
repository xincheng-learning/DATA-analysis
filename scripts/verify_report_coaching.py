"""Real Olist SQL/Python evidence and one budgeted synthesis; preserve original chats."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]


async def run(live):
    import httpx
    from app import execution,llm,main,model_settings,session
    from app.config import settings
    from app.agent.nodes import execute_sql_node,execute_python_node
    from app.analysis_report import compose_report
    from app.report_presentation import report_input
    from app.schemas import AskRequest
    from app.tools import dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.viz import ChartSpec,build_plotly_spec
    from app.checks import verify_chart
    from evals.live_budget import BudgetTransport,write_json
    config=model_settings.load_saved_settings()
    output=ROOT/'output/report-coaching-2026-11-11';output.mkdir(parents=True,exist_ok=True)
    storage=ROOT/'.local/qa-report-coaching-20261111-fixed'
    assert not (storage/'datapilot.sqlite3').exists(),'Use preserved evidence instead of overwriting a coaching run.'
    settings.local_data_dir=storage;settings.app_mode='local';settings.session_backend='sqlite'
    session._store=session.SessionStore(backend=session.SQLiteBackend(storage/'datapilot.sqlite3'))
    dataset_manager._manager=dataset_manager.DatasetManager();main.limiter.enabled=False
    sid='report-coaching-olist'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,client=('127.0.0.1',12345)),base_url='http://localhost:8000',timeout=180) as client:
        files=sorted((ROOT/'.local/ecommerce/demo').glob('*.csv'))
        for index in range(0,len(files),5):
            uploaded=await client.post('/sessions/'+sid+'/upload',files=[('files',(p.name,p.read_bytes(),'text/csv')) for p in files[index:index+5]])
            assert uploaded.status_code==200 and not uploaded.json()['errors'],uploaded.text
        session.get_session_store()._backend.set_title(sid,'表达教学｜结论、证据与图表')
        dataset=dataset_manager.get_dataset_manager().get(sid);version=workbench_snapshot(dataset)
        task=execution.AnalysisTask(owner='local-user',session_id=sid);token=execution._current.set(task)
        def query(question,sql,spec):
            state={'route':'viz','session_id':sid,'question':question,'sql':sql,'data_version':version}
            state.update(execution.controlled_node('execute_sql',execute_sql_node)(state));assert not state['error'],state['error']
            state['chart_spec']=build_plotly_spec(spec,state['rows'])
            state['checks'].append(verify_chart(state['chart_spec'],state['rows'],state['row_count']))
            return state
        monthly=query('2018年7月与6月已送达订单的商品金额、订单数和每单金额', '''SELECT
            STRFTIME(CAST(o.order_purchase_timestamp AS TIMESTAMP),'%Y-%m') AS purchase_month,
            SUM(CAST(i.price AS DECIMAL(18,2))) item_amount_brl,
            COUNT(DISTINCT o.order_id) delivered_orders,
            SUM(CAST(i.price AS DECIMAL(18,2)))/COUNT(DISTINCT o.order_id) avg_item_amount_per_order_brl
            FROM orders o JOIN order_items i USING(order_id) WHERE o.order_status='delivered'
            AND CAST(o.order_purchase_timestamp AS TIMESTAMP)>=TIMESTAMP '2018-06-01'
            AND CAST(o.order_purchase_timestamp AS TIMESTAMP)<TIMESTAMP '2018-08-01'
            GROUP BY purchase_month ORDER BY purchase_month''',
            ChartSpec(chart_type='bar',x_column='purchase_month',y_column='item_amount_brl',title='已送达商品金额｜2018年6—7月',x_label='下单月份',y_label='商品金额（BRL）'))
        assert [r['item_amount_brl'] for r in monthly['rows']]==[42481.28,50588.49]
        assert [r['delivered_orders'] for r in monthly['rows']]==[287,359]
        saved=json.loads((ROOT/'evals/runs/olist-teacher-20261111/04b-sales-chart-completed.json').read_text(encoding='utf8'))['final']
        regions=query('买家州商品金额变动：绝对变动前五州与其余合计',saved['sql'],
            ChartSpec(chart_type='bar',orientation='h',x_column='buyer_state',y_column='change_brl',title='买家州商品金额变动｜7月减6月',x_label='买家州',y_label='商品金额变动（BRL）'))
        assert abs(sum(r['change_brl'] for r in regions['rows'])-8107.21)<.000001
        steps=[{'id':'monthly','tool':'analyze','request':monthly['question'],'status':'succeeded','result':monthly},
               {'id':'regions','tool':'analyze','request':regions['question'],'status':'succeeded','result':regions}]
        final={'route':'workflow','workflow':{'status':'completed','requirements':['可比经营指标','地区变动及理由'],'steps':steps}}
        question='分析2018年7月与6月的经营变化，解释订单数、每单金额与买家州变动的关系，并给出有证据的建议。已送达，商品金额不含运费，BRL，公开5000客户完整历史样本。'
        if live:
            assert config,'A saved model configuration is required for the single live synthesis.'
            budget=BudgetTransport(ROOT/'evals/runs/gold-v1',max_output_tokens=4096)
            initial=budget.ledger['accounted_micro_cny'];budget.label='report-coaching/verified-evidence-synthesis'
            with httpx.Client(transport=budget,timeout=90,follow_redirects=False) as upstream:
                llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
                    thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=4096,http_client=upstream)
                llm._structured_llms.clear()
                evidence,_=report_input(final)
                raw=execution.controlled_node('synthesize',lambda _:compose_report(question,evidence))({})
            write_json(output/'live-synthesis.json',{'report':raw,'model_calls':task.model_calls,
                'stage_cost_cny':(budget.ledger['accounted_micro_cny']-initial)/1e6,
                'shared_total_cny':budget.ledger['accounted_micro_cny']/1e6,'blocked':budget.ledger['blocked']})
            assert not budget.ledger['blocked'] and raw['status']=='generated'
            final['analysis_report']=raw
        response=main._complete_analysis(AskRequest(session_id=sid,question=question),final,sid,{})
        write_json(output/'business-report.json',response.model_dump(mode='json'))
        assert response.analysis_report['sources'][1]['path']==['workflow','steps',1,'result']
        assert all(f['reason'] and f['evidence_ids'] for f in response.analysis_report['findings'])
        execution._current.reset(token)
        old=json.loads((ROOT/'evals/runs/olist-teacher-20261111/07-delivery-statistics.json').read_text(encoding='utf8'))['final']
        task=execution.AnalysisTask(owner='local-user',session_id=sid);token=execution._current.set(task)
        state={'route':'python','session_id':sid,'question':'用Python分析延迟订单与低评价的统计关联，并展示真实结果图表',
               'python_code':old['python_code'],'data_version':version}
        state.update(execution.controlled_node('execute_python',execute_python_node)(state));assert not state['error'],state['error']
        assert [(r['有效订单数'],r['低评价数']) for r in state['rows']]==[(352,218),(4626,440)]
        state['chart_spec']=build_plotly_spec(ChartSpec(chart_type='bar',x_column='分组',y_column='低评价比例',
            value_scale='ratio',title='履约与低评价｜观察性关联',x_label='履约分组',y_label='低评价比例'),state['rows'])
        state['checks'].append(verify_chart(state['chart_spec'],state['rows'],2))
        from app.agent.nodes import compose_answer_node
        state.update(compose_answer_node(state))
        response=main._complete_analysis(AskRequest(session_id=sid,question=state['question']),state,sid,{})
        write_json(output/'python-report.json',response.model_dump(mode='json'))
        execution._current.reset(token)
        history=(await client.get('/sessions/'+sid)).json()['messages']
        assert sum(bool(m['metadata'].get('analysis_report')) for m in history)==2
        write_json(output/'verification.json',{'passed':True,'session_id':sid,'data':'real Olist sample CSV uploaded to isolated store',
            'sql':'Actual SQL executions, chart source checks and independent known totals matched.',
            'python':'Earlier model-generated code rerun in Docker, not on host; group counts independently matched.',
            'model':'One real synthesis over executed evidence; no new model query planning evaluation.' if live else 'No model requests.',
            'api_history_reports':2,'original_chats_modified':False})
    main.tasks.close()
    print(json.dumps({'passed':True,'session_id':sid,'output':str(output)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live-synthesis',action='store_true');args=p.parse_args()
    asyncio.run(run(args.live_synthesis))
