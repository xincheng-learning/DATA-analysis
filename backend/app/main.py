"""FastAPI app for DataPilot.

v1 endpoints:
    GET  /health             — liveness probe
    GET  /schema             — current dataset schema (debug aid)
    POST /ask                — ask one question, get one answer
    GET  /sessions/{sid}     — debug: inspect what the agent remembers

Safety:
    - Per-IP rate limiting via slowapi
    - CORS allow-list (configurable via env)
    - SQL execution has a hard timeout (see tools/sql.py)
    - Mutating SQL is rejected at the tool layer
"""
import logging
import asyncio
import json
import uuid
import threading
import subprocess
import sqlite3
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field, field_validator

from app.agent.graph import record_query_after_run, run_agent
from app.agent.investigation import run_investigation, investigation_to_state
from app.execution import AnalysisCancelled, ModelBudgetExceeded, UnexpectedModelReply, checkpoint, commit_action, controlled_node, finish_task, task_evidence, tasks, partial_result, save_partial_result, draft_answer
from app.auth import AuthMiddleware
from app import database
from app import chat_storage
from app import folder_picker
from app import boards, attachments, clarification
from app.config import settings
from app.llm import LLMConfigurationError, check_model_connection, get_chat_llm, reset_cache
from app.context_memory import ContextMemoryError
from app.model_settings import (
    ModelSettingsInput,
    SecretStorageError,
    get_model_status,
    resolve_model_input,
    save_model_settings,
    settings_lock,
)
from app.schemas import AskRequest, AskResponse, MessageOut, SessionDetail, SessionListItem
from app.session import SQLiteBackend, SupabaseBackend, get_session_store
from app.tools.dataset_manager import (
    MemoryBudgetExceeded,
    get_dataset_manager,
)
from app.tools.file_storage import local_session_dir
from app.tools.ingest import MAX_FILES_PER_BATCH, IngestionError, validate_and_convert


def _verify_session_ownership(sid: str, user_id: str, allow_creation: bool = False) -> None:
    """Verify the authenticated user owns this session. Raises 403 if not.

    If allow_creation=True and the session doesn't exist, returns silently
    (the caller will create it with the correct user_id).
    """
    if settings.app_mode == "local":
        try:
            local_session_dir(sid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="对话编号无效") from exc
        if not allow_creation and get_session_store().get(sid) is None:
            raise HTTPException(status_code=404, detail="对话不存在")
        return
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        return  # In-memory backend has no user scoping
    res = backend._client.table("sessions").select("user_id").eq("id", sid).execute()
    if not res.data:
        if allow_creation:
            return  # Session will be created by the caller
        raise HTTPException(status_code=404, detail="对话不存在")
    owner = res.data[0].get("user_id")
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问此对话")

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("datapilot")
data_lock = threading.RLock()


def data_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with data_lock:
            return function(*args, **kwargs)
    return wrapped

# --- FastAPI app + middleware ----------------------------------------------

app = FastAPI(
    title="DataPilot",
    version="0.5.0",
    description="Conversational data analysis for CSV files.",
)
from app.superset_routes import router as superset_router
app.include_router(superset_router)

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Auth middleware — validates JWT, attaches user_id to request.state
app.add_middleware(AuthMiddleware)

# CORS middleware — added LAST so it's the outermost in Starlette's stack.
# This ensures CORS headers are present on ALL responses, including 401s from auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Lifecycle --------------------------------------------------------------


@app.on_event("startup")
def _warm_up() -> None:
    logger.info(
        "Rate limits: default=%s, /ask=%s",
        settings.rate_limit_default,
        settings.rate_limit_ask,
    )
    # Ensure Supabase Storage bucket exists
    try:
        from app.tools.file_storage import ensure_bucket_exists
        ensure_bucket_exists()
    except Exception as e:
        logger.warning("Could not ensure storage bucket: %s", e)


# --- Endpoints --------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def safe_settings_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(("/settings/model", "/settings/database")):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse(status_code=415, content={"detail": "设置必须使用 JSON 请求。"}, headers={"Cache-Control": "no-store"})
        return JSONResponse(status_code=422, content={"detail": "配置格式不正确：模型外部接口须使用 HTTPS；数据库目前仅接受本机地址。请检查填写内容。"}, headers={"Cache-Control": "no-store"})
    return await request_validation_exception_handler(request, exc)


def _require_local_settings(request: Request, *, mutation: bool = False):
    if settings.app_mode != "local":
        raise HTTPException(status_code=403, detail="此功能仅在本地单用户模式开放。")
    if mutation and request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(status_code=415, detail="设置必须使用 JSON 请求。")


def _prepare_attachments(req):
    try:
        if req.cleaning_reply and (settings.app_mode!='local' or not req.session_id or req.clarification_reply or req.quote or req.board or req.conversation or req.charts or req.images):
            raise ValueError('请在方案所属聊天中单独确认清洗，不要混入其他附件。')
        clarification.prepare_reply(req)
        attachments.prepare_context(req)
    except (ValueError,KeyError) as exc:
        raise HTTPException(400,str(exc).strip("'")) from None


def _board_action(request,action,*args,mutation=False):
    _require_local_settings(request,mutation=mutation)
    try:
        return action(*args)
    except boards.BoardConflict as exc:
        raise HTTPException(409,str(exc)) from None
    except KeyError:
        raise HTTPException(404,'面板不存在或已删除。') from None
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None


@app.get('/boards')
@data_operation
def list_analysis_boards(request:Request):
    return _board_action(request,boards.list_boards)


@app.post('/boards')
@data_operation
def create_analysis_board(request:Request,body:boards.BoardName):
    return _board_action(request,boards.create_board,body.title,mutation=True)


@app.get('/boards/{board_id}')
@data_operation
def get_analysis_board(request:Request,board_id:str):
    return _board_action(request,boards.get_board,board_id)


@app.put('/boards/{board_id}')
@data_operation
def save_analysis_board(request:Request,board_id:str,body:boards.BoardSave):
    return _board_action(request,boards.save_board,board_id,body,mutation=True)


