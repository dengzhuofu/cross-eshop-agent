"""M6 RAG 知识库 + 客服融合单测：工具治理链路、跨租户隔离、冲突回退硬保证。

全程不出网（conftest 清空 key → 嵌入走 hash 引擎、LLM 路径用 monkeypatch 假客户端）；
持久化走真实 WorkflowRepository（临时 SQLite）。注意测试库文件整个 pytest 会话共享、
init_db 只建表不清数据，因此每个用例用独立租户 id，避免相互泄漏。
"""

from app.graphs.product_launch import nodes as N
from app.graphs.product_launch.nodes import _etas_in, node_support
from app.graphs.product_launch.state import initial_state
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

# 注册副作用：import 即把 search_knowledge / get_order_status 登记进 registry
from app.tools.catalog import knowledge as _knowledge_catalog  # noqa: F401
from app.tools.catalog import order as _order_catalog  # noqa: F401
from app.tools.context import ToolContext
from app.tools.executor import execute_tool

_seq = 0


async def _harness(tag: str):
    """独立租户 + 独立工作流，返回 (repo, wf, rec, state, ctx)。"""
    global _seq
    _seq += 1
    tenant_id = f"t_m6_{tag}_{_seq}"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title="m6 support demo",
        product_idea="可折叠床底收纳箱",
        marketplaces=["amazon"],
        status="queued",
        input_json={},
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    state = initial_state(wf.id, tenant_id, {"product_idea": "可折叠床底收纳箱"})
    ctx = ToolContext(tenant_id=tenant_id, workflow_id=wf.id, actor_id="test")
    return repo, wf, rec, state, ctx


async def _seed_doc(tenant_id: str, *, category: str, title: str, ref: str,
                    content: str) -> str:
    from app.llm.embeddings import embed_texts

    repo = WorkflowRepository()
    vectors, _u, _e = await embed_texts([content])
    return await repo.insert_knowledge(
        tenant_id=tenant_id,
        category=category,
        title=title,
        content=content,
        embedding=vectors[0],
        ref=ref,
    )


# ---- 1. search_knowledge 工具：治理链路 + 跨租户隔离 ----


async def test_search_knowledge_shape_and_tenant_isolation():
    _repo, _wf, _rec, _state, ctx = await _harness("shape")
    tenant_id = ctx.tenant_id
    await _seed_doc(
        tenant_id,
        category="policy",
        title="退换货政策",
        ref="POL-RTN-07 v2.1",
        content="7 天无理由退货（未拆封）；物流延迟可参考补偿方案，具体时效以订单实时物流为准。",
    )
    await _seed_doc(
        tenant_id,
        category="faq",
        title="物流时效 FAQ",
        ref="FAQ-01",
        content="标准跨境物流 7-10 个工作日，优先物流 5-7 个工作日。",
    )
    await _seed_doc(
        "t_m6_foreign_tenant",
        category="policy",
        title="他租户政策",
        ref="POL-XX-01",
        content="他租户的退换货政策，本租户检索必须不可见。",
    )

    res = await execute_tool(
        "search_knowledge",
        {"query_text": "退换货政策 物流延迟", "top_k": 5},
        ctx,
        WorkflowRepository(),
    )
    hits = res.output["results"]
    assert len(hits) == 2  # 跨租户文档不可见
    assert {h["ref"] for h in hits} == {"POL-RTN-07 v2.1", "FAQ-01"}
    assert hits[0]["similarity"] >= hits[1]["similarity"]  # 相似度降序
    assert all(h["content"] and h["category"] for h in hits)

    # category 过滤
    res = await execute_tool(
        "search_knowledge",
        {"query_text": "退换货政策", "category": "policy"},
        ctx,
        WorkflowRepository(),
    )
    assert {h["ref"] for h in res.output["results"]} == {"POL-RTN-07 v2.1"}


async def test_get_order_status_known_and_unknown():
    _repo, _wf, _rec, _state, ctx = await _harness("order")
    res = await execute_tool(
        "get_order_status", {"order_id": "ord_88123"}, ctx, WorkflowRepository()
    )
    out = res.output
    assert out["found"] is True
    assert out["status"] and out["eta_text"] == "3-5 个工作日"
    assert out["payment_status"] and len(out["logistics"]) >= 2

    res = await execute_tool(
        "get_order_status", {"order_id": "ord_does_not_exist"}, ctx, WorkflowRepository()
    )
    assert res.output["found"] is False


# ---- 2. 融合铁律：草稿时效与工具冲突 → 整稿弃用回退模板 ----


