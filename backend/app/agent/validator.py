"""Result validator.

Rule-based, no LLM call. Catches the common failure modes that an LLM
naturally produces:

  1. Wrong column name → SQL execution error (already in state.error)
  2. Over-restrictive filter → zero rows on a question that implies rows
  3. Type mismatch → execution error or zero rows

LLM-based plausibility checking ("does this number look right?") is
deliberately NOT in v1. It's expensive per question and easy to
over-correct on legitimate empty results.
"""
from __future__ import annotations

import re
from typing import Any

from app.agent.state import AgentState

# Phrases that strongly imply the user expects at least one row back.
# If the SQL returns zero rows on a question with one of these, the
# query is probably under-restricted or filters too narrowly.
_NON_EMPTY_HINTS = re.compile(
    r"\b(top|highest|lowest|most|least|best|worst|biggest|smallest|"
    r"average|mean|median|sum|count|how many|which|who|what is the)\b|最高|最低|平均|总额|总数|前\s*\d+",
    re.IGNORECASE,
)

_VALID_EMPTY = re.compile(
    r"(?<!不)(?<!不能)(?<!不要)(?<!不得)返回空表|允许为空|可以为空|无匹配.*(?:空|正常)|"
    r"empty (?:result|table)(?:s)? (?:is|are) (?:valid|acceptable|allowed)|"
    r"(?:allow|return) (?:an? )?empty (?:result|table)", re.IGNORECASE,
)


def validator_node(state: AgentState) -> dict[str, Any]:
    """Decide if the latest SQL result is plausible.

    Sets state["validation_failure"] with a short message when the result
    looks wrong. Empty string (or absent) means the result is acceptable.
    """
    failure = ""
    null_note = None
    rows = state.get("rows", [])
    row_count = state.get("row_count", 0)
    # Business formulas are context, not evidence that this filter must match rows.
    question = state.get("question", "").rsplit("当前问题：", 1)[-1]

    if not state.get("error"):
        if row_count == 0 and _NON_EMPTY_HINTS.search(question) and not _VALID_EMPTY.search(question):
            failure = (
                "Query returned zero rows but the question implies at least "
                "one row should match. The WHERE clause may be too restrictive "
                "or use values not present in the data."
            )
        else:
            cols = state.get("columns", [])
            if rows and len(cols) == 1 and all(r[cols[0]] is None for r in rows):
                null_note = {'id': 'null_result', 'status': 'warning',
                    'detail': '结果为NULL，保留未知或未定义值，不当作0；空值本身不能证明查询错误，需要结合原始数据和分母判断。'}
            rank_columns = [column for column in cols if re.search(r'(^|_)(rank|ranking|rn)(_|$)',column,re.IGNORECASE)]
            if any(row[column] is None for column in rank_columns for row in rows):
                failure = (
                    'Ranking contains NULL ranks and can omit a category from the requested comparison. '
                    'Establish period order counts: no qualifying orders is zero, but unknown amounts '
                    'on existing orders must remain unknown. Do not silently exclude these categories '
                    'or assert a reliable top-N until this distinction is resolved.'
                )

    out: dict[str, Any] = {"validation_failure": failure}
    if null_note:
        out['checks'] = [*state.get('checks', []), null_note]
    error = state.get("error") or failure
    if error:
        # Count every failed attempt once, including successful SQL with invalid results.
        out["previous_attempts"] = [
            *state.get("previous_attempts", []),
            {"sql": state.get("sql", ""), "error": error},
        ]
    return out
