"""M0 stub 执行器节点。

全部为确定性内容、零外部 LLM 调用。M2 起逐个替换为真实现（LLM + typed tools），
但节点签名 (state, config) -> state 增量、以及通过 recorder 落 trace/决策的方式保持不变——
这是 walking skeleton 的意义：先把骨架契约钉死。

每个自主决策点都写 AgentDecision（理由 + 备选项），对应 PRD §8.3 决策点清单。
"""

import json
import logging
import re
import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.config import get_settings
from app.domain.enums import AgentDecisionType, WorkflowStatus
from app.llm import extract_json, get_llm_client, llm_enabled
from app.observability.recorder import recorder_from_config
from app.persistence.memory import MemoryWorkflowRepository
from app.tools import ToolContext, ToolError, execute_tool

logger = logging.getLogger(__name__)

# Critic 声明黑名单（MVP 规则校验器雏形；M7 移入 guardrails.detectors）
BANNED_CLAIM_PHRASES = ("保证", "100%", "治愈", "根治")

# LLM 文案的绝对化措辞"降级替换"表（M4）：真实冒烟中 odor-free 连续三轮换皮重现——
# 研究痛点里的"新箱异味"持续诱导生成端，prompt 约束压不住。与分数封顶同思路：
# LLM 只提议、代码做硬保证。只作用于 LLM 产物；stub 剧情的埋雷文案不动
# （那是 critic 打回演示的种子）。键匹配不区分大小写。
CLAIM_HEDGE_MAP = {
    "odor-free": "low-odor",
    "odor free": "low odor",
    "odorless": "low-odor",
    "no odor": "reduced odor",
    "100%": "",
    "保证10年不坏": "加厚 PP 材质",
}


def _sanitize_llm_copy(text: str) -> tuple[str, List[str]]:
    """对单条文案字段做违禁绝对化词的确定性改写，返回（新文本, 改动记录）。"""
    out = text
    changes: List[str] = []
    for phrase, hedge in CLAIM_HEDGE_MAP.items():
        matches = re.findall(re.escape(phrase), out, flags=re.IGNORECASE)
        if matches:
            out = re.sub(re.escape(phrase), hedge, out, flags=re.IGNORECASE)
            changes.append(f"{phrase}→{hedge or '(删除)'} x{len(matches)}")
    return re.sub(r"\s{2,}", " ", out).strip(), changes

# 卖点数量不满足平台下限时的通用补位文案（按规则动态取用）
GENERIC_BULLET_FILLERS = (
    "Sturdy handle for easy pull",
    "Wipes clean in seconds",
    "Stackable modular design",
    "Reinforced base panel",
)


def _tool_repo(config: RunnableConfig):
    """工具审计与状态同库；无 DB 的 langgraph dev 模式退化为内存仓储。"""
    rec = recorder_from_config(config)
    return getattr(rec, "repo", None) or MemoryWorkflowRepository()


def _tool_ctx(state: Dict[str, Any]) -> ToolContext:
    # 铁律：tenant_id 只由系统注入 ctx，绝不进入工具参数（PRD §13.2）
    return ToolContext(
        tenant_id=state["tenant_id"],
        workflow_id=state["workflow_id"],
        actor_id="graph",
        approved=bool(state.get("approved")),
    )


def _merge_llm_usage(
    state: Dict[str, Any], prompt_tokens: int, completion_tokens: int
) -> Dict[str, Any]:
    """PRD §17 计量接缝：M2 只累计 + 超阈值告警日志，不做硬熔断（M4 再接预算控制器）。"""
    u = dict(state.get("llm_usage") or {})
    u["calls"] = int(u.get("calls", 0)) + 1
    u["prompt_tokens"] = int(u.get("prompt_tokens", 0)) + prompt_tokens
    u["completion_tokens"] = int(u.get("completion_tokens", 0)) + completion_tokens
    u["total_tokens"] = u["prompt_tokens"] + u["completion_tokens"]
    threshold = get_settings().token_alert_threshold
    if u["total_tokens"] > threshold:
        logger.warning(
            "token budget alert: workflow %s累计 %s tokens > 阈值 %s",
            state.get("workflow_id"),
            u["total_tokens"],
            threshold,
        )
    return u


def _llm_available(state: Dict[str, Any]) -> bool:
    """所有 LLM 调用点的统一闸门（PRD §17 M4 硬熔断）：key 可用 且 预算未烧穿。
    超出 hard_budget 后本工作流后续 LLM 调用一律降级确定性路径——宁可靠规则兜底，
    不烧穿租户成本。"""
    if not llm_enabled():
        return False
    used = int((state.get("llm_usage") or {}).get("total_tokens", 0))
    return used < get_settings().llm_hard_budget


def _compress_tool_outputs(
    tool_outputs: Dict[str, Any], *, per_tool: int = 700, total: int = 2400
) -> str:
    """上下文压缩（PRD §9）：工具输出塞 prompt 前的确定性瘦身接缝。
    按工具分块截断，整体再设总闸；截断处加标记让 LLM 知道数据不完整。"""
    blocks = []
    for name, out in tool_outputs.items():
        block = json.dumps({name: out}, ensure_ascii=False)
        if len(block) > per_tool:
            block = block[:per_tool] + "…(truncated)"
        blocks.append(block)
    text = "\n".join(blocks)
    if len(text) > total:
        text = text[:total] + "…(truncated)"
    return text


