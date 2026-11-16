"""One local analysis worker, cooperative cancellation and observable execution."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import Runnable

from app.usage import UsageCollector, price_usage, reported_usage, summarize_usage


class AnalysisCancelled(RuntimeError):
    pass


class ModelBudgetExceeded(RuntimeError):
    pass


class AnalysisPersistenceError(RuntimeError):
    pass


class UnexpectedModelReply(RuntimeError):
    pass


@dataclass
class AnalysisTask:
    owner: str
    session_id: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    stage: str = "等待执行"
    started_at: float = field(default_factory=time.monotonic)
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: Any = field(default_factory=threading.RLock)
    trace: list[dict] = field(default_factory=list)
    usage_records: list[dict] = field(default_factory=list)
    model_calls: int = 0
    max_model_calls: int = 30
    budget_exhausted: bool = False
    result: Any = None
    error: str = ""
    draft_answer: str = ""
    finished_at: float | None = None

    def public(self):
        with self.lock:
            return {"task_id": self.task_id, "session_id": self.session_id, "status": self.status,
                    "stage": self.stage, "elapsed_seconds": round((self.finished_at or time.monotonic()) - self.started_at, 1),
                    "model_calls": self.model_calls, "max_model_calls": self.max_model_calls,
                    "trace": [dict(item) for item in self.trace], "usage": summarize_usage(self.usage_records),
                    "result": self.result, "error": self.error, "draft_answer": self.draft_answer}


_current: ContextVar[AnalysisTask | None] = ContextVar("analysis_task", default=None)
STAGES = {"router": "选择分析路线", "write_sql": "生成查询", "execute_sql": "执行查询",
          'review_evidence':'复核问题与计算证据','write_report':'组织分析结论并检查问题覆盖',
          'prepare_analysis':'核对数据粒度、缺失与时间顺序',
          "validator": "验证结果", "make_chart": "生成图表", "write_python": "准备统计分析",
          "execute_python": "隔离执行统计分析", "chat_node": "组织回复", "planning": "根据证据选择下一步",
          "compose_answer": "整理结论", "persist": "保存证据", 'cleaning':'检查数据并制定清洗方案',
          'diagnose_data':'检查整表数据质量', 'propose_cleaning':'推荐清洗策略', 'preview_cleaning':'试算处理影响，尚未清洗',
          'apply_cleaning':'执行已确认的清洗方案', 'investigate_node':'调查经营变化', 'synthesize':'核对证据并组织综合结论'}


def checkpoint(stage: str | None = None):
    task = _current.get()
    if task is None:
        return
    with task.lock:
        if task.cancelled.is_set():
            raise AnalysisCancelled("已停止后续分析")
        if stage:
            task.stage = STAGES.get(stage, stage)


def publish_answer(text: str, *, append=False):
    task = _current.get()
    if task is not None:
        with task.lock:
            checkpoint()
            task.draft_answer = task.draft_answer + text if append else text


def draft_answer():
    task = _current.get()
    if task is None:
        return ''
    with task.lock:
        return task.draft_answer


def invoke_model(model, messages, *, stream_text=False, on_partial=None):
    checkpoint()
    from app.privacy import model_safe_text, model_safe_value
    messages = [message.model_copy(update={'content': model_safe_text(message.content) if isinstance(message.content, str) else model_safe_value(message.content)}) for message in messages]
    from app.config import settings
    if settings.app_mode=='local':
        from app.context_memory import ContextMemoryError,token_count
        def input_size(content):
            if isinstance(content,str):return token_count(content)
            # shortcut: reserve 4096 context tokens per image; use model-specific budgets for higher-resolution providers.
            return sum(4096 if isinstance(block,dict) and block.get('type') in {'image_url','image'}
                       else token_count(str(block.get('text',''))) if isinstance(block,dict) and block.get('type')=='text'
                       else token_count(str(block)) for block in content)
        if sum(input_size(message.content)+8 for message in messages)>settings.context_memory_tokens*2:
            raise ContextMemoryError('本次模型输入超过应用上下文额度，请减少图表附件或缩小分析范围；未截断数据，也未发出该请求。')
    task = _current.get()
    if task is not None:
        with task.lock:
            if task.model_calls >= task.max_model_calls:
                task.budget_exhausted = True
                raise ModelBudgetExceeded("模型调用预算已用尽，已停止后续步骤。")
            task.model_calls += 1
    collector = UsageCollector()
    requested_at = datetime.now(timezone.utc)
    response = None
    # shortcut: reject observed DSML and structured calls; add markers when another provider requires them.
    protocol='<｜｜DSML｜｜'
    pending=''
    protocol_reply=False
    status = {}
    if task is not None:
        from app.model_settings import get_model_status
        status = get_model_status()
        if isinstance(model, Runnable):
            limits = {STAGES['router']:6144, STAGES['write_sql']:2048, STAGES['write_python']:6144,
                      STAGES['write_report']:4096}
            summary = task.stage.startswith('压缩聊天上下文')
            limit = 1024 if summary else limits.get(task.stage,4096)
            options = {'max_tokens':max(limit,6144) if status.get('thinking_mode')=='enabled' else limit}
            from urllib.parse import urlsplit
            if (urlsplit(status.get('base_url','')).hostname=='api.deepseek.com' and status.get('thinking_mode')=='auto'
                    and (summary or task.stage in {STAGES['write_sql'],STAGES['write_python']})):
                options['extra_body']={'thinking':{'type':'disabled'}}
            model=model.bind(**options)
    try:
        if task is not None and isinstance(model, Runnable) and (stream_text or on_partial):
            if stream_text and draft_answer():
                publish_answer('\n\n', append=True)
            with closing(model.stream(messages, config={"callbacks": [collector]})) as chunks:
                for chunk in chunks:
                    response = chunk if response is None or on_partial else response + chunk
                    checkpoint()
                    if on_partial:
                        on_partial(chunk)
                    else:
                        content = chunk.content
                        text = content if isinstance(content, str) else ''.join(block.get('text','') for block in content if isinstance(block,dict) and block.get('type')=='text')
                        pending+=text
                        protocol_reply=protocol_reply or protocol in pending or bool(getattr(chunk,'tool_calls',None) or getattr(chunk,'tool_call_chunks',None))
                        if protocol_reply:
                            pending=''
                            continue
                        # Hold a partial protocol marker across chunks; drain the stream to retain billable usage.
                        held=next((n for n in range(min(len(pending),len(protocol)-1),0,-1) if protocol.startswith(pending[-n:])),0)
                        ready=pending[:-held] if held else pending
                        if ready:publish_answer(ready,append=True)
                        pending=pending[-held:] if held else ''
            if pending:protocol_reply=True
        else:
            response = (model.invoke(messages, config={"callbacks": [collector]})
                        if task is not None and isinstance(model, Runnable) else model.invoke(messages))
        if stream_text and (protocol_reply or protocol in str(response.content) or getattr(response,'tool_calls',None)):
            raise UnexpectedModelReply('模型返回了未执行的工具指令，本次没有据此执行查询，也未生成有效回答。已保留现有结果；不会自动补发模型请求。')
    finally:
        if task is not None:
            records = collector.records or [reported_usage(response)]
            with task.lock:
                task.usage_records.extend({**price_usage(item, status, requested_at), "stage": task.stage,
                                           "requested_at": requested_at.isoformat()} for item in records)
    checkpoint()
    return response


def controlled_node(name: str, function: Callable):
    def run(state):
        checkpoint(name)
        started = time.monotonic()
        task = _current.get()
        entry = {"node": name, "label": STAGES.get(name, name), "status": "running", "seconds": 0}
        if name == "execute_sql":
            entry.update(sql=state.get("sql", ""), question=state.get("question", ""))
        if task is not None:
            with task.lock:
                task.trace.append(entry)
        try:
            result = function(state)
        except Exception as exc:
            if task is not None:
                with task.lock:
                    entry.update(status="cancelled" if isinstance(exc, AnalysisCancelled) else "failed",
                                 seconds=round(time.monotonic() - started, 3), error="当前步骤已中断。")
            raise
        if task is not None:
            with task.lock:
                entry.update({key: result[key] for key in ("route", "sql", "row_count", "error", "validation_failure") if key in result})
                error = result.get("error") or result.get("validation_failure")
                entry.update(status=("blocked" if str(error).startswith("Repeated SQL blocked") else "failed") if error else "succeeded",
                             seconds=round(time.monotonic() - started, 3))
        checkpoint()
        return result
    return run


def task_evidence():
    task = _current.get()
    if task is None:
        return {}
    with task.lock:
        return {"task_id": task.task_id, "trace": [dict(item) for item in task.trace], "usage": summarize_usage(task.usage_records), "model_calls": task.model_calls,
                "max_model_calls": task.max_model_calls, "budget_exhausted": task.budget_exhausted}


def save_partial_result(result):
    task = _current.get()
    if task is not None:
        from copy import deepcopy
        with task.lock:
            task.result = deepcopy(result)


def partial_result():
    task = _current.get()
    return task.result if task and task.result else {}


def finish_task(result, persist):
    """Make saving and completion atomic with respect to a stop request."""
    task = _current.get()
    if task is None:
        persist()
        return
    with task.lock:
        checkpoint("persist")
        task.result = result
        try:
            persist()
        except Exception as exc:
            raise AnalysisPersistenceError('结果已生成，但保存失败。下方保留本轮答案和用量；请先复制保存，后端重启或任务记录过期后无法恢复。') from exc
        task.result, task.status, task.stage = result, "succeeded", "分析完成"


def commit_action(action):
    """A stop can win before a data commit, never between the commit and its receipt."""
    task = _current.get()
    if task is None:
        return action()
    with task.lock:
        checkpoint()
        return action()


class TaskRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._tasks: dict[str, AnalysisTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="datapilot-analysis")

    def submit(self, owner, session_id, runner, max_model_calls=30):
        with self._lock:
            if any(item.owner == owner and item.status in {"queued", "running", "cancelling"} for item in self._tasks.values()):
                raise ValueError("已有分析正在进行，请等待完成或先停止当前任务。")
            finished = [key for key, item in self._tasks.items() if item.status in {"succeeded", "failed", "cancelled"}]
            for key in finished[:max(0, len(self._tasks) - 29)]:
                del self._tasks[key]
            task = AnalysisTask(owner, session_id, max_model_calls=max_model_calls)
            self._tasks[task.task_id] = task
            self._executor.submit(self._run, task, runner)
            return task.public()

    def _run(self, task, runner):
        token = _current.set(task)
        try:
            checkpoint()
            with task.lock:
                task.status = "running"
            result = runner()
            with task.lock:
                if task.status != "succeeded":
                    checkpoint()
                    task.result = result
                task.status, task.stage = "succeeded", "分析完成"
        except AnalysisCancelled:
            with task.lock:
                task.status, task.stage = "cancelled", "已停止；不会继续启动后续步骤"
        except Exception as exc:
            from app.context_memory import ContextMemoryError
            with task.lock:
                task.status = "failed"
                task.error = str(exc) if isinstance(exc, (ModelBudgetExceeded,ContextMemoryError,AnalysisPersistenceError,UnexpectedModelReply)) else "分析未能完成，请检查模型连接和数据；已执行步骤可在记录中查看。"
        finally:
            task.finished_at = time.monotonic()
            _current.reset(token)

    def _find(self, task_id, owner):
        task = self._tasks.get(task_id)
        if task is None or task.owner != owner:
            raise KeyError(task_id)
        return task

    def read(self, task_id, owner):
        with self._lock:
            return self._find(task_id, owner).public()

    def cancel(self, task_id, owner):
        with self._lock:
            task = self._find(task_id, owner)
            with task.lock:
                if task.status in {"queued", "running", "cancelling"}:
                    task.cancelled.set()
                    task.status = "cancelling"
            return task.public()

    def active_for_session(self, session_id):
        with self._lock:
            return any(task.session_id == session_id and task.status in {"queued", "running", "cancelling"} for task in self._tasks.values())

    def has_active_tasks(self):
        with self._lock:
            return any(task.status in {"queued", "running", "cancelling"} for task in self._tasks.values())

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)


tasks = TaskRegistry()
