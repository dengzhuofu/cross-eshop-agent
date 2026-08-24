"""M8 集成：主链路自用 RAG——planner/listing 经 search_knowledge 检索
ops_playbook 运营知识库（hermetic，hash 嵌入，无 LLM 走 stub 路径）。

设计要点：检索在 stub 路径同样执行，knowledge_refs 进 step detail，
封闭测试可直接断言「外挂知识库被主链路真实消费」。
"""

from app.graphs.product_launch.agent import graph
from app.graphs.product_launch.state import initial_state
from app.llm.embeddings import embed_texts
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

TASK = {
    "product_idea": "可折叠床底收纳箱",
    "marketplaces": ["amazon", "tiktok_shop"],
    "target_market": "US",
}

PLAYBOOK_DOCS = [
    {
        "category": "ops_playbook",
        "title": "选品评估方法论",
        "ref": "OPS-SEL-01",
        "content": (
            "选品四维评估：市场容量、竞争度、利润红线、供应链稳定性。"
            "收纳类选题优先考察床底缝隙场景，运营打法上以规格差异点切入。"
        ),
    },
    {
        "category": "ops_playbook",
        "title": "Amazon Listing 优化守则",
        "ref": "OPS-AMZ-LS1",
        "content": (
            "Amazon 文案守则：标题关键词前置，五点描述利益优先规格佐证，"
            "卖点必须对应研究证据；家居收纳类目突出安装时长与承重实测值。"
        ),
    },
    {
        "category": "ops_playbook",
        "title": "TikTok Shop 内容电商运营守则",
        "ref": "OPS-TTS-CM1",
        "content": (
            "TikTok Shop 打法：短视频三段式脚本与直播讲款节奏，"
            "收纳品类高转化素材是小空间扩容对比镜头。"
        ),
    },
]

# 干扰项：policy 类知识不应进入主链路的 ops_playbook 定向检索
POLICY_DOC = {
    "category": "policy",
    "title": "退换货政策",
    "ref": "POL-RTN-07 v2.1",
    "content": "未拆封商品签收后 7 天内可无理由退货，物流延误按实时轨迹处理。",
}


async def _seed(repo: WorkflowRepository, tenant_id: str, docs: list[dict]) -> None:
    for doc in docs:
        vectors, _u, _e = await embed_texts([doc["content"]])
        await repo.insert_knowledge(
            tenant_id=tenant_id,
            category=doc["category"],
            title=doc["title"],
            content=doc["content"],
            embedding=vectors[0],
            ref=doc["ref"],
        )


async def _run_graph(tenant_id: str, name: str) -> tuple[WorkflowRepository, str]:
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, name)
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title=f"m8 ops rag {tenant_id}",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    await graph.ainvoke(
        initial_state(wf.id, tenant_id, TASK),
        config={"configurable": {"recorder": rec}},
    )
    return repo, wf.id


_KNOWN_REFS = {d["ref"] for d in PLAYBOOK_DOCS}


async def test_planner_and_listing_retrieve_ops_playbook():
    tenant_id = "t_m8_opsrag_a"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    await _seed(repo, tenant_id, PLAYBOOK_DOCS + [POLICY_DOC])

    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title=f"m8 ops rag {tenant_id}",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    await graph.ainvoke(
        initial_state(wf.id, tenant_id, TASK),
        config={"configurable": {"recorder": rec}},
    )

    steps = await repo.steps(tenant_id, wf.id)
    plan = next(s for s in steps if s.node == "planner")
    plan_refs = (plan.detail or {}).get("knowledge_refs")
    assert plan_refs, "planner 应检索到 ops_playbook 知识"
    assert set(plan_refs) <= _KNOWN_REFS, "policy 类干扰文档不得混入定向检索"

    listing = next(s for s in steps if s.node == "listing")
    per_mp = (listing.detail or {}).get("knowledge_refs") or {}
    assert per_mp.get("amazon"), "amazon 应检索到 Listing 守则"
    assert per_mp.get("tiktok_shop"), "tiktok_shop 应检索到内容电商守则"
    assert all(set(v) <= _KNOWN_REFS for v in per_mp.values())

    calls = await repo.tool_calls(tenant_id, wf.id)
    kb_calls = [c for c in calls if c.tool == "search_knowledge"]
    assert len(kb_calls) >= 3, "planner 1 次 + 每平台各 1 次检索"


async def test_ops_rag_is_tenant_scoped():
    """没种知识的租户检索为空——RAG 不跨租户、不凭空捏造参考。"""
    tenant_id = "t_m8_opsrag_b"
    repo, wf_id = await _run_graph(tenant_id, f"Test Co {tenant_id}")

    steps = await repo.steps(tenant_id, wf_id)
    plan = next(s for s in steps if s.node == "planner")
    assert not (plan.detail or {}).get("knowledge_refs")

    listing = next(s for s in steps if s.node == "listing")
    per_mp = (listing.detail or {}).get("knowledge_refs") or {}
    assert all(not v for v in per_mp.values())
