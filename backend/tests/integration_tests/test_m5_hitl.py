"""M5 HITL 集成：interrupt 挂起 → 人工通过/驳回 → resume 收敛（hermetic，零出网）。

直接驱动带 AsyncSqliteSaver 的图实例（与 FastAPI lifespan 同构）；
API 层的 404/409 守卫由真实 E2E 覆盖，这里钉死图语义。
注意：挂起与恢复必须在 saver 连接存活期内完成，因此用 async CM 包住全流程。
"""

from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.config import get_settings
from app.graphs.product_launch.agent import build_graph
from app.graphs.product_launch.state import initial_state
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

TASK = {
    "product_idea": "可折叠床底收纳箱",
    "marketplaces": ["amazon", "tiktok_shop"],
    "target_market": "US",
}


class _Harness:
    """一次 HITL 流程的驱动句柄：图、配置与断言用的仓储。"""

    def __init__(self, graph, config, task_input, workflow_id, tenant_id, repo):
        self.graph = graph
        self.config = config
        self.task_input = task_input
        self.workflow_id = workflow_id
        self.tenant_id = tenant_id
        self.repo = repo


@asynccontextmanager
async def hitl_harness(tmp_path, monkeypatch, task_input: dict):
    """建工作流并返回可反复 ainvoke 的图；checkpointer 在块内保持可用。"""
    global_auto = task_input.pop("_global_auto", True)
    monkeypatch.setattr(get_settings(), "auto_approve", global_auto)
    repo = WorkflowRepository()
    await repo.ensure_tenant("t_test", "Test Co")
    wf = await repo.create_workflow(
        tenant_id="t_test",
        title="hitl demo",
        product_idea=task_input["product_idea"],
        marketplaces=task_input["marketplaces"],
        status="queued",
        input_json=dict(task_input),
    )
    rec = RunRecorder(repo, wf.id, "t_test")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        g = build_graph(checkpointer=saver)
        cfg = {"configurable": {"recorder": rec, "thread_id": wf.id}}
        yield _Harness(g, cfg, task_input, wf.id, "t_test", repo)


def _interrupt_payload(state: dict) -> dict | None:
    intr = state.get("__interrupt__")
    return dict(intr[0].value or {}) if intr else None


async def test_hitl_suspend_then_approve_publishes(tmp_path, monkeypatch):
    async with hitl_harness(tmp_path, monkeypatch, {**TASK, "_global_auto": False}) as h:
        suspended = await h.graph.ainvoke(
            initial_state(h.workflow_id, h.tenant_id, h.task_input), config=h.config
        )
        payload = _interrupt_payload(suspended)
        # 挂起态：审批材料完整进 payload，图未发布任何东西
        assert payload is not None and payload["listings"]
        assert payload["listings"][0]["title"]
        assert isinstance(payload["margin_pct"], (int, float))
        assert suspended.get("published") == []

        resumed = await h.graph.ainvoke(
            Command(resume={"approved": True, "comment": "数据没问题，发"}), config=h.config
        )
        assert _interrupt_payload(resumed) is None
        assert resumed["approved"] is True and resumed["published"]
        assert resumed["approval_decision"]["comment"] == "数据没问题，发"

        # 人工决定落审计：human_approval 决策 + 发布幂等键照常生成
        decisions = await h.repo.decisions(h.tenant_id, h.workflow_id)
        assert any(d.decision_type == "human_approval" for d in decisions)


async def test_hitl_reject_halts_without_publish(tmp_path, monkeypatch):
    async with hitl_harness(tmp_path, monkeypatch, {**TASK, "_global_auto": False}) as h:
        await h.graph.ainvoke(
            initial_state(h.workflow_id, h.tenant_id, h.task_input), config=h.config
        )
        resumed = await h.graph.ainvoke(
            Command(resume={"approved": False, "comment": "利润太薄，先不上"}), config=h.config
        )
        assert _interrupt_payload(resumed) is None
        assert resumed["approved"] is False
        assert not resumed.get("published")
        assert resumed["approval_decision"] == {
            "approved": False,
            "comment": "利润太薄，先不上",
        }
        # 驳回走 cancelled 语义的 halted 步骤（附言入审计）
        steps = await h.repo.steps(h.tenant_id, h.workflow_id)
        halted = [s for s in steps if s.node == "halted"]
        assert halted and "人工驳回" in str(halted[-1].detail)


async def test_per_workflow_auto_approve_false_overrides_global_true(tmp_path, monkeypatch):
    """全局 dev 自动放行时，单个工作流仍可显式要求 HITL（demo/生产并存的关键）。"""
    async with hitl_harness(tmp_path, monkeypatch, {**TASK, "auto_approve": False}) as h:
        suspended = await h.graph.ainvoke(
            initial_state(h.workflow_id, h.tenant_id, h.task_input), config=h.config
        )
        assert _interrupt_payload(suspended) is not None


async def test_global_auto_approve_keeps_legacy_behavior(tmp_path, monkeypatch):
    """AUTO_APPROVE=true 且无工作流级覆盖：不产生 interrupt，旧链路行为不变。"""
    async with hitl_harness(tmp_path, monkeypatch, dict(TASK)) as h:
        final_state = await h.graph.ainvoke(
            initial_state(h.workflow_id, h.tenant_id, h.task_input), config=h.config
        )
        assert _interrupt_payload(final_state) is None
        assert final_state["approved"] is True
