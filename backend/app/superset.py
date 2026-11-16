"""Local Superset snapshots; model text never supplies credentials or raw tool names."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from collections import Counter,deque
from decimal import Decimal,InvalidOperation
from pathlib import Path

import httpx
import jwt
import psycopg
from psycopg import sql
from pydantic import BaseModel,ConfigDict,Field

from app.config import settings
from app.execution import checkpoint,commit_action
from app.model_settings import _dpapi,SecretStorageError
from app.tools.data_workbench import _write_json

ROOT=Path(__file__).resolve().parents[2]
ORIGIN='http://localhost:8088'
MCP_RELAY='http://127.0.0.1:3000/api/integrations/superset/mcp'
SECRET_NAMES=('postgres_password','metadata_password','superset_secret','admin_password',
              'reader_password','writer_password','mcp_secret','guest_secret','relay_key')
CHART_TYPES=('pivot_table','treemap','sunburst','sankey')
PRESENCE_METRIC='结果记录数'


class ViewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=100)
    chart_type:str=Field(pattern=r'^(pivot_table|treemap|sunburst|sankey)$')
    dimensions:list[str]=Field(min_length=1,max_length=4)
    measure:str=Field(min_length=1,max_length=255)


def runtime_dir():
    directory=settings.local_data_dir/'superset'
    if directory.resolve()!=directory.absolute():
        raise ValueError('高级可视化目录不能重定向。')
    return directory


def credentials():
    path=runtime_dir()/'credentials.bin'
    if not path.exists():
        raise ValueError('Superset 尚未配置，请先在高级可视化中启动本机服务。')
    try:
        return json.loads(_dpapi(path.read_bytes(),decrypt=True))
    except Exception:
        raise SecretStorageError('Superset 本机加密配置不可读，原配置未覆盖。') from None


def initialize_credentials():
    directory=runtime_dir()
    directory.mkdir(parents=True,exist_ok=True)
    private=directory/'secrets'
    private.mkdir(exist_ok=True)
    path=directory/'credentials.bin'
    if path.exists():
        bundle=credentials()
    else:
        bundle={name:secrets.token_urlsafe(36) for name in SECRET_NAMES}
        temporary=path.with_suffix('.tmp')
        temporary.write_bytes(_dpapi(json.dumps(bundle).encode()))
        os.replace(temporary,path)
    for name,value in bundle.items():
        target=private/name
        target.write_text(value,encoding='utf-8')
        target.chmod(0o600)
    return bundle


def _number(value):
    if value is None:
        return None
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
        raise ValueError('指标必须是实际数值列，不能将文本或布尔值当作金额/权重。')
    result=Decimal(str(value))
    if not result.is_finite():
        raise ValueError('指标存在非有限数值，不能绘制高级图表。')
    return result


def prepare_rows(rows,dimensions,measure,chart_type):
    if chart_type != 'pivot_table' and re.search(r'(^|_)(avg|average|mean|median|rate|ratio|pct|percent|aov)(_|$)|平均|均价|客单价|中位数|留存率|转化率|占比',measure,re.I):
        raise ValueError('平均值或比率不能直接作为可加总的层级/流向权重，请改用透视表，或先查询金额、数量等可加总指标。')
    if chart_type not in CHART_TYPES or not rows or len(rows)>5000:
        raise ValueError('高级图表需要1至5000行完整结果，请先查询或聚合。')
    if not dimensions or len(set(dimensions))!=len(dimensions) or measure in dimensions:
        raise ValueError('维度不能重复，也不能与指标使用同一列。')
    if chart_type=='sankey' and len(dimensions)!=2:
        raise ValueError('桑基图需要明确的来源、去向两个维度。')
    prepared=[]
    seen=set()
    edges=[]
    for row in rows:
        if not set([*dimensions,measure]).issubset(row):
            raise ValueError('所选维度或指标不存在于真实查询结果。')
        labels=tuple(None if row[name] is None else str(row[name]) for name in dimensions)
        if labels in seen:
            raise ValueError('结果粒度不唯一，请先按所选维度完成聚合，不在绘图时重复加总。')
        seen.add(labels)
        value=_number(row[measure])
        if chart_type!='pivot_table' and (value is None or value<0 or any(label is None or label=='' for label in labels)):
            raise ValueError('层级/流向图需要完整维度和非负已知权重；请核对数据或使用保留未知的透视表。')
        prepared.append({**{f'dim_{index}':label for index,label in enumerate(labels)},'metric_value':value})
        if chart_type=='sankey':
            edges.append(labels)
    if edges:
        neighbors={node:[] for edge in edges for node in edge}
        degrees={node:0 for node in neighbors}
        for start,end in edges:
            neighbors[start].append(end)
            degrees[end]+=1
        queue=deque(node for node,count in degrees.items() if not count)
        visited=0
        while queue:
            node=queue.popleft()
            visited+=1
            for following in neighbors[node]:
                degrees[following]-=1
                if degrees[following]==0:
                    queue.append(following)
        if visited!=len(degrees):
            raise ValueError('桑基图存在环路或相同节点自连接，不能按无环流向绘制。')
    return prepared


def returned_rows(rows):
    result=[]
    for row in rows:
        value=row.get('metric_value')
        try:
            value=_number(Decimal(value) if isinstance(value,str) else value)
        except (InvalidOperation,ValueError):
            raise ValueError('Superset 返回的指标不是有效数值，未确认成功。') from None
        result.append({**row,'metric_value':value})
    return result


def verify_rows(expected,actual):
    actual=returned_rows(actual)
    columns=list(expected[0]) if expected else []
    def keys(rows):
        result=[]
        for row in rows:
            if not set(columns).issubset(row):
                raise ValueError('Superset 图表列与查询结果不一致，未确认成功。')
            result.append(tuple(_number(row[name]) if name=='metric_value' else row[name] for name in columns))
        return Counter(result)
    if not expected or keys(expected)!=keys(actual):
        raise ValueError('Superset 图表的维度、行数或数值与完整查询结果不一致，未确认成功。')
    return {'id':'superset_source','status':'passed','detail':f'已逐组核对 Superset 实际图表查询与本地完整结果，共 {len(expected)} 行。',
            'source_rows':len(expected),'query_rows':len(actual),'scope':'核对当前保存视图的默认查询；不证明业务口径或临时交互筛选。'}


def fingerprint(rows):
    normalized=[]
    for row in rows:
        value=_number(row['metric_value'])
        text=None if value is None else format(value,'f')
        if text and '.' in text:
            text=text.rstrip('0').rstrip('.')
        normalized.append({**row,'metric_value':'0' if value==0 else text})
    return hashlib.sha256(json.dumps(sorted(normalized,key=lambda row:json.dumps(row,sort_keys=True,ensure_ascii=False)),
        sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def mcp_call(name,arguments=None,*,timeout=100):
    checkpoint('调用 Superset MCP：'+name)
    bundle=credentials()
    token=jwt.encode({'sub':'datapilot','iss':'datapilot-local','aud':'superset-mcp',
                      'iat':int(time.time()),'exp':int(time.time())+180},bundle['mcp_secret'],algorithm='HS256')
    try:
        with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False) as client:
            response=client.post(MCP_RELAY,headers={'X-DataPilot-Relay-Key':bundle['relay_key'],'Authorization':'Bearer '+token},
                json={'name':name,'arguments':{'request':arguments} if arguments is not None else {}})
            response.raise_for_status()
            result=response.json()['data']
    except (httpx.HTTPError,KeyError,ValueError):
        raise ValueError('Superset MCP 调用失败，请检查高级服务、网页服务及本机连接状态；未将失败标记为完成。') from None
    checkpoint()
    if isinstance(result,dict) and (result.get('error') or result.get('success') is False or result.get('permission_denied')):
        raise ValueError('Superset 拒绝了 '+name+' 操作，请检查视图配置和权限。')
    return result


class SupersetAPI:
    def __init__(self):
        self.client=httpx.Client(base_url=ORIGIN,timeout=90,follow_redirects=False,trust_env=False)

    def __enter__(self):
        password=credentials()['admin_password']
        try:
            response=self.client.post('/api/v1/security/login',json={'username':'datapilot','password':password,'provider':'db','refresh':True})
            response.raise_for_status()
            self.client.headers['Authorization']='Bearer '+response.json()['access_token']
            token=self.client.get('/api/v1/security/csrf_token/').json()['result']
            self.client.headers['X-CSRFToken']=token
        except (httpx.HTTPError,KeyError,ValueError):
            self.client.close()
            raise ValueError('Superset 本机登录失败，请检查高级服务状态。') from None
        return self

    def __exit__(self,*args):
        self.client.close()

    def call(self,method,path,body=None):
        checkpoint('调用 Superset 官方接口')
        response=self.client.request(method,path,json=body)
        if response.status_code>=400:
            raise ValueError(f'Superset 官方接口操作失败（{response.status_code}），未确认成功。')
        return response.json()


def publish_rows(view_id,rows):
    table='view_'+view_id
    bundle=credentials()
    with psycopg.connect(host='127.0.0.1',port=55433,dbname='datapilot_visual',user='datapilot_writer',
            password=bundle['writer_password'],connect_timeout=5,options='-c statement_timeout=30000') as connection:
        columns=list(rows[0])
        fields=[sql.SQL('{} {}').format(sql.Identifier(name),sql.SQL('NUMERIC' if name=='metric_value' else 'TEXT')) for name in columns]
        connection.execute(sql.SQL('CREATE TABLE exports.{} ({})').format(sql.Identifier(table),sql.SQL(',').join(fields)))
        statement=sql.SQL('INSERT INTO exports.{} ({}) VALUES ({})').format(sql.Identifier(table),
            sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
        with connection.cursor() as cursor:
            cursor.executemany(statement,[tuple(row[name] for name in columns) for row in rows])
    return table


def chart_configuration(dataset_id,chart_type,dimension_count,retain_unknown=False):
    dims=[f'dim_{index}' for index in range(dimension_count)]
    metric={'expressionType':'SIMPLE','column':{'column_name':'metric_value'},'aggregate':'MAX',
            'label':'metric_value','hasCustomLabel':True,'optionName':'metric_value'}
    form={'datasource':f'{dataset_id}__table','viz_type':{'pivot_table':'pivot_table_v2','treemap':'treemap_v2',
        'sunburst':'sunburst_v2','sankey':'sankey_v2'}[chart_type],
        'row_limit':5001,'adhoc_filters':[],'color_scheme':'supersetColors','show_labels':True,
        'number_format':'.12~g','label_type':'key_value','show_upper_labels':True,'sort_by_metric':False}
    if chart_type=='pivot_table':
        form.update(groupbyRows=dims[:-1] or dims,groupbyColumns=dims[-1:] if len(dims)>1 else [],
            metrics=[metric],aggregateFunction='Maximum',rowTotals=False,colTotals=False,
            transposePivot=False,combineMetric=False,valueFormat='.12~g')
        if retain_unknown:
            # Superset 6.1 filters null metric records before pivoting; this keeps their groups visible.
            form['metrics'].append({**metric,'aggregate':'COUNT','label':PRESENCE_METRIC,'optionName':'result_rows',
                                    'column':{'column_name':dims[0]},'expressionType':'SQL','sqlExpression':'COUNT(*)'})
    else:
        form.update(metric=metric,groupby=dims)
        if chart_type=='sunburst':
            form.update(columns=dims,secondary_metric=None,show_total=False,show_labels_threshold=3,
                        label_type='key',color_by_category=True)
        if chart_type=='sankey':
            form.update(source=dims[0],target=dims[1])
    query={'datasource':{'id':dataset_id,'type':'table'},'queries':[{'columns':dims,'metrics':[metric],
        'filters':[],'extras':{},'row_limit':5001,'orderby':[[name,True] for name in dims],'post_processing':[]}],
        'form_data':form,'result_format':'json','result_type':'full','force':True}
    if chart_type=='pivot_table':
        query['queries'][0]['metrics']=form['metrics']
    return form,query


def check_chart_configuration(view):
    form,query=chart_configuration(view['dataset_id'],view['chart_type'],len(view['dimensions']),view.get('retain_unknown',False))
    with SupersetAPI() as api:
        chart=api.call('GET',f"/api/v1/chart/{view['chart_id']}")['result']
    # MCP may execute the saved query_context while the page uses params; verify both.
    cosmetic={'color_scheme','show_labels','number_format','label_type','show_upper_labels','sort_by_metric',
              'valueFormat','show_total','show_labels_threshold','color_by_category'}
    def configuration(form,query):
        return {'form':{key:value for key,value in form.items() if key not in cosmetic},
                'query':{key:({name:value for name,value in value.items() if name not in cosmetic} if key=='form_data' else value)
                         for key,value in query.items() if key!='force'}}
    try:
        actual=configuration(json.loads(chart['params']),json.loads(chart['query_context']))
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('Superset 已保存的图表配置无法核对，请重新创建视图；未采用为原分析证据。') from None
    if actual!=configuration(form,query):
        raise ValueError('Superset 默认图表配置已变化，与保存快照的查询口径不一致；请重新创建或核对，未采用为原分析证据。')


def chart_rows(view):
    check_chart_configuration(view)
    actual=returned_rows(mcp_call('get_chart_data',{'identifier':view['chart_id'],'limit':5001,'force_refresh':True})['data'])
    if view.get('retain_unknown'):
        if any(row.get(PRESENCE_METRIC)!=1 for row in actual):
            raise ValueError('透视表结果记录数与唯一粒度不一致，未确认成功。')
        actual=[{key:value for key,value in row.items() if key!=PRESENCE_METRIC} for row in actual]
    return actual


def _store_view(view):
    from app.boards import backend
    with backend()._connect() as con:
        con.execute('INSERT OR REPLACE INTO advanced_views VALUES (?,?,?)',
                    (view['id'],json.dumps(view,ensure_ascii=False),view['created_at']))


def get_view(view_id):
    from app.boards import backend
    with backend()._connect() as con:
        row=con.execute('SELECT document FROM advanced_views WHERE id=?',(view_id,)).fetchone()
    if row is None:
        raise ValueError('高级视图不存在。')
    return json.loads(row['document'])


def list_views():
    from app.boards import backend
    with backend()._connect() as con:
        rows=con.execute('SELECT document FROM advanced_views ORDER BY updated_at DESC').fetchall()
    return [view_summary(json.loads(row['document'])) for row in rows]


def view_summary(view):
    return {name:view.get(name) for name in ('id','title','chart_type','dimensions','measure','row_count',
        'created_at','status','error','checks','data_version','embedded_id','dashboard_id','appearance')}


def fit_dashboard(position):
    for item in position.values():
        if isinstance(item,dict) and item.get('type')=='CHART':
            item['meta'].update(width=12,height=90)
    return position


def native_number_format(measure,rows):
    money=re.search(r'_(brl|cny|usd|eur)$',measure,re.IGNORECASE)
    return ',.2f' if money and all(row['metric_value'] is None or _number(row['metric_value']).as_tuple().exponent>=-2 for row in rows) else '.12~g'


def save_appearance(view_id,appearance):
    view=get_view(view_id)
    if view['status']!='ready':
        raise ValueError('请等高级图表核对完成后再调整外观。')
    view['appearance']=appearance.model_dump(exclude_none=True)
    _store_view(view)
    return view_summary(view)


def create_view(session_id,source,request):
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    from app.tools.query_results import result_path
    import pyarrow as pa
    dataset=get_dataset_manager().get(session_id)
    computed_python=source.get('python_code') and any(check.get('id')=='python_result' and check.get('status')=='passed' for check in source.get('checks',[]))
    if (not dataset or not (source.get('sql') or computed_python) or source.get('error') or source.get('validation_failure')
            or source.get('output_limit_reached') or not source.get('data_version')
            or source['data_version']!=workbench_snapshot(dataset)):
        raise ValueError('高级绘图需要当前数据版本的成功完整查询，请先重新查询，不能沿用过期或未知版本结果。')
    with pa.memory_map(str(result_path(session_id,source['result_id'])),'r') as stream:
        reader=pa.ipc.open_file(stream)
        if int(reader.schema.metadata[b'row_count'])>5000:
            raise ValueError('高级图表最多5000行，请先聚合查询。')
        rows=reader.read_all().to_pylist()
    prepared=prepare_rows(rows,request.dimensions,request.measure,request.chart_type)
    view={'id':uuid.uuid4().hex,**request.model_dump(),'row_count':len(rows),'created_at':time.time(),
          'status':'creating','source_session':session_id,'source_result':source['result_id'],
          'data_version':source['data_version'],'source_sql':source.get('sql',''),'source_python':source.get('python_code',''),'checksum':fingerprint(prepared),'checks':[]}
    view['retain_unknown']=request.chart_type=='pivot_table' and any(row['metric_value'] is None for row in prepared)
    _store_view(view)
    try:
        table=publish_rows(view['id'],prepared)
        databases=mcp_call('list_databases',{'search':'DataPilot verified results'})['databases']
        database=next(item for item in databases if item['database_name']=='DataPilot verified results')
        result=mcp_call('create_virtual_dataset',{'database_id':database['id'],'dataset_name':request.title+' '+view['id'][:8],
            'sql':f'SELECT * FROM exports."{table}"','schema':'exports','description':'独立查询结果快照；仅覆盖已保存维度。'})
        view['dataset_id']=result['id'];_store_view(view)
        with SupersetAPI() as api:
            dataset_info=api.call('GET',f"/api/v1/dataset/{view['dataset_id']}")['result']
            labels={f'dim_{index}':name for index,name in enumerate(request.dimensions)}
            labels['metric_value']=request.measure
            api.call('PUT',f"/api/v1/dataset/{view['dataset_id']}",{'columns':[
                {'id':column['id'],'column_name':column['column_name'],'verbose_name':labels[column['column_name']]}
                for column in dataset_info['columns'] if column['column_name'] in labels]})
        form,query=chart_configuration(view['dataset_id'],request.chart_type,len(request.dimensions),view['retain_unknown'])
        form['number_format']=native_number_format(request.measure,prepared)
        if request.chart_type=='pivot_table':form['valueFormat']=form['number_format']
        if request.chart_type=='pivot_table':
            result=mcp_call('generate_chart',{'dataset_id':view['dataset_id'],'chart_name':request.title,'save_chart':True,
                'generate_preview':False,'preview_formats':[],'config':{'chart_type':'pivot_table',
                    'rows':[{'name':name} for name in form['groupbyRows']],
                    'columns':[{'name':name} for name in form['groupbyColumns']],
                    'metrics':[{'name':'metric_value','aggregate':'MAX','label':'metric_value'}],
                    'aggregate_function':'Maximum','show_row_totals':False,'show_column_totals':False,'row_limit':5001}})
            view['chart_id']=result['chart']['id']
        with SupersetAPI() as api:
            body={'params':json.dumps(form),'query_context':json.dumps(query),
                  'description':'已保存的完整查询结果快照；空指标仍为未知，不代表零。'+
                    ('结果记录数用于保留未知指标分组，不是原始订单数。' if view['retain_unknown'] else '')}
            if 'chart_id' in view:
                api.call('PUT',f"/api/v1/chart/{view['chart_id']}",body)
            else:
                result=api.call('POST','/api/v1/chart/',{**body,'slice_name':request.title,'viz_type':form['viz_type'],
                    'datasource_id':view['dataset_id'],'datasource_type':'table'})
                view['chart_id']=result['id']
        _store_view(view)
        actual=chart_rows(view)
        view['checks']=[verify_rows(prepared,actual)]
        if view['retain_unknown']:
            view['checks'].append({'id':'superset_unknown','status':'warning',
                'detail':('所有指标值均未知，透视表只展示结果记录数；原指标没有可显示数值。' if all(row['metric_value'] is None for row in prepared) else '空指标保持未知。')+
                    '透视表增加“结果记录数”以保留指标全未知的分组；它统计每个维度组合的结果行数，不是原始订单数。'})
        result=mcp_call('generate_dashboard',{'chart_ids':[view['chart_id']],'dashboard_title':request.title,
            'description':'独立快照；临时筛选不改变默认查询口径；源数据版本 '+source['data_version'],'published':True})
        view['dashboard_id']=result['dashboard']['id'];_store_view(view)
        with SupersetAPI() as api:
            dashboard=api.call('GET',f"/api/v1/dashboard/{view['dashboard_id']}")['result']
            api.call('PUT',f"/api/v1/dashboard/{view['dashboard_id']}",{
                'position_json':json.dumps(fit_dashboard(json.loads(dashboard['position_json'])))})
            result=api.call('POST',f"/api/v1/dashboard/{view['dashboard_id']}/embedded",{
                'allowed_domains':['http://localhost:3000','http://127.0.0.1:3000']})
            view['embedded_id']=result['result']['uuid']
        view['status']='ready'
        commit_action(lambda:_store_view(view))
        return view_summary(view)
    except Exception as exc:
        view.update(status='failed',error='高级视图未完成，未确认成功；已产生的独立快照保留以便排查。')
        _store_view(view)
        if isinstance(exc,psycopg.OperationalError):
            raise ValueError('无法连接高级可视化服务，请在界面“高级可视化”中启动服务后重试；未确认创建成功。') from None
        raise


def read_view(view_id,session_id=None,*,all_rows=False):
    view=get_view(view_id)
    if view['status']!='ready':
        raise ValueError('该高级视图尚未完成，不能读取为成功分析。')
    actual=chart_rows(view)
    if len(actual)!=view['row_count'] or fingerprint(actual)!=view['checksum']:
        raise ValueError('高级视图默认配置或数据已变化，与保存快照不一致；请重新创建或核对，未采用为原分析证据。')
    mapping={f'dim_{index}':name for index,name in enumerate(view['dimensions'])}
    mapping['metric_value']=view['measure']
    rows=[{mapping[name]:format(value,'f') if isinstance(value,Decimal) else value for name,value in row.items() if name in mapping} for row in actual]
    displayed=rows if all_rows else rows[:50]
    result={'advanced_view':view_summary(view),'columns':list(mapping.values()),'rows':displayed,
            'row_count':len(rows),'checks':view['checks'],
            'answer':f"已回读并核对高级视图“{view['title']}”的默认查询，共 {len(rows)} 行，显示 {len(displayed)} 行。临时交互筛选不在本次回读范围。"}
    if session_id:
        import pandas as pd
        from app.tools.query_results import save_result
        result['result_id']=save_result(session_id,pd.DataFrame(rows))
    return result


def guest_token(view_id):
    view=get_view(view_id)
    if view['status']!='ready':
        raise ValueError('高级视图尚未完成。')
    with SupersetAPI() as api:
        result=api.call('POST','/api/v1/security/guest_token/',{'user':{'username':'datapilot-viewer'},
            'resources':[{'type':'dashboard','id':view['embedded_id']}],'rls':[]})
    return {'token':result['token'],'embedded_id':view['embedded_id'],'origin':ORIGIN}
