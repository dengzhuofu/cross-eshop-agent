/** 后端 API 的类型化契约(与 backend/ 实际响应字段一一对应)。 */

export type Tone = 'green' | 'amber' | 'red' | 'blue' | 'purple' | 'teal' | 'gray';

export type Marketplace = 'amazon' | 'shopify' | 'tiktok_shop';
export type TargetMarket = 'US' | 'UK' | 'DE' | 'JP';
export type RiskPreference = 'conservative' | 'balanced' | 'aggressive';

export interface Tenant {
  id: string;
  name: string;
}

/** GET /api/v1/workflows 列表项 */
export interface WorkflowSummary {
  id: string;
  title: string;
  status: string;
}

/** POST /api/v1/workflows 请求体 */
export interface WorkflowCreatePayload {
  product_idea: string;
  marketplaces: Marketplace[];
  target_market: TargetMarket;
  risk_preference: RiskPreference;
  /** 是否自动审批:缺省 true;false 时工作流在发布前挂起等待人工审批(HITL) */
  auto_approve?: boolean;
}

/** POST /api/v1/workflows 响应体 */
export interface WorkflowCreated {
  id: string;
  status: string;
  title: string;
}

/** GET /api/v1/approvals?limit=20 列表项中单个平台的 Listing 草稿预览 */
export interface ApprovalListing {
  marketplace: string;
  title: string;
  bullets: string[];
  claim: string;
}

/** GET /api/v1/approvals 列表项携带的待审上下文(决策门产出的快照) */
export interface PendingApproval {
  /** 利润率小数(如 0.2541 → 展示为 25.41%) */
  margin_pct: number;
  primary_supplier: string;
  risk_flags: string[];
  critique_rounds: number;
  listings: ApprovalListing[];
}

/** GET /api/v1/approvals?limit=20 列表项 */
export interface ApprovalQueueItem {
  id: string;
  title: string;
  product_idea: string;
  marketplaces: string[];
  created_at: string;
  pending_approval: PendingApproval;
}

/** POST /api/v1/workflows/{id}/approval 的人工决策 */
export type ApprovalDecision = 'approve' | 'reject';

/** POST /api/v1/workflows/{id}/approval 请求体 */
export interface ApprovalRequestPayload {
  decision: ApprovalDecision;
  comment: string;
}

/** POST /api/v1/workflows/{id}/approval 响应体(409 = 已不在待审状态) */
export interface ApprovalResult {
  id: string;
  status: string;
}

/** GET /api/v1/workflows/{id} */
export interface WorkflowDetailData {
  id: string;
  title: string;
  status: string;
  current_node: string | null;
  error: string | null;
  product_idea: string;
  marketplaces: string[];
  step_count: number;
  decision_count: number;
}

/** GET /api/v1/workflows/{id}/trace 中的单个步骤 */
export interface WorkflowStep {
  seq: number;
  node: string;
  status: string;
  detail: unknown;
  latency_ms: number | null;
  /** M13：步骤完成时间——活动流与决策/工具调用交错排序的锚点 */
  created_at?: string;
}

/** trace 中的一条 Agent 决策记录(可审计性核心数据) */
export interface DecisionRecord {
  agent: string;
  decision_type: string;
  reasoning: string;
  chosen_option: unknown;
  alternatives: unknown;
  created_at: string;
}

/** trace 中的一次工具调用审计记录 */
export interface ToolCall {
  id: number | string;
  tool: string;
  risk_level: string;
  status: string;
  idempotency_key: string | null;
  error: string | null;
  latency_ms: number | null;
  /** M13：executor 七步管线审计里本就存了的输入/输出摘要（大对象截断），工具卡展示用 */
  input_summary?: unknown;
  output_summary?: unknown;
  created_at?: string;
}

/** GET /api/v1/workflows/{id}/trace */
export interface TraceResponse {
  workflow: { id: string; status: string; error: string | null };
  steps: WorkflowStep[];
  decisions: DecisionRecord[];
  tool_calls: ToolCall[];
}

/* ------------------------------------------------------------------ */
/* Bad Case(M8 防线可观测)                                            */
/* ------------------------------------------------------------------ */

