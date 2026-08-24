"""product_launch 图状态定义（含共享 scratchpad，PRD §11.3）。

规则（v1.4 §18 目录职责）：state 只保存可序列化业务状态与 reducer，不存客户端/密钥。
节点只返回 state 增量；循环计数（research_rounds/critique_rounds）由 edges 消费防死循环。
"""

from typing import Any, Dict, List

from typing_extensions import TypedDict


def new_scratchpad(task: str) -> Dict[str, Any]:
    """三角之间交换结构化中间产物的共享工作内存。"""
    return {
        "task": task,
        "constraints": [],  # Critic 下发给 Executor 的硬约束集
        "artifacts": {},  # 各阶段结构化产出（research/profit/supplier/listing…）
        "critique": None,  # 最近一轮 Critic 结构化评审
        "decision_briefs": {},  # 大段结果的压缩摘要（M4 接 summarization）
    }


class ProductLaunchState(TypedDict, total=False):
    workflow_id: str
    tenant_id: str
    task_input: Dict[str, Any]  # {product_idea, marketplaces[], target_market, risk_preference}

    scratchpad: Dict[str, Any]

    # 研究阶段（自主深化回路：最多 MAX_RESEARCH_ROUNDS 轮）
    research_evidence_score: float
    research_rounds: int

    # 利润 / 供应商
    profit: Dict[str, Any]
    suppliers: Dict[str, Any]

    # go/no-go 闸门（PRD §7.13）
    go_no_go: str  # proceed | revise | abort

    # Listing 生成-评审-重写闭环（最多 MAX_CRITIQUE_ROUNDS 轮）
    listings: List[Dict[str, Any]]
    critique_issues: List[Dict[str, Any]]
    critique_rounds: int

    approved: bool
    approval_decision: Dict[str, Any]  # M5：人工审批结果 {approved, comment}（halted 节点消费）

    # LLM 计量（PRD §17 接缝：M2 累计 + 阈值告警日志，M4 接预算控制器）
    llm_usage: Dict[str, Any]

    published: List[Dict[str, Any]]
    ops: Dict[str, Any]
    support: Dict[str, Any]
    retrospective: Dict[str, Any]

    error: str


def initial_state(
    workflow_id: str, tenant_id: str, task_input: Dict[str, Any]
) -> ProductLaunchState:
    return {
        "workflow_id": workflow_id,
        "tenant_id": tenant_id,
        "task_input": task_input,
        "scratchpad": new_scratchpad(task_input.get("product_idea", "")),
        "research_evidence_score": 1.0,
        "research_rounds": 0,
        "critique_rounds": 0,
        "listings": [],
        "critique_issues": [],
        "published": [],
        "approved": False,
    }
