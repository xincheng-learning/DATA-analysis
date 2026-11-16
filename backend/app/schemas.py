"""Pydantic models for HTTP request/response bodies."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator
from app.calculation_contract import CalculationPlan
from app.business_contract import MetricDefinition,MetricRequest


class QuoteInput(BaseModel):
    message_id: int = Field(gt=0)
    text: str = Field(min_length=1,max_length=4000)


class BoardReference(BaseModel):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(ge=1)


class MessageReference(BaseModel):
    session_id: str = Field(min_length=1,max_length=64)
    message_ids: list[int] = Field(min_length=1,max_length=5)


class ConversationReference(BaseModel):
    model_config = {'extra':'forbid'}
    session_id: str = Field(min_length=1,max_length=64)


class ImageReference(BaseModel):
    id: str = Field(pattern=r'^[a-f0-9]{64}\.(png|jpg|webp)$')
    filename: str = Field(min_length=1, max_length=255)


class ClarificationDecision(BaseModel):
    id: str = Field(default='',max_length=50,description='Assigned by the program; do not invent historical IDs.')
    question: str = Field(min_length=1,max_length=1000,description='Exact unresolved parts.criterion.intent_gap; no unrelated or already confirmed decision.')
    why: str = Field(default='',max_length=1000,description='Why the decision changes the analysis; distinguish observed facts from assumptions.')
    options: list[str] = Field(default_factory=list,max_length=3)
    recommendation: str = Field(default='',max_length=1000,description='A supported suggestion, never a confirmed choice. With options, copy one option exactly; leave empty without sufficient basis.')
    impact: str = Field(default='',max_length=1000,description='Which requested calculations or conclusions change with this choice.')


class ClarificationQuestion(BaseModel):
    question: str = Field(min_length=1,max_length=1000)
    why: str = Field(default='',max_length=1000)
    options: list[str] = Field(default_factory=list,max_length=3,description='Only needed, supported choices; two are enough. Do not invent a third formula/difference. Column names do not prove freight, tax, refund or currency meanings.')
    decisions: list[ClarificationDecision] = Field(default_factory=list,max_length=12,description='Collect all material business decisions in this response, matching the exact intent_gap questions. Explain reasons, supported suggestions and impacts; never pre-confirm a suggestion.')


class ClarificationReply(BaseModel):
    message_id: int = Field(gt=0)
    answers: dict[str,str] = Field(default_factory=dict,max_length=12)

    @model_validator(mode='after')
    def explicit_answers(self):
        if any(not value.strip() or len(value)>1000 for value in self.answers.values()):
            raise ValueError('每项口径需填写明确回答，且每项不超过1000字。')
        return self


class CleaningReply(BaseModel):
    message_id: int = Field(gt=0)
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class RequirementSpec(BaseModel):
    requirement_index: int = Field(ge=0,le=11)
    request_quote: str = Field(default='',max_length=1000,description='Exact CURRENT USER words supporting this requested part. Never quote schema, assistant suggestions or unrelated historical requests. A quotation anchors this part, not proof that all intent was understood.')
    intent_gap: str = Field(default='',max_length=500,description='Only a missing business decision that changes this part of the answer; ask one concrete question. Leave empty when current or confirmed context resolves it. Other supported parts remain executable.')
    answer_type: Literal['value','comparison','explanation','table','chart','definition','feasibility','constraint','scope']
    scope_key: str = Field(default='',max_length=50,description='For scope only: exact key of a scope_sources USER quotation to acknowledge, e.g. population. This is a user declaration, not an execution fact.')
    method_topic: Literal['','causal_boundary'] = Field(default='',description='Use causal_boundary only for a requested explanation that statistical association alone cannot establish causation. The program publishes this boundary; other methods use an empty value.')
    constraint_kind: Literal['', 'output_shape', 'preserve_source', 'precision'] = Field(default='', description='Only the specific requested constraint: output_shape for rows/columns, preserve_source for not modifying original tables, precision for unrounded values. Other restrictions remain unverified.')
    subject: str = Field(default='',max_length=1000,description='Explicit object of a non-numerical explanation, method definition or execution constraint; never invent a result column for it.')
    metric_ids: list[str] = Field(default_factory=list,max_length=12,description='Exact metric catalog IDs requested in this part; never use item_amount for profit.')
    result_columns: list[str] = Field(default_factory=list,max_length=12,description='Explicit result columns for custom metrics or a requested table. These must agree with the execution plan.')
    metric_request_id: str = Field(default='',max_length=40,description='ID of the fixed metric request producing this requirement. Program binds stage and columns.')
    result_stage: str = Field(default='',max_length=50,description='Name of the calculation stage delivering THIS requirement at its requested grain. Bind totals and daily/channel tables to separate stages even when column names match.')
    target_status: Literal['explicit','unverified'] = Field(default='explicit',description='A chart with no metric/column binding stays unverified; it must not count as an answered deliverable.')

    @model_validator(mode='after')
    def analytical_target_is_explicit(self):
        if self.answer_type=='scope' and not self.scope_key.strip():
            raise ValueError('A scope declaration needs the key of its quoted user source.')
        if self.answer_type=='scope' and (self.metric_ids or self.result_columns):
            raise ValueError('A scope acknowledgement cannot replace a numerical target.')
        if self.answer_type in {'value','comparison','table','chart'} and not (self.metric_ids or self.result_columns or self.metric_request_id) and not (self.answer_type=='chart' and self.target_status=='unverified'):
            raise ValueError('This answer type needs explicit metric IDs or result columns.')
        if self.answer_type in {'definition','feasibility'} and not (self.subject.strip() or self.metric_ids or self.result_columns):
            raise ValueError('A definition or feasibility question needs an explicit subject or analytical target.')
        return self


class ScopeSource(BaseModel):
    key: str = Field(min_length=1,max_length=50,description='Stable scope key, e.g. period, currency, population, amount_basis or profit_policy. Replace only keys changed by the user.')
    text: str = Field(min_length=1,max_length=1000,description='Exact quotation from a USER message confirming this scope, not an assistant inference.')
    message_id: int | None = Field(default=None,gt=0,description='Actual historical user message ID; null for the current user question.')


class MetricBinding(BaseModel):
    role: str = Field(min_length=1,max_length=50,description='Metric dependency role from the catalog, e.g. revenue or costs.')
    columns: list[str] = Field(min_length=1,max_length=12,description='Actual table.column names whose meaning the user explicitly confirmed.')
    confirmation: ScopeSource


class PairedCriterion(RequirementSpec):
    requirement_index: int = 0


class RequirementPart(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)
    criterion: PairedCriterion


class AnalysisBrief(BaseModel):
    goal: str = Field(min_length=1,max_length=2000)
    revision_quote: str = Field(default='',max_length=1000,description='Exact CURRENT USER instruction explicitly changing or recomputing the preceding analysis, e.g. 改成六月，其他不变. Empty for a new independent question, definitions, feasibility or merely asking why. Never infer that every follow-up replaces the previous result.')
    requirements: list[str] = Field(default_factory=list,max_length=12,description='Legacy input. For new decisions use parts instead; do not provide both forms.')
    confirmed_scope: list[str] = Field(default_factory=list,max_length=12,description='Only already confirmed dates, units, population and metric definitions; do not guess from column names.')
    acceptance: list[RequirementSpec] = Field(default_factory=list,max_length=12,description='Legacy input; new decisions use parts with each requirement and criterion together.')
    parts: list[RequirementPart] = Field(default_factory=list,max_length=12,exclude=True,description='Preferred input: every requested part with its own criterion. Indices are assigned by the program; no parallel requirements/acceptance arrays.')
    metric_definitions: list[MetricDefinition] = Field(default_factory=list,max_length=24,description='For a new supported business calculation: provide complete candidate definitions from actual fields and USER quotes. Empty only when reusing stored IDs or no numerical task.')
    metric_requests: list[MetricRequest] = Field(default_factory=list,max_length=4,description='REQUIRED nonempty for supported sql/viz numerical tasks. Select fixed metric IDs, period, entity filters and grouping. Criteria reference these request IDs. result_columns alone cannot execute.')
    calculation_plan: CalculationPlan | None = None
    unverified_constraints:list[str]=Field(default_factory=list,max_length=12,description='Unverified model suggestions only, never user instructions or confirmed scope. Do not execute these suggestions.')
    scope_sources: list[ScopeSource] = Field(default_factory=list,max_length=12)
    metric_bindings: list[MetricBinding] = Field(default_factory=list,max_length=12)

    @model_validator(mode='before')
    @classmethod
    def project_paired_requirements(cls, value):
        if isinstance(value, dict) and value.get('parts'):
            if value.get('requirements') or value.get('acceptance'):
                raise ValueError('Do not mix paired parts with legacy requirements/acceptance.')
            value = dict(value)
            raw_parts=value.pop('parts')
            parts=[]
            for part in raw_parts:
                if isinstance(part,dict) and isinstance(part.get('criterion'),dict):
                    criterion=part['criterion']
                    if criterion.get('answer_type')=='chart' and not (criterion.get('metric_ids') or criterion.get('result_columns') or criterion.get('metric_request_id')):
                        part={**part,'criterion':{**criterion,'target_status':'unverified'}}
                parts.append(RequirementPart.model_validate(part).model_dump())
            value['requirements'] = [part['requirement'] for part in parts]
            value['acceptance'] = [{**part['criterion'], 'requirement_index': i} for i, part in enumerate(parts)]
        return value

    @model_validator(mode='after')
    def every_requirement_has_one_criterion(self):
        if not self.requirements or not self.acceptance:
            raise ValueError('Every request needs nonempty requirements and criteria.')
        # A missing chart binding must not discard an otherwise executable table.
        tables=[spec for spec in self.acceptance if spec.answer_type=='table' and spec.result_columns]
        final_columns={item.name for item in self.calculation_plan.stages[-1].select} if self.calculation_plan else set()
        if len(tables)==1 and set(tables[0].result_columns)==final_columns:
            for spec in self.acceptance:
                if spec.answer_type=='chart' and not (spec.metric_ids or spec.result_columns):
                    spec.result_columns=list(tables[0].result_columns)
                    spec.metric_ids=list(tables[0].metric_ids)
                    spec.target_status='explicit'
        merged=[]
        for spec in self.acceptance:
            compatible=next((item for item in merged if spec.answer_type=='constraint'
                and spec.requirement_index<len(self.requirements) and spec.subject.strip() and item.subject.strip()
                and len(item.subject)+len(spec.subject)+1<=1000
                and item.model_dump(exclude={'subject'})==spec.model_dump(exclude={'subject'})),None)
            if compatible:compatible.subject='；'.join(dict.fromkeys([compatible.subject,spec.subject]))
            else:merged.append(spec)
        self.acceptance=merged
        extras=sorted((spec for spec in self.acceptance if spec.requirement_index>=len(self.requirements)),key=lambda spec:spec.requirement_index)
        if extras:
            if [spec.requirement_index for spec in extras]!=list(range(len(self.requirements),len(self.requirements)+len(extras))) or any(
                    not (spec.answer_type=='constraint' and spec.subject.strip() or spec.answer_type=='scope'
                        and any(source.key==spec.scope_key for source in self.scope_sources)) for spec in extras):
                raise ValueError('Extra criteria must be consecutive constraint suggestions or existing quoted scopes, never numerical targets.')
            self.unverified_constraints.extend(spec.subject.strip() for spec in extras if spec.answer_type=='constraint')
            self.acceptance=[spec for spec in self.acceptance if spec.requirement_index<len(self.requirements)]
        if sorted(spec.requirement_index for spec in self.acceptance)!=list(range(len(self.requirements))):
            raise ValueError('Each requirement needs exactly one acceptance criterion.')
        for spec in self.acceptance:
            if spec.answer_type in {'explanation','constraint'} and not spec.subject.strip():
                spec.subject=self.requirements[spec.requirement_index]
        return self


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    mode: Literal["quick", "investigate"] = "quick"
    quote: QuoteInput | None = None
    board: BoardReference | None = None
    conversation: ConversationReference | None = None
    charts: MessageReference | None = None
    images: list[ImageReference] = Field(default_factory=list, max_length=5)
    clarification_reply: ClarificationReply | None = None
    cleaning_reply: CleaningReply | None = None
    _clarification_context: dict = PrivateAttr(default_factory=dict)
    _investigation_resume: dict = PrivateAttr(default_factory=dict)
    _image_content: list[dict] = PrivateAttr(default_factory=list)
    _attachments: dict = PrivateAttr(default_factory=dict)
    _conversation_source: dict = PrivateAttr(default_factory=dict)
    _quote_model_context: dict = PrivateAttr(default_factory=dict)


Route = Literal["sql", "viz", "python", "chat", "clarify", "refuse", "cleaning", "investigate", "workflow"]


class MessageOut(BaseModel):
    """A single message in the conversation history."""
    role: str  # "user" or "assistant"
    id: int | str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class SessionListItem(BaseModel):
    """Returned in GET /sessions list."""
    session_id: str
    title: str | None = None
    created_at: str
    last_accessed_at: str
    folder_id: str | None = None


class AskResponse(BaseModel):
    question: str
    answer: str
    session_id: str
    data_dictionary: list[dict[str, Any]] = Field(default_factory=list)
    data_dictionary_next_steps: list[str] = Field(default_factory=list)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    context_status: dict[str, Any] = Field(default_factory=dict)

    # Routing transparency
    route: Route | None = None
    route_reason: str | None = None

    # SQL path
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    result_id: str | None = None
    result_error: str | None = None

    python_code: str | None = None
    python_output: str | None = None
    python_summary: str | None = Field(default=None,max_length=4000)
    python_attempts: list[dict[str, str]] = Field(default_factory=list)

    # Viz path
    chart_spec: dict[str, Any] | None = None
    chart_error: str | None = None

    # Self-correction transparency
    retry_count: int = 0
    previous_attempts: list[dict[str, Any]] = Field(default_factory=list)
    validation_failure: str | None = None

    # Failure mode
    error: str | None = None
    investigation: dict[str, Any] | None = None
    relationship_understanding: dict[str,Any] | None = None
    analysis_report: dict[str, Any] | None = None
    analysis_brief: dict[str, Any] | None = None
    analysis_process: dict[str, Any] | None = None
    analysis_scope: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    advanced_view: dict[str, Any] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    table_previews: list[dict[str, Any]] = Field(default_factory=list)
    data_version: str | None = None
    facts: list[dict[str, Any]] = Field(default_factory=list)
    metric_context: dict[str, Any] = Field(default_factory=dict)
    output_limit_reached: bool = False
    cleaning: dict[str, Any] | None = None
    cleaning_event: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    clarification_context: dict[str, Any] = Field(default_factory=dict)
    sql_proposal: dict[str, Any] | None = None
    sql_source: dict[str, Any] | None = None
    continuation_source: dict[str, Any] | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)


class CleaningApplyRequest(BaseModel):
    preview_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class CleaningUndoRequest(BaseModel):
    table: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=100)
    snapshot: str = Field(min_length=1, max_length=64)


class SessionDetail(BaseModel):
    """Returned by GET /sessions/{id} — full message history."""
    session_id: str
    title: str | None = None
    created_at: str
    last_accessed_at: str
    messages: list[MessageOut] = Field(default_factory=list)
