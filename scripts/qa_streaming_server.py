"""Isolated, controlled model stream for real browser/HTTP verification; never calls a provider."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

import uvicorn
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import Runnable
from app import main,llm,model_settings,session
from app.agent import nodes,router
from app.config import settings
from app.tools import dataset_manager
from app.execution import _current

directory=ROOT/'.local/qa-streaming-20261111'
settings.local_data_dir=directory;settings.app_mode='local';settings.session_backend='sqlite'
session._store=session.SessionStore(backend=session.SQLiteBackend(directory/'datapilot.sqlite3'))
dataset_manager._manager=dataset_manager.DatasetManager()
main.limiter.enabled=False
status=main.get_model_status()
status.update(configured=True,model='受控流式验收',base_url='https://controlled.invalid')
main.get_model_status=lambda:status
model_settings.get_model_status=lambda:status

class Reply(Runnable):
    def invoke(self,input,config=None,**kwargs):
        raise AssertionError('Browser reply must use the actual streaming path.')
    def stream(self,input,config=None,**kwargs):
        question=str(input[-1].content)
        text='这是受控流式验收文字，用于检查首段在任务完成前出现。\n\n分析回答会先给出判断，再把依据和理由放在一起；前轮确认的口径需要沿用，有缺口就说明具体缺什么。\n\n连续追问仍应覆盖本轮的每个子问题，而不是只回答其中一项。'
        try:
            for index in range(0,len(text),2):
                if '失败' in question and index>25:
                    raise RuntimeError('controlled stream interruption')
                time.sleep(.04)
                yield AIMessageChunk(content=text[index:index+2])
        finally:
            (directory/'stream-closed.log').open('a',encoding='utf-8').write('closed\n')

labels={'order_id':'订单编号','customer_id':'订单客户编号','customer_unique_id':'跨订单客户编号',
 'order_status':'订单状态','order_purchase_timestamp':'下单时间','order_approved_at':'审批时间',
 'order_delivered_carrier_date':'交接承运时间','order_delivered_customer_date':'实际送达时间','order_estimated_delivery_date':'预计送达时间',
 'customer_zip_code_prefix':'客户邮编前缀','customer_city':'客户城市','customer_state':'客户州',
 'order_item_id':'订单明细序号','product_id':'商品编号','seller_id':'卖家编号','shipping_limit_date':'发货截止时间',
 'price':'商品明细金额','freight_value':'运费金额'}
titles={'orders':'订单主表','customers':'客户表','order_items':'订单商品明细表'}
descriptions={'orders':'记录下单、审批、交接与送达节点，适合查看订单状态和履约时间。概念粒度为订单，主键唯一性仍需实际核验。',
 'customers':'记录订单对应的客户标识与所在地区。跨订单客户应使用customer_unique_id识别，不能直接把customer_id当复购客户。',
 'order_items':'记录每个订单包含的商品、卖家、金额与运费；同一订单可能包含多条明细，关联后要避免重复计数。'}

class Guide(Runnable):
    def invoke(self,input,config=None,**kwargs):
        return list(self.stream(input,config,**kwargs))[-1]
    def stream(self,input,config=None,**kwargs):
        data=dataset_manager.get_dataset_manager().get(_current.get().session_id)
        tables=[];impacts={}
        for table in data.preview():
            for column,count in table['null_counts'].items():
                if count:
                    impacts[f"{table['table_name']}.{column}"]=f"{labels.get(column,column)}并未覆盖全部记录。依赖它的分析需说明有效记录范围，不能从空值推断业务事件未发生。"
        for table in data.preview():
            name=table['table_name']
            tables.append(nodes.TableExplanation(table_name=name,title=titles.get(name,name),description=descriptions.get(name,'已导入的电商表。'),
                fields=[nodes.FieldExplanation(column=column,label=labels.get(column,column),meaning='用于本表的'+labels.get(column,column)+'；原始字段保留。') for column in table['columns']]))
            time.sleep(.45)
            yield nodes.OverviewExplanation(impacts=impacts,tables=list(tables))

reply=Reply()
main.get_chat_llm=lambda:reply
nodes._get_llm=lambda:reply
nodes.get_structured_llm=lambda schema:Guide() if schema is nodes.OverviewExplanation else (_ for _ in ()).throw(AssertionError('No provider allowed'))
def route(messages):
    overview='介绍' in messages[-1].content
    return router.RouteDecision(route='chat',reason='受控浏览器验收',show_data_preview=overview)
router._get_router_llm=lambda:SimpleNamespace(invoke=route)
llm.build_compatible_client=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('Paid provider calls are disabled'))
if __name__=='__main__':
    uvicorn.run(main.app,host='127.0.0.1',port=8102,access_log=False)
