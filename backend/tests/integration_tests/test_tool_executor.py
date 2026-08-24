"""ToolExecutor 集成测试：schema 校验 / 跨租户引用 / 审批门 / 幂等回放 / 审计留痕。"""

import pytest

from app.config import get_settings
from app.persistence.repositories.workflow_repo import WorkflowRepository
from app.tools import (
    ApprovalRequiredError,
    CrossTenantReferenceError,
    ToolContext,
    UnknownToolError,
    execute_tool,
)

VALID_LISTING = {
    "title": "Foldable Under-Bed Storage Box",
    "bullets": ["a", "b", "c", "d", "e"],
    "claim": "采用加厚 PP 材质，实验室测试承重 40kg",
}


async def _setup(repo: WorkflowRepository, tenant: str = "t_a") -> str:
    await repo.ensure_tenant(tenant, f"{tenant} Co")
    wf = await repo.create_workflow(
        tenant_id=tenant,
        title="tool test",
        product_idea="box",
        marketplaces=["amazon"],
        status="queued",
        input_json={},
    )
    return wf.id


async def test_publish_ok_and_audit_row_written():
    repo = WorkflowRepository()
    wf_id = await _setup(repo)
    ctx = ToolContext(tenant_id="t_a", workflow_id=wf_id, approved=True)

    res = await execute_tool(
        "publish_listing",
        {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
         "idempotency_key": "itest-pub-0001"},
        ctx,
        repo,
    )
    assert res.ok and not res.replayed
    assert res.output["listing_id"].startswith("ama_")

    calls = await repo.tool_calls("t_a", wf_id)
    assert len(calls) == 1
    assert calls[0].status == "ok"
    assert calls[0].risk_level == "high"


async def test_idempotent_replay_returns_cached_output():
    repo = WorkflowRepository()
    wf_id = await _setup(repo)
    ctx = ToolContext(tenant_id="t_a", workflow_id=wf_id, approved=True)
    payload = {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
               "idempotency_key": "itest-pub-0002"}

    first = await execute_tool("publish_listing", payload, ctx, repo)
    second = await execute_tool("publish_listing", payload, ctx, repo)

    assert second.replayed is True
    assert second.output == first.output  # 同 key 同 listing_id，不重复发布
    statuses = [c.status for c in await repo.tool_calls("t_a", wf_id)]
    assert statuses.count("ok") == 1 and statuses.count("replayed") == 1


async def test_cross_tenant_workflow_reference_is_rejected():
    repo = WorkflowRepository()
    wf_id = await _setup(repo, tenant="t_owner")
    await repo.ensure_tenant("t_attacker", "Attacker Co")
    # 租户上下文是 t_attacker，却引用 t_owner 的 workflow —— 必须拒绝并落审计
    ctx = ToolContext(tenant_id="t_attacker", workflow_id=wf_id, approved=True)

    with pytest.raises(CrossTenantReferenceError):
        await execute_tool(
            "publish_listing",
            {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
             "idempotency_key": "itest-pub-0003"},
            ctx,
            repo,
        )
    calls = await repo.tool_calls("t_attacker", wf_id)
    # 注意：审计行挂在调用方租户名下，workflow_id 归属校验失败也要留痕
    assert any(c.error == "cross_tenant_reference" for c in calls)


async def test_approval_gate_blocks_without_credential(monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE", "false")
    get_settings.cache_clear()
    try:
        repo = WorkflowRepository()
        wf_id = await _setup(repo)
        ctx = ToolContext(tenant_id="t_a", workflow_id=wf_id, approved=False)  # 无审批凭据

        with pytest.raises(ApprovalRequiredError):
            await execute_tool(
                "publish_listing",
                {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
                 "idempotency_key": "itest-pub-0004"},
                ctx,
                repo,
            )
        calls = await repo.tool_calls("t_a", wf_id)
        assert any(c.error == "approval_required" for c in calls)

        # 带凭据后放行（AUTO_APPROVE=false 下审批凭据是唯一通路）
        ctx_ok = ToolContext(tenant_id="t_a", workflow_id=wf_id, approved=True)
        res = await execute_tool(
            "publish_listing",
            {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
             "idempotency_key": "itest-pub-0004"},
            ctx_ok,
            repo,
        )
        assert res.ok and res.output["status"] == "published"
    finally:
        get_settings.cache_clear()  # 还原全局 settings 缓存，避免污染其他用例


async def test_rule_violation_returns_validation_failed_not_exception():
    repo = WorkflowRepository()
    wf_id = await _setup(repo)
    ctx = ToolContext(tenant_id="t_a", workflow_id=wf_id, approved=True)

    bad = dict(VALID_LISTING, bullets=["only-one"])
    res = await execute_tool(
        "publish_listing",
        {"marketplace": "amazon", "workflow_id": wf_id, "listing": bad,
         "idempotency_key": "itest-pub-0005"},
        ctx,
        repo,
    )
    assert res.ok  # 调用本身成功；违规是业务结果而非异常
    assert res.output["status"] == "validation_failed"
    assert any("bullets" in e for e in res.output["validation_errors"])


async def test_input_schema_and_unknown_tool_errors():
    repo = WorkflowRepository()
    wf_id = await _setup(repo)
    ctx = ToolContext(tenant_id="t_a", workflow_id=wf_id)

    from app.tools import SchemaValidationError

    with pytest.raises(SchemaValidationError):
        await execute_tool(  # idempotency_key 少于 8 位 → 输入模型拒绝
            "publish_listing",
            {"marketplace": "amazon", "workflow_id": wf_id, "listing": VALID_LISTING,
             "idempotency_key": "short"},
            ctx,
            repo,
        )
    with pytest.raises(UnknownToolError):
        await execute_tool("no_such_tool", {}, ctx, repo)