async def _call_llm_json(
    system: str, user: str, *, temperature: float | None = None, max_tokens: int = 1200
) -> tuple[dict, Dict[str, Any]]:
    """一次 LLM 调用 + JSON 解析；返回 (解析结果, usage 增量)。解析失败抛 LlmError。"""
    s = get_settings()
    result = await get_llm_client().chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=s.llm_temperature if temperature is None else temperature,
        max_tokens=max_tokens,
    )
    parsed = extract_json(result.content)
    return parsed, {"prompt": result.prompt_tokens, "completion": result.completion_tokens}


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


RESEARCH_SYSTEM_PROMPT = """你是跨境电商选品研究官。
基于工具返回的数据评估选题证据完整度并输出结构化结论。只输出一个 JSON 对象，schema：
{"evidence_score": 0.0~1.0, "demand_signal": "一句话需求信号",
 "competitor_gap": "一句话竞品缺口", "review_pain_points": ["痛点1", "痛点2"],
 "evidence_refs": ["数据来源编号"], "reasoning": "评分理由(中文,≤120字)"}

评分 rubric（必须遵守）：
- 缺少竞品数据或评论痛点任一维度时，evidence_score 必须 ≤ 0.60；
- 三个维度（趋势/竞品/评论）齐全且有正反面证据时，evidence_score 取 0.75~0.95；
- 数据相互矛盾时下调并在 reasoning 说明。"""

STUB_REFS_ROUND1 = ["ev_trend_001", "ev_comp_014"]
STUB_REFS_ROUND2 = ["ev_trend_001", "ev_comp_014", "ev_rev_103", "ev_kw_021"]


async def _research_via_llm(
    state: Dict[str, Any], config: RunnableConfig, idea: str, market: str, rounds: int
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """LLM 研究路径：数据一律来自受治理的工具，LLM 只做综合与评分。"""
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)
    # 第一轮只取趋势（证据天然单薄）；深化轮才补竞品与评论——深化的动机是数据可得性
    wanted: list[tuple[str, dict]] = [
        ("search_market_trends", {"keyword": idea, "target_market": market})
    ]
    if rounds >= 1:
        wanted += [
            ("search_competitor_listings", {"keyword": idea}),
            ("search_customer_reviews", {"keyword": idea}),
        ]
    tool_outputs: Dict[str, Any] = {}
    usage = {"prompt": 0, "completion": 0}
    for name, payload in wanted:
        try:
            res = await execute_tool(name, payload, ctx, repo)
            tool_outputs[name] = res.output
        except ToolError as exc:
            tool_outputs[name] = {"error": str(exc)}

    parsed, u = await _call_llm_json(
        RESEARCH_SYSTEM_PROMPT,
        "选题：{}（目标市场 {}）\n工具数据：\n{}".format(
            idea, market, _compress_tool_outputs(tool_outputs)
        ),
    )
    usage["prompt"] += u["prompt"]
    usage["completion"] += u["completion"]

    score = max(0.0, min(1.0, float(parsed.get("evidence_score", 0.0))))
    # 硬保证：LLM 只提议分数，维度缺失时的 ≤0.60 封顶由代码执行（与 listing 的
    # 确定性整形同思路）——真实 LLM 偶发违反 rubric（曾给 0.70），不能依赖自觉
    has_comp = "search_competitor_listings" in tool_outputs and not tool_outputs[
        "search_competitor_listings"
    ].get("error")
    has_rev = "search_customer_reviews" in tool_outputs and not tool_outputs[
        "search_customer_reviews"
    ].get("error")
    if not (has_comp and has_rev):
        score = min(score, 0.60)
    brief = {
        "round": rounds + 1,
        "evidence_score": score,
        "demand_signal": str(parsed.get("demand_signal", ""))[:200],
        "competitor_gap": str(parsed.get("competitor_gap", ""))[:200],
        "review_pain_points": [str(x) for x in (parsed.get("review_pain_points") or [])][:6],
        "evidence_refs": [str(x) for x in (parsed.get("evidence_refs") or [])][:8],
        "reasoning": str(parsed.get("reasoning", ""))[:400],
        "tool_outputs": tool_outputs,
    }
    return brief, usage


