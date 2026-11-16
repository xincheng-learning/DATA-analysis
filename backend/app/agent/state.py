"""LangGraph state shared across nodes.

We use a TypedDict so LangGraph can merge updates correctly. Keep it small —
every field added here pushes us toward over-engineering before we know
what we need.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

Route = Literal["sql", "viz", "python", "chat", "clarify", "refuse", "cleaning", "investigate", "workflow"]


class AttemptRecord(TypedDict):
    sql: str
    error: str  # empty string when the attempt succeeded


class AgentState(TypedDict, total=False):
    # Input
    question: str
    user_request: str
    session_context: str  # rendered prior queries (empty when no follow-up)
    session_id: str  # used by execute_sql to pick the right DuckDB connection
    data_version: str | None
    facts: list[dict[str, Any]]
    output_limit_reached: bool
    planned_sql: bool  # experimental controller already selected the SQL subgraph
    internal_analysis: bool  # child computations return evidence; the outer turn writes the report once
    analysis_brief: dict[str, Any]
    analysis_context: dict[str, Any]
    analysis_evidence: dict[str, Any]
    analysis_process: dict[str, Any]
    analysis_scope: dict[str, Any]
    dependency_response: str
    metric_context: dict[str, Any]
    blocked_sql: list[str]  # SQL attempted in previous steps of this investigation

    # Schema injected at graph entry so nodes don't reach into the SQL tool
    schema: str

    # Filled by router_node
    route: Route
    route_reason: str
    clarification: dict[str, Any] | None
    checks: list[dict[str, Any]]
    show_data_preview: bool
    table_descriptions: dict[str,str]
    cleaning_intent: str
    workflow: dict[str, Any]
    relationship_understanding: dict[str,Any]
    advanced_view: dict[str, Any]
    cleaning: dict[str, Any]
    cleaning_event: dict[str, Any]
    investigation: dict[str, Any]
    analysis_report: dict[str, Any]

    # Filled / overwritten by write_sql each attempt
    sql: str
    sql_source: dict[str, Any]

    # Filled / overwritten by execute_sql each attempt
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    result_id: str | None
    result_error: str
    error: str  # SQL execution failed on the latest attempt

    # Self-correction tracking (week 4)
    retry_count: int
    previous_attempts: list[AttemptRecord]
    validation_failure: str  # set by validator when result is suspicious
    sql_retryable: bool

    # Filled by make_chart node (only on viz route)
    chart_spec: dict[str, Any]
    chart_error: str  # chart build failed (SQL still succeeded)

    # Filled by python tool (only on python route)
    python_code: str
    python_output: str
    python_chart: bool
    python_summary: str
    python_attempts: list[dict[str, str]]
    python_retryable: bool

    # Final user-facing answer composed at the end of the graph
    answer: str
    data_dictionary: list[dict[str, Any]]
    data_dictionary_next_steps: list[str]
