import type { Tenant, Tone } from './types';

/* ------------------------------------------------------------------ */
/* 中文标签映射:界面所有状态/节点/决策类型一律展示中文,原始值仅作 key。 */
/* ------------------------------------------------------------------ */

/** 工作流状态 → 中文 */
export const WORKFLOW_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  queued: '排队中',
  running: '运行中',
  planning: '规划中',
  researching: '市场研究中',
  analyzing_profit: '利润测算',
  evaluating_suppliers: '供应商评估',
  decision_gate: '决策门',
  drafting_listings: 'Listing 生成',
  critique_loop: '审查重写',
  awaiting_approval: '待人工审批',
  executing: '发布执行',
  monitoring: '运营监控',
  handling_support: '客服处理',
  retrospective: '复盘',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  blocked: '阻塞待人工',
  quarantined: '已隔离',
  reroute: '重路由',
};

/** 图节点名 → 中文 */
export const NODE_LABELS: Record<string, string> = {
  planner: '规划器',
  research: '市场研究',
  profit: '利润测算',
  supplier: '供应商筛选',
  decision_gate: '决策门',
  listing: 'Listing 生成',
  critic: '合规审查',
  approval_check: '审批闸门',
  publish: '多平台发布',
  ops: '运营分析',
  support: '客服',
  retrospective: '复盘',
  halted: '终止',
  bad_case_scan: '坏例防线扫描',
};

/** Agent 决策类型 → 中文 */
export const DECISION_TYPE_LABELS: Record<string, string> = {
  plan: '规划',
  research_deepening: '研究深化',
  go_no_go: '取舍决策(go/no-go)',
  rewrite: '打回重写',
  auto_approval: '自动审批',
  human_approval: '人工审批',
  supplier_reselect: '供应商重选',
  replan: '重规划',
  ops_suggestion: '运营建议',
  support_reply: '客服回复',
  bad_case_handling: '坏例处置',
};

/** 工具调用风险等级 → 中文 */
export const RISK_LEVEL_LABELS: Record<string, string> = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
};

/** 工具调用状态 → 中文(ok 绿 / error 红 / replayed 蓝) */
export const TOOL_STATUS_LABELS: Record<string, string> = {
  ok: '成功',
  error: '失败',
  replayed: '已重放',
};

/** 步骤状态 → 中文 */
export const STEP_STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  ok: '成功',
  running: '执行中',
  started: '执行中',
  pending: '等待中',
  skipped: '已跳过',
  error: '出错',
  failed: '失败',
};

/** Agent 名 → 中文(human_approver 为 HITL 人工审批者) */
export const AGENT_LABELS: Record<string, string> = {
  human_approver: '人工审批者',
  support_agent: '客服专员',
  ops_analyst: '运营分析师',
};

/** 渠道显示名 */
export const MARKETPLACE_LABELS: Record<string, string> = {
  amazon: 'Amazon',
  shopify: 'Shopify',
  tiktok_shop: 'TikTok Shop',
};

/** Bad Case 类别 → 中文(PRD 八类防线) */
export const BADCASE_CATEGORY_LABELS: Record<string, string> = {
  input_anomaly: '输入异常',
  output_runaway: '输出失控',
  calc_anomaly: '计算异常',
  tool_failure: '工具故障',
  flow_anomaly: '流程异常',
  memory_anomaly: '记忆异常',
  context_anomaly: '上下文异常',
  biz_violation: '业务违规',
};

/** Bad Case 处置状态 → 中文 */
export const BADCASE_STATUS_LABELS: Record<string, string> = {
  detected: '已检出',
  quarantined: '已隔离',
  resolved: '已处置',
  escalated: '已升级',
  aborted: '已中止',
};

/** 坏例检测器 → 中文 */
export const DETECTOR_LABELS: Record<string, string> = {
  input_injection: '提示注入',
  output_absolute_claims: '违禁声明',
  memory_poisoning: '记忆投毒',
};

/** bad_case_scan 扫描步骤的 origin → 中文(扫描发生在哪条链路上) */
export const BADCASE_ORIGIN_LABELS: Record<string, string> = {
  planner: '选题输入',
  listing: 'Listing 产出',
  retrospective: '记忆回写',
};

/** 两个演示租户(与后端种子数据一致) */
export const TENANTS: Tenant[] = [
  { id: 't_demo_acme', name: 'Acme Cross-border' },
  { id: 't_demo_globex', name: 'Globex Trading' },
];