def test_etas_in_extracts_normalized_timeframes():
    assert _etas_in("预计 3-5 个工作日内送达，退款 48 小时处理") == ["3-5个工作日"]
    assert _etas_in("没有时效表述") == []


async def test_support_conflict_falls_back_to_template(monkeypatch):
    """LLM 引用了知识库通用时效（7-10）而工具实时是 3-5 → 判冲突，弃稿回退模板。"""
    repo, wf, rec, state, _ctx = await _harness("conflict")

    async def _bad_llm(system, user, **kwargs):
        return (
            {"draft": "您好，订单预计 7-10 个工作日送达。", "cited_refs": [], "escalate": False},
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _bad_llm)

    result = await node_support(state, {"configurable": {"recorder": rec}})
    support = result["support"]
    assert support["eta_text"] == "3-5 个工作日"  # 事实字段始终来自工具
    assert "3-5 个工作日" in support["draft"]  # 草稿回退为工具事实模板
    assert "7-10" not in support["draft"]
    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["conflict_check"]["detected"] is True
    assert detail["draft_source"] == "template"
    decisions = await repo.decisions(wf.tenant_id, wf.id)
    assert any(d.decision_type == "support_reply" and "冲突" in d.reasoning for d in decisions)


async def test_support_consistent_llm_draft_adopted_with_ref_whitelist(monkeypatch):
    """草稿时效与工具一致 → 采纳 LLM 草稿；cited_refs 过滤为 RAG 命中白名单。"""
    repo, wf, rec, state, ctx = await _harness("adopt")
    await _seed_doc(
        ctx.tenant_id,
        category="policy",
        title="退换货政策",
        ref="POL-RTN-07 v2.1",
        content="物流延迟处理：可申请补偿券或等待，具体以订单实时物流为准。",
    )

    async def _good_llm(system, user, **kwargs):
        return (
            {
                "draft": "您好，订单 ord_88123 当前为国际运输中，预计 3-5 个工作日送达，"
                "感谢耐心等待。如需延迟处理可参考退换货政策，我们 100% 为您跟进。",
                "cited_refs": ["POL-RTN-07 v2.1", "FAKE-REF-404"],
                "escalate": False,
            },
            {"prompt": 120, "completion": 40},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _good_llm)

    result = await node_support(state, {"configurable": {"recorder": rec}})
    support = result["support"]
    steps = await repo.steps(ctx.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["draft_source"] == "llm"
    assert detail["conflict_check"]["detected"] is False
    assert support["refs"] == ["POL-RTN-07 v2.1"]  # 白名单外的 FAKE-REF-404 被过滤
    assert "100%" not in support["draft"]  # 绝对化措辞被生成端整形


async def test_support_stub_path_uses_tool_facts_and_rag_refs():
    """无 LLM（conftest 清 key）：模板草稿仍满足——事实来自工具、引用来自 RAG。"""
    repo, wf, rec, state, ctx = await _harness("stub")
    await _seed_doc(
        ctx.tenant_id,
        category="policy",
        title="退换货政策",
        ref="POL-RTN-07 v2.1",
        content="物流延迟处理方案与退换货流程说明，具体时效以订单实时物流为准。",
    )

    result = await node_support(state, {"configurable": {"recorder": rec}})
    support = result["support"]
    assert support["order_status"] == "国际运输中"
    assert "3-5 个工作日" in support["draft"]
    assert support["refs"] == ["POL-RTN-07 v2.1"]
    steps = await repo.steps(ctx.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["draft_source"] == "template"
    assert detail["rag_hits"] == 1
    assert detail["order_found"] is True

    # 工具审计：两个 M6 工具都经 ToolExecutor 落审计
    calls = await repo.tool_calls(ctx.tenant_id, wf.id)
    assert {"get_order_status", "search_knowledge"} <= {c.tool for c in calls}


async def test_support_rag_isolation_globex_sees_no_acme_knowledge():
    """知识只 seed 给 acme 租户：globex 工单检索 RAG 为空，草稿退化为纯工具事实句。"""
    await _seed_doc(
        "t_m6_acme_only",
        category="policy",
        title="acme 政策",
        ref="POL-ACME-01",
        content="acme 专属政策内容。",
    )
    repo, wf, rec, state, _ctx = await _harness("isol")

    result = await node_support(state, {"configurable": {"recorder": rec}})
    support = result["support"]
    assert support["refs"] == []  # 跨租户知识不可见
    assert "3-5 个工作日" in support["draft"]  # 工具事实仍然可用
