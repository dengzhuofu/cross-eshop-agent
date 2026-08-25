"""M11 集成：agentic RAG 的策略自适应循环——LLM 按问题提议 direct/rewrite/hyde，
代码校验（枚举外弃用回退规则）；hyde 假设文档只进检索工具语义路参数；零相关沿
升级阶梯换策略重试；无 LLM 时 hyde 降级、闭环不断。conftest 强制无 key：默认
全离线，LLM 场景 monkeypatch 假客户端按 system 提示词子串路由（与 M9 同手法）。
"""

from app.graphs.product_launch import nodes as N
from app.graphs.product_launch.nodes import node_support
from app.graphs.product_launch.state import initial_state
from app.llm.embeddings import embed_texts
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

TASK = {
    "product_idea": "可折叠床底收纳箱",
    "marketplaces": ["amazon"],
    "target_market": "US",
}


async def _harness(tag: str):
    """独立租户 + 独立工作流，返回 (repo, wf, rec, state)。"""
    tenant_id = f"t_m11_{tag}"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title=f"m11 strategy {tag}",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    state = initial_state(wf.id, tenant_id, TASK)
    return repo, wf, rec, state


# ---- 1. 全离线：规则 rewrite 首轮 → 阶梯升 hyde 无从生成 → 确定性降级改写变体 ----


async def test_offline_rule_rewrite_escalation_degrades_hyde_to_rewrite_variant():
    repo, wf, rec, state = await _harness("esc")  # 不种知识 → 两轮全零命中
    await node_support(state, {"configurable": {"recorder": rec}})

    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    trace = detail["retrieval_trace"]
    assert len(trace) == 2
    assert trace[0]["strategy"] == "rewrite" and trace[0]["hyde"] is False
    # 无 LLM：阶梯 rewrite→hyde 无从生成假设文档，确定性降级为改写变体重试
    assert trace[1]["strategy"] == "rewrite" and trace[1]["hyde"] is False
    assert trace[0]["query"] != trace[1]["query"], "重试轮必须换查询"
    assert all(e["hits"] == 0 for e in trace)
    assert detail["strategy_source"] == "rule" and detail["strategy_reason"] == ""
    assert detail["rewrite_source"] == "deterministic"


# ---- 2. LLM 提议 hyde：假设文档进工具参数（语义路专用），重试轮换角度重生成 ----


async def test_llm_proposed_hyde_feeds_document_to_tool_semantic_leg(monkeypatch):
    repo, wf, rec, state = await _harness("hyde")
    hyde_users: list[str] = []

    async def _router(system, user, **kwargs):
        if "策略规划器" in system:
            return (
                {"strategy": "hyde", "query": "退换货政策 退款流程", "reason": "长问题需语义泛化"},
                {"prompt": 20, "completion": 8},
            )
        if "HyDE" in system:
            hyde_users.append(user)
            return (
                {"document": "未拆封商品支持七天无理由退换货，物流延误可选择补偿券或继续等待。"},
                {"prompt": 25, "completion": 15},
            )
        return (
            {"draft": "您好，您的订单问题已收到，客服正在为您核实处理。",
             "cited_refs": [], "escalate": False},
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _router)

    await node_support(state, {"configurable": {"recorder": rec}})
    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["rewrite_source"] == "hyde"
    assert detail["strategy_source"] == "llm"
    assert detail["strategy_reason"] == "长问题需语义泛化"

    trace = detail["retrieval_trace"]
    # 空知识库两轮全零命中，但策略始终是 hyde 且假设文档真实生成（第二轮换角度）
    assert len(trace) == 2
    assert all(e["strategy"] == "hyde" and e["hyde"] is True for e in trace)
    assert len(hyde_users) == 2
    assert "换一个角度" not in hyde_users[0]
    assert "换一个角度" in hyde_users[1]

    calls = await repo.tool_calls(wf.tenant_id, wf.id)
    searches = [c for c in calls if c.tool == "search_knowledge"]
    assert len(searches) == 2
    for c in searches:
        summary = c.input_summary or {}
        assert summary.get("hyde_text"), "hyde 轮必须把假设文档传给检索工具（语义路专用）"
        assert summary.get("mode") == "hybrid"
        assert "where_is_my_order" in str(summary.get("query_text")), "词面 query 保持用户原句"
    # 计量：策略 20 + 两轮 HyDE 25×2 + 草稿 100（空库零命中不触发判级）
    assert detail["llm_usage"]["prompt"] == 170


# ---- 3. LLM 提议 direct：原句直检不改写；零相关后阶梯降档 rewrite 重试 ----