async def node_research(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    s = get_settings()
    t0 = time.perf_counter()
    rounds = state.get("research_rounds", 0)
    task_input = state["task_input"]
    idea = str(task_input.get("product_idea") or "Foldable Under-Bed Storage Box")
    market = str(task_input.get("target_market") or "US")

    engine = "stub"
    usage: Dict[str, Any] | None = None
    brief: Dict[str, Any] | None = None
    # M4 硬熔断（PRD §17）：key 可用但 token 预算烧穿时跳过 LLM，只走确定性路径
    budget_cut = llm_enabled() and not _llm_available(state)
    if _llm_available(state):
        try:
            brief, usage = await _research_via_llm(state, config, idea, market, rounds)
            engine = "llm"
        except Exception:  # noqa: BLE001 —— LLM 失败降级 stub，主链路不中断
            logger.exception("research llm path failed; falling back to stub")

    if brief is None:
        # stub 剧情（对齐 PRD §24 demo 步骤 3）：首轮证据薄 → 低于阈值；深化后达标
        score = 0.82 if rounds >= 1 else 0.55
        brief = {
            "round": rounds + 1,
            "evidence_score": score,
            "demand_signal": "床底收纳近90天搜索环比 +23%",
            "competitor_gap": "头部竞品普遍不支持折叠，差评集中在占空间",
            "review_pain_points": ["易塌陷", "异味", "拉链损坏"],
            "evidence_refs": STUB_REFS_ROUND1 if rounds == 0 else STUB_REFS_ROUND2,
        }
    score = float(brief["evidence_score"])

    await rec.status(WorkflowStatus.researching.value)
    detail = {
        "round": rounds + 1,
        "evidence_score": score,
        "engine": engine,
    }
    if budget_cut:
        detail["llm_budget_cut"] = True
    if usage is not None:
        detail["llm_usage"] = usage
    await rec.step("research", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000))
    if score < s.evidence_threshold:
        reason = brief.get("reasoning") or (
            f"证据完整度 {score:.2f} 低于阈值 {s.evidence_threshold}，触发第二轮研究补充证据"
        )
        await rec.decision(
            agent="planner",
            decision_type=AgentDecisionType.research_deepening.value,
            reasoning=f"证据完整度 {score:.2f} 低于阈值 {s.evidence_threshold}；{reason}",
            chosen_option="deepen",
            alternatives=["abort"],
        )
    update: Dict[str, Any] = {
        "research_evidence_score": score,
        "research_rounds": rounds + 1,
        "scratchpad": _artifact(state, "research_brief", brief),
    }
    if usage is not None:
        update["llm_usage"] = _merge_llm_usage(
            state, usage["prompt"], usage["completion"]
        )
    return update


async def node_profit(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    marketplace = (task_input.get("marketplaces") or ["amazon"])[0]
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    via = "fallback"
    try:
        # M3：确定性商业计算进治理管线（PRD §7.3）——佣金率取自 adapter 规则，
        # 节点不做算术；售价/采购价为 demo 固定输入，其余参数走工具默认值。
        res = await execute_tool(
            "estimate_profit",
            {"marketplace": marketplace, "sale_price_usd": 29.99, "supplier_price_usd": 6.80},
            ctx,
            repo,
        )
        o = res.output or {}
        margin = float(o.get("margin_pct") or 0.0)
        contribution = float(o.get("contribution_profit_usd") or 0.0)
        profit = {
            "assumptions": o.get("assumptions") or {},
            "contribution_profit": contribution,
            "margin_pct": margin,
            "platform_fee_pct": o.get("platform_fee_pct"),
            "break_even_price_usd": o.get("break_even_price_usd"),
            "total_cost_usd": o.get("total_cost_usd"),
        }
        via = "tool"
    except ToolError:  # 工具故障降级为旧 stub 数字，主链路不中断
        logger.exception("estimate_profit failed; falling back to hardcoded numbers")
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
        detail={"margin_pct": margin, "contribution": contribution, "via": via},
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"profit": profit}


async def node_supplier(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    idea = str(task_input.get("product_idea") or "Foldable Under-Bed Storage Box")
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    try:
        res = await execute_tool("search_suppliers", {"keyword": idea}, ctx, repo)
        candidates: List[Dict[str, Any]] = [
            dict(c) for c in ((res.output or {}).get("candidates") or [])
        ]
    except ToolError:
        logger.exception("search_suppliers failed; falling back to seed catalog")
        candidates = [
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
            },
        ]

    # M4 长期记忆（PRD §9）：实时检索本租户的历史供应商风险记忆并命中打标——
    # 记忆内容须匹配候选 id/name 才附加 memory_hit；检索失败不阻断主链路。
    # 选择逻辑仍是确定性代码（LLM 只提议、代码做硬保证），记忆只是额外证据源。
    memory_engine = ""
    try:
        mres = await execute_tool(
            "retrieve_memory",
            {
                "kind": "supplier_risk",
                "query_text": f"{idea} supplier quality defect risk",
                "top_k": 5,
            },
            ctx,
            repo,
        )
        mout = mres.output or {}
        memory_engine = str(mout.get("engine") or "")
        for hit in mout.get("results") or []:
            text = str(hit.get("content") or "")
            for cand in candidates:
                if cand.get("id") in text or cand.get("name") in text:
                    cand["memory_hit"] = {
                        "source_workflow_id": hit.get("source_workflow_id"),
                        "reason": text[:120],
                        "similarity": round(float(hit.get("similarity") or 0.0), 4),
                    }
                    break
    except ToolError:
        logger.exception("retrieve_memory failed; continuing without risk-memory downweight")

    # 选择逻辑留在确定性代码里（PRD §7.3）：低风险优先；同风险按质检分降序、报价升序
    low_risk = sorted(
        (c for c in candidates if c.get("risk") == "low"),
        key=lambda c: (-int(c.get("quality_score") or 0), float(c.get("price_usd") or 0)),
    )
    primary = None
    if low_risk:
        primary = low_risk[0]["id"]
    elif candidates:
        primary = candidates[0]["id"]  # 全员高风险时只能取目录第一个并如实暴露风险
    backup = low_risk[1]["id"] if len(low_risk) > 1 else None
    risk_flags = [f"{c['id']} 已按历史风险记忆降权" for c in candidates if c.get("memory_hit")]
    suppliers = {
        "primary": primary,
        "backup": backup,
        "candidates": candidates,  # 目录原序保留，排序只影响选择不影响展示
        "risk_flags": risk_flags,
    }
    await rec.status(WorkflowStatus.evaluating_suppliers.value)
    await rec.step(
        "supplier",
        detail={
            "primary": suppliers["primary"],
            "flags": suppliers["risk_flags"],
            "memory_engine": memory_engine,
        },
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"suppliers": suppliers}


