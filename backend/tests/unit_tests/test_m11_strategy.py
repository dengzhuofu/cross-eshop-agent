"""M11 单测：检索增强策略选择（app.rag.strategy）+ 节点侧提示词路由契约。

规则策略与提议校验是纯函数零网络；策略枚举与升级阶梯是代码常量——
一致性在这里钉死（LLM 只提议，枚举外的提议一律弃用回退规则结果）。
"""

from app.graphs.product_launch import nodes as N
from app.rag.strategy import (
    ESCALATION,
    STRATEGIES,
    deterministic_strategy,
    normalize_proposal,
)


def _fallback_plan() -> dict:
    return {
        "strategy": "rewrite",
        "query": "退换货政策",
        "query_source": "deterministic",
        "strategy_source": "rule",
        "reason": "",
    }


# ---- 枚举与升级阶梯自洽 ----


def test_strategy_enum_and_escalation_ladder_are_consistent():
    assert STRATEGIES == ("direct", "rewrite", "hyde")
    assert set(ESCALATION) == set(STRATEGIES), "阶梯必须覆盖全部策略"
    assert all(v in STRATEGIES for v in ESCALATION.values())
    # 阶梯只上不下；hyde 是终点（重试换角度重新生成，而非换档）
    assert ESCALATION["direct"] == "rewrite"
    assert ESCALATION["rewrite"] == "hyde"
    assert ESCALATION["hyde"] == "hyde"


# ---- 规则策略：短+精确信号直检 / 长+问题式 HyDE / 默认改写 ----


def test_short_query_with_exact_signal_goes_direct():
    assert deterministic_strategy("ORD88123 到哪了") == "direct"
    boundary = "查一下 ORD88123 啊"
    assert len(boundary) <= 14, "恰好压在直检长度上限"
    assert deterministic_strategy(boundary) == "direct"


def test_long_vague_question_without_signal_goes_hyde():
    long_q = (
        "我在你们店里买的东西已经用了好几天了，但是家人说不太合适，"
        "想问一下这种情况下还能不能退换货呀？"
    )
    assert len(long_q) >= 40
    assert deterministic_strategy(long_q) == "hyde"


def test_signal_blocks_hyde_even_for_long_questions():
    # 含精确信号的长问题仍走 rewrite：词面本身是最强特征，无需假设文档泛化
    assert deterministic_strategy(
        "我的订单 ORD88123 到底什么时候才能到货啊，等了好久了"
    ) == "rewrite"


def test_default_and_edge_cases_fall_back_to_rewrite():
    assert deterministic_strategy("你们的退换货政策是怎样的？") == "rewrite"  # 中短无信号
    assert deterministic_strategy("帮查下 ORD88123 到哪了") == "rewrite"  # 带信号但超长
    # 问题式但不够长
    assert deterministic_strategy("家人说不太合适想问问还能不能退换货呢") == "rewrite"
    assert deterministic_strategy("") == "rewrite"
    assert deterministic_strategy("   ") == "rewrite"


# ---- LLM 提议校验：越界/缺失整体弃用；合法只覆盖 strategy/strategy_source/reason ----


def test_normalize_proposal_accepts_valid_and_normalizes_case():
    fb = _fallback_plan()
    out = normalize_proposal({"strategy": "HyDE", "reason": "长问题需语义泛化"}, fb)
    assert out["strategy"] == "hyde"
    assert out["strategy_source"] == "llm"
    assert out["reason"] == "长问题需语义泛化"
    # 其余字段保持规则结果不动
    assert out["query"] == fb["query"]
    assert out["query_source"] == "deterministic"


def test_normalize_proposal_discards_invalid_or_missing_strategy():
    fb = _fallback_plan()
    for parsed in ({"strategy": "magic", "query": "x"}, {}, None):
        out = normalize_proposal(parsed, fb)
        assert out == fb, "枚举外/缺失的提议必须整体弃用回退规则结果"
        assert out is not fb  # 返回副本，不共享可变状态


def test_normalize_proposal_truncates_reason_and_keeps_fallback_intact():
    fb = _fallback_plan()
    snapshot = dict(fb)
    out = normalize_proposal({"strategy": "direct", "reason": "r" * 300}, fb)
    assert len(out["reason"]) == 120
    assert fb == snapshot, "fallback 不得被原地修改"


# ---- 假客户端按 system 提示词子串路由的契约（既有 M9/M11 测试都依赖）----


def test_prompt_routing_contract_substrings():
    assert "策略规划器" in N.SUPPORT_STRATEGY_SYSTEM_PROMPT
    assert "查询改写器" in N.SUPPORT_STRATEGY_SYSTEM_PROMPT, "M9 test6 的路由子串不得丢失"
    assert "质检员" in N.SUPPORT_GRADE_SYSTEM_PROMPT
    assert "HyDE" in N.SUPPORT_HYDE_SYSTEM_PROMPT
