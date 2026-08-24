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
    supplier_reselect = "supplier_reselect"
    replan = "replan"
    ops_suggestion = "ops_suggestion"
    bad_case_handling = "bad_case_handling"


class MemoryType(str, Enum):
    """M4 启用 episodic/semantic 两条线；procedural 为 Phase 2 接缝（v1.4 §1.3）。"""

    episodic = "episodic"
    semantic = "semantic"
    procedural = "procedural"


class BadCaseCategory(str, Enum):
    """PRD §20.1 八类；MVP 只实现 A/B/H 三条 seed 的 detector（v1.4 §1.5），枚举保留全集。"""

    a_input = "A_input"
    b_output = "B_output"
    c_computation = "C_computation"
    d_tool = "D_tool"
    e_process = "E_process"
    f_memory = "F_memory"
    g_context = "G_context"
    h_business = "H_business"
