"""Sequential evaluation only: durable request audit and shared RMB40 ceiling."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

LIMIT = 40_000_000  # User explicitly raised the cumulative cap to RMB40 on 2026-11-15; retain every prior charge.
PRICE = {"input_micro_cny": 2, "output_micro_cny": 8, "verified_date": "2026-10-21",
         "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"}


class BudgetStop(RuntimeError):
    pass


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class BudgetTransport(httpx.BaseTransport):
    def __init__(self, directory: Path, upstream=None, *, max_output_tokens=4096):
        if type(max_output_tokens) is not int or not 0 < max_output_tokens <= 16384:
            raise BudgetStop('评测输出上限只能在1至16384之间，总费用仍受累计40元限制。')
        self.max_output_tokens = max_output_tokens
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.directory / "budget.lock"
        try:
            self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BudgetStop("评测锁已存在；确认旧进程已退出并核对未结算请求后才能解除。") from None
        try:
            self.path = self.directory / "budget.json"
            self.ledger = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
                "limit_micro_cny": LIMIT, "accounted_micro_cny": 0, "blocked": False, "price": PRICE, "calls": []}
            if self.ledger["limit_micro_cny"] not in (30_000_000, LIMIT) or self.ledger["price"] != PRICE:
                raise BudgetStop("预算或价格记录不一致，禁止重置后继续计费。")
            if self.ledger["accounted_micro_cny"] != sum(c["charged_micro_cny"] for c in self.ledger["calls"]):
                raise BudgetStop("预算账本不一致，已停止。")
            if any(c["status"] == "reserved" for c in self.ledger["calls"]):
                self.ledger["blocked"] = True
            if self.ledger['limit_micro_cny'] == 30_000_000:
                self.ledger.setdefault('budget_amendments', []).append({
                    'previous_limit_micro_cny': 30_000_000, 'approved_limit_micro_cny': LIMIT,
                    'authorization_date': '2026-11-15', 'authorization': '提高到40的预算，给我继续跑，并且重验24种图形'})
                self.ledger['limit_micro_cny'] = LIMIT
            write_json(self.path, self.ledger)
        except Exception:
            os.close(self.lock_fd)
            self.lock_path.unlink()
            raise
        self.upstream = upstream or httpx.HTTPTransport(retries=0)
        self.label = ""

    def handle_request(self, request):
        if self.ledger["blocked"]:
            raise BudgetStop("存在费用未知请求，已禁止后续调用，需核对服务商账单。")
        payload = json.loads(request.content)
        if (request.method != "POST" or request.url.scheme != "https" or request.url.host != "api.deepseek.com"
                or request.url.path not in ("/chat/completions", "/v1/chat/completions")
                or payload.get("model") != "deepseek-flash" or payload.get("stream")
                or type(payload.get("max_tokens")) is not int or not 0 < payload["max_tokens"] <= self.max_output_tokens):
            raise BudgetStop("评测只允许已核价的DeepSeek Flash、非流式请求和明确输出上限。")
        # shortcut: UTF8 bytes plus 8192 covers text/tool framing conservatively; reject other modalities and review if usage exceeds it.
        input_bound = len(request.content) + 8192
        reserve = input_bound * 2 + payload["max_tokens"] * 8
        if self.ledger["accounted_micro_cny"] + reserve > LIMIT:
            raise BudgetStop("剩余40元总预算不足以预留本次最坏输出，已在发送前停止。")
        number = len(self.ledger["calls"]) + 1
        item = {"id": number, "label": self.label, "utc": datetime.now(timezone.utc).isoformat(),
                "request_sha256": hashlib.sha256(request.content).hexdigest(),
                "audit_file": f"requests/{number:04d}.json", "status": "reserved",
                "reserved_micro_cny": reserve, "charged_micro_cny": reserve}
        self.ledger["calls"].append(item)
        self.ledger["accounted_micro_cny"] += reserve
        audit = {"request_id": number, "label": self.label, "request": payload}
        write_json(self.directory / item["audit_file"], audit)
        write_json(self.path, self.ledger)
        try:
            response = self.upstream.handle_request(request)
            response.read()
            item["http_status"] = response.status_code
            if response.status_code != 200:
                raise ValueError("HTTP status failed")
            body = response.json()
            usage = body["usage"]
            prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
            if any(type(v) is not int or v < 0 for v in (prompt, completion)):
                raise ValueError("Invalid usage")
            if prompt > input_bound or completion > payload["max_tokens"]:
                raise ValueError("Usage exceeded conservative bounds")
            charge = prompt * 2 + completion * 8
            item.update(status="settled", charged_micro_cny=charge, usage=usage, model=body.get("model"))
            self.ledger["accounted_micro_cny"] += charge - reserve
            # No authentication headers or hidden reasoning are persisted.
            audit["response"] = {"model": body.get("model"), "usage": usage, "choices": [
                {"finish_reason": choice.get("finish_reason"), "message": {
                    key: value for key, value in choice.get("message", {}).items() if key != "reasoning_content"}}
                for choice in body.get("choices", [])]}
            write_json(self.directory / item["audit_file"], audit)
            write_json(self.path, self.ledger)
            return response
        except Exception as exc:
            self.ledger["blocked"] = True
            item.update(status="uncertain", failure_type=type(exc).__name__)
            # A timeout/error may still be billed: keep the entire reservation.
            write_json(self.path, self.ledger)
            raise BudgetStop("请求未能可靠结算，已保存预留费用并停止后续调用；详见安全账本。") from None

    def close(self):
        self.upstream.close()
        os.close(self.lock_fd)
        self.lock_path.unlink(missing_ok=True)
