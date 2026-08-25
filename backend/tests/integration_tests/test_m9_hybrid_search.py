"""M9 集成：search_knowledge 混合检索（BM25+余弦 RRF）端到端。

核心回归：纯中文查询在 hash 嵌入（离线）下旧纯余弦路径曾是零向量任意序，
hybrid 模式必须靠 BM25 词面路命中正确文档。工具级断言走完整治理管线。
"""

import pytest

from app.llm.embeddings import EMBEDDING_DIM, embed_texts
from app.persistence.repositories.workflow_repo import WorkflowRepository
from app.tools.context import ToolContext
from app.tools.executor import ToolError, execute_tool

DOCS = [
    {
        "category": "policy",
        "title": "退换货政策",
        "ref": "POL-RTN-07",
        "content": "退换货政策：未拆封商品签收后 7 天内可无理由退货，质量问题 15 天内可换新。",
    },
    {
        "category": "platform_rule",
        "title": "发货时效规则",
        "ref": "PLT-SHP-02",
        "content": "平台发货时效规则：订单须在 48 小时内发货，物流延误按实时轨迹处理。",
    },
    {
        "category": "faq",
        "title": "物流时效 FAQ",
        "ref": "FAQ-01",
        "content": "常见问题：跨境订单物流时效一般为 7-15 天，清关期间轨迹可能短暂停滞。",
    },
]


async def _seed(repo: WorkflowRepository, tenant_id: str) -> None:
    for doc in DOCS:
        vectors, _u, _e = await embed_texts([doc["content"]])
        await repo.insert_knowledge(
            tenant_id=tenant_id,
            category=doc["category"],
            title=doc["title"],
            content=doc["content"],
            embedding=vectors[0],
            ref=doc["ref"],
        )


async def test_hybrid_hits_right_doc_for_chinese_query():
    tenant_id = "t_m9_hybrid_a"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    await _seed(repo, tenant_id)

    vectors, _u, _e = await embed_texts(["东西坏了想退货怎么退"])
    rows = await repo.search_knowledge(
        tenant_id=tenant_id,
        category=None,
        query_embedding=vectors[0],
        top_k=3,
        query_text="东西坏了想退货怎么退",
    )
    assert rows, "混合检索应命中"
    assert rows[0]["ref"] == "POL-RTN-07"
    for key in ("bm25", "rrf"):
        assert key in rows[0], f"混合模式返回应带 {key} 分数"


async def test_vector_mode_keeps_legacy_behavior():
    tenant_id = "t_m9_hybrid_b"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    await _seed(repo, tenant_id)

    vectors, _u, _e = await embed_texts(["退货"])
    rows = await repo.search_knowledge(
        tenant_id=tenant_id,
        category=None,
        query_embedding=vectors[0],
        top_k=2,
    )
    assert len(rows) <= 2
    assert all("bm25" not in r for r in rows), "旧行为（query_text=None）不得多出混合分数"


async def test_hybrid_tenant_isolation():
    repo = WorkflowRepository()
    tenant_a, tenant_b = "t_m9_hybrid_c", "t_m9_hybrid_d"
    await repo.ensure_tenant(tenant_a, f"Test Co {tenant_a}")
    await repo.ensure_tenant(tenant_b, f"Test Co {tenant_b}")
    await _seed(repo, tenant_a)

    vectors, _u, _e = await embed_texts(["退货"])
    rows_b = await repo.search_knowledge(
        tenant_id=tenant_b,
        category=None,
        query_embedding=vectors[0],
        top_k=5,
        query_text="退货",
    )
    assert rows_b == [], "租户 B 不得看到租户 A 的知识（多租户铁律）"


async def _setup(repo: WorkflowRepository, tenant_id: str) -> str:
    """建租户 + 建真实工作流（工具审计行的 FK 需要），返回 workflow_id。"""
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title=f"m9 hybrid {tenant_id}",
        product_idea="退货政策测试",
        marketplaces=["amazon"],
        status="queued",
        input_json={},
    )
    return wf.id


async def test_tool_hybrid_mode_with_grading():
    tenant_id = "t_m9_hybrid_e"
    repo = WorkflowRepository()
    wf_id = await _setup(repo, tenant_id)
    await _seed(repo, tenant_id)

    ctx = ToolContext(tenant_id=tenant_id, workflow_id=wf_id)
    res = await execute_tool(
        "search_knowledge",
        {"query_text": "退货 政策", "top_k": 3, "mode": "hybrid", "grade": True},
        ctx,
        repo,
    )
    results = res.output["results"]
    assert results
    first = results[0]
    assert first["ref"] == "POL-RTN-07"
    assert isinstance(first["grade"], bool) and first["grade"] is True
    assert first["grade_score"] >= 0.0
    assert res.output["mode"] == "hybrid"


async def test_tool_vector_mode_backward_compatible():
    tenant_id = "t_m9_hybrid_f"
    repo = WorkflowRepository()
    wf_id = await _setup(repo, tenant_id)
    await _seed(repo, tenant_id)

    ctx = ToolContext(tenant_id=tenant_id, workflow_id=wf_id)
    res = await execute_tool(
        "search_knowledge",
        {"query_text": "退货", "top_k": 2, "mode": "vector"},
        ctx,
        repo,
    )
    assert res.output["mode"] == "vector"
    assert all(r.get("grade") is None for r in res.output["results"])
    assert all(r.get("bm25") is None for r in res.output["results"])


async def test_tool_rejects_invalid_mode():
    tenant_id = "t_m9_hybrid_g"
    repo = WorkflowRepository()
    wf_id = await _setup(repo, tenant_id)
    ctx = ToolContext(tenant_id=tenant_id, workflow_id=wf_id)
    with pytest.raises(ToolError):
        await execute_tool(
            "search_knowledge",
            {"query_text": "退货", "mode": "bm25"},
            ctx,
            repo,
        )


async def test_zero_vector_probe_idempotency_still_works():
    """seed_knowledge 的零向量探测幂等路径在混合模式下不受影响。"""
    tenant_id = "t_m9_hybrid_h"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    await _seed(repo, tenant_id)
    rows = await repo.search_knowledge(
        tenant_id=tenant_id,
        category=None,
        query_embedding=[0.0] * EMBEDDING_DIM,
        top_k=1,
    )
    assert len(rows) == 1
