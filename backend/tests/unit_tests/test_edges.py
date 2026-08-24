"""路由边单元测试：循环硬上限与分支语义。"""

from app.graphs.product_launch.edges import (
    route_after_approval,
    route_after_critic,
    route_after_gate,
    route_after_research,
)


def test_deepen_when_below_threshold_and_under_cap():
    state = {"research_evidence_score": 0.55, "research_rounds": 1}
    assert route_after_research(state) == "deepen"


def test_gate_when_score_ok():
    state = {"research_evidence_score": 0.9, "research_rounds": 0}
    assert route_after_research(state) == "gate"


def test_hard_stop_at_research_cap():
    # 分数再低，轮次达到上限也必须离开回路（防死循环护栏）
    state = {"research_evidence_score": 0.1, "research_rounds": 2}
    assert route_after_research(state) == "gate"


def test_gate_routing():
    assert route_after_gate({"go_no_go": "proceed"}) == "proceed"
    assert route_after_gate({"go_no_go": "abort"}) == "halt"
    assert route_after_gate({}) == "halt"


def test_critic_rewrite_when_issues_and_under_cap():
    state = {"critique_issues": [{"phrase": "保证"}], "critique_rounds": 2}
    assert route_after_critic(state) == "rewrite"


def test_critic_approve_when_no_issues():
    assert route_after_critic({"critique_issues": [], "critique_rounds": 3}) == "approve"


def test_critic_hard_stop_at_cap():
    state = {"critique_issues": [{"phrase": "保证"}], "critique_rounds": 3}
    assert route_after_critic(state) == "approve"


def test_approval_routing():
    assert route_after_approval({"approved": True}) == "publish"
    assert route_after_approval({}) == "halt"
