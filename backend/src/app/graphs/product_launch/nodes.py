"""M0 stub 执行器节点。

全部为确定性内容、零外部 LLM 调用。M2 起逐个替换为真实现（LLM + typed tools），
但节点签名 (state, config) -> state 增量、以及通过 recorder 落 trace/决策的方式保持不变——
这是 walking skeleton 的意义：先把骨架契约钉死。

每个自主决策点都写 AgentDecision（理由 + 备选项），对应 PRD §8.3 决策点清单。
"""

import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.config import get_settings
from app.domain.enums import AgentDecisionType, WorkflowStatus
from app.guardrails.badcases import run_all_detectors, scrub_untrusted
from app.llm import LlmError, extract_json, get_llm_client, llm_enabled
from app.observability.recorder import recorder_from_config
from app.persistence.memory import MemoryWorkflowRepository
from app.rag.strategy import ESCALATION, deterministic_strategy, normalize_proposal
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


async def _record_bad_cases(
    state: Dict[str, Any], config: RunnableConfig, origin: str, texts: Dict[str, str]
) -> List[Dict[str, Any]]:
    """M7 纵深检测：对 {来源: 文本} 跑全部注册 detector（确定性规则，零 LLM）。

    命中一律落 bad_cases 表（status=quarantined）+ bad_case_scan 步骤留痕；
    记录失败不阻断主流程。返回命中列表供节点做处置（跳过回写/加约束等）。
    """
    if not texts:
        return []
    hits: List[Dict[str, Any]] = []
    for source, text in texts.items():
        for r in run_all_detectors(text or ""):
            hits.append(
                {
                    "source": source,
                    "category": r.category.value,
                    "detector": r.detector,
                    "severity": r.severity,
                    "summary": r.summary,
                    "evidence": r.evidence,
                }
            )
    if not hits:
        return []
    rec = recorder_from_config(config)
    repo = _tool_repo(config)
    try:
        for h in hits:
            await repo.insert_bad_case(
                tenant_id=state["tenant_id"],
                workflow_id=state.get("workflow_id"),
                category=h["category"],
                severity=h["severity"],
                detector=h["detector"],
                summary=f"[{origin}/{h['source']}] {h['summary']}",
                evidence=h["evidence"],
                status="quarantined",
            )
    except Exception:
        logger.warning("bad_case 落库失败（不阻断主流程）: %s", origin, exc_info=True)
    await rec.step(
        "bad_case_scan",
        detail={"origin": origin, "hits": hits},
        latency_ms=0,
    )
    return hits


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


async def _fetch_ops_knowledge(
    state: Dict[str, Any],
    config: RunnableConfig,
    query: str,
    *,
    top_k: int = 2,
) -> List[Dict[str, Any]]:
    """M8：主链路外挂 RAG——经治理工具检索运营知识库（ops_playbook 类）。

    检索结果只作为生成侧参考资料（LLM 只提议，硬保证仍在代码）；检索失败
    不阻断主链路。客服场景的融合铁律不适用于此：这里没有工具实时事实可冲突。
    """
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)
    try:
        res = await execute_tool(
            "search_knowledge",
            {"query_text": query[:400], "category": "ops_playbook", "top_k": top_k},
            ctx,
            repo,
        )
        return list((res.output or {}).get("results") or [])
    except ToolError as exc:
        logger.warning("search_knowledge failed (non-blocking): %s", exc)
        return []