GATE_SYSTEM_PROMPT = """你是跨境电商业务的决策官，负责选品推进的 go/no-go 闸门。只输出 JSON：
{"chosen": "proceed|revise|abort", "reasoning": "决策理由(中文,≤150字)"}

决策 rubric（按优先级自上而下，命中即停）：
- 利润率 < 5%，或 supplier_risk.primary_risk=high 且 has_backup=false → abort；
- 贡献利润率 ≥ 15% 且证据完整度 ≥ 阈值 → proceed；
  此时 notes 里其他供应商的历史风险等次要信息只能写进 reasoning 供后续参考，不得改变结论；
- 其余情况 → revise（调整售价/成本后再评估）。
备选项是 revise 与 abort（除非你选的就是其中之一）。"""


async def node_decision_gate(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """顶层 go/no-go（PRD §7.13）：综合研究/利润/供应商证据做显式决策。"""
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    margin = (state.get("profit") or {}).get("margin_pct", 0)
    score = state.get("research_evidence_score", 0)
    threshold = get_settings().evidence_threshold

    engine = "stub"
    go: str | None = None
    reasoning = ""
    usage: Dict[str, Any] | None = None
    if _llm_available(state):
        try:
            brief = (state.get("scratchpad", {}).get("artifacts", {}) or {}).get(
                "research_brief"
            ) or {}
            # 主供应商风险等级在代码里判定后显式下发，避免 LLM 从 flags 文本误读（曾把
            # "sup_002 已降权"当成主供应商高风险导致 revise/abort 摇摆）
            sup = state.get("suppliers") or {}
            cands = {c["id"]: c for c in (sup.get("candidates") or [])}
            supplier_risk = {
                "primary_id": sup.get("primary"),
                "primary_risk": (cands.get(sup.get("primary")) or {}).get("risk", "unknown"),
                "has_backup": bool(sup.get("backup")),
                "notes": sup.get("risk_flags"),
            }
            evidence_summary = {
                "research": {
                    "evidence_score": score,
                    "demand_signal": brief.get("demand_signal"),
                    "pain_points": brief.get("review_pain_points"),
                },
                "profit": state.get("profit"),
                "supplier_risk": supplier_risk,
                "thresholds": {"evidence": threshold, "margin": 0.15},
            }
            parsed, u = await _call_llm_json(
                GATE_SYSTEM_PROMPT, json.dumps(evidence_summary, ensure_ascii=False)
            )
            chosen = str(parsed.get("chosen", "")).strip().lower()
            if chosen in ("proceed", "revise", "abort"):
                go = chosen
                reasoning = str(parsed.get("reasoning", ""))[:400]
                engine = "llm"
                usage = u
        except Exception:  # noqa: BLE001
            logger.exception("decision gate llm path failed; falling back to rule")

    if go is None:
        # 规则兜底：利润与证据双达标即 proceed（stub 与 LLM 故障时都走这里）
        go = "proceed" if margin >= 0.15 and score >= threshold else "abort"
        reasoning = (
            f"证据完整度 {score:.2f}、贡献利润率 {margin:.1%}；"
            "利润与证据均达阈值，主供应商风险低，决定进入 Listing 生成"
        )

    await rec.status(WorkflowStatus.decision_gate.value)
    detail: Dict[str, Any] = {"chosen": go, "engine": engine}
    if usage is not None:
        detail["llm_usage"] = usage
    await rec.step(
        "decision_gate", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000)
    )
    await rec.decision(
        agent="planner",
        decision_type=AgentDecisionType.go_no_go.value,
        reasoning=reasoning,
        chosen_option=go,
        alternatives=["revise", "abort"],
    )
    update: Dict[str, Any] = {"go_no_go": go}
    if usage is not None:
        update["llm_usage"] = _merge_llm_usage(state, usage["prompt"], usage["completion"])
    return update


LISTING_SYSTEM_PROMPT = """你是跨境电商平台的资深 Listing 文案官。
根据产品信息、平台规则与研究证据撰写该平台的商品文案。只输出 JSON：
{"title": "平台标题", "bullets": ["卖点1", "卖点2"], "claim": "一句核心卖点声明",
 "keywords": ["关键词1"]}

硬性要求：
- title 长度 ≤ {title_max} 字符；bullets 数量在 {bmin}~{bmax} 条之间；
- claim/bullets 中每句功效表述必须有研究证据支撑，禁止无证据的绝对化承诺（如"保证/100%/最佳"）；
- {constraint_block}
- 语言与风格贴合目标市场。"""

STUB_FLAVOR = {
    "amazon": ["Fits under most beds", "Foldable flat in 3s", "Reinforced zipper"],
    "shopify": ["SEO: under bed storage", "Story block: 小空间收纳灵感"],
    "tiktok_shop": ["3秒折叠！床底瞬间扩容", "Before/After 对比镜头"],
}


