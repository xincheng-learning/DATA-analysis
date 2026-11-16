"""Local, self-contained chart boards; no source chat text or SQL is copied."""
import hashlib
import json
import time
import uuid
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field

from app.session import SQLiteBackend, get_session_store
from app.chart_appearance import ChartAppearance


class BoardName(BaseModel):
    title: str = Field(min_length=1,max_length=100)


class BoardItem(BaseModel):
    id: str = Field(min_length=1,max_length=64)
    kind: Literal['chart','text']
    title: str = Field(default='',max_length=200)
    text: str = Field(default='',max_length=5000)
    placed: bool = False
    x: float = Field(default=40,ge=0,le=20000,allow_inf_nan=False)
    y: float = Field(default=40,ge=0,le=20000,allow_inf_nan=False)
    width: float = Field(default=520,ge=160,le=4000,allow_inf_nan=False)
    height: float = Field(default=400,ge=80,le=4000,allow_inf_nan=False)
    appearance: ChartAppearance = Field(default_factory=ChartAppearance)


class BoardSave(BoardName):
    version: int = Field(ge=1)
    items: list[BoardItem] = Field(max_length=100)


class ChartSource(BaseModel):
    session_id: str = Field(min_length=1,max_length=64)
    message_id: int = Field(gt=0)
    step_id: str = Field(default='',pattern=r'^(?:[a-z][a-z0-9_]{0,39})?$')
    source_path: list[str | int] = Field(default_factory=list,max_length=28)


class BoardConflict(ValueError):
    pass


def backend():
    store=get_session_store()._backend
    if not isinstance(store,SQLiteBackend):
        raise ValueError('分析面板需要本地聊天存储。')
    return store


def get_board(board_id):
    with backend()._connect() as con:
        row=con.execute('SELECT document FROM analysis_boards WHERE id=?',(board_id,)).fetchone()
    if row is None:
        raise KeyError('面板不存在或已删除。')
    return json.loads(row['document'])


def list_boards():
    with backend()._connect() as con:
        rows=con.execute('SELECT document FROM analysis_boards ORDER BY updated_at DESC').fetchall()
    return [{k:board[k] for k in ('id','title','version','updated_at')} | {
        'chart_count':sum(item['kind']=='chart' and item['placed'] for item in board['items']),
        'note_count':sum(item['kind']=='text' and item['placed'] for item in board['items'])}
        for row in rows for board in [json.loads(row['document'])]]


def create_board(title):
    if not title.strip():
        raise ValueError('请输入面板名称。')
    board={'id':uuid.uuid4().hex,'title':title.strip(),'version':1,'updated_at':time.time(),'items':[]}
    return _insert(board)


def _insert(board):
    with backend()._connect() as con:
        con.execute('INSERT INTO analysis_boards VALUES (?,?,?)',(board['id'],json.dumps(board,ensure_ascii=False),board['updated_at']))
    return board


def _replace(board,expected):
    board={**board,'version':expected+1,'updated_at':time.time()}
    with backend()._connect() as con:
        changed=con.execute("UPDATE analysis_boards SET document=?,updated_at=? WHERE id=? AND json_extract(document,'$.version')=?",
                            (json.dumps(board,ensure_ascii=False,allow_nan=False),board['updated_at'],board['id'],expected)).rowcount
    if not changed:
        raise BoardConflict('面板已在其他窗口更新，当前修改已保留，请重新打开面板读取最新版本。')
    return board


def save_board(board_id,payload:BoardSave):
    board=get_board(board_id)
    if board['version']!=payload.version:
        raise BoardConflict('面板已更新，当前修改已保留，请重新打开面板后再编辑。')
    old={item['id']:item for item in board['items']}
    if len({item.id for item in payload.items})!=len(payload.items):
        raise ValueError('面板项目编号重复。')
    items=[]
    for item in payload.items:
        original=old.get(item.id,{})
        if item.kind=='chart' and original.get('kind')!='chart' or original and original['kind']!=item.kind:
            raise ValueError('请通过“加入面板”添加图表。')
        items.append({**original,**item.model_dump(exclude_none=True)})
    if not payload.title.strip():
        raise ValueError('请输入面板名称。')
    return _replace({**board,'title':payload.title.strip(),'items':items},payload.version)


