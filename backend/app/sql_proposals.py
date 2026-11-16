"""Freeze a suggested query; execution is a separate explicit, read-only action."""
from app.agent.nodes import execute_sql_node, compose_answer_node
from app.agent.validator import validator_node
from app.execution import controlled_node
from app.tools.sql_sanitizer import check_sql_safety
from app.tools.data_workbench import workbench_snapshot


def proposal_from_reply(recommended_sql,data):
    if not data or not data.files:return None
    if not recommended_sql:return None
    sql=recommended_sql.strip()
    if len(sql)>20000 or check_sql_safety(sql,allowed_tables=data.table_names):return None
    return {'sql':sql,'data_version':workbench_snapshot(data),'scope':'仅当前聊天数据；执行前仍会检查版本。'}


def execute_proposal(sid,mid,proposal):
    state={'question':'只读验证这条已展示的推荐 SQL','session_id':sid,'sql':proposal['sql'],'route':'sql','data_version':proposal['data_version'],
           'sql_source':{'message_id':mid,'kind':'image_recommendation'},'previous_attempts':[]}
    state.update(controlled_node('execute_sql',execute_sql_node)(state))
    state.update(controlled_node('validator',validator_node)(state))
    error=state.get('error') or state.get('validation_failure')
    if error:
        state['answer']='推荐 SQL 的验证未通过。未自动改写查询；请查看具体执行错误和检查项。'
        state['error']=error
    else:
        state.update(compose_answer_node(state))
        state['answer']='已执行所展示的只读 SQL，结果如下。执行及列出的检查项通过，不代表所有业务口径均已确认。\n\n'+state['answer']
    return state