async def _listing_via_llm(
    idea: str,
    market: str,
    rules: Dict[str, Any],
    research_brief: Dict[str, Any],
    constraints: List[str],
    usage_acc: Dict[str, int],
) -> Dict[str, Any] | None:
    """LLM 文案路径。返回字段 dict；任何失败返回 None 由调用方降级。"""
    constraint_block = (
        "Critic 合规约束（必须逐条满足）：\n- " + "\n- ".join(constraints)
        if constraints
        else "本轮尚未有 Critic 约束。"
    )
    evidence_pack = {
        "demand_signal": research_brief.get("demand_signal"),
        "competitor_gap": research_brief.get("competitor_gap"),
        "review_pain_points": research_brief.get("review_pain_points"),
    }
    parsed, u = await _call_llm_json(
        LISTING_SYSTEM_PROMPT.replace("{title_max}", str(rules.get("title_max_length", 200)))
        .replace("{bmin}", str(rules.get("bullets_min", 0)))
        .replace("{bmax}", str(rules.get("bullets_max", 10)))
        .replace("{constraint_block}", constraint_block),
        json.dumps(
            {"product": idea, "target_market": market, "evidence": evidence_pack},
            ensure_ascii=False,
        ),
        temperature=0.6,
        max_tokens=900,
    )
    usage_acc["prompt"] += u["prompt"]
    usage_acc["completion"] += u["completion"]
    title = str(parsed.get("title", "")).strip()
    bullets = [str(b).strip() for b in (parsed.get("bullets") or []) if str(b).strip()]
    claim = str(parsed.get("claim", "")).strip()
    keywords = [str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()]
    if not title or not bullets or not claim:
        return None

    # 生成端硬保证：绝对化措辞在代码里直接改写为留余量表述（LLM 自审不可靠）
    sanitized: List[str] = []
    title, ch = _sanitize_llm_copy(title)
    sanitized += [f"title: {c}" for c in ch]
    claim, ch = _sanitize_llm_copy(claim)
    sanitized += [f"claim: {c}" for c in ch]
    hedged_bullets: List[str] = []
    for b in bullets:
        b, ch = _sanitize_llm_copy(b)
        sanitized += [f"bullets: {c}" for c in ch]
        if b:
            hedged_bullets.append(b)
    bullets = hedged_bullets or bullets
    return {
        "title": title,
        "bullets": bullets,
        "claim": claim,
        "keywords": keywords,
        "sanitized": sanitized,
    }


async def node_listing(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    idea = str(task_input.get("product_idea") or "Foldable Under-Bed Storage Box")
    market = str(task_input.get("target_market") or "US")
    marketplaces = task_input.get("marketplaces") or ["amazon", "shopify", "tiktok_shop"]
    rounds = state.get("critique_rounds", 0)
    constraints = (_sp(state).get("constraints")) or []
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    engine = "stub"
    usage_acc = {"prompt": 0, "completion": 0}
    research_brief = (_sp(state).get("artifacts") or {}).get("research_brief") or {}
    use_llm = _llm_available(state)
    if use_llm:
        engine = "llm"

    drafts: List[Dict[str, Any]] = []
    rules_fetched: Dict[str, Any] = {}
    sanitized_all: List[str] = []
    for mp in marketplaces:
        bullets = list(STUB_FLAVOR.get(mp, ["Durable foldable storage"]))
        title = f"{idea} | Foldable Under-Bed Storage Box"
        if rounds == 0:
            # stub 模式故意埋雷确保 Critic 打回（demo §24 步骤 8）；LLM 模式由真实生成接管
            claim = "保证10年不坏，100%承重无变形"
        else:
            claim = "采用加厚 PP 材质，实验室测试承重 40kg"

        # M1：经 ToolExecutor 查平台规则，卖点/标题按真实规则整形——规则唯一来源是 adapter
        rules: Dict[str, Any] = {}
        try:
            res = await execute_tool("get_marketplace_rules", {"marketplace": mp}, ctx, repo)
            rules = res.output["rules"] if res.output else {}
            rules_fetched[mp] = {
                "bullets_min": rules.get("bullets_min"),
                "bullets_max": rules.get("bullets_max"),
            }
        except ToolError as exc:  # 未知平台等：降级为未整形草稿，留给 critic/人工兜底
            rules_fetched[mp] = {"error": str(exc)}

        # M2：LLM 文案生成（rewrite 轮注入 Critic 约束）；失败降级 stub 文案
        keywords = ["under bed storage", "foldable box", "bedroom organizer"]
        if use_llm:
            try:
                gen = await _listing_via_llm(
                    idea, market, rules, research_brief, constraints, usage_acc
                )
            except Exception:  # noqa: BLE001
                logger.exception("listing llm generation failed for %s; fallback stub", mp)
                gen = None
            if gen is not None:
                title = gen["title"]
                bullets = gen["bullets"]
                claim = gen["claim"]
                if gen["keywords"]:
                    keywords = gen["keywords"]
                sanitized_all.extend(f"{mp}: {c}" for c in gen.get("sanitized") or [])

        # 规则整形是硬保证：LLM 输出也必须过这一关（不信任生成端遵守了限制）
        i = 0
        while len(bullets) < int(rules.get("bullets_min") or 0):
            bullets.append(GENERIC_BULLET_FILLERS[i % len(GENERIC_BULLET_FILLERS)])
            i += 1
        bmax = rules.get("bullets_max")
        if bmax:
            bullets = bullets[: int(bmax)]
        tmax = rules.get("title_max_length")
        if tmax and len(title) > int(tmax):
            title = title[: int(tmax)]

        # M3：ImageSpec 规则经工具从 adapter 取得，结构化 brief 直接进草稿；
        # 真实出图仍是 Phase 2 接缝（v1.4 §1.1），MVP 只出文字 brief
        image_brief: Dict[str, Any] = {
            "main": "白底主图：展开态45°角",
            "scene": "床底推入场景",
            "infographic": "尺寸对比与承重标注",
        }
        try:
            res = await execute_tool(
                "generate_image_brief",
                {"marketplace": mp, "product_idea": idea, "listing_title": title},
                ctx,
                repo,
            )
            if res.output:
                image_brief = res.output  # 前端只做 JSON 展示，保留工具的新键即可
        except ToolError as exc:  # 工具故障降级为硬编码三键 brief，主链路不中断
            logger.exception("generate_image_brief failed for %s; fallback stub", mp)
            rules_fetched[f"{mp}_image_brief"] = {"error": str(exc)}

        drafts.append(
            {
                "marketplace": mp,
                "title": title,
                "bullets": bullets,
                "claim": claim,
                "keywords": keywords,
                "image_brief": image_brief,
            }
        )
    await rec.status(WorkflowStatus.drafting_listings.value)
    detail: Dict[str, Any] = {
        "count": len(drafts),
        "round": rounds + 1,
        "engine": engine,
    }
    if rounds > 0:
        detail["applied_constraints"] = constraints
    detail["rules_fetched_via_tools"] = rules_fetched
    # 文案是演示的核心产出，把标题放进 step 详情供 UI 直接展示（完整内容在 listings 状态里）
    detail["titles"] = {d["marketplace"]: d["title"] for d in drafts}
    if sanitized_all:
        detail["llm_copy_sanitized"] = sanitized_all
    if usage_acc["prompt"] or usage_acc["completion"]:
        detail["llm_usage"] = usage_acc
    await rec.step("listing", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000))
    update: Dict[str, Any] = {"listings": drafts}
    if usage_acc["prompt"] or usage_acc["completion"]:
        update["llm_usage"] = _merge_llm_usage(
            state, usage_acc["prompt"], usage_acc["completion"]
        )
    return update


