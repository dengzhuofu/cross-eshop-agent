"""业务枚举。取值与 PRD §8.4 / §13 / §20 对齐；新增值先改这里，不改散落各处的字符串。"""

from enum import Enum


class WorkflowStatus(str, Enum):
    draft = "draft"
    queued = "queued"
    planning = "planning"
    researching = "researching"
    decision_gate = "decision_gate"
    analyzing_profit = "analyzing_profit"
    evaluating_suppliers = "evaluating_suppliers"
    drafting_listings = "drafting_listings"
    critique_loop = "critique_loop"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    monitoring = "monitoring"
    handling_support = "handling_support"
    retrospective = "retrospective"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"
    reroute = "reroute"
    quarantined = "quarantined"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AgentDecisionType(str, Enum):
    plan = "plan"
    research_deepening = "research_deepening"
    go_no_go = "go_no_go"
    rewrite = "rewrite"
    auto_approval = "auto_approval"  # 仅 dev 模式；M5 起由人工审批取代
    human_approval = "human_approval"  # M5：Approval Center 的人工通过/驳回
    supplier_reselect = "supplier_reselect"
    replan = "replan"
    ops_suggestion = "ops_suggestion"
    support_reply = "support_reply"  # M6：客服回复草稿（RAG 引用 + 工具实时事实）
    bad_case_handling = "bad_case_handling"


class MemoryType(str, Enum):
    """M4 启用 episodic/semantic 两条线；procedural 为 Phase 2 接缝（v1.4 §1.3）。"""

    episodic = "episodic"
    semantic = "semantic"
    procedural = "procedural"


class BadCaseCategory(str, Enum):
    """八类分类法（PRD §20.1）。detector 注册表按类独立实现、独立注册（v1.4 §1.5）。"""

    input_anomaly = "input_anomaly"        # A：注入/超长/非法 schema/PII
    output_runaway = "output_runaway"      # B：幻觉/schema 不符/夸大声明
    calc_anomaly = "calc_anomaly"          # C：除零/缺输入/负利润
    tool_failure = "tool_failure"          # D：adapter 错误/限流/超时
    flow_anomaly = "flow_anomaly"          # E：死循环/审批拒绝/状态机卡死
    memory_anomaly = "memory_anomaly"      # F：记忆投毒/膨胀/不相关
    context_anomaly = "context_anomaly"    # G：token 超预算/压缩丢信息
    biz_violation = "biz_violation"        # H：违规 Listing/过度承诺/退款异常


class BadCaseStatus(str, Enum):
    """状态机（PRD §20.4）：detected → quarantined → retry/reroute/escalate/abort → resolved。"""

    detected = "detected"
    quarantined = "quarantined"
    resolved = "resolved"
    escalated = "escalated"
    aborted = "aborted"
