"""全链路集成测试：十三步 walking skeleton 对真库跑通。"""

import json

from app.graphs.product_launch.agent import graph
from app.graphs.product_launch.state import initial_state
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

TASK = {
    "product_idea": "可折叠床底收纳箱",
    "marketplaces": ["amazon", "tiktok_shop"],
    "target_market": "US",
}


async def _create_and_run(repo: WorkflowRepository):
    wf = await repo.create_workflow(
        tenant_id="t_test",
        title="walking skeleton demo",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, "t_test")
    final_state = await graph.ainvoke(
        initial_state(wf.id, "t_test", TASK),
        config={"configurable": {"recorder": rec}},
    )
    return wf, final_state


async def test_full_run_completes_with_expected_loops():
    repo = WorkflowRepository()
    await repo.ensure_tenant("t_test", "Test Co")
    wf, final_state = await _create_and_run(repo)

    # 状态机收敛到 completed（唯一真源在 repositories）
    stored = await repo.get("t_test", wf.id)
    assert stored is not None and stored.status == "completed"

    # 自主深化：首轮证据不足触发第二轮，共 2 轮后达阈值
    assert final_state["research_rounds"] == 2

    # CritiqueLoop：打回恰好一轮；重写后的 Listing 不再含违规声明（约束生效）
    assert final_state["critique_rounds"] == 1
    dumped = json.dumps(final_state["listings"], ensure_ascii=False)
    for phrase in ("保证", "100%", "治愈"):
        assert phrase not in dumped

    # 顶层 go/no-go 显式存在且为 proceed
    assert final_state["go_no_go"] == "proceed"

    # 决策时间线覆盖关键决策点（PRD §8.3）
    decisions = await repo.decisions("t_test", wf.id)
    decision_types = {d.decision_type for d in decisions}
    assert {"plan", "research_deepening", "go_no_go", "rewrite", "auto_approval"} <= decision_types
    # 每条决策必须带理由与备选项
    for d in decisions:
        assert d.reasoning
        assert d.chosen_option

    # trace 完整：步数 ≥12；critic 先 rewrite 后 pass 可追溯
    steps = await repo.steps("t_test", wf.id)
    assert len(steps) >= 12
    critic_steps = [s for s in steps if s.node == "critic"]
    verdicts = [(s.detail or {}).get("verdict") for s in critic_steps]
    assert "rewrite" in verdicts and "pass" in verdicts

    # 发布产物带幂等键（PRD §18.7）
    assert final_state["published"]
    assert all(p["idempotency_key"] for p in final_state["published"])

    # 复盘存在且声明记忆回写接缝
    assert final_state["retrospective"]["memory_writeback"]


async def test_cross_tenant_read_is_blocked():
    repo = WorkflowRepository()
    await repo.ensure_tenant("t_test", "Test Co")
    wf, _ = await _create_and_run(repo)

    # IDOR：跨租户按 id 读取等同不存在（上层转 404，防枚举）
    assert await repo.get("t_other_tenant", wf.id) is None
    assert await repo.get("t_test", wf.id) is not None