CRITIC_SYSTEM_PROMPT = """你是跨境电商 Listing 合规与质量审查官。
输入是各平台 Listing 草稿 JSON 与研究痛点证据。只输出一个 JSON 对象，schema：
{"issues": [{"marketplace": "平台", "field": "title|bullets|claim",
             "issue": "问题描述(中文)", "severity": "high|medium", "rule": "违反的规则"}],
 "reasoning": "审查理由(中文,≤100字)"}

审查 rubric（必须遵守）：
- 只有硬违规才报 severity="high"：
  (a) 绝对化/不可证实承诺——100%、永不、保证、零/完全无 X 之类的绝对表述（中英文都算），
      即使它是在回应某个品类痛点也违规，正确写法是留有余量的可证实表述
      （如 odor-free→low-odor、never sags→holds up to 30kg）；
  (b) 与本品自身研究事实直接矛盾的宣称（注意：品类差评点≠本品矛盾——
      针对品类痛点给出解决方案型卖点恰是正当营销，不要报）；
- sturdy/durable/heavy-duty/reinforced 等常规卖点词是可接受的行业修辞，不要报；
- low-odor/minimize/reduce/help 等已留余量的表述正是合规写法，不要报；
- 风格润色类建议最多 severity="medium"（medium 只记录、不触发重写）；
- 不臆造证据里不存在的问题；每条 issue 用 field 精确定位到 title/bullets/claim。"""


