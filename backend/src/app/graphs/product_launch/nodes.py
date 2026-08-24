"""M0 stub 执行器节点。

全部为确定性内容、零外部 LLM 调用。M2 起逐个替换为真实现（LLM + typed tools），
但节点签名 (state, config) -> state 增量、以及通过 recorder 落 trace/决策的方式保持不变——
这是 walking skeleton 的意义：先把骨架契约钉死。

每个自主决策点都写 AgentDecision（理由 + 备选项），对应 PRD §8.3 决策点清单。
"""

import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from app.config import get_settings
from app.domain.enums import AgentDecisionType, WorkflowStatus
from app.observability.recorder import recorder_from_config

# Critic 声明黑名单（MVP 规则校验器雏形；M7 移入 guardrails.detectors）
BANNED_CLAIM_PHRASES = ("保证", "100%", "治愈", "根治")


def _sp(state: Dict[str, Any]) -> Dict[str, Any]:
    return dict(state.get("scratchpad") or {})


def _artifact(state: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
    sp = _sp(state)
    artifacts = dict(sp.get("artifacts") or {})
    artifacts[key] = value
    sp["artifacts"] = artifacts
    return sp


def _constraint(state: Dict[str, Any], c: str) -> Dict[str, Any]:
    sp = _sp(state)
    constraints = list(sp.get("constraints") or [])
    if c not in constraints:
        constraints.append(c)
    sp["constraints"] = constraints
    return sp


async def node_planner(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    plan = {
        "goal": f"为「{task_input.get('product_idea')}」完成选品到铺货全链路",
        "marketplaces": task_input.get("marketplaces") or ["amazon", "shopify", "tiktok_shop"],
        "target_market": task_input.get("target_market", "US"),
    }
    await rec.status(WorkflowStatus.planning.value)
    await rec.step("planner", detail=plan, latency_ms=int((time.perf_counter() - t0) * 1000))
    await rec.decision(
        agent="planner",
        decision_type=AgentDecisionType.plan.value,
        reasoning="按主链路规划：研究(可深化)→利润→供应商→go/no-go→Listing(可重写)→审批→发布→运营→客服→复盘",
        chosen_option="proceed_to_research",
        alternatives=["abort"],
    )
    return {"scratchpad": _artifact(state, "plan", plan)}


async def node_research(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    s = get_settings()
    t0 = time.perf_counter()
    rounds = state.get("research_rounds", 0)

    # stub 剧情（对齐 PRD §24 demo 步骤 3）：首轮缺评论/关键词证据 → 分数低于阈值；
    # 深化一轮补齐后达标。M2 替换为 search_market_trends 等 typed tools 的真实评分。
    score = 0.82 if rounds >= 1 else 0.55
    refs = (
        ["ev_trend_001", "ev_comp_014"]
        if rounds == 0
        else ["ev_trend_001", "ev_comp_014", "ev_rev_103", "ev_kw_021"]
    )
    brief = {
        "round": rounds + 1,
        "evidence_score": score,
        "demand_signal": "床底收纳近90天搜索环比 +23%",
        "competitor_gap": "头部竞品普遍不支持折叠，差评集中在占空间",
        "review_pain_points": ["易塌陷", "异味", "拉链损坏"],
        "evidence_refs": refs,
    }
    await rec.status(WorkflowStatus.researching.value)
    await rec.step(
        "research",
        detail={"round": rounds + 1, "evidence_score": score},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    if score < s.evidence_threshold:
        await rec.decision(
            agent="planner",
            decision_type=AgentDecisionType.research_deepening.value,
            reasoning=(
                f"证据完整度 {score:.2f} 低于阈值 {s.evidence_threshold}，"
                "缺少评论痛点与关键词维度，触发第二轮研究补充证据"
            ),
            chosen_option="deepen",
            alternatives=["abort"],
        )
    return {
        "research_evidence_score": score,
        "research_rounds": rounds + 1,
        "scratchpad": _artifact(state, "research_brief", brief),
    }


async def node_profit(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()

    # 确定性计算（不由 LLM 在文本里算，PRD §7.3）。M2 换成 estimate_landed_cost 等工具的真实输出。
    revenue = 29.99
    landed_cost = 9.80
    platform_fee = 4.50
    fulfillment = 4.50
    ads = 3.00
    total_cost = round(landed_cost + platform_fee + fulfillment + ads, 2)
    contribution = round(revenue - total_cost, 2)
    margin = round(contribution / revenue, 4)
    profit = {
        "assumptions": {
            "sale_price": revenue,
            "landed_cost": landed_cost,
            "platform_fee": platform_fee,
            "fulfillment": fulfillment,
            "ads_budget": ads,
            "return_rate": 0.04,
        },
        "contribution_profit": contribution,
        "margin_pct": margin,
        "break_even_price": round(total_cost / (1 - 0.15), 2),
        "sensitivity": {"ads+1usd": "-3.3pp margin", "return_rate x2": "-4.1pp margin"},
    }
    await rec.status(WorkflowStatus.analyzing_profit.value)
    await rec.step(
        "profit",
        detail={"margin_pct": margin, "contribution": contribution},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"profit": profit}


async def node_supplier(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()

    # M4 接缝：memory_hit 将由 retrieve_memory 实时检索供应商风险记忆；
    # 此处为 seed 数据，演示"历史高风险供应商自动降权"的展示形态。
    candidates: List[Dict[str, Any]] = [
        {
            "id": "sup_001",
            "name": "Ningbo Foldable Factory",
            "price_usd": 6.80,
            "moq": 500,
            "lead_time_days": 25,
            "quality_score": 86,
            "risk": "low",
        },
        {
            "id": "sup_002",
            "name": "Yiwu General Trading",
            "price_usd": 5.90,
            "moq": 300,
            "lead_time_days": 35,
            "quality_score": 41,
            "risk": "high",
            "memory_hit": {
                "source_workflow_id": "wf_seed_2026_07",
                "reason": "历史缺陷率 12% 超标被标记",
            },
        },
    ]
    suppliers = {
        "primary": "sup_001",
        "backup": None,
        "candidates": candidates,
        "risk_flags": ["sup_002 已按历史风险记忆降权"],
    }
    await rec.status(WorkflowStatus.evaluating_suppliers.value)
    await rec.step(
        "supplier",
        detail={"primary": suppliers["primary"], "flags": suppliers["risk_flags"]},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"suppliers": suppliers}


async def node_decision_gate(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """顶层 go/no-go（PRD §7.13）：综合研究/利润/供应商证据做显式决策。"""
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    margin = (state.get("profit") or {}).get("margin_pct", 0)
    score = state.get("research_evidence_score", 0)

    # stub 判定规则（M2 由 Planner LLM 结合 rubric 决策）：利润≥15% 且证据≥阈值 即 proceed
    go = "proceed" if margin >= 0.15 and score >= get_settings().evidence_threshold else "abort"
    reasoning = (
        f"证据完整度 {score:.2f}、贡献利润率 {margin:.1%}；"
        "利润与证据均达阈值，主供应商风险低，决定进入 Listing 生成"
    )
    await rec.status(WorkflowStatus.decision_gate.value)
    await rec.step(
        "decision_gate", detail={"chosen": go}, latency_ms=int((time.perf_counter() - t0) * 1000)
    )
    await rec.decision(
        agent="planner",
        decision_type=AgentDecisionType.go_no_go.value,
        reasoning=reasoning,
        chosen_option=go,
        alternatives=["revise", "abort"],
    )
    return {"go_no_go": go}


async def node_listing(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    idea = task_input.get("product_idea", "Foldable Under-Bed Storage Box")
    marketplaces = task_input.get("marketplaces") or ["amazon", "shopify", "tiktok_shop"]
    rounds = state.get("critique_rounds", 0)
    constraints = (_sp(state).get("constraints")) or []

    drafts: List[Dict[str, Any]] = []
    for mp in marketplaces:
        if rounds == 0:
            # 故意埋雷：首轮包含无证据绝对化声明，确保 Critic 打回（demo §24 步骤 8）
            claim = "保证10年不坏，100%承重无变形"
        else:
            # 应用 Critic 约束后的可证实表述（可 diff 证明约束生效）
            claim = "采用加厚 PP 材质，实验室测试承重 40kg"
        flavor = {
            "amazon": ["Fits under most beds", "Foldable flat in 3s", "Reinforced zipper"],
            "shopify": ["SEO: under bed storage", "Story block: 小空间收纳灵感"],
            "tiktok_shop": ["3秒折叠！床底瞬间扩容", "Before/After 对比镜头"],
        }.get(mp, ["Durable foldable storage"])
        drafts.append(
            {
                "marketplace": mp,
                "title": f"{idea} | Foldable Under-Bed Storage Box",
                "bullets": flavor,
                "claim": claim,
                "keywords": ["under bed storage", "foldable box", "bedroom organizer"],
                "image_brief": {
                    "main": "白底主图：展开态45°角",
                    "scene": "床底推入场景",
                    "infographic": "尺寸对比与承重标注",
                },  # 生图为 Phase 2 接缝（v1.4 §1.1），MVP 只出文字 brief
            }
        )
    await rec.status(WorkflowStatus.drafting_listings.value)
    detail: Dict[str, Any] = {"count": len(drafts), "round": rounds + 1}
    if rounds > 0:
        detail["applied_constraints"] = constraints
    await rec.step("listing", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000))
    return {"listings": drafts}


async def node_critic(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    issues: List[Dict[str, Any]] = []
    for d in state.get("listings") or []:
        for field in ("title", "claim"):
            text = str(d.get(field) or "")
            for phrase in BANNED_CLAIM_PHRASES:
                if phrase in text:
                    issues.append(
                        {
                            "marketplace": d.get("marketplace"),
                            "field": field,
                            "phrase": phrase,
                            "severity": "high",
                            "rule": "无证据绝对化声明",
                        }
                    )

    if issues:
        critique = {
            "issues": issues,
            "constraints": ["移除所有无证据的绝对化声明，替换为可证实表述（材质/承重数据）"],
        }
        await rec.status(WorkflowStatus.critique_loop.value)
        await rec.step(
            "critic",
            detail={"verdict": "rewrite", "issue_count": len(issues)},
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        await rec.decision(
            agent="critic",
            decision_type=AgentDecisionType.rewrite.value,
            reasoning=f"发现 {len(issues)} 处无证据绝对化声明，打回重写并下发约束",
            chosen_option="rewrite",
            alternatives=["escalate_to_human"],
        )
        sp = _constraint(state, critique["constraints"][0])
        sp["critique"] = critique
        return {
            "critique_issues": issues,
            "critique_rounds": state.get("critique_rounds", 0) + 1,
            "scratchpad": sp,
        }

    await rec.step(
        "critic",
        detail={"verdict": "pass"},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    sp = _sp(state)
    sp["critique"] = None
    return {"critique_issues": [], "scratchpad": sp}


async def node_approval_check(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """高风险动作闸门。AUTO_APPROVE=true 仅限 M0 dev 演示；M5 替换为 interrupt() 人工审批。"""
    rec = recorder_from_config(config)
    await rec.status(WorkflowStatus.awaiting_approval.value)
    if get_settings().auto_approve:
        await rec.step("approval_check", detail={"mode": "auto_approve(dev)"})
        await rec.decision(
            agent="system",
            decision_type=AgentDecisionType.auto_approval.value,
            reasoning="AUTO_APPROVE=true 的 dev 演示模式自动放行；生产路径必须人工审批（PRD §7.8）",
            chosen_option="approve",
        )
        return {"approved": True}
    # M5 接入点：此处将调用 langgraph interrupt() 暂停等待 Approval Center 的人工决定
    await rec.step("approval_check", status="blocked", detail={"mode": "manual_pending(M5)"})
    return {"approved": False}


async def node_publish(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    wf_id = state["workflow_id"]
    published = []
    for idx, d in enumerate(state.get("listings") or []):
        mp = d.get("marketplace", f"mp_{idx}")
        published.append(
            {
                "marketplace": mp,
                # M1 接缝：改为 MockAmazonAdapter.publish_listing(payload, idempotency_key)
                "listing_id": f"{mp[:3].lower()}_{wf_id[:8]}_{idx + 1}",
                "status": "published",
                "idempotency_key": f"pub_{wf_id}_{mp}",
            }
        )
    await rec.status(WorkflowStatus.executing.value)
    await rec.step(
        "publish",
        detail={"published": len(published)},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"published": published}


async def node_ops(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    metrics = {
        "impressions": 12400,
        "clicks": 512,
        "ctr": 0.041,
        "conversion": 0.018,
        "orders": 9,
        "refund_signals": 1,
        "window_days": 7,
    }
    suggestion = {
        "action": "优化 TikTok 主图视频前 3 秒钩子",
        "risk_level": "high",
        "needs_approval": True,  # 高风险动作一律人工（PRD §14.1）
    }
    await rec.status(WorkflowStatus.monitoring.value)
    await rec.step(
        "ops",
        detail={"orders": metrics["orders"]},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    await rec.decision(
        agent="ops_analyst",
        decision_type=AgentDecisionType.ops_suggestion.value,
        reasoning="TikTok 转化率 1.8% 低于类目基准，建议优化素材；属高风险动作，仅建议、需人工审批",
        chosen_option="suggest_optimize",
        alternatives=["no_action"],
    )
    return {"ops": {"metrics": metrics, "suggestion": suggestion}}


async def node_support(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    # M6 接缝：订单状态走业务工具、政策引用走 RAG（PRD §7.11 融合优先级铁律）
    support = {
        "ticket_id": "tk_1042",
        "type": "where_is_my_order",
        "order_id": "ord_88123",
        "draft": (
            "您好，订单 ord_88123 当前物流状态为「国际运输中」，预计 3-5 个工作日内到达。"
            "如需延迟处理方案，可参考《退换货政策》POL-RTN-07 v2.1 第 3 条。"
        ),
        "refs": ["POL-RTN-07 v2.1"],
    }
    await rec.status(WorkflowStatus.handling_support.value)
    await rec.step(
        "support",
        detail={"ticket": support["ticket_id"]},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"support": support}


async def node_retrospective(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    published = state.get("published") or []
    retrospective = {
        "summary": (
            f"「{state['task_input'].get('product_idea')}」完成全链路："
            f"研究深化 {state.get('research_rounds', 0)} 轮，"
            f"Critique 重写 {state.get('critique_rounds', 0)} 轮，"
            f"{len(published)} 个平台已发布"
        ),
        "key_decisions": [
            "go/no-go = proceed",
            f"研究深化 x{max(state.get('research_rounds', 0) - 1, 0)}",
            f"Critic 重写 x{state.get('critique_rounds', 0)}",
        ],
        # M4 接缝：写入 memory_records（episodic），供后续工作流 retrieve_memory 命中验证
        "memory_writeback": [
            {
                "memory_type": "episodic",
                "entity_type": "category_performance",
                "content": "收纳类目 TikTok 首周转化 1.8%，偏低待验证",
            }
        ],
        "next_experiments": ["售价上探至 34.99 测试需求弹性", "补充 sup_003 备选供应商询价"],
    }
    await rec.status(WorkflowStatus.retrospective.value)
    await rec.step(
        "retrospective",
        detail=retrospective["summary"],
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    await rec.status(WorkflowStatus.completed.value)
    return {"retrospective": retrospective}


async def node_halted(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    status = (
        WorkflowStatus.cancelled.value
        if state.get("go_no_go") == "abort"
        else WorkflowStatus.blocked.value
    )
    reason = (
        "go/no-go 决策为 abort"
        if state.get("go_no_go") == "abort"
        else "等待人工审批（AUTO_APPROVE=false 且 M5 未接入 interrupt/resume）"
    )
    await rec.status(status, error=None if status == "cancelled" else reason)
    await rec.step("halted", status=status, detail={"reason": reason})
    return {}
