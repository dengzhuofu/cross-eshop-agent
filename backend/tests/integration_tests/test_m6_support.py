"""M6 集成：全链路对真库跑通时，support 节点完成 RAG + 工具融合（hermetic，hash 嵌入）。"""

import json

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

DOC = {
    "category": "policy",
    "title": "退换货政策",
    "ref": "POL-RTN-07 v2.1",
    "content": (
        "7 天无理由退货（未拆封）。物流延迟处理：订单延误时可选择补偿券或继续等待，"
        "具体时效以订单实时物流轨迹为准。"
    ),
}


async def test_full_run_support_fuses_tool_facts_with_rag_refs():
    tenant_id = "t_m6_integration"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, "Test Co integration")
    vectors, _u, _e = await embed_texts([DOC["content"]])
    await repo.insert_knowledge(
        tenant_id=tenant_id,
        category=DOC["category"],
        title=DOC["title"],
        content=DOC["content"],
        embedding=vectors[0],
        ref=DOC["ref"],
    )

    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title="m6 full run",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    final_state = await graph.ainvoke(
        initial_state(wf.id, tenant_id, TASK),
        config={"configurable": {"recorder": rec}},
    )

    assert final_state["support"]["refs"] == [DOC["ref"]]
    assert final_state["support"]["order_status"]
    assert "3-5 个工作日" in final_state["support"]["draft"]

    steps = await repo.steps(tenant_id, wf.id)
    support = next(s for s in steps if s.node == "support")
    detail = support.detail or {}
    assert detail["order_found"] is True
    assert detail["rag_hits"] == 1
    assert detail["conflict_check"]["detected"] is False
    assert detail["draft_source"] == "template"  # hermetic 无 LLM → 确定性模板

    # M6 两个新工具都经治理管线落审计
    calls = await repo.tool_calls(tenant_id, wf.id)
    assert {"get_order_status", "search_knowledge"} <= {c.tool for c in calls}

    # 客服决策入时间线（PRD §8.3 决策点）
    decisions = await repo.decisions(tenant_id, wf.id)
    support_decisions = [d for d in decisions if d.decision_type == "support_reply"]
    assert support_decisions and support_decisions[0].reasoning
    assert json.dumps(final_state["support"], ensure_ascii=False)