async def _critic_via_llm(
    state: Dict[str, Any], config: RunnableConfig
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """LLM 审查路径：确定性违禁词扫描之外的语义层补漏；解析异常向上抛由调用方降级。

    数据全部来自 state（listings + research_brief），不新发工具调用——审查只读不写。
    """
    brief = (_sp(state).get("artifacts") or {}).get("research_brief") or {}
    slim_listings = [
        {
            "marketplace": d.get("marketplace"),
            "title": d.get("title"),
            "bullets": d.get("bullets"),
            "claim": d.get("claim"),
        }
        for d in (state.get("listings") or [])
    ]
    evidence: Dict[str, Any] = {"review_pain_points": brief.get("review_pain_points")}
    comp = (brief.get("tool_outputs") or {}).get("search_competitor_listings")
    if comp and not comp.get("error"):
        evidence["competitor_listings"] = comp

    parsed, usage = await _call_llm_json(
        CRITIC_SYSTEM_PROMPT,
        json.dumps({"listings": slim_listings, "evidence": evidence}, ensure_ascii=False),
    )
    # 硬保证：severity 只出 high/medium（与 research 的分数封顶同思路，不信任生成端自觉）
    issues = [
        {
            "marketplace": str(i.get("marketplace", "")),
            "field": str(i.get("field", "")),
            "issue": str(i.get("issue", ""))[:200],
            "severity": "high" if i.get("severity") == "high" else "medium",
            "rule": str(i.get("rule", "")),
        }
        for i in (parsed.get("issues") or [])
        if isinstance(i, dict)
    ][:20]
    return issues, usage


async def node_critic(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()

    # 第一层：确定性违禁词扫描（零成本、结果稳定，先跑）
    det_issues: List[Dict[str, Any]] = []
    for d in state.get("listings") or []:
        for field in ("title", "claim"):
            text = str(d.get(field) or "")
            for phrase in BANNED_CLAIM_PHRASES:
                if phrase in text:
                    det_issues.append(
                        {
                            "marketplace": d.get("marketplace"),
                            "field": field,
                            "phrase": phrase,
                            "severity": "high",
                            "rule": "无证据绝对化声明",
                        }
                    )

    # 第二层：LLM 语义审查补漏；失败只降级为纯确定性扫描，主链路不中断
    engine = "deterministic"
    usage: Dict[str, Any] | None = None
    llm_issues: List[Dict[str, Any]] = []
    if _llm_available(state):
        try:
            llm_issues, usage = await _critic_via_llm(state, config)
            engine = "llm"
        except Exception:  # noqa: BLE001 —— 与 research 同款降级语义
            logger.exception("critic llm path failed; falling back to deterministic")
            usage = None

    issues = det_issues + llm_issues
    # 重写只由硬违规触发（确定性红线 + LLM high）；LLM medium 仅记录——审美级意见若也
    # 打回，会无限循环到上限才放行（真实冒烟曾 7→3→6 三轮耗尽额度）
    blocking = det_issues + [i for i in llm_issues if i["severity"] == "high"]
    advisory = [i for i in llm_issues if i["severity"] != "high"]
    detail: Dict[str, Any] = {
        "issue_count": len(issues),
        "blocking_count": len(blocking),
        "advisory_count": len(advisory),
        "engine": engine,
        # 问题原文进 trace（短语截断），收敛行为可离线复盘
        "blocking_issues": [
            {
                "marketplace": i.get("marketplace"),
                "field": i.get("field"),
                "phrase": str(i.get("phrase") or i.get("issue") or "")[:60],
                "rule": i.get("rule"),
            }
            for i in blocking
        ],
    }
    if usage is not None:
        detail["llm_usage"] = usage

    if blocking:
        critique = {
            "issues": blocking,
            "constraints": ["移除所有无证据的绝对化声明，替换为可证实表述（材质/承重数据）"],
        }
        await rec.status(WorkflowStatus.critique_loop.value)
        await rec.step(
            "critic",
            detail={**detail, "verdict": "rewrite"},
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        await rec.decision(
            agent="critic",
            decision_type=AgentDecisionType.rewrite.value,
            reasoning=(
                f"发现 {len(blocking)} 处合规硬违规"
                + (f"（另有 {len(advisory)} 条建议项仅记录）" if advisory else "")
                + "，打回重写并下发约束"
            ),
            chosen_option="rewrite",
            alternatives=["escalate_to_human"],
        )
        sp = _constraint(state, critique["constraints"][0])
        sp["critique"] = critique
        update: Dict[str, Any] = {
            "critique_issues": blocking,
            "critique_rounds": state.get("critique_rounds", 0) + 1,
            "scratchpad": sp,
        }
    else:
        await rec.step(
            "critic",
            detail={**detail, "verdict": "pass"},
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        sp = _sp(state)
        # 建议项随行归档供运营参考，但不构成发布阻塞
        sp["critique"] = {"issues": advisory, "advisory_only": True} if advisory else None
        update = {"critique_issues": [], "scratchpad": sp}

    if usage is not None:
        update["llm_usage"] = _merge_llm_usage(state, usage["prompt"], usage["completion"])
    return update


async def node_approval_check(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """高风险动作闸门（PRD §7.8/§14.1：发布动作必须人工审批）。

    M5 两条路径：
    - dev 自动放行：AUTO_APPROVE=true（或该工作流 task_input.auto_approve 显式覆盖）；
    - 真实 HITL：langgraph interrupt() 挂起图执行，把审批材料放进 payload；
      Approval Center 经 API 以 Command(resume=...) 回填人工决定后图才继续。
    注意 LangGraph 恢复语义：resume 后本节点会从头重跑，interrupt() 必须位于
    全部副作用之前——挂起期间的 status/step 落库由 API 层在检测到 __interrupt__ 时做。
    """
    rec = recorder_from_config(config)
    per_workflow = (state.get("task_input") or {}).get("auto_approve")
    auto = get_settings().auto_approve if per_workflow is None else bool(per_workflow)

    if not auto:
        decision = interrupt(
            {
                "reason": "高风险发布动作：多平台 listing 上架需人工确认",
                "product_idea": (state.get("task_input") or {}).get("product_idea"),
                "margin_pct": (state.get("profit") or {}).get("margin_pct"),
                "primary_supplier": (state.get("suppliers") or {}).get("primary"),
                "risk_flags": (state.get("suppliers") or {}).get("risk_flags"),
                "critique_rounds": state.get("critique_rounds", 0),
                "listings": [
                    {
                        "marketplace": d.get("marketplace"),
                        "title": d.get("title"),
                        "bullets": d.get("bullets"),
                        "claim": d.get("claim"),
                    }
                    for d in (state.get("listings") or [])
                ],
            }
        )
        approved = bool(decision.get("approved"))
        comment = str(decision.get("comment") or "")
        await rec.step(
            "approval_check",
            detail={"mode": "human", "approved": approved, "comment": comment[:200]},
        )
        await rec.decision(
            agent="human_approver",
            decision_type=AgentDecisionType.human_approval.value,
            reasoning=f"人工{'通过' if approved else '驳回'}；附言：{comment or '（无）'}",
            chosen_option="approve" if approved else "reject",
            alternatives=["reject"] if approved else ["approve"],
        )
        return {
            "approved": approved,
            "approval_decision": {"approved": approved, "comment": comment},
        }

    await rec.status(WorkflowStatus.awaiting_approval.value)
    await rec.step("approval_check", detail={"mode": "auto_approve(dev)"})
    await rec.decision(
        agent="system",
        decision_type=AgentDecisionType.auto_approval.value,
        reasoning="AUTO_APPROVE=true 的 dev 演示模式自动放行；生产路径必须人工审批（PRD §7.8）",
        chosen_option="approve",
    )
    return {"approved": True}


async def node_publish(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """发布必须经 ToolExecutor（审批门 + 幂等 + 审计），不允许直连 adapter（PRD §7.2）。"""
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    wf_id = state["workflow_id"]
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    published = []
    ok_count = 0
    replay_count = 0
    for idx, d in enumerate(state.get("listings") or []):
        mp = d.get("marketplace", f"mp_{idx}")
        idem_key = f"pub_{wf_id}_{mp}"
        payload = {
            "marketplace": mp,
            "workflow_id": wf_id,
            "listing": d,
            "idempotency_key": idem_key,
        }
        try:
            res = await execute_tool("publish_listing", payload, ctx, repo)
        except ToolError as exc:
            published.append(
                {
                    "marketplace": mp,
                    "listing_id": "",
                    "status": "error",
                    "error": str(exc),
                    "idempotency_key": idem_key,
                }
            )
            continue
        o = res.output or {}
        if res.replayed:
            replay_count += 1
        if o.get("status") == "published":
            ok_count += 1
        published.append(
            {
                "marketplace": mp,
                "listing_id": o.get("listing_id", ""),
                "status": o.get("status", "error"),
                "validation_errors": o.get("validation_errors", []),
                "idempotency_key": idem_key,
                "replayed": res.replayed,
            }
        )
    await rec.status(WorkflowStatus.executing.value)
    detail = {"published": ok_count, "replayed": replay_count, "total": len(published)}
    await rec.step(
        "publish", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000)
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
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    # M4：复盘经验真实回写长期记忆（kind=launch_lesson），供后续工作流
    # retrieve_memory 召回验证跨工作流学习；回写失败不阻断收尾（记忆是增益不是依赖）。
    lesson = (
        f"「{state['task_input'].get('product_idea')}」复盘要点：go/no-go="
        f"{state.get('go_no_go') or 'n/a'}，研究深化 {state.get('research_rounds', 0)} 轮，"
        f"Critic 重写 {state.get('critique_rounds', 0)} 轮，{len(published)} 个平台已发布；"
        "运营观测：收纳类目 TikTok 首周转化 1.8% 偏低待验证。"
    )
    memory_writeback: List[Dict[str, Any]] = []
    try:
        rres = await execute_tool(
            "record_memory",
            {
                "kind": "launch_lesson",
                "content": lesson[:2000],
                "source_workflow_id": state.get("workflow_id"),
            },
            ctx,
            repo,
        )
        rout = rres.output or {}
        memory_writeback.append(
            {
                "memory_type": "launch_lesson",
                "memory_id": rout.get("memory_id"),
                "engine": rout.get("engine"),
            }
        )
    except ToolError:
        logger.exception("record_memory failed; retrospective continues without writeback")

    retrospective = {
        "summary": (
            f"「{state['task_input'].get('product_idea')}」完成全链路："
            f"研究深化 {state.get('research_rounds', 0)} 轮，"
            f"Critic 重写 {state.get('critique_rounds', 0)} 轮，"
            f"{len(published)} 个平台已发布"
        ),
        "key_decisions": [
            "go/no-go = proceed",
            f"研究深化 x{max(state.get('research_rounds', 0) - 1, 0)}",
            f"Critic 重写 x{state.get('critique_rounds', 0)}",
        ],
        "memory_writeback": memory_writeback,
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
    approval = state.get("approval_decision") or {}
    if state.get("go_no_go") == "abort":
        status = WorkflowStatus.cancelled.value
        reason = "go/no-go 决策为 abort"
    elif approval and not approval.get("approved"):
        # M5：人工驳回走 cancelled，附言进审计
        status = WorkflowStatus.cancelled.value
        reason = f"人工驳回发布申请：{approval.get('comment') or '（无附言）'}"
    else:
        status = WorkflowStatus.blocked.value
        reason = "流程被阻断（无放行条件命中）"
    await rec.status(status, error=None if status == "cancelled" else reason)
    await rec.step("halted", status=status, detail={"reason": reason})
    return {}
