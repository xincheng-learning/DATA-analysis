"""Local-only controls for the optional visualization service."""
import json
import subprocess
import sys
import threading
import time
from typing import Literal

import httpx
import psycopg
from fastapi import APIRouter,Depends,HTTPException,Request,Response,Query
from pydantic import Field

from app import superset
from app.config import settings
from app.chart_appearance import ChartAppearance


def local_only(request:Request,response:Response):
    if settings.app_mode!='local':
        raise HTTPException(403,'高级可视化仅支持本机单用户模式。')
    if request.method!='GET' and request.headers.get('content-type','').split(';')[0]!='application/json':
        raise HTTPException(415,'请使用 JSON 请求。')
    response.headers['Cache-Control']='no-store'


router=APIRouter(prefix='/superset',dependencies=[Depends(local_only)])
_control_lock=threading.Lock()
_process=None


@router.get('/status')
def status():
    path=superset.runtime_dir()/'state.json'
    state=json.loads(path.read_text('utf-8')) if path.exists() else {'status':'unconfigured','message':'首次启动会准备本机高级服务。'}
    ready=False
    if state['status'] not in {'starting','stopping'}:
        try:
            with httpx.Client(timeout=3,trust_env=False) as client:
                ready=client.get('http://127.0.0.1:8088/health').status_code==200
            if ready:
                ready=superset.mcp_call('health_check').get('status')=='healthy'
        except (httpx.HTTPError,ValueError):
            ready=False
        state.update(status='ready' if ready else 'stopped' if state['status']=='ready' else state['status'])
    elif _process is not None and _process.poll() is not None:
        state.update(status='error',message='高级服务操作已退出，请重试并查看本机服务日志。')
    return {key:state.get(key) for key in ('status','message','updated_at')}


@router.post('/service/{action}')
def control(action:Literal['start','stop']):
    global _process
    with _control_lock:
        if _process is not None and _process.poll() is None:
            raise HTTPException(409,'高级服务正在启动或停止，请等待当前操作完成。')
        directory=superset.runtime_dir();directory.mkdir(parents=True,exist_ok=True)
        superset._write_json(directory/'state.json',{'status':'starting' if action=='start' else 'stopping',
            'message':'正在启动现有高级服务。' if action=='start' else '正在停止高级服务。','updated_at':time.time()})
        with (directory/'service.log').open('a',encoding='utf-8') as log:
            _process=subprocess.Popen([sys.executable,'-X','utf8',str(superset.ROOT/'scripts/superset_local.py'),action],
                cwd=superset.ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    return {'accepted':True}


def perform(function,*args):
    try:
        return function(*args)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    except (httpx.HTTPError,psycopg.Error,KeyError):
        raise HTTPException(502,'高级服务未返回完整结果，请检查服务状态；未确认成功。') from None


@router.get('/views')
def views():
    return perform(superset.list_views)


class CreateRequest(superset.ViewRequest):
    session_id:str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    message_id:int=Field(gt=0)
    step_id:str=Field(default='',pattern=r'^(?:[a-z][a-z0-9_]{0,39})?$')
    source_path:list[str|int]=Field(default_factory=list,max_length=28)


@router.post('/views')
def create(body:CreateRequest):
    from app.agent.workflow import Result,_result_source
    source=perform(_result_source,Result(message_id=body.message_id,source_step_id=body.step_id,source_path=body.source_path),{'session_id':body.session_id},[])
    return perform(superset.create_view,body.session_id,source,superset.ViewRequest.model_validate(body.model_dump(include={'title','chart_type','dimensions','measure'})))


@router.post('/views/{view_id}/token')
def token(view_id:str):
    return perform(superset.guest_token,view_id)


@router.post('/views/{view_id}/read')
def read(view_id:str,session_id:str|None=Query(default=None,min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')):
    return perform(superset.read_view,view_id,session_id)


@router.get('/views/{view_id}/presentation')
def presentation(view_id:str):
    return perform(lambda: superset.read_view(view_id,all_rows=True))


@router.put('/views/{view_id}/appearance')
def appearance(view_id:str,body:ChartAppearance):
    return perform(superset.save_appearance,view_id,body)


@router.post('/login')
def login():
    return perform(lambda:{'url':superset.ORIGIN,'username':'datapilot','password':superset.credentials()['admin_password']})
