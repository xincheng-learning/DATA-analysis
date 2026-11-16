const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

import { supabase } from "./supabase";

async function getAuthHeaders(): Promise<Record<string, string>> {
  if (!supabase) return {};
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export interface AskResponse {
  question: string;
  answer: string;
  session_id: string;
  data_dictionary?: DataGuideTable[];
  data_dictionary_next_steps?:string[];
  route: "sql" | "viz" | "python" | "chat" | "clarify" | "refuse" | "cleaning" | "investigate" | "workflow" | null;
  route_reason: string | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  result_id?: string | null;
  result_error?: string | null;
  validation_failure?:string|null;
  output_limit_reached?:boolean;
  chart_spec: Record<string, unknown> | null;
  chart_error: string | null;
  python_code?: string | null;
  python_output?: string | null;
  python_summary?: string | null;
  python_attempts?: {code:string;error:string}[];
  error: string | null;
  investigation?: Record<string, unknown>;
  relationship_understanding?:Record<string,unknown>;
  analysis_scope?:Record<string,unknown>|null;
  analysis_report?: Record<string, unknown> | null;
  analysis_brief?: Record<string, unknown> | null;
  analysis_process?: Record<string, unknown> | null;
  workflow?: WorkflowReport;
  advanced_view?:AdvancedView;
  evidence?: Record<string, unknown>;
  data_sources?: Record<string, unknown>[];
  table_previews?: DataTable[];
  data_version?: string | null;
  cleaning?: CleaningPlan | null;
  cleaning_event?: Record<string,unknown> | null;
  clarification?:Clarification|null;
  clarification_context?:Record<string,unknown>;
  sql_proposal?:SqlProposal|null;
  sql_source?:{message_id:number;kind:string}|null;
  checks?:VerificationCheck[];
}

export interface WorkflowReport {
  status:string; requirements:string[];
  steps:{id:string;request:string;tool:string;status:string;error?:string;
    result?:Partial<AskResponse>&{board?:{id:string;title:string;version:number};
      download?:{result_id:string;row_count:number};diagnosis?:DataDiagnosis;relationship?:Record<string,unknown>}}[];
}

export interface AdvancedView {
  id:string;title:string;chart_type:string;dimensions:string[];measure:string;row_count:number;
  created_at:number;status:string;error?:string;data_version:string;embedded_id?:string;dashboard_id?:number;
  checks:VerificationCheck[];
  appearance?:import('./chart-appearance').ChartAppearance;
}

export interface CleaningRule {
  operation: 'trim' | 'null_tokens' | 'fill' | 'parse_date' | 'deduplicate' | 'parse_number' | 'map_values';
  column?: string; tokens?: string[]; value?: string | number; format?: string;
  keys?: string[]; keep?: 'first' | 'last'; confirmed?: boolean;
  decimal_separator?: '.'|','; thousands_separator?: ''|'.'|','|' '; currency_token?: string;
  failure_policy?: 'reject'|'null'; mapping?: Record<string,string>;
}
export interface CommerceFact {
  id: string; label: string; value: number | string | null; unit: string; definition: string;
}
export interface CommerceReport {
  profile: string | null; currency: string; facts: CommerceFact[]; warnings: string[]; scope: string; confirmed?: boolean;
}
export interface WorkbenchTable extends DataTable {
  data_version: string; can_undo: boolean; original_available: boolean;
  history: { version: string; rules: CleaningRule[] }[];
  temporal_checks?:TemporalCheck[];
  temporal_summary?:TemporalSummary;
  analysis_impact?:CleaningAnalysisImpact;
}
export interface CleaningAnalysisSide {status:string;reason?:string;rows:Record<string,unknown>[];columns?:string[];samples?:{stage:string;columns:string[];rows:Record<string,unknown>[]}[];input_coverage:{input_rows:number;missing_rows:Record<string,number>}|null}
export interface CleaningAnalysisImpact {status:string;phase:'preview'|'applied';reason?:string;goal?:string;source_message_id?:number|null;scope?:string;plan_sha256?:string;before_snapshot?:string;after_snapshot?:string;preview_agrees?:boolean;before?:CleaningAnalysisSide;after?:CleaningAnalysisSide;change_count?:number;changes?:{dimensions:Record<string,unknown>;metric:string;before:unknown;after:unknown;delta:string|null;row_status:string}[]}
export interface TemporalCheck {id?:string;earlier_column:string;later_column:string;eligible_rows:number;reversed_rows:number;unavailable_rows:number;missing_rows?:number;invalid_rows?:number;scope:string;examples:{active_row:number;earlier:string;later:string;order_id?:string|null}[]}
export interface TemporalSummary {unit:'rows';affected_rows:number;multiple_rule_rows:number;overlaps:{check_ids:string[];rows:number}[]}
export interface TemporalRows {snapshot:string;data_version:string;table:string;check_id:string;result_id:string;row_count:number;rows:Record<string,unknown>[];columns:string[]}
export interface WorkbenchSnapshot {
  snapshot: string; tables: WorkbenchTable[]; commerce: CommerceReport;
}
export interface CleaningPreview {
  preview_id: string; snapshot: string; table: string; before_rows: number; after_rows: number;
  affected_rows: number; conversion_failures: number; conflicting_keys: number;
  examples: { before: Record<string, unknown>; after: Record<string, unknown> | null }[];
  before_null_counts: Record<string, number>; after_null_counts: Record<string, number>;
  metrics_before: CommerceReport; metrics_after: CommerceReport;
  rules: CleaningRule[];
  type_changes?: {column:string;before:string;after:string}[];
  analysis_impact?:CleaningAnalysisImpact;
}

export interface DataDiagnosis {
  snapshot: string;
  tables: {table:string;rows:number;duplicate_rows:number;scope:string;temporal_checks?:TemporalCheck[];temporal_summary?:TemporalSummary;fields:{column:string;dtype:string;null_count:number;null_percent:number;trim_count:number;distinct_count:number;null_candidates:{value:string;count:number}[];currency_markers?:string[];number_candidates?:{success_count:number;failure_count:number;failure_examples:string[]}[]}[]}[];
}
export interface CleaningPlan {
  plan_id?: string; status: string; summary:string; notes:string[]; questions:string[];
  diagnosis:DataDiagnosis; preview?:CleaningPreview; current_snapshot?:string; can_undo?:boolean;
  record_error?:string;
  receipt?:{analysis_impact?:CleaningAnalysisImpact;after_snapshot?:string};
}

export async function workbenchRequest<T>(sid: string, action = 'workbench', body?: unknown): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}/${action}`, {
    method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await res.json();
  if (!res.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '规则无效，请检查字段、格式和确认选项。');
  return result;
}

export interface SessionListItem {
  session_id: string;
  title: string | null;
  created_at: string;
  last_accessed_at: string;
  folder_id?: string | null;
}

export interface ChatFolder { id: string; title: string }

export interface Message {
  id?: number | string | null;
  role: "user" | "assistant";
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface SessionDetail {
  session_id: string;
  title: string | null;
  created_at: string;
  last_accessed_at: string;
  messages: Message[];
}

export interface UploadResult {
  status: "ok" | "partial" | "failed";
  session_id: string;
  uploaded: { filename: string; upload_index:number; table_name?: string; rows?: number; columns?: string[]; image?:ImageReference }[];
  errors: { filename: string; upload_index:number; error: string }[];
}

export type ThinkingMode = "auto" | "enabled" | "disabled";
export type ReasoningEffort = "low" | "high" | "max";

export interface StorageSettingsStatus {
  directory: string;
  session_count: number;
  file_count: number;
  size_bytes: number;
  previous_directory?: string;
  browse_available: boolean;
}

export async function getStorageSettings(): Promise<StorageSettingsStatus> {
  const response = await fetch(`${API_BASE}/settings/storage`, { cache: "no-store" });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "无法读取聊天存储设置。");
  return result;
}

export async function saveStorageSettings(directory: string): Promise<StorageSettingsStatus> {
  const response = await fetch(`${API_BASE}/settings/storage`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ directory }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "未完成存储位置切换，请重新打开设置核对当前目录。");
  return result;
}

export async function browseStorageDirectory(directory: string): Promise<string | null> {
  const response = await fetch(`${API_BASE}/settings/storage/browse`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({directory}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || '无法打开文件夹选择窗口，请直接填写路径。');
  return result.directory;
}

export interface ModelSettingsStatus {
  provider: string;
  base_url: string;
  model: string;
  configured: boolean;
  has_api_key: boolean;
  encrypted: boolean;
  source: "web" | "env" | "none";
  thinking_mode: ThinkingMode;
  reasoning_effort: ReasoningEffort;
  thinking_supported: boolean;
}

export interface ModelSettingsInput {
  base_url: string;
  model: string;
  api_key?: string;
  thinking_mode?: ThinkingMode;
  reasoning_effort?: ReasoningEffort;
}

export async function getModelSettings(): Promise<ModelSettingsStatus> {
  const res = await fetch(`${API_BASE}/settings/model`, { cache: "no-store" });
  if (!res.ok) {
    const error = await res.json().catch(() => null);
    throw new Error(error?.detail || "无法读取模型设置，请确认后端服务已启动。");
  }
  return res.json();
}

export async function saveModelSettings(config: ModelSettingsInput): Promise<ModelSettingsStatus> {
  const res = await fetch(`${API_BASE}/settings/model`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config),
  });
  const result = await res.json().catch(() => null);
  if (!res.ok) throw new Error(result?.detail || "模型设置保存失败，请稍后重试。");
  return result;
}

export async function testModelSettings(config: ModelSettingsInput, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`${API_BASE}/settings/model/test`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config), signal,
  });
  const result = await res.json().catch(() => null);
  if (!res.ok) throw new Error(result?.detail || "连接检查失败，请检查接口和网络设置。");
  return result.message;
}

export async function askQuestion(
  question: string,
  sessionId?: string,
  signal?: AbortSignal
): Promise<AskResponse> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ question, session_id: sessionId || undefined }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(`请求失败（${res.status}）：${err?.detail || "请稍后重试。"}`);
  }
  return res.json();
}

export async function listSessions(): Promise<SessionListItem[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions`, { headers: authHeaders, cache: 'no-store' });
  if (!res.ok) throw new Error('聊天列表读取失败，请重试。');
  return res.json();
}

