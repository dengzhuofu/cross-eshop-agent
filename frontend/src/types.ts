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
}

/** GET /api/v1/workflows/{id}/trace */
export interface TraceResponse {
  workflow: { id: string; status: string; error: string | null };
  steps: WorkflowStep[];
  decisions: DecisionRecord[];
  tool_calls: ToolCall[];
}