/** GET /api/v1/badcases 列表项 */
export interface BadCase {
  id: string;
  tenant_id: string;
  /** 关联的工作流,面板卡片点击后跳转到该工作流详情页 */
  workflow_id: string;
  /** PRD 八类之一:input_anomaly / output_runaway / calc_anomaly / tool_failure /
   *  flow_anomaly / memory_anomaly / context_anomaly / biz_violation */
  category: string;
  /** high / medium / low */
  severity: string;
  /** 检测器标识,如 input_injection / output_absolute_claims / memory_poisoning */
  detector: string;
  summary: string;
  /** 结构化证据(如 { patterns: [...] } / { phrases: [...] }),渲染时兜底为 JSON 展示 */
  evidence: unknown;
  /** detected / quarantined / resolved / escalated / aborted */
  status: string;
  /** 处置留痕(PRD §20.4):流转到终态时由处置请求写入的 note */
  outcome?: string | null;
  created_at: string;
}

/** POST /api/v1/badcases/{id}/status 允许的目标状态:仅终态(PRD §20.4) */
export type BadCaseTerminalStatus = 'resolved' | 'escalated' | 'aborted';

/** POST /api/v1/badcases/{id}/status 载荷 */
export interface BadCaseStatusPayload {
  status: BadCaseTerminalStatus;
  /** 处置说明,后端写入 outcome 字段作留痕 */
  note?: string;
}

/** POST /api/v1/badcases/{id}/status 响应 */
export interface BadCaseStatusResult {
  id: string;
  status: string;
  outcome: string | null;
}

/**
 * trace 中 node="bad_case_scan" 步骤的 detail 形状。
 * 后端字段可能略有出入,所有属性均按可选处理,渲染层做好空值兜底;
 * 未识别的字段仍可通过 CollapsibleJson 以原始 JSON 查看。
 */
export interface BadCaseScanDetail {
  /** 扫描发生的链路:planner / listing / retrospective */
  origin?: string;
  /** 扫描命中列表 */
  hits?: Array<{
    source?: unknown;
    category?: string;
    detector?: string;
    severity?: string;
    summary?: string;
    /** 典型形状:{ patterns?: string[] } 或 { phrases?: string[] } */
    evidence?: unknown;
  }>;
  /** 汇总结论:可能是数组、对象或字符串,渲染层容错解析 */
  findings?: unknown;
  [key: string]: unknown;
}

// ---- M10 反馈-分诊-沉淀闭环 ----

/** 分诊子 agent 的归类归因结果(POST /api/v1/feedback 响应与列表项内嵌) */
export interface FeedbackTriage {
  /** 固定 taxonomy:positive/kb_gap/retrieval_miss/hallucination/claim_violation/... */
  category: string;
  root_cause: string;
  /** 沉淀 sink:none/knowledge_candidate/golden_candidate/badcase_memory/memory_only */
  sink: string;
  /** rule | llm(LLM 增强失败自动回退 rule) */
  source: string;
  rule_hits?: string[];
  suggested_fix?: string;
  /** 沉淀产物引用:候选知识 id / 黄金集文件路径等 */
  sink_ref?: string | null;
}

/** POST /api/v1/feedback 载荷 */
export interface FeedbackPayload {
  workflow_id?: string | null;
  target_type: 'support_draft' | 'listing_copy' | 'plan' | 'research_brief' | 'other';
  target_key?: string | null;
  verdict: 'helpful' | 'unhelpful';
  comment?: string;
  quote?: string;
}

/** POST /api/v1/feedback 响应(201,同步返回分诊结果) */
export interface FeedbackCreated extends FeedbackTriage {
  id: string;
  status: string;
}

/** GET /api/v1/feedback 列表项 */
export interface FeedbackItem {
  id: string;
  workflow_id: string | null;
  target_type: string;
  target_key: string | null;
  verdict: 'helpful' | 'unhelpful';
  comment: string | null;
  quote: string | null;
  triage: FeedbackTriage | null;
  status: string;
  created_at: string;
}

/** GET /api/v1/knowledge/candidates 列表项(反馈沉淀的待审知识) */
export interface KnowledgeCandidate {
  id: string;
  category: string;
  title: string;
  content: string;
  ref: string | null;
  meta: { feedback_id?: string; status?: string; origin?: string } & Record<string, unknown>;
  created_at: string;
}

/** POST /api/v1/knowledge/{id}/review 载荷 */
export interface KnowledgeReviewPayload {
  action: 'approve' | 'reject';
}
