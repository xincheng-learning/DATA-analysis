"""Install/start/stop the optional local visualization service, never the base app."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

import httpx
import psycopg
from psycopg import sql

from app import superset
from app.tools.data_workbench import _write_json

COMPOSE=['docker','compose','-f',str(ROOT/'compose.superset.yaml')]


def record(status,message):
    directory=superset.runtime_dir()
    directory.mkdir(parents=True,exist_ok=True)
    _write_json(directory/'state.json',{'status':status,'message':message,'updated_at':time.time(),'pid':os.getpid()})
    print(message,flush=True)


def run(arguments,timeout=600):
    env={**os.environ,'DATAPILOT_SUPERSET_ROOT':superset.runtime_dir().as_posix()}
    result=subprocess.run(arguments,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,
                          **({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}))
    if result.returncode:
        # The private build log is useful without copying credentials into API errors.
        (superset.runtime_dir()/'service-error.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        raise RuntimeError('本机高级服务操作失败，请查看高级可视化状态和本机 service-error.log。')
    return result.stdout


def prepare():
    bundle=superset.initialize_credentials()
    private=superset.runtime_dir()/'secrets'
    if sys.platform=='win32':
        account=run(['whoami'],timeout=10).strip()
        run(['icacls',str(private),'/inheritance:r','/grant:r',account+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],timeout=15)
    env_file=ROOT/'frontend/.env.local'
    original=env_file.read_text('utf-8') if env_file.exists() else ''
    lines=[line for line in original.splitlines() if not line.startswith('DATAPILOT_SUPERSET_DIR=')]
    lines.append('DATAPILOT_SUPERSET_DIR="'+superset.runtime_dir().as_posix()+'"')
    env_file.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return bundle


def database(bundle):
    with psycopg.connect(host='127.0.0.1',port=55433,dbname='postgres',user='datapilot_admin',
                        password=bundle['postgres_password'],connect_timeout=5,autocommit=True) as con:
        for role,key in [('superset_meta','metadata_password'),('datapilot_writer','writer_password'),('datapilot_reader','reader_password')]:
            if not con.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                con.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(bundle[key])))
        for name,owner in [('superset_meta','superset_meta'),('datapilot_visual','datapilot_writer')]:
            if not con.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                con.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(owner)))
        con.execute('ALTER ROLE datapilot_reader SET default_transaction_read_only=on')
    with psycopg.connect(host='127.0.0.1',port=55433,dbname='datapilot_visual',user='datapilot_admin',
                        password=bundle['postgres_password'],connect_timeout=5) as con:
        con.execute('REVOKE ALL ON DATABASE datapilot_visual FROM PUBLIC')
        con.execute('GRANT CONNECT ON DATABASE datapilot_visual TO datapilot_writer,datapilot_reader')
        con.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        con.execute('CREATE SCHEMA IF NOT EXISTS exports AUTHORIZATION datapilot_writer')
        con.execute('GRANT USAGE ON SCHEMA exports TO datapilot_reader')
        con.execute('GRANT SELECT ON ALL TABLES IN SCHEMA exports TO datapilot_reader')
        con.execute('ALTER DEFAULT PRIVILEGES FOR ROLE datapilot_writer IN SCHEMA exports GRANT SELECT ON TABLES TO datapilot_reader')


def setup():
    run(['docker','version','--format','{{.Server.Version}}'],timeout=20)
    bundle=prepare()
    record('starting','准备可选 Superset 镜像与独立数据库。')
    run([*COMPOSE,'build','web'],timeout=1200)
    run([*COMPOSE,'up','-d','--wait','database'],timeout=120)
    database(bundle)
    record('starting','初始化 Superset 权限与本机只读数据源。')
    run([*COMPOSE,'run','--rm','--no-deps','web','/app/.venv/bin/superset','db','upgrade'],timeout=180)
    run([*COMPOSE,'run','--rm','--no-deps','web','/app/.venv/bin/superset','init'],timeout=180)
    run([*COMPOSE,'run','--rm','--no-deps','web','/app/.venv/bin/python','/app/datapilot/bootstrap.py'],timeout=120)
    run([*COMPOSE,'up','-d','web','mcp'],timeout=120)
    start()


def start():
    names=['datapilot-superset-'+service+'-1' for service in ('database','web','mcp')]
    existing=run(['docker','ps','-a','--filter','label=com.docker.compose.project=datapilot-superset','--format','{{.Names}}'],timeout=20).splitlines()
    if not set(names)<=set(existing):
        raise RuntimeError('现有高级服务尚未准备完整。日常启动不会自动构建或迁移，请先完成首次准备。')
    record('starting','正在启动现有高级服务，保留数据库与已保存视图。')
    for name in names:
        run(['docker','start',name],timeout=30)
        if name==names[0]:
            for _ in range(30):
                if run(['docker','inspect','--format','{{.State.Health.Status}}',name],timeout=5).strip()=='healthy':
                    break
                time.sleep(1)
            else:
                raise RuntimeError('数据库尚未就绪，未启动网页和 MCP；请检查现有容器健康状态。')
    with httpx.Client(timeout=3,trust_env=False) as client:
        for _ in range(45):
            try:
                if client.get('http://127.0.0.1:8088/health').status_code==200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Superset 启动尚未完成，请检查本机服务日志。')
    for _ in range(5):
        try:
            if superset.mcp_call('health_check',timeout=5).get('status')=='healthy':
                record('ready','Superset 已启动；高级视图按独立结果快照保存。')
                return
        except ValueError:
            pass
        time.sleep(1)
    raise RuntimeError('Superset MCP 尚未就绪，请检查本机服务日志。')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','setup','start','stop'])
    args=parser.parse_args()
    try:
        if args.action=='prepare':
            prepare()
            record('configured','本机高级服务配置已准备，密钥未输出。')
        elif args.action=='start':
            start()
        elif args.action=='setup':
            setup()
        else:
            run([*COMPOSE,'stop'],timeout=120)
            record('stopped','Superset 已停止，已保存高级视图保留。')
    except Exception as exc:
        record('error',str(exc) if isinstance(exc,RuntimeError) else '高级服务准备失败，请检查 Docker Desktop 和本机权限。')
        raise SystemExit(1) from None