/* ------------------------------------------------------------------ */
/* 终态判定 + 徽章配色                                                 */
/* ------------------------------------------------------------------ */

/** 终态集合:命中后停止轮询 */
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'blocked', 'quarantined']);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/**
 * 状态 → 徽章色调:
 * 绿 = 已完成;琥珀 = 进行中;红 = 失败/隔离;蓝 = 排队等信息态;灰 = 取消。
 */
export function workflowStatusTone(status: string): Tone {
  if (status === 'completed') return 'green';
  if (status === 'failed' || status === 'quarantined') return 'red';
  if (status === 'queued' || status === 'draft') return 'blue';
  if (status === 'cancelled') return 'gray';
  return 'amber'; // 其余一律视为进行中
}

/** 步骤状态 → 色调 */
export function stepStatusTone(status: string): Tone {
  const s = status.toLowerCase();
  if (s === 'completed' || s === 'ok') return 'green';
  if (s === 'error' || s === 'failed') return 'red';
  if (s === 'skipped' || s === 'pending') return 'gray';
  return 'amber';
}

/** 工具调用状态 → 色调 */
export function toolStatusTone(status: string): Tone {
  const s = status.toLowerCase();
  if (s === 'ok' || s === 'success') return 'green';
  if (s === 'error' || s === 'failed') return 'red';
  if (s === 'replayed') return 'blue';
  return 'gray';
}

/** 风险等级 → 色调(高=红) */
export function riskLevelTone(level: string): Tone {
  const l = level.toLowerCase();
  if (l === 'high') return 'red';
  if (l === 'medium') return 'amber';
  return 'gray';
}

/** 坏例严重度 → 色调(高=红 / 中=琥珀 / 低=灰) */
export function badCaseSeverityTone(severity: string): Tone {
  return riskLevelTone(severity);
}

/**
 * 坏例处置状态 → 色调:
 * 蓝 = 已检出(信息态);琥珀 = 已隔离(已控制但未了结);红 = 已升级(需人工介入);
 * 绿 = 已处置;灰 = 已中止。
 */
export function badCaseStatusTone(status: string): Tone {
  switch (status.toLowerCase()) {
    case 'resolved':
      return 'green';
    case 'quarantined':
      return 'amber';
    case 'escalated':
      return 'red';
    case 'detected':
      return 'blue';
    default: // aborted 及未知值
      return 'gray';
  }
}

/** 坏例类别 → 色调(仅作轻量区分,严重程度交给 severity 徽标表达) */
export function badCaseCategoryTone(category: string): Tone {
  switch (category) {
    case 'input_anomaly':
      return 'purple';
    case 'output_runaway':
    case 'biz_violation':
      return 'red';
    case 'calc_anomaly':
      return 'amber';
    case 'tool_failure':
      return 'teal';
    case 'memory_anomaly':
      return 'green';
    default: // flow_anomaly / context_anomaly
      return 'blue';
  }
}

/** bad_case_scan 扫描来源 → 色调 */
export function badCaseOriginTone(origin: string): Tone {
  switch (origin) {
    case 'planner':
      return 'blue';
    case 'listing':
      return 'purple';
    case 'retrospective':
      return 'teal';
    default:
      return 'gray';
  }
}

/** 决策类型 → 卡片左侧色条颜色(不同类型一眼可辨) */
export function decisionTypeTone(type: string): Tone {
  switch (type) {
    case 'go_no_go':
    case 'replan':
      return 'purple';
    case 'auto_approval':
      return 'green';
    case 'rewrite':
    case 'ops_suggestion':
      return 'amber';
    case 'bad_case_handling':
      return 'red';
    case 'supplier_reselect':
      return 'teal';
    default: // plan / research_deepening
      return 'blue';
  }
}

/* ------------------------------------------------------------------ */
/* 展示辅助                                                           */
/* ------------------------------------------------------------------ */

export function statusLabel(status: string | null | undefined): string {
  if (!status) return '—';
  return WORKFLOW_STATUS_LABELS[status] ?? status;
}

export function nodeLabel(node: string | null | undefined): string {
  if (!node) return '—';
  return NODE_LABELS[node] ?? node;
}

export function agentLabel(agent: string | null | undefined): string {
  if (!agent) return '—';
  return AGENT_LABELS[agent] ?? agent;
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return `${ms} ms`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { hour12: false });
}