@app.post('/boards/{board_id}/charts')
@data_operation
def add_board_chart(request:Request,board_id:str,body:boards.ChartSource):
    _verify_session_ownership(body.session_id,request.state.user_id)
    return _board_action(request,boards.add_chart,board_id,body,mutation=True)


@app.delete('/boards/{board_id}')
@data_operation
def delete_analysis_board(request:Request,board_id:str):
    _board_action(request,boards.delete_board,board_id,mutation=True)
    return {'status':'deleted'}


@app.get('/settings/storage')
@data_operation
def storage_status(request: Request):
    _require_local_settings(request)
    try:
        return JSONResponse(chat_storage.storage_status(get_session_store()), headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.put('/settings/storage')
def update_storage(request: Request, config: chat_storage.StorageInput):
    _require_local_settings(request, mutation=True)
    if not data_lock.acquire(blocking=False):
        raise HTTPException(409, '有数据操作正在进行，请等待完成后再切换存储位置。')
    try:
        if tasks.has_active_tasks():
            raise HTTPException(409, '有分析任务正在运行，请等待完成或停止后再切换存储位置。')
        result = chat_storage.copy_and_switch(config.directory, get_session_store(), get_dataset_manager())
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        logger.exception('Chat storage copy failed; original location remains active')
        raise HTTPException(400, '复制或保存未完成，仍使用原目录。请检查文件夹权限、磁盘空间；目标目录中的副本可保留核对。') from None
    finally:
        data_lock.release()


@app.post('/settings/storage/browse')
def browse_storage(request: Request, config: chat_storage.BrowseInput):
    _require_local_settings(request, mutation=True)
    initial = Path(config.directory) if config.directory else chat_storage.chat_directory()
    if not initial.is_absolute() or not initial.is_dir():
        initial = chat_storage.chat_directory()
    try:
        return JSONResponse({'directory': folder_picker.choose_directory(initial)}, headers={'Cache-Control': 'no-store'})
    except folder_picker.PickerBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.exception('Native folder picker failed')
        raise HTTPException(503, '当前无法打开本机文件夹窗口，请重试或直接填写完整路径。') from None


@app.get("/settings/model")
@limiter.limit(settings.rate_limit_default)
def model_status(request: Request):
    _require_local_settings(request)
    try:
        return JSONResponse(get_model_status(), headers={"Cache-Control": "no-store"})
    except SecretStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None


@app.put("/settings/model")
@limiter.limit(settings.rate_limit_default)
def update_model_settings(request: Request, config: ModelSettingsInput):
    _require_local_settings(request, mutation=True)
    if not data_lock.acquire(blocking=False):
        raise HTTPException(409, '有分析或数据操作正在进行，请结束后再调整模型和思考强度。')
    try:
        if tasks.has_active_tasks():
            raise HTTPException(409, '有分析任务正在运行，请等待完成或停止后再调整模型和思考强度。')
        with settings_lock:
            save_model_settings(config)
            reset_cache()
            return JSONResponse(get_model_status(), headers={"Cache-Control": "no-store"})
    except SecretStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        data_lock.release()


@app.post("/settings/model/test")
@limiter.limit(settings.rate_limit_ask)
def test_model_settings(request: Request, config: ModelSettingsInput):
    _require_local_settings(request, mutation=True)
    try:
        candidate = resolve_model_input(config)
    except SecretStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        check_model_connection(candidate)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            detail = "密钥无效或无权使用此模型，请检查密钥和模型权限。"
        elif status == 429:
            detail = "服务商额度不足或请求过于频繁，请检查账户额度后重试。"
        elif status == 402:
            detail = "服务商账户余额不足（HTTP 402），请检查 API 账户余额。"
        elif status in (400, 422):
            detail = f"服务商拒绝了请求参数（HTTP {status}）。请检查模型名称及工具调用参数兼容性；不是单纯的网络连接失败。"
        elif status == 404:
            detail = "未找到接口或模型，请检查基础地址和模型名称。"
        elif "Timeout" in type(exc).__name__:
            detail = "连接超时，请检查网络、接口地址或本地模型是否已启动。"
        elif isinstance(exc, ValueError):
            detail = ("思考模式已开启，但未获得有效工具结果。请检查输出是否被截断，或自行调整思考模式；项目未自动关闭思考或补发请求。"
                      if candidate.thinking_mode == "enabled" else "接口返回结果未通过工具调用格式检查，请确认模型支持工具调用，或检查输出是否被截断。")
        else:
            detail = "连接或工具调用检查失败，请确认接口、模型和网络设置。"
        logger.warning("Model connection test failed: status=%s, exception=%s", status, type(exc).__name__)
        raise HTTPException(status_code=502, detail=detail) from None
    return JSONResponse({"message": "连接和工具调用检查通过。尚未保存，请点击保存并生效。"}, headers={"Cache-Control": "no-store"})


@app.get("/settings/database")
def database_status(request: Request):
    _require_local_settings(request)
    try:
        return JSONResponse(database.database_status(), headers={"Cache-Control": "no-store"})
    except SecretStorageError as exc:
        raise HTTPException(500, str(exc)) from None


@app.put("/settings/database")
def update_database(request: Request, config: database.DatabaseInput):
    _require_local_settings(request, mutation=True)
    try:
        database.save_database(config)
        return JSONResponse(database.database_status(), headers={"Cache-Control": "no-store"})
    except (ValueError, SecretStorageError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/settings/database/test")
def check_database(request: Request):
    _require_local_settings(request, mutation=True)
    try:
        return {"tables": database.list_database_tables()}
    except Exception:
        raise HTTPException(502, "无法读取数据库表，请检查数据库是否启动、连接设置和只读账号权限。") from None


@app.post("/sessions/{sid}/database/import")
@data_operation
def import_database(request: Request, sid: str, body: database.SnapshotInput):
    _require_local_settings(request, mutation=True)
    _verify_session_ownership(sid, request.state.user_id, allow_creation=True)
    _require_idle(sid)
    store = get_session_store()
    store.get_or_create(sid, user_id=request.state.user_id)
    manager = get_dataset_manager()
    dataset = manager.get_or_create(sid, local_session_dir(sid))
    try:
        imported = database.import_snapshots(dataset, manager, body.tables)
    except (ValueError, IngestionError, MemoryBudgetExceeded) as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(502, "导入失败，请检查数据库连接、表权限和数据类型；未返回连接密码。") from None
    names = ", ".join(item["table_name"] for item in imported)
    if isinstance(store._backend, SQLiteBackend):
        store._backend.set_title(sid, f"数据库：{names}", only_if_empty=True)
        store._backend.save_message(sid, "user", f"已导入 PostgreSQL 快照：{names}", {"type": "upload", "files": imported})
    return {"session_id": sid, "uploaded": imported}


@app.get("/health")
@limiter.limit(settings.rate_limit_default)
def health(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema")
@limiter.limit(settings.rate_limit_default)
@data_operation
def schema(request: Request) -> dict[str, str]:
    """Show the schema for the user's most recent session with uploads."""
    user_id = request.state.user_id
    store = get_session_store()
    backend = store._backend
    if isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        sessions = backend.list_sessions(user_id=user_id)
        if sessions:
            mgr = get_dataset_manager()
            for s in sessions:
                ds = mgr.get(s["id"])
                if ds and ds.files:
                    return {"schema": ds.schema_summary()}
    return {"schema": "No data uploaded yet. Upload a CSV or Excel file to see the schema."}


@app.post("/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit_ask)
@data_operation
def ask(request: Request, req: AskRequest = Body(...)) -> AskResponse:
    user_id = request.state.user_id
    # If a session_id is provided, verify the user owns it
    if req.session_id:
        _verify_session_ownership(req.session_id, user_id)
        _require_idle(req.session_id)
    _prepare_attachments(req)
    try:
        if settings.app_mode == "local":
            get_chat_llm()  # Validate configuration before creating an empty session.
        return _analyse(req, user_id)
    except LLMConfigurationError as e:
        raise HTTPException(
            status_code=503,
            detail=("尚未配置模型 API。请点击侧边栏或顶部的“模型设置”，填写接口地址、模型名称和 API 密钥后保存。"
                    if settings.app_mode == "local" else "模型服务尚未配置，请联系管理员检查模型连接设置。"),
        ) from e
    except ContextMemoryError as e:
        raise HTTPException(status_code=422,detail=str(e)) from None
    except UnexpectedModelReply as e:
        raise HTTPException(status_code=502,detail=str(e)) from None
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent crashed")
        raise HTTPException(
            status_code=500,
            detail="处理请求时发生错误，请稍后重试。",
        ) from e


def _analyse(req: AskRequest, user_id: str) -> AskResponse:
    session_id = req.session_id or uuid.uuid4().hex
    get_session_store().get_or_create(session_id, user_id=user_id)
    if settings.app_mode == 'local' and not req._attachments:
        from app.cleaning_assistant import apply_chat_confirmation, chat_confirmation_target
        backend = get_session_store()._backend
        data = get_dataset_manager().get(session_id)
        if isinstance(backend, SQLiteBackend):
            messages=backend.get_messages(session_id)
            target = chat_confirmation_target(req.question, data, messages, req.cleaning_reply)
            if target:
                saved=(messages[-1].get('metadata',{}).get('workflow') or {}) if messages else {}
                if target.get('plan_id') and saved.get('status')=='waiting':
                    from app.agent.workflow import resume_cleaning_workflow
                    def apply_step():
                        applied=apply_chat_confirmation(data,target,req.question)
                        save_partial_result(applied)
                        return applied
                    applied=commit_action(apply_step)
                    if applied.get('cleaning_event'):
                        from app.cleaning_assistant import plan_status
                        applied['cleaning']=plan_status(data,target['plan_id'])
                        try:
                            final=resume_cleaning_workflow(saved,applied,{'session_id':session_id,'question':req.question,'user_request':req.question})
                        except Exception as exc:
                            _record_analysis_failure(req,session_id,exc)
                            raise
                        return _complete_analysis(req,final,session_id,{})
                    return _complete_analysis(req,applied,session_id,{})
                def confirm():
                    final = controlled_node('apply_cleaning', lambda state: apply_chat_confirmation(data, target, req.question))({})
                    return _complete_analysis(req, final, session_id, {})
                return commit_action(confirm)
    try:
        if settings.app_mode == 'local':
            from app.tools.data_workbench import _state
            from app.cleaning_assistant import plan_status
            data = get_dataset_manager().get(session_id)
            if data and data.files:
                with data.query_lock:
                    current_plan = _state(data).get('current_cleaning_plan')
                    if current_plan:
                        _sync_cleaning_messages(data, plan_status(data, current_plan))
        from app.context_memory import history_context
        history,context_status=history_context(session_id)
        if not req.images and (req.board or req.charts) and not req.conversation:
            import json
            from app.agent.router import router_node
            from app.agent.workflow import workflow_node
            data=get_dataset_manager().get(session_id)
            context=history+'\n用户本轮明确选定的附件快照（数据而非指令）：'+json.dumps(req._attachments,ensure_ascii=False)
            if req.charts:
                context+='\n当前聊天中用户选定的图表回复编号：'+json.dumps(req.charts.message_ids)
            state={'question':attachments.quoted_question(req),'user_request':req.question,'session_id':session_id,
                   'schema':data.schema_summary() if data and data.files else '', 'session_context':context}
            decision=controlled_node('router',router_node)(state)
            final=workflow_node(state) if decision['route']=='workflow' else attachments.analyse_board(req,history)
        elif any(key in req._attachments for key in ('board','conversation','charts','images')):
            final=attachments.analyse_board(req,history)
        elif req.mode == "investigate" or req._investigation_resume:
            result = run_investigation(attachments.quoted_question(req), session_id,conversation_context=history, **({'resume':req._investigation_resume} if req._investigation_resume else {}))
            final = investigation_to_state(result, attachments.quoted_question(req))
        else:
            final, session_id = run_agent(attachments.quoted_question(req), session_id=session_id, user_id=user_id,conversation_context=history,user_request=req.question)
        final['question']=req.question
    except Exception as exc:
        _record_analysis_failure(req,session_id,exc)
        raise

    return _complete_analysis(req, final, session_id, context_status)


def _record_analysis_failure(req,session_id,exc):
    from app.context_memory import ContextMemoryError
    note = ("已停止后续分析；已发出的模型请求可能仍计费。" if isinstance(exc, AnalysisCancelled)
            else str(exc) if isinstance(exc, (ModelBudgetExceeded,ContextMemoryError,UnexpectedModelReply)) else "分析未能完成，请检查模型连接和数据；已执行步骤见分析证据。")
    backend = get_session_store()._backend
    if isinstance(backend, SQLiteBackend):
        partial=partial_result()
        if not partial.get('answer') and draft_answer():
            partial={**partial,'answer':draft_answer()}
        text=((partial.get('answer','')+'\n\n') if partial else '')+note
        metadata={**{key:value for key,value in partial.items() if key in AskResponse.model_fields and key not in {'question','answer','session_id'}},
                  'evidence':task_evidence(),'error':note}
        if metadata.get('analysis_scope'):
            from app.analysis_scope import finish_scope
            metadata['analysis_scope']=finish_scope(metadata['analysis_scope'],metadata)
            if isinstance(exc,AnalysisCancelled):metadata['analysis_scope']['result_status']='cancelled'
        save_partial_result({**metadata,'question':req.question,'answer':text,'session_id':session_id})
        backend.save_messages(session_id, [('user', req.question, clarification.user_metadata(req)),
            ('assistant', text, metadata)])


def _complete_analysis(req, final, session_id, context_status):
    checkpoint()
    final['question'] = req.question
    if not final.get('analysis_process'):
        from app.analysis_pipeline import run_delivery
        try:
            final=run_delivery(final)
        except AnalysisCancelled as exc:
            _record_analysis_failure(req,session_id,exc)
            raise
    clarification.complete_question(req, final)
    dataset = get_dataset_manager().get(session_id) if final.get('workflow') or not any(key in req._attachments for key in ('board','conversation','charts','images')) else None
    final['context_snapshot']=req._attachments
    final['context_status']=context_status
    final["data_sources"] = [{"table_name": f.table_name, "filename": f.original_filename, "rows": f.row_count,
                              "source": f.source, "data_version": f.data_version} for f in dataset.files] if dataset else []
    from app.tools.data_workbench import workbench_snapshot
    final['data_version'] = workbench_snapshot(dataset) if dataset and dataset.files else None
    if req.images and final.get('route')!='clarify':
        from app.sql_proposals import proposal_from_reply
        final['sql_proposal']=proposal_from_reply(final.get('recommended_sql'),get_dataset_manager().get(session_id))
    if dataset and dataset.files and (final.get('sql') or final.get('investigation')):
        from app.metrics import metric_context
        final['metric_context'] = {**final.get('metric_context',{}),**metric_context(dataset)}
    if final.get('show_data_preview') and dataset and dataset.files:
        final['table_previews']=dataset.preview()
        for table in final['table_previews']:
            table['preview']=[{column:row.get(column) for column in table['columns'][:6]} for row in table['preview'][:3]]
    from app.report_presentation import build_analysis_report
    report = build_analysis_report(final)
    if report:
        final['analysis_report'] = report
        final['answer'] = report['answer']
    brief=final.get('analysis_brief') or {}
    if dataset and dataset.files and any(spec.get('metric_ids') or spec.get('result_columns') for spec in brief.get('acceptance',[])):
        from app.analysis_scope import create_scope,finish_scope
        scope=create_scope(brief,get_session_store()._backend.get_messages(session_id),data_version=final['data_version'],
            table_versions={file.table_name:file.data_version for file in dataset.files})
        final['analysis_scope']=finish_scope(scope,final)
    final["evidence"] = task_evidence()
    if final["evidence"].get("budget_exhausted"):
        final["answer"] = final.get("answer", "") + "\n模型调用预算已用尽，以上仅为已获得的证据；未完成的分析仍存在缺口。"
    if context_status.get('degraded'):
        final['answer']=final.get('answer','')+'\n\n会话记忆提示：'+context_status['warning']
    response = AskResponse(
        question=req.question, answer=final.get("answer", ""), session_id=session_id,
        **{key: final[key] for key in AskResponse.model_fields if key in final and key not in {"question", "answer", "session_id", "retry_count"}},
        retry_count=len(final.get('previous_attempts', []))+max(0,len(final.get('python_attempts', []))-1),
    )
    finish_task(response.model_dump(mode="json"), lambda: _persist_analysis(req, final, response))
    return response


def _persist_analysis(req: AskRequest, final: dict, response: AskResponse):
    session_id = response.session_id
    record_query_after_run(session_id, final)

    # Persist full messages for frontend chat history.
    store = get_session_store()
    backend = store._backend
    if isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        # Auto-title: set session title to first question if not already titled
        try:
            if isinstance(backend, SQLiteBackend):
                backend.set_title(session_id, req.question, only_if_empty=True)
            else:
                res = backend._client.table("sessions").select("title").eq("id", session_id).execute()
                if res.data and not res.data[0].get("title"):
                    backend._client.table("sessions").update(
                        {"title": req.question[:100]}
                    ).eq("id", session_id).execute()
        except Exception:
            pass

        metadata = response.model_dump(mode="json", exclude={"question", "answer", "session_id"})
        messages = [('user', req.question, clarification.user_metadata(req)),
                    ('assistant', final.get('answer', ''), metadata)]
        if isinstance(backend, SQLiteBackend):
            backend.save_messages(session_id, messages)
        else:
            for role, content, meta in messages:
                backend.save_message(session_id, role, content, meta)


@app.post("/tasks")
@limiter.limit(settings.rate_limit_ask)
@data_operation
def start_analysis(request: Request, req: AskRequest = Body(...)):
    _require_local_settings(request, mutation=True)
    owner = request.state.user_id
    if req.session_id:
        _verify_session_ownership(req.session_id, owner)
    _prepare_attachments(req)
    if req.mode == "investigate" and not (req.board or req.conversation or req.charts or req.images):
        dataset = get_dataset_manager().get(req.session_id) if req.session_id else None
        if dataset is None or not dataset.files:
            raise HTTPException(400, "调查模式需要先上传文件或导入数据库表。")
    try:
        get_chat_llm()
    except LLMConfigurationError:
        raise HTTPException(503, "尚未配置模型 API，请点击“模型设置”填写并保存。") from None
    req = req.model_copy(update={"session_id": req.session_id or uuid.uuid4().hex})
    try:
        return tasks.submit(owner, req.session_id, lambda: _analyse(req, owner), settings.max_model_calls_per_task)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/tasks/{task_id}")
def read_analysis(request: Request, task_id: str):
    _require_local_settings(request)
    try:
        return tasks.read(task_id, request.state.user_id)
    except KeyError:
        raise HTTPException(404, "任务记录不存在，后端可能已重启，请查看对话中已保存的证据。") from None


@app.get('/tasks/{task_id}/events')
async def stream_analysis(request: Request, task_id: str):
    _require_local_settings(request)
    owner = request.state.user_id
    try:
        tasks.read(task_id, owner)
    except KeyError:
        raise HTTPException(404, '任务记录不存在，后端可能已重启，请查看已保存的对话。') from None

    async def events():
        previous = None
        heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            snapshot = tasks.read(task_id, owner)
            if snapshot != previous:
                yield 'event: task\ndata: ' + json.dumps(snapshot, ensure_ascii=False) + '\n\n'
                previous = snapshot
            if snapshot['status'] in {'succeeded', 'failed', 'cancelled'}:
                return
            now = asyncio.get_running_loop().time()
            if now - heartbeat >= 10:
                yield ': keep-alive\n\n'
                heartbeat = now
            await asyncio.sleep(.1)
    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache', 'X-Accel-Buffering':'no'})


@app.post('/sessions/{sid}/messages/{mid}/execute-sql')
@data_operation
def execute_saved_sql(request: Request,sid: str,mid: int):
    _require_local_settings(request,mutation=True)
    owner=request.state.user_id
    _verify_session_ownership(sid,owner)
    _require_idle(sid)
    messages=get_session_store()._backend.get_messages(sid)
    source=next((message for message in messages if message['id']==mid and message['role']=='assistant'),None)
    proposal=source['metadata'].get('sql_proposal') if source else None
    if not proposal:raise HTTPException(404,'这条回复没有可执行的推荐 SQL。')
    data=get_dataset_manager().get(sid)
    from app.tools.sql_sanitizer import check_sql_safety
    from app.tools.data_workbench import workbench_snapshot
    from app.sql_proposals import execute_proposal
    if not data or not data.files or workbench_snapshot(data)!=proposal['data_version']:
        raise HTTPException(409,'数据版本已变化，请基于当前表重新生成推荐 SQL 后验证。')
    error=check_sql_safety(proposal['sql'],allowed_tables=data.table_names)
    if error:raise HTTPException(400,error)
    req=AskRequest(question=f'验证第 {mid} 条回复的推荐 SQL',session_id=sid)
    try:
        return tasks.submit(owner,sid,lambda:_complete_analysis(req,execute_proposal(sid,mid,proposal),sid,{}),settings.max_model_calls_per_task)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None


@app.post('/sessions/{sid}/messages/{mid}/continue')
@data_operation
def continue_saved_analysis(request:Request,sid:str,mid:int):
    _require_local_settings(request,mutation=True)
    owner=request.state.user_id
    _verify_session_ownership(sid,owner)
    _require_idle(sid)
    from app.continuation import source_for_resume,resume_saved
    try:source=source_for_resume(sid,mid)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    req=AskRequest(question=f'继续第 {mid} 条回复中尚未完成的原任务',session_id=sid)
    def run():
        try:final=resume_saved(source,sid,mid)
        except Exception as exc:
            _record_analysis_failure(req,sid,exc)
            raise
        return _complete_analysis(req,final,sid,{})
    try:return tasks.submit(owner,sid,run,settings.max_model_calls_per_task)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@app.post("/tasks/{task_id}/cancel")
def cancel_analysis(request: Request, task_id: str):
    _require_local_settings(request, mutation=True)
    try:
        return tasks.cancel(task_id, request.state.user_id)
    except KeyError:
        raise HTTPException(404, "任务记录不存在") from None


def _require_idle(sid: str):
    if tasks.active_for_session(sid):
        raise HTTPException(409, "此对话正在分析，请先停止任务或等待完成，再修改数据。")


@app.post("/sessions/{sid}/upload")
@limiter.limit(settings.rate_limit_ask)
@data_operation
def upload_files(
    request: Request,
    sid: str,
    files: list[UploadFile] = File(...),
) -> dict:
    """Upload CSV/Excel files to a session. Converts to Parquet on ingest.

    - Max 5 files per batch
    - Max 10 MB per file
    - Files are losslessly compressed to Parquet
    - Each file becomes a queryable table in the session's DuckDB
    """
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"每次最多上传 {MAX_FILES_PER_BATCH} 个文件，本次选择了 {len(files)} 个。",
        )

    # Ensure the session exists in the session store
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id, allow_creation=True)
    _require_idle(sid)
    store = get_session_store()
    store.get_or_create(sid, user_id=user_id)

    # Set up parquet output directory
    from pathlib import Path
    parquet_dir = (local_session_dir(sid) if settings.app_mode == "local"
                   else Path(settings.dataset_path).parent.parent / "uploads" / sid)
    mgr = get_dataset_manager()
    mgr.get_or_create(sid, parquet_dir)

    results = []
    errors = []

    for upload_index, upload in enumerate(files):
        try:
            if Path(upload.filename or '').suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
                if settings.app_mode != 'local':
                    raise IngestionError('图片附件目前仅在本地模式开放。')
                from app.images import save_image
                image = save_image(sid, upload.filename, upload.file)
                results.append({'filename':upload.filename,'upload_index':upload_index,'image':image})
                continue
            ds = mgr.get(sid)
            ingested = validate_and_convert(
                filename=upload.filename or "unknown.csv",
                file_data=upload.file,
                output_dir=parquet_dir,
                existing_table_names=ds.table_names if ds else [],
            )
            mgr.add_file(sid, ingested)
            # Persist to Supabase Storage for cross-restart survival
            try:
                from app.tools.file_storage import upload_parquet
                upload_parquet(sid, ingested.table_name, ingested.parquet_path)
            except Exception as e:
                logger.warning("Failed to persist %s to storage: %s", ingested.table_name, e)
            results.append({
                "upload_index": upload_index,
                "filename": ingested.original_filename,
                "table_name": ingested.table_name,
                "rows": ingested.row_count,
                "columns": ingested.columns,
                "parquet_size_kb": round(ingested.size_bytes / 1024, 1),
            })
        except IngestionError as e:
            errors.append({"filename": upload.filename, "upload_index": upload_index, "error": str(e)})
        except MemoryBudgetExceeded as e:
            if "ingested" in locals() and ingested not in (ds.files if ds else []):
                ingested.parquet_path.unlink(missing_ok=True)
                if ingested.source.get('raw_file'):
                    (parquet_dir / ingested.source['raw_file']).unlink(missing_ok=True)
            errors.append({"filename": upload.filename, "upload_index": upload_index, "error": str(e)})
            break  # No point trying more files if budget is hit

    status = "ok" if not errors else ("partial" if results else "failed")
    imported = [item for item in results if 'image' not in item]
    if imported and isinstance(store._backend, SQLiteBackend):
        names = ", ".join(item["filename"] for item in imported)
        store._backend.set_title(sid, f"数据：{names}", only_if_empty=True)
        store._backend.save_message(sid, "user", f"📎 已上传：{names}", {"type": "upload", "files": imported})
    return {
        "status": status,
        "session_id": sid,
        "uploaded": results,
        "errors": errors,
    }


@app.get('/sessions/{sid}/images/{image_id}')
@data_operation
def get_image(request: Request, sid: str, image_id: str):
    _require_local_settings(request)
    _verify_session_ownership(sid, request.state.user_id)
    from app.images import image_path, FORMATS
    try:
        path = image_path(sid, image_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not path.is_file():
        raise HTTPException(404, '图片不存在或不属于当前聊天。')
    mime = next(mime for extension, mime in FORMATS.values() if path.suffix == '.' + extension)
    return Response(content=path.read_bytes(), media_type=mime, headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@app.get("/sessions/{sid}/tables")
@limiter.limit(settings.rate_limit_default)
@data_operation
def list_tables(request: Request, sid: str) -> dict:
    """List tables available in a session's dataset."""
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)
    mgr = get_dataset_manager()
    ds = mgr.get(sid)
    if ds is None or not ds.files:
        return {"session_id": sid, "tables": []}
    return {
        "session_id": sid,
        "tables": ds.preview(),
    }


from app.tools.data_workbench import (CleaningRequest, WorkbenchConflict, describe_workbench,
                                      preview_cleaning, apply_cleaning, undo_cleaning, TemporalRowsRequest, temporal_rows)
from app.schemas import CleaningApplyRequest, CleaningUndoRequest
from app.tools.relationships import (RelationshipRequest, relationship_report, confirm_relationship,
                                     relationship_overview,
                                     RelationshipUnderstandingRequest)


@app.get('/sessions/{sid}/relationships')
@data_operation
def read_relationships(request: Request, sid: str):
    return _workbench_action(request, sid, lambda ds, manager: relationship_overview(ds))


@app.get('/sessions/{sid}/workbench/tables/{table}/rows')
@data_operation
def browse_table_rows(request:Request,sid:str,table:str,snapshot:str=Query(min_length=1,max_length=64),offset:int=Query(default=0,ge=0,multiple_of=50)):
    from app.tools.data_workbench import table_rows
    return _workbench_action(request,sid,lambda ds,manager:table_rows(ds,table,snapshot,offset))


@app.post('/sessions/{sid}/relationships/check')
@data_operation
def check_relationship(request: Request, sid: str, body: RelationshipRequest):
    return _workbench_action(request, sid, lambda ds, manager: relationship_report(ds, body))


@app.post('/sessions/{sid}/relationships/understand')
def understand_table_relationships(request:Request,sid:str,body:RelationshipUnderstandingRequest):
    from app.tools.data_workbench import _check_snapshot
    from app.agent.workflow import WorkflowPlan,_run_plan
    _require_local_settings(request,mutation=True)
    _verify_session_ownership(sid,request.state.user_id)
    with data_lock:
        dataset=get_dataset_manager().get(sid)
        if not dataset or not dataset.files:raise HTTPException(404,'请先导入数据。')
        try:_check_snapshot(dataset,body.snapshot)
        except WorkbenchConflict as exc:raise HTTPException(409,str(exc)) from None
    req=AskRequest(session_id=sid,question='理解当前导入表的用途、粒度和关系；核对实际键与风险，说明可独立分析的表，不确认业务关系。')
    plan=WorkflowPlan(requirements=['理解表关系并核对证据'],steps=[{'id':'relationships','request':'理解表关系并核对证据',
        'covers':[0],'tool':'relationships','arguments':{'action':'understand'}}])
    def run():
        _check_snapshot(dataset,body.snapshot)
        try:result=_run_plan({'session_id':sid,'question':req.question},plan)
        except (AnalysisCancelled,ModelBudgetExceeded) as exc:
            _record_analysis_failure(req,sid,exc)
            raise
        return _complete_analysis(req,result,sid,{})
    try:return tasks.submit(request.state.user_id,sid,run,settings.max_model_calls_per_task)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@app.post('/sessions/{sid}/relationships/confirm')
@data_operation
def save_relationship(request: Request, sid: str, body: RelationshipRequest):
    return _workbench_action(request, sid, lambda ds, manager: confirm_relationship(ds, body))


@app.post('/sessions/{sid}/commerce/confirm')
@data_operation
def confirm_commerce(request: Request, sid: str, body: dict = Body(...)):
    def confirm(ds, manager):
        from app.tools.data_workbench import _check_snapshot, _state, _write_state
        from app.commerce import detect_olist
        if body.get('confirmed') is not True or body.get('profile') != 'olist_v1' or body.get('currency') != 'BRL':
            raise ValueError('请明确确认 Olist 口径与 BRL 币种。')
        _check_snapshot(ds, body.get('snapshot'))
        if not {'orders', 'customers', 'order_items'}.issubset(detect_olist(ds.files)):
            raise ValueError('缺少 Olist 订单、客户或商品明细结构，不能应用该模板。')
        state = _state(ds)
        state['commerce_snapshot'] = body['snapshot']
        _write_state(ds, state)
        return describe_workbench(ds)
    return _workbench_action(request, sid, confirm)


def _workbench_action(request, sid, action):
    _require_local_settings(request, mutation=request.method != 'GET')
    _verify_session_ownership(sid, request.state.user_id)
    if request.method != 'GET':
        _require_idle(sid)
    manager = get_dataset_manager()
    dataset = manager.get(sid)
    if dataset is None or not dataset.files:
        raise HTTPException(404, '请先导入数据。')
    try:
        with dataset.query_lock:
            return action(dataset, manager)
    except WorkbenchConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except MemoryBudgetExceeded as exc:
        raise HTTPException(503, str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(404, '预览或数据版本不存在，请重新读取数据。') from None
    except (ValueError, IngestionError) as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(409, '保存数据版本失败，当前版本未改变；请检查磁盘空间和文件占用。') from None


@app.get('/sessions/{sid}/workbench')
@data_operation
def read_workbench(request: Request, sid: str):
    return _workbench_action(request, sid, lambda ds, manager: describe_workbench(ds))


@app.post('/sessions/{sid}/workbench/temporal-rows')
@data_operation
def read_temporal_rows(request: Request, sid: str, body: TemporalRowsRequest):
    return _workbench_action(request,sid,lambda ds,manager:temporal_rows(ds,body))


@app.post('/sessions/{sid}/cleaning/preview')
@data_operation
def cleaning_preview(request: Request, sid: str, body: CleaningRequest):
    return _workbench_action(request, sid, lambda ds, manager: preview_cleaning(ds, body))


@app.post('/sessions/{sid}/cleaning/apply')
@data_operation
def cleaning_apply(request: Request, sid: str, body: CleaningApplyRequest):
    return _workbench_action(request, sid, lambda ds, manager: apply_cleaning(ds, body.preview_id, manager))


@app.post('/sessions/{sid}/cleaning/undo')
@data_operation
def cleaning_undo(request: Request, sid: str, body: CleaningUndoRequest):
    return _workbench_action(request, sid, lambda ds, manager: undo_cleaning(ds, body.table, body.snapshot, manager))


def _sync_cleaning_messages(dataset, plan):
    receipt = plan.get('receipt') or {}
    if receipt.get('status') not in {'applied', 'undone', 'dismissed'}:
        return plan
    backend = get_session_store()._backend
    if not isinstance(backend, SQLiteBackend):
        return plan
    try:
        known = {(message.get('metadata', {}).get('cleaning_event') or {}).get('event_id')
                 for message in backend.get_messages(dataset.session_id)}
        actions = ['apply', 'undo'] if receipt['status']=='undone' else ['apply'] if receipt['status']=='applied' else ['dismiss']
        for action in actions:
            event_id = plan['plan_id']+':'+action
            if event_id in known:
                continue
            event = {**receipt, 'event_id':event_id, 'plan_id':plan['plan_id'], 'action':action,
                     'status':{'apply':'applied','undo':'undone','dismiss':'dismissed'}[action]}
            text = ('已按确认方案清洗 '+receipt['table']+'，影响 '+str(receipt['affected_rows'])+' 行。原文件保留，后续查询使用新版本。' if action=='apply'
                    else '已撤销这次清洗，后续查询使用恢复后的版本。' if action=='undo' else '已暂不处理这份方案，数据未改变。')
            backend.save_message(dataset.session_id, 'assistant', text, {'cleaning_event':event, 'route':'cleaning'})
    except (OSError, sqlite3.Error):
        plan['record_error'] = '数据操作已完成，但聊天记录暂未写入；重新核对状态会补记，请勿重复清洗。'
    return plan


@app.get('/sessions/{sid}/cleaning/plans/{plan_id}')
@data_operation
def read_cleaning_plan(request: Request, sid: str, plan_id: str):
    from app.cleaning_assistant import plan_status
    return _workbench_action(request, sid, lambda ds, manager: _sync_cleaning_messages(ds, plan_status(ds, plan_id)))


@app.post('/sessions/{sid}/cleaning/plans/{plan_id}/{action}')
@data_operation
def act_cleaning_plan(request: Request, sid: str, plan_id: str, action: str):
    from app.cleaning_assistant import act_on_plan
    return _workbench_action(request, sid, lambda ds, manager: _sync_cleaning_messages(ds, act_on_plan(ds, plan_id, action, manager)))


@app.get("/sessions/{sid}/results/{result_id}")
@limiter.limit(settings.rate_limit_default)
@data_operation
def result_rows(request: Request, response: Response, sid: str, result_id: str, offset: int = Query(default=50, ge=0)):
    _require_local_settings(request)
    _verify_session_ownership(sid, request.state.user_id)
    from app.tools.query_results import read_result
    try:
        result = read_result(sid, result_id, offset)
    except ValueError:
        raise HTTPException(422, "结果编号或分页位置无效。") from None
    except FileNotFoundError:
        raise HTTPException(404, "已保存的查询结果不存在，无法展开更多行。") from None
    except Exception:
        raise HTTPException(409, "已保存的结果暂时无法读取，请重试。") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get('/sessions/{sid}/results/{result_id}/export')
@limiter.limit(settings.rate_limit_default)
@data_operation
def export_query_result(request:Request,sid:str,result_id:str):
    _require_local_settings(request)
    _verify_session_ownership(sid,request.state.user_id)
    from app.tools.query_results import result_csv
    try:
        chunks=result_csv(sid,result_id)
    except ValueError:
        raise HTTPException(422,'无效的结果编号。') from None
    except FileNotFoundError:
        raise HTTPException(404,'已保存的完整结果不存在。') from None
    return StreamingResponse(chunks,media_type='text/csv; charset=utf-8',headers={
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Disposition':f'attachment; filename="result-{result_id}.csv"'})


@app.get("/sessions", response_model=list[SessionListItem])
@limiter.limit(settings.rate_limit_default)
@data_operation
def list_sessions(request: Request) -> list[SessionListItem]:
    """List recent sessions for the sidebar — filtered by authenticated user."""
    user_id = request.state.user_id
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        return []
    rows = backend.list_sessions(user_id=user_id)
    return [
        SessionListItem(
            session_id=r["id"],
            title=r.get("title"),
            created_at=r["created_at"],
            last_accessed_at=r["last_accessed_at"],
            folder_id=r.get("folder_id"),
        )
        for r in rows
    ]


class ChatFolderTitle(BaseModel):
    title: str = Field(min_length=1, max_length=60)

    @field_validator('title')
    @classmethod
    def trim_title(cls, value):
        if not value.strip():
            raise ValueError('文件夹名称不能为空。')
        return value.strip()


class MoveChats(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = None


def _folder_store(request):
    _require_local_settings(request)
    backend = get_session_store()._backend
    if not isinstance(backend, SQLiteBackend):
        raise HTTPException(409, '聊天归类需要本地持久存储。')
    return backend


@app.get('/chat-folders')
@data_operation
def chat_folders(request: Request):
    return _folder_store(request).list_folders()


@app.post('/chat-folders')
@data_operation
def create_chat_folder(request: Request, body: ChatFolderTitle):
    return _folder_store(request).create_folder(body.title)


@app.patch('/chat-folders/{folder_id}')
@data_operation
def rename_chat_folder(request: Request, folder_id: str, body: ChatFolderTitle):
    try:
        _folder_store(request).rename_folder(folder_id, body.title)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {'status': 'renamed'}


@app.delete('/chat-folders/{folder_id}')
@data_operation
def remove_chat_folder(request: Request, folder_id: str):
    try:
        _folder_store(request).delete_folder(folder_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {'status': 'removed', 'chats_preserved': True}


@app.post('/chat-folders/move')
@data_operation
def move_chats(request: Request, body: MoveChats):
    try:
        _folder_store(request).move_sessions(body.session_ids, body.folder_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {'status': 'moved', 'session_ids': body.session_ids, 'folder_id': body.folder_id}


@app.get('/context')
def empty_context_status(request:Request):
    _require_local_settings(request)
    from app.context_memory import context_status
    return context_status()


@app.get('/sessions/{sid}/context')
@data_operation
def session_context_status(request:Request,sid:str):
    _require_local_settings(request)
    _verify_session_ownership(sid,request.state.user_id)
    from app.context_memory import context_status
    return context_status(sid)


@app.get("/sessions/{sid}", response_model=SessionDetail)
@limiter.limit(settings.rate_limit_default)
@data_operation
def get_session(request: Request, sid: str) -> SessionDetail:
    """Get full message history for a session."""
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        raise HTTPException(status_code=404, detail="对话存储不可用")
    s = backend.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    from app.analysis_scope import scope_history
    dataset=get_dataset_manager().get(sid)
    versions={file.table_name:file.data_version for file in dataset.files} if dataset else {}
    from app.analysis_contract_store import load_registry
    messages_raw = scope_history(backend.get_messages(sid),table_versions=versions,business_versions=load_registry(sid)['active'])
    messages = [
        MessageOut(
            id=m.get('id'),
            role=m["role"],
            content=m["content"],
            metadata=m.get("metadata", {}),
            created_at=m.get("created_at"),
        )
        for m in messages_raw
    ]
    return SessionDetail(
        session_id=s.session_id,
        title=s.title or (s.recent_queries[0].question if s.recent_queries else None),
        created_at=datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
        last_accessed_at=datetime.fromtimestamp(s.last_accessed_at, tz=timezone.utc).isoformat(),
        messages=messages,
    )


@app.patch("/sessions/{sid}")
@limiter.limit(settings.rate_limit_default)
@data_operation
def rename_session(request: Request, sid: str, body: dict = Body(...)) -> dict:
    """Rename a session."""
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)

    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="对话名称不能为空")

    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        raise HTTPException(status_code=404, detail="对话存储不可用")

    if isinstance(backend, SQLiteBackend):
        backend.set_title(sid, title)
    else:
        backend._client.table("sessions").update({"title": title[:100]}).eq("id", sid).execute()
    return {"status": "renamed", "session_id": sid, "title": title[:100]}


@app.delete("/sessions/{sid}")
@limiter.limit(settings.rate_limit_default)
@data_operation
def delete_session(request: Request, sid: str) -> dict[str, str]:
    """Delete a session and all its messages, files, and in-memory data."""
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)
    _require_idle(sid)

    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, (SupabaseBackend, SQLiteBackend)):
        raise HTTPException(status_code=404, detail="对话存储不可用")

    # Release Windows file handles before removing local Parquet files.
    mgr = get_dataset_manager()
    mgr.close_session(sid)

    # Delete files before DB records.
    storage_error = None
    try:
        from app.tools.file_storage import delete_session_files
        delete_session_files(sid)
    except Exception as e:
        storage_error = str(e)
        logger.error("Failed to delete storage files for %s: %s", sid, e)
        if settings.app_mode == "local":
            raise HTTPException(status_code=500, detail="本地文件未能删除，会话记录已保留，请关闭占用文件的程序后重试。") from e

    # 3. Delete from database (sessions + messages + query_memories via CASCADE)
    backend.delete(sid)

    if storage_error:
        return {"status": "partial", "session_id": sid, "warning": f"对话已删除，但文件清理失败：{storage_error}"}

    return {"status": "deleted", "session_id": sid}