async def test_llm_proposed_direct_keeps_raw_question_then_ladder_rewrites(monkeypatch):
    repo, wf, rec, state = await _harness("direct")
    hyde_called: list[str] = []

    async def _router(system, user, **kwargs):
        if "策略规划器" in system:
            return (
                {"strategy": "direct", "reason": "含单号直检保真"},
                {"prompt": 20, "completion": 8},
            )
        if "HyDE" in system:  # direct→rewrite 的阶梯不该碰 HyDE 生成器
            hyde_called.append(user)
            return ({"document": "不应被调用"}, {"prompt": 1, "completion": 1})
        return (
            {"draft": "您好，已收到您的咨询，客服将尽快为您核实处理。",
             "cited_refs": [], "escalate": False},
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _router)

    await node_support(state, {"configurable": {"recorder": rec}})
    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    trace = detail["retrieval_trace"]
    assert trace[0]["strategy"] == "direct" and trace[0]["hyde"] is False
    assert detail["rewrite_source"] == "as-is"
    assert detail["strategy_source"] == "llm"
    assert detail["strategy_reason"] == "含单号直检保真"
    # 零相关 → 阶梯 direct→rewrite，换确定性改写变体而非原样复读
    assert trace[1]["strategy"] == "rewrite"
    assert trace[0]["query"] != trace[1]["query"]
    assert not hyde_called, "direct/rewrite 轮不得触发 HyDE 生成器"

    calls = await repo.tool_calls(wf.tenant_id, wf.id)
    searches = [c for c in calls if c.tool == "search_knowledge"]
    assert len(searches) == 2
    assert all(
        "hyde_text" not in (c.input_summary or {}) for c in searches
    ), "非 hyde 策略不得携带假设文档参数"
    # 计量：策略 20 + 草稿 100（零命中不触发判级）
    assert detail["llm_usage"]["prompt"] == 120


# ---- 4. LLM 提议越界：策略整体弃用回退规则，合法 query 仍可复用 ----


async def test_out_of_enum_strategy_proposal_discarded_rule_fallback(monkeypatch):
    repo, wf, rec, state = await _harness("badprop")

    async def _router(system, user, **kwargs):
        if "策略规划器" in system:
            return (
                {"strategy": "magic", "query": "退换货政策 退款流程", "reason": "越界提议"},
                {"prompt": 20, "completion": 8},
            )
        if "HyDE" in system:  # 升级轮 hyde 无从生成（空文档）→ 确定性降级改写变体
            return ({"document": ""}, {"prompt": 12, "completion": 4})
        return (
            {"draft": "您好，已收到您的咨询，客服将尽快为您核实处理。",
             "cited_refs": [], "escalate": False},
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _router)

    await node_support(state, {"configurable": {"recorder": rec}})
    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    trace = detail["retrieval_trace"]
    # 策略回退规则（demo 工单复合问题 → rewrite），但提议里的合法 query 仍被采用
    assert detail["strategy_source"] == "rule" and detail["strategy_reason"] == ""
    assert trace[0]["strategy"] == "rewrite"
    assert trace[0]["query"] == "退换货政策 退款流程"
    assert detail["rewrite_source"] == "llm"
    # 计量：策略 20 + 升级轮空 HyDE 12 + 草稿 100（零命中不触发判级）
    assert detail["llm_usage"]["prompt"] == 132


# ---- 5. 仓储层 alt 向量：语义路对每篇文档取 max(cos(主查询), cos(假设文档)) ----


async def test_search_knowledge_alt_embedding_takes_max_cosine():
    repo = WorkflowRepository()
    tenant_id = "t_m11_altvec"
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    text_a = "退换货政策：未拆封商品七天内可以退货退款。"
    text_b = "支付方式：支持信用卡、PayPal 等在线支付渠道。"
    va, _u1, _e1 = await embed_texts([text_a])
    vb, _u2, _e2 = await embed_texts([text_b])
    vc, _u3, _e3 = await embed_texts(["物流时效：国际订单通常七到十个工作日送达。"])
    await repo.insert_knowledge(
        tenant_id=tenant_id, category="policy", title="退货政策",
        content=text_a, embedding=va[0], ref="POL-A",
    )
    await repo.insert_knowledge(
        tenant_id=tenant_id, category="faq", title="支付方式",
        content=text_b, embedding=vb[0], ref="PAY-B",
    )

    # 无 alt：主查询向量=C（与两篇都不同）→ A 不是满分也不登顶
    plain = await repo.search_knowledge(
        tenant_id=tenant_id, category=None, query_embedding=vc[0], top_k=2,
    )
    plain_by_ref = {r["ref"]: r for r in plain}
    assert plain_by_ref["POL-A"]["similarity"] < 0.999

    # 带 alt=A 文档向量：A 的相似度按 max(cos 主, cos alt) 提到满分并登顶——
    # 这正是 HyDE「假设文档只增强语义召回」的实现路径；max 只抬不压，B 不封顶
    fused = await repo.search_knowledge(
        tenant_id=tenant_id, category=None, query_embedding=vc[0],
        query_embedding_alt=va[0], top_k=2,
    )
    fused_by_ref = {r["ref"]: r for r in fused}
    assert abs(fused_by_ref["POL-A"]["similarity"] - 1.0) < 1e-3
    assert fused_by_ref["PAY-B"]["similarity"] < 1.0
    assert (
        fused_by_ref["PAY-B"]["similarity"]
        >= plain_by_ref["PAY-B"]["similarity"] - 1e-9
    )
    assert [r["ref"] for r in fused][0] == "POL-A"