async def node_planner(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    task_input = state["task_input"]
    # M7 红队（PRD §20.3 A 类处置）：选题文本按不可信数据处理——先扫描留痕，
    # 再脱敏（剥掉注入/投毒模式）后才允许流入后续生成链路
    raw_idea = str(task_input.get("product_idea") or "")
    idea_hits = await _record_bad_cases(state, config, "planner", {"product_idea": raw_idea})
    cleaned_idea, scrubbed = scrub_untrusted(raw_idea)
    if scrubbed:
        task_input = {**task_input, "product_idea": cleaned_idea}
    plan = {
        "goal": f"为「{cleaned_idea}」完成选品到铺货全链路",
        "marketplaces": task_input.get("marketplaces") or ["amazon", "shopify", "tiktok_shop"],
        "target_market": task_input.get("target_market", "US"),
    }
    if idea_hits:
        plan["bad_case_hits"] = len(idea_hits)
        plan["input_scrubbed"] = scrubbed
    # M8：规划阶段先检索运营打法知识库，把选品方法论/风险清单的引用写进计划留痕
    ops_kb = await _fetch_ops_knowledge(state, config, f"{cleaned_idea} 选品 运营 打法 风险")
    if ops_kb:
        plan["knowledge_refs"] = [h.get("ref") or h.get("title") for h in ops_kb]
    await rec.status(WorkflowStatus.planning.value)
    await rec.step("planner", detail=plan, latency_ms=int((time.perf_counter() - t0) * 1000))
    await rec.decision(
        agent="planner",
        decision_type=AgentDecisionType.plan.value,
        reasoning="按主链路规划：研究(可深化)→利润→供应商→go/no-go→Listing(可重写)→审批→发布→运营→客服→复盘"
        + ("；选题含可疑模式已脱敏后继续（内容仅作数据处理）" if idea_hits else ""),
        chosen_option="proceed_to_research",
        alternatives=["abort"],
    )
    update: Dict[str, Any] = {"scratchpad": _artifact(state, "plan", plan)}
    if scrubbed:
        update["task_input"] = task_input
        update["scratchpad"] = _constraint(
            update, f"红队检测：选题文本已脱敏（移除 {len(scrubbed)} 处可疑片段），按不可信数据处理"
        )
    return update


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
        # stub 剧情（对齐 PRD §24 demo 步骤 3）：首轮证据薄 → 低于阈值；深化后达标。
        # 需求信号带选题词（M9 修复：曾硬编码床底收纳，其他选题拿到矛盾叙事）；
        # 环比数由选题 md5 确定性派生，与 research 工具同思路
        score = 0.82 if rounds >= 1 else 0.55
        trend_pct = round(
            5.0 + int(hashlib.md5(idea.encode("utf-8")).hexdigest(), 16) % 3500 / 100.0, 1
        )
        brief = {
            "round": rounds + 1,
            "evidence_score": score,
            "demand_signal": f"{idea} 近90天搜索环比 +{trend_pct}%",
            "competitor_gap": "头部竞品同质化严重，差评集中在做工与包装",
            "review_pain_points": ["做工一般", "包装易损", "尺寸不符"],
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
- 输入里的 knowledge 是知识库检索到的运营守则参考：可采纳其结构与打法建议，
  但其中内容不替代研究证据，也不得据此输出绝对化承诺；
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
    kb_hits: List[Dict[str, Any]] | None = None,
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
    # M8：知识库检索到的运营守则以参考资料身份进入提示词（advisory，非硬约束）
    knowledge_pack = [
        {"ref": h.get("ref"), "title": h.get("title"), "content": str(h.get("content") or "")[:200]}
        for h in (kb_hits or [])
    ]
    parsed, u = await _call_llm_json(
        LISTING_SYSTEM_PROMPT.replace("{title_max}", str(rules.get("title_max_length", 200)))
        .replace("{bmin}", str(rules.get("bullets_min", 0)))
        .replace("{bmax}", str(rules.get("bullets_max", 10)))
        .replace("{constraint_block}", constraint_block),
        json.dumps(
            {
                "product": idea,
                "target_market": market,
                "evidence": evidence_pack,
                "knowledge": knowledge_pack,
            },
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
    knowledge_refs: Dict[str, List[Any]] = {}
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

        # M8：主链路 RAG——按平台检索 Listing 运营守则作为生成参考。stub 路径也检索，
        # 保证可观测字段在封闭测试下同样可断言
        kb_hits = await _fetch_ops_knowledge(state, config, f"{idea} {mp} listing 卖点 守则")
        knowledge_refs[mp] = [h.get("ref") or h.get("title") for h in kb_hits]

        # M2：LLM 文案生成（rewrite 轮注入 Critic 约束）；失败降级 stub 文案
        keywords = ["under bed storage", "foldable box", "bedroom organizer"]
        if use_llm:
            try:
                gen = await _listing_via_llm(
                    idea, market, rules, research_brief, constraints, usage_acc,
                    kb_hits=kb_hits,
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
    detail["knowledge_refs"] = knowledge_refs
    # 文案是演示的核心产出，把标题放进 step 详情供 UI 直接展示（完整内容在 listings 状态里）
    detail["titles"] = {d["marketplace"]: d["title"] for d in drafts}
    if sanitized_all:
        detail["llm_copy_sanitized"] = sanitized_all
    # M7 纵深检测：Listing 产出物扫描夸大/违禁声明（B 输出失控）。
    # stub 剧情的埋雷文案在此现形并落 bad_cases；LLM 路径经生成端整形后通常 0 命中
    claim_hits = await _record_bad_cases(
        state,
        config,
        "listing",
        {
            f"listing:{d['marketplace']}": " ".join(
                [d["title"], d["claim"], *d["bullets"]]
            )
            for d in drafts
        },
    )
    if claim_hits:
        detail["bad_case_hits"] = len(claim_hits)
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


SUPPORT_TICKET = {
    "ticket_id": "tk_1042",
    "type": "where_is_my_order",
    "order_id": "ord_88123",
}

SUPPORT_SYSTEM_PROMPT = """你是跨境电商客服专员，为工单起草回复。只输出一个 JSON 对象，schema：
{"draft": "回复正文(中文,120~250字)", "cited_refs": ["引用的知识库文档编号"],
 "escalate": false, "reason": "是否升级及理由(≤50字)"}

铁律（必须遵守）：
- 订单状态、物流时效等实时事实只能采用「工具实时数据」里的数值，禁止使用知识库文档中的通用时效；
- cited_refs 只能从提供的知识库编号中选，没有合适引用就留空数组；
- 语气专业友善；不出现「保证」「100%」等绝对化承诺。"""

# 工单里的时效表述（订单 ETA 必须与工具一致——PRD §7.11 冲突以工具为准的判定基础）
_ETA_RE = re.compile(
    r"\d+\s*[-~至到]\s*\d+\s*个工作日|\d+\s*个工作日|\d+\s*[-~至到]\s*\d+\s*天|\d+\s*天"
)


def _etas_in(text: str) -> List[str]:
    return [re.sub(r"\s+", "", m.group(0)) for m in _ETA_RE.finditer(text or "")]


def _template_reply(
    ticket: Dict[str, Any], facts: Dict[str, Any], rag_hits: List[Dict[str, Any]]
) -> tuple[str, List[str]]:
    """确定性兜底草稿：事实句全部来自工具输出，引用句来自 RAG 命中。"""
    refs = [h["ref"] for h in rag_hits if h.get("ref")]
    order_id = ticket.get("order_id", "")
    if ticket.get("type") == "refund_request":
        draft = (
            f"您好，已收到您关于订单 {order_id} 的退款申请。"
            "退款资格与金额以订单实时状态审核为准（48 小时内人工审核），审核结果将站内信通知。"
        )
    elif facts.get("found") and facts.get("status"):
        eta = (
            f"，预计 {facts.get('eta_text')} 内送达（以物流实时更新为准）"
            if facts.get("eta_text")
            else ""
        )
        draft = f"您好，订单 {order_id} 当前物流状态为「{facts.get('status')}」{eta}。"
    else:
        draft = f"您好，暂时未能查询到订单 {order_id} 的实时状态，已为您升级人工核实。"
    if refs:
        draft += f"如需延迟/退换处理方案，可参考店铺政策（{refs[0]}）。"
    return draft, refs


# ---- M9/M11 agentic RAG：客服检索循环的确定性构件（LLM 只提议，代码做硬保证）----

SUPPORT_STRATEGY_SYSTEM_PROMPT = """你是客服知识库检索的策略规划器兼查询改写器。先判断该问题适合
哪种检索增强策略，再给出改写后的检索短语。只输出一个 JSON 对象，schema：
{"strategy": "direct|rewrite|hyde", "query": "改写后的检索短语", "reason": "选择理由(≤40字)"}
策略定义：direct=原句直检，适合含订单号/型号/SKU 等精确词的短查询（改写会稀释精确词面）；
rewrite=改写检索，默认，适合一般口语化问题；hyde=假设性回答增强，适合长而模糊、
需要语义泛化的开放式问题（假设文档由另一个角色生成，你只负责标记策略）。
query 要求：中文短语且不超过 40 字；去掉工单号、寒暄等无语义成分；保留问题主题词
（如 物流时效 / 退换货政策 / 退款流程）。"""

SUPPORT_HYDE_SYSTEM_PROMPT = """你是客服政策知识库的 HyDE 生成器：假设知识库里存在一条能回答该问题
的条目，直接写出这条目的正文片段（2~3 句，客观政策口吻，覆盖问题的主题关键词，
禁止编造具体时效天数/比例等数字承诺）。只输出一个 JSON 对象，schema：
{"document": "假设条目正文"}"""

SUPPORT_GRADE_SYSTEM_PROMPT = """你是客服知识检索质检员。给定工单问题与候选知识条目
（编号[标题]：摘要），判断每条是否真正有助于回复该问题。只输出一个 JSON 对象，schema：
{"relevant": ["条目编号或标题", ...]}
要求：只列确实相关的条目，拿不准的一律不列；全部不相关就输出空数组。"""

# 工单类型 → 检索路由基线（PRD §7.11）：实时事实走业务工具、政策知识走 RAG；
# 未登记的类型退回关键词判定。退款类天然双真：退款资格要实时订单审核，话术要引政策。
_SUPPORT_ROUTE_BY_TYPE = {
    "where_is_my_order": {"realtime": True, "policy": True},
    "shipping_issue": {"realtime": True, "policy": True},
    "refund_request": {"realtime": True, "policy": True},
    "policy_question": {"realtime": False, "policy": True},
    "product_consult": {"realtime": False, "policy": True},
}

# 关键词兜底（对小写化文本做子串匹配）：实时=订单/物流状态语义；政策=规则/流程语义。
# 注意「退款/退货」只算政策词不算实时词——纯咨询问政策不应触发实时工具语义。
_SUPPORT_REALTIME_KW = (
    "订单", "物流", "发货", "配送", "包裹", "快递", "签收", "到货",
    "运单", "查件", "order", "shipment", "delivery", "tracking", "package",
)
_SUPPORT_POLICY_KW = (
    "退款", "退货", "退换", "换货", "政策", "时效", "流程", "规则",
    "补偿", "保修", "refund", "return", "exchange", "policy", "warranty",
)


def _classify_route(ticket: Dict[str, Any]) -> Dict[str, bool]:
    """M9 确定性路由分类：该工单需要实时订单工具、政策知识库，还是都要。

    纯规则零 LLM——路由本身是硬保证的一部分，不交模型自由发挥；结果进 step detail，
    policy=False 时整个 RAG 循环跳过。
    """
    ttype = str(ticket.get("type") or "").strip().lower()
    question = f"{ttype} {ticket.get('question') or ticket.get('subject') or ''}".lower()
    base = _SUPPORT_ROUTE_BY_TYPE.get(ttype) or {}
    realtime = bool(base.get("realtime")) or any(k in question for k in _SUPPORT_REALTIME_KW)
    policy = bool(base.get("policy")) or any(k in question for k in _SUPPORT_POLICY_KW)
    return {"realtime": realtime, "policy": policy}


def _support_question_text(ticket: Dict[str, Any]) -> str:
    """工单的自然语言化问题文本（改写种子）：补充描述 + 类型 + 单号 + 领域词。"""
    parts = [
        str(ticket.get("question") or "").strip(),
        str(ticket.get("subject") or "").strip(),
        str(ticket.get("type") or "").strip(),
        str(ticket.get("order_id") or "").strip(),
        "物流时效 退换货政策 退款流程",
    ]
    return " ".join(p for p in parts if p)


def _det_rewrite(query: str) -> str:
    """确定性查询改写：优先用并行交付的 app.rag.rewrite 契约实现；
    模块未就绪时退化为本地极简清洗（剥标点/压空白），节点行为保持可预期。"""
    try:
        from app.rag.rewrite import deterministic_rewrite  # 并行契约模块（M9）
    except ImportError:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", query or "")
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return (cleaned or (query or ""))[:120]
    return (deterministic_rewrite(query or "") or (query or ""))[:120]


async def _support_plan_query(
    state: Dict[str, Any], question: str, aux_usage: Dict[str, int]
) -> Dict[str, Any]:
    """M11 首轮策略规划：确定性规则打底（deterministic_strategy），LLM 可用时
    一次调用同时提议策略与改写查询——strategy 越界/缺失整体弃用回退规则
    （normalize_proposal），query 缺失降级确定性改写。
    返回 {strategy, query, query_source, strategy_source, reason}。"""
    rule_strategy = deterministic_strategy(question)
    plan: Dict[str, Any] = {
        "strategy": rule_strategy,
        "query": "",
        "query_source": "deterministic",
        "strategy_source": "rule",
        "reason": "",
    }
    if _llm_available(state):
        try:
            parsed, u = await _call_llm_json(
                SUPPORT_STRATEGY_SYSTEM_PROMPT,
                f"工单问题：{question}",
                temperature=0.0,
                max_tokens=140,
            )
            aux_usage["prompt"] += u["prompt"]
            aux_usage["completion"] += u["completion"]
            plan = normalize_proposal(parsed, plan)
            q = str((parsed or {}).get("query") or "").strip()
            if q:
                plan["query"] = q[:200]
                plan["query_source"] = "llm"
            else:
                plan["query"] = _det_rewrite(question)
        except Exception:  # noqa: BLE001 —— 规划失败不阻断主流程，规则兜底
            logger.warning("support strategy plan llm failed; fallback rules")
            plan["query"] = _det_rewrite(question) if rule_strategy == "rewrite" else ""
    elif rule_strategy == "rewrite":
        plan["query"] = _det_rewrite(question)
    return plan


async def _support_hyde_document(
    state: Dict[str, Any], question: str, aux_usage: Dict[str, int],
    *, variant: bool = False,
) -> str:
    """M11 HyDE：LLM 生成假设性知识条目正文，只进检索语义路（词面路用用户原词）。
    无 LLM/生成失败/输出为空一律返回 ""——调用方据此把策略降级 rewrite。
    variant=True 时换角度重新生成（升级重试轮避免复读同一假设文档）。"""
    if not _llm_available(state):
        return ""
    try:
        user = f"工单问题：{question}" + (
            "（换一个角度，侧重不同的主题关键词）" if variant else ""
        )
        parsed, u = await _call_llm_json(
            SUPPORT_HYDE_SYSTEM_PROMPT,
            user,
            temperature=0.3,
            max_tokens=200,
        )
        aux_usage["prompt"] += u["prompt"]
        aux_usage["completion"] += u["completion"]
        return str(parsed.get("document") or "").strip()[:600]
    except Exception:  # noqa: BLE001 —— HyDE 失败降级，检索闭环不断
        logger.warning("support hyde document llm failed; degrade to rewrite")
        return ""


def _retry_query(question: str, first_query: str) -> str:
    """第二轮改写：对原始问题再做一次确定性改写；与首轮相同时叠加固定政策词组合，
    保证重试轮的查询有区分度而不是原样复读。"""
    alt = _det_rewrite(question)
    if not alt or alt == first_query:
        alt = f"{alt} 退货政策 退款流程 物流时效 补偿方案".strip()
    return alt


async def _grade_hits(
    state: Dict[str, Any],
    ticket: Dict[str, Any],
    hits: List[Dict[str, Any]],
    aux_usage: Dict[str, int],
) -> tuple[List[Dict[str, Any]], str]:
    """两级相关性判级（LLM 只收窄，硬保证在确定性一侧）：
    底座=search_knowledge 返回的确定性 grade 字段（工具未交付该字段时保守视为相关，
    兼容旧契约）；LLM 可用时再判级并与底座取交集。返回 (相关命中, grade_source)。"""
    if not hits:
        return [], "deterministic"
    llm_keys: set[str] | None = None
    if _llm_available(state):
        catalog = "\n".join(
            f"[{h.get('ref') or h.get('title')}] {h.get('title')}：{str(h.get('content'))[:160]}"
            for h in hits
        )
        try:
            parsed, u = await _call_llm_json(
                SUPPORT_GRADE_SYSTEM_PROMPT,
                f"工单问题：{_support_question_text(ticket)}\n候选条目：\n{catalog}",
                temperature=0.0,
                max_tokens=200,
            )
            aux_usage["prompt"] += u["prompt"]
            aux_usage["completion"] += u["completion"]
            raw = parsed.get("relevant")
            # 输出不合契约（缺 relevant 键）视为判级不可用，退回确定性分级
            if isinstance(raw, list):
                llm_keys = {str(k).strip() for k in raw if str(k).strip()}
        except Exception:  # noqa: BLE001 —— 判级失败退回确定性分级
            logger.warning("support hit grading llm failed; fallback deterministic")
    # 仅过滤确定性判级为「明确不相关」的命中：grade 缺失/None（旧契约或未分级）
    # 一律保守视为相关，避免把没判级的命中误杀
    det_relevant = [h for h in hits if h.get("grade") is not False]
    if llm_keys is None:
        return det_relevant, "deterministic"
    relevant = [
        h for h in det_relevant if h.get("ref") in llm_keys or h.get("title") in llm_keys
    ]
    return relevant, "llm+deterministic"


async def _agentic_rag_retrieve(
    state: Dict[str, Any],
    ctx: ToolContext,
    repo: Any,
    ticket: Dict[str, Any],
    *,
    aux_usage: Dict[str, int],
) -> Dict[str, Any]:
    """M9→M11 agentic RAG 循环：策略规划（direct/rewrite/hyde 自适应）→ 混合检索
    （hyde 时假设文档只进语义路）→ 分级 → 零相关沿升级阶梯换策略重试（≤2 轮）。

    最多 2 轮防成本失控；每轮 {round, query, strategy, hyde, hits, relevant_count}
    进 retrieval_trace 留痕；rag_block 只由「相关命中」构成。返回
    {hits, trace, rewrite_source, grade_source, strategy_source, strategy_reason, error}。
    """
    trace: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "hits": [],
        "trace": trace,
        "rewrite_source": None,
        "grade_source": None,
        "strategy_source": None,
        "strategy_reason": None,
        "error": None,
    }
    question = _support_question_text(ticket)
    plan = await _support_plan_query(state, question, aux_usage)
    out["strategy_source"] = plan["strategy_source"]
    out["strategy_reason"] = plan["reason"]

    # 首轮执行形态由策略决定：direct=原句；rewrite=改写短语；hyde=原句+假设文档
    # （假设文档生成失败/无 LLM → 降级 rewrite，HyDE 永不阻塞检索闭环）
    cur_strategy = plan["strategy"]
    cur_query = question
    cur_hyde = ""
    if cur_strategy == "hyde":
        cur_hyde = await _support_hyde_document(state, question, aux_usage)
        if not cur_hyde:
            cur_strategy = "rewrite"
    if cur_strategy == "rewrite":
        cur_query = plan["query"] or _det_rewrite(question)
    if cur_strategy == "rewrite":
        out["rewrite_source"] = plan["query_source"]
    elif cur_strategy == "hyde":
        out["rewrite_source"] = "hyde"
    else:
        out["rewrite_source"] = "as-is"

    first_query = ""
    for rnd in range(2):
        if rnd == 1:
            # 零相关命中 → 沿升级阶梯换策略（direct→rewrite→hyde；hyde 重试换角度）
            nxt = ESCALATION.get(cur_strategy, "rewrite")
            if nxt == "hyde":
                cur_hyde = await _support_hyde_document(
                    state, question, aux_usage, variant=(cur_strategy == "hyde")
                )
                if cur_hyde:
                    cur_strategy, cur_query = "hyde", question
                else:  # hyde 无从生成（无 LLM/失败）→ 退回确定性改写变体
                    cur_strategy, cur_query, cur_hyde = (
                        "rewrite",
                        _retry_query(question, first_query),
                        "",
                    )
            else:
                cur_strategy, cur_query, cur_hyde = "rewrite", "", ""
                cur_query = _retry_query(question, first_query)
        first_query = first_query or cur_query
        tool_input: Dict[str, Any] = {
            "query_text": cur_query[:400],
            "top_k": 5,
            "mode": "hybrid",
            "grade": True,
        }
        if cur_strategy == "hyde" and cur_hyde:
            tool_input["hyde_text"] = cur_hyde[:600]
        try:
            res = await execute_tool("search_knowledge", tool_input, ctx, repo)
            hits = list((res.output or {}).get("results") or [])
        except ToolError as exc:
            out["error"] = str(exc)[:120]
            trace.append(
                {
                    "round": rnd + 1,
                    "query": cur_query,
                    "strategy": cur_strategy,
                    "hyde": bool(cur_hyde),
                    "hits": 0,
                    "relevant_count": 0,
                }
            )
            break
        relevant, out["grade_source"] = await _grade_hits(state, ticket, hits, aux_usage)
        trace.append(
            {
                "round": rnd + 1,
                "query": cur_query,
                "strategy": cur_strategy,
                "hyde": bool(cur_hyde),
                "hits": len(hits),
                "relevant_count": len(relevant),
            }
        )
        if relevant:
            out["hits"] = relevant
            break
    return out


def _skipped_rag() -> Dict[str, Any]:
    """policy=False 路由的占位结果：跳过检索循环，trace 为空。"""
    return {
        "hits": [],
        "trace": [],
        "rewrite_source": None,
        "grade_source": None,
        "strategy_source": None,
        "strategy_reason": None,
        "error": None,
    }


async def node_support(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """客服售后（M6→M9/M11，PRD §7.11）：agentic RAG——确定性路由分类 → 检索策略
    自适应规划（direct/rewrite/hyde，LLM 提议越界弃用 / 规则兜底；hyde 假设文档只进
    语义路）→ 混合检索 → 相关性分级 → 零相关沿阶梯换策略重试（≤2 轮）。订单实时
    事实走 get_order_status 工具，LLM 只起草，代码做融合铁律的硬保证——草稿里任何
    与工具 ETA 不一致的时效表述都判冲突，整稿弃用回退确定性模板。
    """
    rec = recorder_from_config(config)
    t0 = time.perf_counter()
    ticket = dict(SUPPORT_TICKET)
    repo = _tool_repo(config)
    ctx = _tool_ctx(state)

    facts: Dict[str, Any]
    try:
        res = await execute_tool(
            "get_order_status", {"order_id": ticket["order_id"]}, ctx, repo
        )
        facts = dict(res.output)
    except ToolError as exc:
        facts = {"order_id": ticket["order_id"], "found": False, "error": str(exc)}

    # M9 确定性路由分类：policy=False 时跳过整个检索循环（rag 为空）
    route = _classify_route(ticket)
    aux_usage = {"prompt": 0, "completion": 0}  # 改写/判级等辅助 LLM 调用的计量
    rag_res = (
        await _agentic_rag_retrieve(state, ctx, repo, ticket, aux_usage=aux_usage)
        if route["policy"]
        else _skipped_rag()
    )
    rag = rag_res["hits"]

    template_draft, template_refs = _template_reply(ticket, facts, rag)
    draft, refs, draft_source = template_draft, template_refs, "template"
    engine = "stub"
    conflict: Dict[str, Any] = {"detected": False}
    escalate = ticket["type"] == "refund_request"  # 退款必须审批（PRD §14.1）
    usage: Dict[str, Any] | None = None

    if _llm_available(state):
        engine = "llm"
        rag_block = "\n".join(
            f"[{h.get('ref') or h.get('title')}] {h.get('title')}：{str(h.get('content'))[:300]}"
            for h in rag
        ) or "（无命中）"
        try:
            parsed, u = await _call_llm_json(
                SUPPORT_SYSTEM_PROMPT,
                "工单：{}\n工具实时数据：{}\n知识库检索结果：\n{}".format(
                    json.dumps(ticket, ensure_ascii=False),
                    json.dumps(facts, ensure_ascii=False),
                    rag_block,
                ),
            )
            usage = u
            llm_draft = str(parsed.get("draft", "")).strip()
            allowed_refs = {h.get("ref") for h in rag if h.get("ref")}
            llm_refs = [r for r in (parsed.get("cited_refs") or []) if r in allowed_refs]
            tool_eta = re.sub(r"\s+", "", str(facts.get("eta_text") or ""))
            draft_etas = _etas_in(llm_draft)
            bad_etas = [e for e in draft_etas if tool_eta and e != tool_eta]
            if llm_draft and not bad_etas:
                # LLM 草稿与工具事实一致：采纳，但引用与措辞仍过确定性整形
                hedged, changes = _sanitize_llm_copy(llm_draft)
                draft, draft_source = hedged, "llm"
                refs = llm_refs or template_refs
                if changes:
                    conflict["sanitized"] = changes
            else:
                # 硬保证：草稿时效与工具冲突（或空草稿）→ 整稿弃用回退模板
                conflict = {
                    "detected": bool(bad_etas),
                    "tool_eta": tool_eta or None,
                    "draft_etas": draft_etas,
                }
            escalate = bool(parsed.get("escalate")) or escalate
        except LlmError as exc:
            conflict["llm_error"] = str(exc)[:120]

    # 辅助 LLM 调用（改写/判级）的计量并入总账（PRD §17 接缝不留免费午餐）
    if aux_usage["prompt"] or aux_usage["completion"]:
        usage = dict(aux_usage) if usage is None else {
            "prompt": usage["prompt"] + aux_usage["prompt"],
            "completion": usage["completion"] + aux_usage["completion"],
        }

    support = {
        "ticket_id": ticket["ticket_id"],
        "type": ticket["type"],
        "order_id": ticket["order_id"],
        "draft": draft,
        "refs": refs,
        "escalate": escalate,
        "order_status": facts.get("status"),
        "eta_text": facts.get("eta_text"),
    }
    detail = {
        "ticket": ticket["ticket_id"],
        "type": ticket["type"],
        "order_found": bool(facts.get("found")),
        "order_status": facts.get("status"),
        "eta_text": facts.get("eta_text"),
        "draft_preview": draft[:220],
        "draft_source": draft_source,
        "engine": engine,
        "refs": refs,
        "escalate": escalate,
        "conflict_check": conflict,
        # M9/M11 agentic RAG 痕迹：路由、策略规划、逐轮检索轨迹、改写/分级来源（现有键全保留）
        "route": route,
        "retrieval_trace": rag_res["trace"],
        "rewrite_source": rag_res["rewrite_source"],
        "grade_source": rag_res["grade_source"],
        "strategy_source": rag_res["strategy_source"],
        "strategy_reason": rag_res["strategy_reason"],
        "rag_hits": len(rag),
    }
    if rag_res["error"]:
        detail["rag_error"] = rag_res["error"]
    if usage:
        detail["llm_usage"] = usage
    await rec.status(WorkflowStatus.handling_support.value)
    await rec.step("support", detail=detail, latency_ms=int((time.perf_counter() - t0) * 1000))
    await rec.decision(
        agent="support_agent",
        decision_type=AgentDecisionType.support_reply.value,
        reasoning=(
            "回复草稿融合：订单/物流实时事实取自 get_order_status 工具，政策引用取自 agentic RAG"
            + (
                f"（策略规划→混合检索→分级，共 {len(rag_res['trace'])} 轮）"
                if rag_res["trace"]
                else "（本单路由判定无需政策检索）"
            )
            + "；"
            + (
                f"检测到草稿时效与工具冲突（{conflict.get('draft_etas')} vs 工具 "
                f"{conflict.get('tool_eta')}），按 PRD §7.11 铁律弃稿回退模板"
                if conflict.get("detected")
                else "草稿与工具事实一致，引用已校验白名单"
            )
        ),
        chosen_option="escalate" if escalate else "draft_reply",
        alternatives=["draft_reply"] if escalate else ["escalate"],
    )
    update: Dict[str, Any] = {"support": support}
    if usage:
        update["llm_usage"] = _merge_llm_usage(
            state, usage["prompt"], usage["completion"]
        )
    return update


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
    # M7 红队：回写前扫描记忆投毒（F）——选题/供应商话术里的营销夸大会随 lesson
    # 进入长期记忆污染后续检索，命中即隔离不回写（PRD §20.1 F：隔离不入 semantic）
    poison_hits = await _record_bad_cases(
        state, config, "retrospective", {"launch_lesson": lesson}
    )
    if poison_hits:
        try:
            await repo.insert_bad_case(
                tenant_id=state["tenant_id"],
                workflow_id=state.get("workflow_id"),
                category="memory_anomaly",
                severity="high",
                detector="memory_writeback_block",
                summary="复盘经验含投毒模式，本次记忆回写已隔离跳过",
                evidence={"hits": len(poison_hits)},
                status="quarantined",
            )
        except Exception:
            logger.warning("bad_case 落库失败（不阻断主流程）", exc_info=True)
    else:
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
        "memory_writeback_blocked": bool(poison_hits),
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