export async function getSession(sid: string): Promise<SessionDetail> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}`, { headers: authHeaders,cache:'no-store' });
  if (!res.ok) throw new Error(`无法读取对话（${res.status}），请确认对话仍然存在。`);
  return res.json();
}

export async function deleteSession(sid: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}`, { method: "DELETE", headers: authHeaders });
  if (!res.ok) throw new Error((await res.json()).detail || "删除失败，请重试。");
}

export async function renameSession(sid: string, title: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || '名称未保存，请重试。');
}

export async function uploadFiles(
  sid: string,
  files: File[]
): Promise<UploadResult> {
  const authHeaders = await getAuthHeaders();
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const res = await fetch(`${API_BASE}/sessions/${sid}/upload`, {
    method: "POST",
    headers: authHeaders,
    body: form,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`上传请求失败（${res.status}）：${err}`);
  }
  return res.json();
}

export async function getSessionTables(
  sid: string
): Promise<DataTable[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}/tables`, { headers: authHeaders });
  if (!res.ok) throw new Error("无法读取数据预览，请稍后重试。");
  const data = await res.json();
  return data.tables || [];
}

export interface DataTable {
  table_name: string; filename: string; rows: number; columns: string[];
  dtypes: Record<string, string>; null_counts: Record<string, number>;
  preview: Record<string, unknown>[];
  source: { kind?: string; imported_at?: string; database?: string; schema?: string; table?: string;
    relationship_metadata?:{status:string;primary_key:string[];foreign_keys:{columns:string[];referenced_schema:string;referenced_table:string;referenced_columns:string[]}[]}};
}
export interface DataGuideTable {
  table_name:string;title:string;description:string;rows:number;filename:string;
  fields:{column:string;label:string;meaning:string;dtype:string;missing:number;missing_percent:number|null;sample:unknown;impact:string}[];
  preview:Record<string,unknown>[];
}

export interface AnalysisTask {
  task_id: string; session_id: string; status: "queued" | "running" | "cancelling" | "cancelled" | "succeeded" | "failed";
  stage: string; elapsed_seconds: number; model_calls: number; max_model_calls: number;
  trace: Record<string, unknown>[]; result: AskResponse | null; error: string;
  usage?: Record<string, unknown>;
  draft_answer?: string;
}

export async function localRequest(path: string, method = "GET", body?: unknown) {
  const res = await fetch(`${API_BASE}${path}`, { method, cache: "no-store",
    headers: { "Content-Type": "application/json" }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw Object.assign(new Error(data?.detail || `请求失败（${res.status}）`), { status: res.status });
  return data;
}

export interface QuoteReference { message_id: number; text: string }
export interface PanelReference { id: string; title: string; version: number; chart_count: number; note_count: number }
export interface ConversationReference {session_id:string;title:string}
export interface ContextReference extends ConversationReference {message_ids:number[];titles?:string[] }
export interface ImageReference {id:string;filename:string;mime?:string;size?:number}
export const imageUrl=(sid:string,id:string)=>`${API_BASE}/sessions/${encodeURIComponent(sid)}/images/${encodeURIComponent(id)}`;
export const resultExportUrl=(sid:string,id:string)=>`${API_BASE}/sessions/${encodeURIComponent(sid)}/results/${encodeURIComponent(id)}/export`;
export interface ClarificationDecision {id:string;question:string;why:string;options:string[];recommendation:string;impact:string}
export interface Clarification {question:string;why?:string;options:string[];decisions?:ClarificationDecision[];goal:string;answers:{question:string;answer:string;message_id?:number}[]}
export interface SqlProposal {sql:string;data_version:string;scope?:string}
export interface VerificationCheck {id:string;status:string;detail:string;scope?:string}
export interface ChatAttachments { quote?: QuoteReference; board?: {id:string;version:number};conversation?:ConversationReference;charts?:ContextReference;images?:ImageReference[];clarification_reply?:{message_id:number;answers?:Record<string,string>};cleaning_reply?:{message_id:number;plan_id:string} }
export const executeSqlProposal=(sid:string,mid:number):Promise<AnalysisTask>=>localRequest(`/sessions/${encodeURIComponent(sid)}/messages/${mid}/execute-sql`,'POST',{});
export const continueAnalysis=(sid:string,mid:number):Promise<AnalysisTask>=>localRequest(`/sessions/${encodeURIComponent(sid)}/messages/${mid}/continue`,'POST',{});
export const startAnalysis = (question: string, session_id: string | undefined, mode: "quick" | "investigate", attachments:ChatAttachments = {}): Promise<AnalysisTask> => localRequest("/tasks", "POST", { question, session_id, mode, ...attachments,...(attachments.conversation?{conversation:{session_id:attachments.conversation.session_id}}:{}) });
export const readAnalysis = (id: string): Promise<AnalysisTask> => localRequest(`/tasks/${id}`);
export async function streamAnalysis(id:string, onTask:(task:AnalysisTask)=>void|Promise<void>, signal:AbortSignal) {
  const response=await fetch(`${API_BASE}/tasks/${encodeURIComponent(id)}/events`,{signal,cache:'no-store',headers:{Accept:'text/event-stream'}});
  if(!response.ok){const error=await response.json().catch(()=>null);throw Object.assign(new Error(error?.detail||'流式连接未建立'),{status:response.status});}
  if(!response.body||!response.headers.get('content-type')?.includes('text/event-stream'))throw new Error('服务没有返回流式内容');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  try{
    while(true){
      const {done,value}=await reader.read();
      buffer=(buffer+decoder.decode(value,{stream:!done})).replace(/\r\n/g,'\n');
      let boundary;
      while((boundary=buffer.indexOf('\n\n'))>=0){
        const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
        const data=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
        if(!data)continue;
        const task:AnalysisTask=JSON.parse(data);
        if(task.task_id!==id||!['queued','running','cancelling','cancelled','succeeded','failed'].includes(task.status)||typeof task.draft_answer!=='string')throw new Error('流式任务内容无效');
        await onTask(task);
        if(['succeeded','failed','cancelled'].includes(task.status))return;
      }
      if(done)throw new Error('流式连接提前结束，正在恢复任务状态');
    }
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
export const getResultRows = (sid: string, resultId: string, offset: number): Promise<{ rows: Record<string, unknown>[]; row_count: number; columns: string[]; offset: number }> => localRequest(`/sessions/${encodeURIComponent(sid)}/results/${encodeURIComponent(resultId)}?offset=${offset}`);
export const cancelAnalysis = (id: string): Promise<AnalysisTask> => localRequest(`/tasks/${id}/cancel`, "POST", {});

export interface DatabaseConfig { host: string; port: number; database: string; username: string; password?: string; configured?: boolean }
export interface DatabaseTable { schema_name: string; table_name: string }
export const getDatabaseSettings = (): Promise<DatabaseConfig> => localRequest("/settings/database");
export const saveDatabaseSettings = (config: DatabaseConfig): Promise<DatabaseConfig> => localRequest("/settings/database", "PUT", config);
export const checkDatabase = (): Promise<{ tables: DatabaseTable[] }> => localRequest("/settings/database/test", "POST", {});
export const importDatabase = (sid: string, tables: DatabaseTable[]): Promise<{ uploaded: UploadResult["uploaded"] }> => localRequest(`/sessions/${sid}/database/import`, "POST", { tables });
