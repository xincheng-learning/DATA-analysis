"""Immutable result conditions and derived history status; original messages stay intact."""

import hashlib
import json
from copy import deepcopy

from app.metrics import DEFINITION_VERSION, METRICS


def create_scope(brief, messages, *, data_version, table_versions):
    ids = list(
        dict.fromkeys(m for spec in brief.get("acceptance", []) for m in spec.get("metric_ids", []))
    )
    stages = (brief.get("calculation_plan") or {}).get("stages", [])
    used = {
        name
        for stage in stages
        for name in [stage["source"], *[join["table"] for join in stage.get("joins", [])]]
    }
    scope = {
        "version": 1,
        "data_version": data_version,
        "table_versions": {
            table: version
            for table, version in table_versions.items()
            if not stages or table in used
        },
        "definition_version": DEFINITION_VERSION if ids else None,
        "metric_ids": ids,
        "business_definition_versions":deepcopy(brief.get("business_contract",{}).get("definition_versions",{})),
        "metric_labels": [
            METRICS.get(identifier, {}).get("label", identifier) for identifier in ids
        ],
        "conditions": [
            {"key": source["key"], "text": source["text"]}
            for source in brief.get("scope_sources", [])
        ],
        "metric_bindings": deepcopy(brief.get("metric_bindings", [])),
        "calculation_stages": deepcopy(stages),
        "outputs": [
            column
            for spec in brief.get("acceptance", [])
            for column in spec.get("result_columns", [])
        ],
    }
    scope["fingerprint"] = hashlib.sha256(
        json.dumps(scope, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    scope.update(
        revision_quote=brief.get("revision_quote", ""),
        revises_message_ids=[],
        changed_fields=[],
        result_status="pending",
    )
    if not scope["revision_quote"]:
        return scope
    previous = next(
        (
            m
            for m in reversed(messages)
            if m["role"] == "assistant"
            and isinstance(m.get("id"), int)
            and (
                (m.get("metadata") or {}).get("analysis_scope")
                or (m.get("metadata") or {}).get("columns")
            )
        ),
        None,
    )
    if not previous:
        return scope
    old = (previous.get("metadata") or {}).get("analysis_scope") or {}
    labels = {
        "period": "时间范围",
        "selected_period": "时间范围",
        "currency": "币种",
        "population": "样本范围",
        "amount_basis": "金额口径",
        "metric_policy": "核算口径",
    }
    before = {item["key"]: item["text"] for item in old.get("conditions", [])}
    after = {item["key"]: item["text"] for item in scope["conditions"]}
    changed = [
        labels.get(key, "筛选/口径：" + key)
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    ]
    for key, label in [
        ("metric_ids", "指标定义"),
        ("business_definition_versions", "已确认业务定义版本"),
        ("table_versions", "数据版本"),
        ("definition_version", "指标字典版本"),
        ("metric_bindings", "指标字段映射"),
        ("calculation_stages", "筛选或计算方式"),
    ]:
        if old.get(key) != scope.get(key):
            changed.append(label)
    scope["changed_fields"] = list(dict.fromkeys(changed)) or ["重新计算（条件记录相同）"]
    scope["revises_message_ids"] = list(
        dict.fromkeys([*old.get("revises_message_ids", []), previous["id"]])
    )
    return scope


def finish_scope(scope, final):
    from app.report_presentation import report_input

    result, _ = report_input(final)
    available = any(
        step.get("columns")
        and isinstance(step.get("rows"), list)
        and step.get("status") == "succeeded"
        and not (step.get("error") or step.get("validation_failure"))
        for step in result["steps"]
    )
    coverage = (final.get("analysis_report") or {}).get("coverage", [])
    status = "failed" if final.get("error") or final.get("validation_failure") else "needs_input"
    if available and status != "failed":
        status = (
            "partial"
            if result["status"] != "completed"
            or any(item["status"] != "answered" for item in coverage)
            else "complete"
        )
    return {**scope, "result_status": status}


def scope_history(messages, *, table_versions=None, definition_version=DEFINITION_VERSION, business_versions=None):
    replacements = {}
    for message in messages:
        scope = (message.get("metadata") or {}).get("analysis_scope") or {}
        for mid in scope.get("revises_message_ids", []):
            replacements[mid] = {
                "status": "historical_scope",
                "changed_fields": scope.get("changed_fields", []),
                "replacement_message_id": message.get("id"),
                "recalculation_status": scope.get("result_status", "pending"),
                "target_conditions": scope.get("conditions", []),
            }
    history = []
    for message in messages:
        meta = message.get("metadata") or {}
        scope = meta.get("analysis_scope") or {}
        state = replacements.get(message.get("id")) or meta.get("analysis_scope_state")
        if not state and scope:
            state = {"status": "current"}
            if table_versions is not None and any(
                table_versions.get(table) != version
                for table, version in scope.get("table_versions", {}).items()
            ):
                state = {
                    "status": "data_changed",
                    "changed_fields": ["数据版本"],
                    "recalculation_status": "pending",
                }
            elif business_versions is not None and any(business_versions.get(k)!=v
                    for k,v in scope.get('business_definition_versions',{}).items()):
                state={'status':'definition_changed','changed_fields':['已确认业务定义版本'],'recalculation_status':'pending'}
            elif (
                scope.get("definition_version")
                and scope["definition_version"] != definition_version
            ):
                state = {
                    "status": "definition_changed",
                    "changed_fields": ["指标字典版本"],
                    "recalculation_status": "pending",
                }
        if state:
            state = dict(state)
            target_conditions = state.pop("target_conditions", None)
            if target_conditions is not None and scope:
                old_conditions = {item["key"]: item["text"] for item in scope.get("conditions", [])}
                new_conditions = {item["key"]: item["text"] for item in target_conditions}
                if old_conditions.get("period") != new_conditions.get("period"):
                    state["changed_fields"] = list(
                        dict.fromkeys([*state.get("changed_fields", []), "时间范围"])
                    )
            text = {
                "complete": "新条件的结果已取得。",
                "partial": "新条件分析仅部分完成，仍有缺口。",
                "failed": "新条件重算失败，尚未取得替代结果。",
                "needs_input": "新条件仍需补充资料或确认。",
                "pending": "新条件尚待重算。",
                "cancelled": "新条件重算已停止。",
            }
            state = {
                **state,
                "message": (
                    "本条结果适用于下列记录条件；核验范围见本轮回执。"
                    if state["status"] == "current"
                    else "本条数字、图和结论保留于原条件，不可直接用于变更后的分析。"
                    + text.get(state.get("recalculation_status"), "新条件尚待重算。")
                ),
            }
            history.append({**message, "metadata": {**meta, "analysis_scope_state": state}})
        else:
            history.append(message)
    return history