def add_chart(board_id,source:ChartSource):
    board=get_board(board_id)
    data=chart_source(source)
    fingerprint=data['key']
    if any(item.get('source_key')==fingerprint for item in board['items']):
        return board
    board['items'].append(_chart_item(data))
    if len(board['items'])>100:
        raise ValueError('面板素材已达100项，请新建面板。')
    return _replace(board,board['version'])


def chart_source(source:ChartSource):
    message=next((m for m in backend().get_messages(source.session_id) if m['id']==source.message_id and m['role']=='assistant'),None)
    metadata=message['metadata'] if message else {}
    from app.report_presentation import saved_result_source
    metadata=saved_result_source(metadata,source.source_path,source.step_id)
    reference=source.step_id
    if len(source.source_path)==4 and source.source_path[0]=='workflow':
        reference=message['metadata']['workflow']['steps'][source.source_path[2]]['id']
    spec=metadata.get('chart_spec')
    if not spec or not spec.get('data'):
        raise ValueError('这条已保存回复没有可加入的图表，请等待结果保存后重试。')
    total=metadata.get('row_count',0)
    shown=len(metadata.get('rows',[]))
    scope=f'图示为已返回前{shown}行；查询共{total}行，非全量结果。' if total>shown and shown else '仅依据图示数据。'
    check=next((check for check in metadata.get('checks',[]) if check.get('id')=='chart_source' and check.get('status')=='passed'),None)
    if check:
        scope=check['scope']
    return {'spec':deepcopy(spec),'scope':scope,
            'key':hashlib.sha256((f'{source.session_id}:{source.message_id}'+(':'+reference if reference else
                ':'+json.dumps(source.source_path) if source.source_path else '')).encode()).hexdigest()}


def _chart_item(source):
    title=source['spec'].get('layout',{}).get('title','分析图表')
    if isinstance(title,dict):
        title=title.get('text','分析图表')
    item=BoardItem(id=uuid.uuid4().hex,kind='chart',title=str(title)[:200]).model_dump()
    return {**item,'spec':deepcopy(source['spec']),'scope':source['scope'],'source_key':source['key']}


def write_tool_board(board_id,title,sources,note,expected_version=None):
    board=get_board(board_id) if board_id else None
    if board and board['version']!=expected_version:
        raise BoardConflict('面板版本已变化，请重新读取最新面板后修改。')
    old=board['items'] if board else []
    additions=[_chart_item(source) for source in sources if not any(item.get('source_key')==source['key'] for item in old)]
    if note:
        additions.append(BoardItem(id=uuid.uuid4().hex,kind='text',text=note,width=520,height=180).model_dump())
    if len(old)+len(additions)>100:
        raise ValueError('面板素材已达100项，请新建面板。')
    bottom=max((item['y']+item['height']+32 for item in old if item['placed']),default=40)
    for index,item in enumerate(additions):
        item.update(placed=True,x=40+(index%2)*552,y=bottom+(index//2)*432)
        BoardItem.model_validate(item)
    if board is None:
        BoardName(title=title)
        if not title.strip():
            raise ValueError('请输入面板名称。')
        return _insert({'id':uuid.uuid4().hex,'title':title.strip(),'version':1,'updated_at':time.time(),'items':additions})
    if title.strip():
        board['title']=title.strip()
    board['items'].extend(additions)
    return _replace(board,board['version'])


def delete_board(board_id):
    get_board(board_id)
    with backend()._connect() as con:
        con.execute('DELETE FROM analysis_boards WHERE id=?',(board_id,))
