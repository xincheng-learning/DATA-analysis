"""Replay fixed-input chart results in the existing disposable QA application."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]


def seed(directory,run,output):
    import httpx
    import pandas as pd
    from app.config import settings
    from app import session
    from app.tools.query_results import save_result
    target=directory.resolve()
    if target!=directory.absolute() or not target.is_relative_to(ROOT/'.local') or not target.name.startswith('qa-') or target==settings.local_data_dir.resolve():
        raise ValueError('只允许已经存在的独立QA目录，禁止真实聊天目录。')
    if not (target/'datapilot.sqlite3').is_file() or output.exists():
        raise ValueError('保留原记录；QA库必须已经存在，输出必须是新文件。')
    settings.local_data_dir=target;settings.app_mode='local';settings.session_backend='sqlite'
    store=session.SessionStore(backend=session.SQLiteBackend(target/'datapilot.sqlite3'));session._store=store
    with httpx.Client(base_url='http://127.0.0.1:8101',timeout=20,headers={'Origin':'http://127.0.0.1:3100'}) as api:
        before_sessions=api.get('/sessions').json();before_boards=api.get('/boards').json();entries=[]
        assert {r['session_id'] for r in before_sessions}=={r['id'] for r in store._backend.list_sessions()}, 'QA API and target database must match'
        for file in sorted((run/'cases').glob('SM*.json')):
            r=json.loads(file.read_text(encoding='utf-8'));ident=r['id'];sid='qa-chart24-20261115-'+ident
            if store.get(sid):raise ValueError('已有本轮QA数据，不覆盖：'+sid)
            assert r.get('chart_spec',{}).get('data'), ident
            s=store.get_or_create(sid);s.title='24图复验 · '+ident+' '+r['name'];store._backend.save(s)
            result_id=save_result(sid,pd.DataFrame(r['rows'],columns=r['columns']))
            metadata={'question':r['question'],'answer':'真实选图结果的固定输入回放；未重新运行SQL分析与报告。',
                'route':'viz','columns':r['columns'],'rows':r['rows'],'row_count':len(r['rows']),
                'result_id':result_id,'chart_spec':r['chart_spec'],'checks':r.get('checks',[]),
                'chart_acceptance_scope':'fixed-input replay; no new model call, SQL-agent or semantic certification'}
            store._backend.save_messages(sid,[('user',r['question'],{}),('assistant',metadata['answer'],metadata)])
            mid=store._backend.get_messages(sid)[-1]['id']
            response=api.post('/boards',json={'title':s.title});response.raise_for_status();board=response.json()
            response=api.post('/boards/'+board['id']+'/charts',json={'session_id':sid,'message_id':mid});response.raise_for_status();board=response.json()
            item=board['items'][0];item.update(placed=True,x=32,y=32,width=1100,height=800 if ident=='SM20' else 700)
            response=api.put('/boards/'+board['id'],json=board);response.raise_for_status();board=response.json()
            entries.append({'id':ident,'name':r['name'],'session_id':sid,'message_id':mid,'result_id':result_id,
                'board_id':board['id'],'board_title':board['title'],'source_rows':r['rows'],'source_spec':r['chart_spec']})
        assert len(entries)==24
        after_sessions=api.get('/sessions').json();after_boards=api.get('/boards').json()
        assert {r['session_id'] for r in before_sessions}<={r['session_id'] for r in after_sessions}
        assert {r['id'] for r in before_boards}<={r['id'] for r in after_boards}
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'scope':'actual application fixed-input replay; not a new complete agent analysis',
            'original_session_ids':[r['session_id'] for r in before_sessions],'original_board_ids':[r['id'] for r in before_boards],
            'entries':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'charts':len(entries),'original_sessions_preserved':len(before_sessions),'original_boards_preserved':len(before_boards)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--qa-directory',type=Path,required=True);parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();seed(a.qa_directory.absolute(),a.run,a.output)
