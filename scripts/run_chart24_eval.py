"""Real chart selection on frozen teacher inputs; no SQL-agent or browser claim."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]
from evals.live_budget import BudgetTransport, BudgetStop, write_json


def run(directory, reference):
    import httpx
    from langchain_core.messages import HumanMessage, SystemMessage
    from app import llm, model_settings
    from app.agent.nodes import make_chart_node
    from app.execution import invoke_model
    from app.superset import ViewRequest
    teacher=json.loads(reference.read_text(encoding='utf-8'))
    if teacher['reference_source_sha256']!=hashlib.sha256((ROOT/'evals/chart24_reference.py').read_bytes()).hexdigest():
        raise ValueError('教师源已变化，不能把新标准混入原轮次。')
    if len(teacher['cases'])!=24 or {c['id'] for c in teacher['cases']}!={f'SM{i:02}' for i in range(1,25)}:
        raise ValueError('必须是完整固定24项教师。')
    if (directory/'configuration.json').exists():
        raise ValueError('请保留已有真实运行，使用新的输出目录。')
    config=model_settings.load_saved_settings()
    if not config or config.base_url.rstrip('/') not in ('https://api.deepseek.com','https://api.deepseek.com/v1') or config.model!='deepseek-flash':
        raise BudgetStop('当前模型不在已核价范围，没有修改配置。')
    directory.mkdir(parents=True,exist_ok=True)
    sources={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/app').rglob('*.py')}
    write_json(directory/'configuration.json',{'scope':'live chart selection and fixed-input production builder; NOT complete SQL agent or browser',
        'reference_sha256':hashlib.sha256(reference.read_bytes()).hexdigest(),'model':model_settings.get_model_status(),'sources':sources})
    with httpx.Client(transport=BudgetTransport(ROOT/'evals/runs/gold-v1'),timeout=30,follow_redirects=False) as http:
        budget=http._transport
        llm._chat_llm=llm.build_compatible_client(config.base_url,config.api_key.get_secret_value(),config.model,
            thinking_mode=config.thinking_mode,reasoning_effort=config.reasoning_effort,max_tokens=4096,http_client=http)
        llm._chat_llm.disable_streaming=True;llm._structured_llms.clear()
        for case in teacher['cases']:
            first=len(budget.ledger['calls']);budget.label=f'{directory.name}/{case["id"]}'
            started=time.monotonic();rows=case['rows'];question=case['question']
            record={'id':case['id'],'name':case['name'],'question':question,'rows':rows,'columns':list(rows[0]),
                'input_sha256':case['expected']['input_sha256'],'scope':'live selection; deterministic full fixed input; not AI SQL or teacher verification'}
            if case['kind']=='treemap':
                choice=invoke_model(llm.get_structured_llm(ViewRequest),[
                    SystemMessage(content='为用户的结果表选择高级图，返回实际ViewRequest结构。只用已有列；支持pivot_table、treemap、sunburst、sankey。不要计算或猜测数据。'),
                    HumanMessage(content=json.dumps({'question':question,'columns':list(rows[0]),'rows':rows},ensure_ascii=False))])
                record.update(advanced_request=choice.model_dump(),chart_spec={},status='advanced_selection')
            else:
                result=make_chart_node({'route':'viz','question':question,'columns':list(rows[0]),'rows':rows,'row_count':len(rows)})
                record.update(result,status='built' if result.get('chart_spec') else 'failed')
            record.update(seconds=round(time.monotonic()-started,3),request_ids=[c['id'] for c in budget.ledger['calls'][first:]],
                cost_upper_cny=sum(c['charged_micro_cny'] for c in budget.ledger['calls'][first:])/1e6)
            write_json(directory/'cases'/(case['id']+'.json'),record)
            if budget.ledger['blocked']:
                raise BudgetStop('存在未结算费用，已保存并停止。')
            print(json.dumps({k:record[k] for k in ('id','status','seconds','request_ids','cost_upper_cny')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True);args=parser.parse_args()
    if not args.live:parser.error('真实模型选择会计费，必须显式--live，继续原40元累计账本。')
    run(args.output,args.reference)
