"""M13 对话式活动流数据面：trace API 必须吐出前端交错排序与工具卡渲染所需的字段。

此前 /trace 的 steps 无 created_at、tool_calls 缺 input/output_summary（审计里存了
但没吐）——对话式「工具调用卡」和活动流排序全靠它们。hermetic：无 key 走 stub 引擎，
租户 id 全会话唯一。
"""

from httpx import ASGITransport, AsyncClient

from app.api.main import app
from app.graphs.product_launch.agent import graph
from app.graphs.product_launch.state import initial_state
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

T = "t_m13_trace"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_trace_api_exposes_activity_fields():
    repo = WorkflowRepository()
    await repo.ensure_tenant(T, f"{T} Co")
    task = {
        "product_idea": "磁吸窗帘遮光片",
        "marketplaces": ["amazon"],
        "target_market": "US",
    }
    wf = await repo.create_workflow(
        tenant_id=T,
        title="M13 trace api 夹具",
        product_idea=task["product_idea"],
        marketplaces=task["marketplaces"],
        status="queued",
        input_json=task,
    )
    rec = RunRecorder(repo, wf.id, T)
    await graph.ainvoke(
        initial_state(wf.id, T, task),
        config={"configurable": {"recorder": rec}},
    )

    async with _client() as client:
        resp = await client.get(
            f"/api/v1/workflows/{wf.id}/trace", headers={"X-Tenant-Id": T}
        )
    assert resp.status_code == 200
    body = resp.json()

    # 步骤带完成时间（活动流与决策/工具调用交错排序的锚点）
    assert body["steps"], "full graph run must record steps"
    assert all(s.get("created_at") for s in body["steps"])

    # 工具调用卡的数据面：输入/输出摘要 + 时间戳；发布调用必须带可读摘要
    calls = body["tool_calls"]
    assert {"get_marketplace_rules", "publish_listing"} <= {c["tool"] for c in calls}
    for c in calls:
        assert c.get("created_at")
    pub = [c for c in calls if c["tool"] == "publish_listing"]
    assert pub and all(c["input_summary"] for c in pub)
    assert all(c["output_summary"] is not None for c in pub)
