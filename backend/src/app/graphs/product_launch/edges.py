"""纯路由函数（无副作用，v1.4 目录职责约束：edges.py 不做业务计算）。

循环硬上限从 Settings 读取（PRD §14.3 护栏）：研究深化 ≤ MAX_RESEARCH_ROUNDS、
Critique 重写 ≤ MAX_CRITIQUE_ROUNDS，防止死循环。
"""

from typing import Any, Dict

from app.config import get_settings


def route_after_research(state: Dict[str, Any]) -> str:
    s = get_settings()
    rounds = state.get("research_rounds", 0)
    if (
        state.get("research_evidence_score", 1.0) < s.evidence_threshold
        and rounds < s.max_research_rounds
    ):
        return "deepen"
    return "gate"


def route_after_gate(state: Dict[str, Any]) -> str:
    return "proceed" if state.get("go_no_go") == "proceed" else "halt"


def route_after_critic(state: Dict[str, Any]) -> str:
    s = get_settings()
    if state.get("critique_issues") and state.get("critique_rounds", 0) < s.max_critique_rounds:
        return "rewrite"
    return "approve"


def route_after_approval(state: Dict[str, Any]) -> str:
    return "publish" if state.get("approved") else "halt"
