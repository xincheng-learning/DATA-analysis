"""Provider-reported usage and a dated, explicit CNY pricing snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from langchain_core.callbacks import BaseCallbackHandler

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICE_VERIFIED = "2026-10-25"
# CNY per million tokens: uncached input, cached input, output (peak prices).
DEEPSEEK_PRICES = {
    "deepseek-flash": ("2", "0.04", "8"),
    "deepseek-v4-flash": ("2", "0.04", "8"),
    "deepseek-v4-flash-vision-exp": ("2", "0.04", "8"),
    "deepseek-v4-pro": ("9", "0.30", "27"),
}


def reported_usage(message):
    usage = getattr(message, "usage_metadata", None) or {}
    raw = (getattr(message, "response_metadata", None) or {}).get("token_usage", {}) or {}
    incoming = usage.get("input_tokens", raw.get("prompt_tokens"))
    outgoing = usage.get("output_tokens", raw.get("completion_tokens"))
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    cached = raw.get("prompt_cache_hit_tokens", (usage.get("input_token_details") or {}).get(
        "cache_read", (raw.get("prompt_tokens_details") or {}).get("cached_tokens")))
    cache_known = type(cached) is int and 0 <= cached <= incoming
    return {"input_tokens": incoming, "output_tokens": outgoing,
            "total_tokens": incoming + outgoing,
            "cached_input_tokens": cached if cache_known else None}


class UsageCollector(BaseCallbackHandler):
    """Callbacks keep usage available even when a structured parser fails."""
    def __init__(self):
        self.records = []

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            if generations:
                self.records.append(reported_usage(getattr(generations[0], "message", None)))


def price_usage(usage, model_status, now=None):
    record = {**(usage or {}), "model": model_status.get("model", ""), "cost_cny": None}
    if usage is None:
        return record
    rates = DEEPSEEK_PRICES.get(record["model"])
    if urlsplit(model_status.get("base_url", "")).hostname != "api.deepseek.com" or rates is None:
        return record
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))
    peak = moment.weekday() < 5 and (9 <= moment.hour < 12 or 14 <= moment.hour < 18)
    multiplier = Decimal("1") if peak else Decimal("0.5")
    incoming_rate, cached_rate, outgoing_rate = [Decimal(rate) * multiplier for rate in rates]
    cached = usage["cached_input_tokens"]
    # shortcut: no holiday calendar dependency; weekday peaks use the official upper rate until a calendar is supplied.
    record["pricing"] = {"source": PRICE_SOURCE, "verified_date": PRICE_VERIFIED,
                         "currency": "CNY", "period": "weekday_peak_upper" if peak else "off_peak",
                         "input_per_million": str(incoming_rate), "cached_input_per_million": str(cached_rate),
                         "output_per_million": str(outgoing_rate), "cache_usage_known": cached is not None}
    cached = cached or 0
    cost = ((usage["input_tokens"] - cached) * incoming_rate + cached * cached_rate
            + usage["output_tokens"] * outgoing_rate) / Decimal(1_000_000)
    record["cost_cny"] = str(cost)
    return record


def summarize_usage(records):
    reported = [item for item in records if "total_tokens" in item]
    priced = [item for item in reported if item.get("cost_cny") is not None]
    return {**{key: sum(item[key] for item in reported) for key in ("input_tokens", "output_tokens", "total_tokens")},
            "reported_calls": len(reported), "missing_usage_calls": len(records) - len(reported),
            "unpriced_calls": len(reported) - len(priced),
            "cost_cny": str(sum((Decimal(item["cost_cny"]) for item in priced), Decimal(0))) if priced else None,
            "calls": [dict(item) for item in records]}
