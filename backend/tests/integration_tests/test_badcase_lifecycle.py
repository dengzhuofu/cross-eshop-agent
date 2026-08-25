"""M8 Bad Case 处置闭环集成测试（PRD §20.4）：quarantined → resolved/escalated/aborted。

repo 直用造数（insert_bad_case），API 走 httpx ASGITransport 直连 app——
不触发 lifespan，表结构来自 conftest 的 init_db(create_all)；hermetic 零出网。
注意：测试库整个 pytest 会话共享且 init_db 不清数据，租户 id 与名称都必须唯一
（tenants.name 有 UNIQUE 约束，ensure_tenant 只按 id 判存在）。
"""

from httpx import ASGITransport, AsyncClient

from app.api.main import app
from app.persistence.repositories.workflow_repo import WorkflowRepository

T_A = "t_bc_lc_a"
T_B = "t_bc_lc_b"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _mk_quarantined_case(repo: WorkflowRepository, tenant_id: str) -> str:
    """造一条落库即隔离的 bad case（与真实检测链路同构），返回 bad_case_id。"""
    await repo.ensure_tenant(tenant_id, f"{tenant_id} Co")
    return await repo.insert_bad_case(
        tenant_id=tenant_id,
        category="input_anomaly",
        severity="high",
        detector="input_injection",
        summary="提示注入命中，已隔离待处置",
        evidence={"patterns": ["ignore previous instructions"]},
        status="quarantined",
    )


async def _get_row(repo: WorkflowRepository, tenant_id: str, bad_case_id: str) -> dict:
    rows = await repo.list_bad_cases(tenant_id=tenant_id, limit=200)
    return next(r for r in rows if r["id"] == bad_case_id)


async def test_resolve_updates_status_and_outcome():
    """正常闭环：resolved 流转成功，响应契约 {id,status,outcome} 且库内留痕。"""
    repo = WorkflowRepository()
    case_id = await _mk_quarantined_case(repo, T_A)
    async with _client() as c:
        res = await c.post(
            f"/api/v1/badcases/{case_id}/status",
            headers={"X-Tenant-Id": T_A},
            json={"status": "resolved", "note": "复核为误报，已回滚发布"},
        )
    assert res.status_code == 200
    assert res.json() == {
        "id": case_id,
        "status": "resolved",
        "outcome": "复核为误报，已回滚发布",
    }
    row = await _get_row(repo, T_A, case_id)
    assert row["status"] == "resolved"
    assert "误报" in row["outcome"]


async def test_escalate_and_abort_terminal_states():
    """另两个合法终态 escalated/aborted 同样可流转并写 outcome。"""
    repo = WorkflowRepository()
    esc_id = await _mk_quarantined_case(repo, T_B)
    abort_id = await _mk_quarantined_case(repo, T_B)
    async with _client() as c:
        r1 = await c.post(
            f"/api/v1/badcases/{esc_id}/status",
            headers={"X-Tenant-Id": T_B},
            json={"status": "escalated", "note": "疑似新型注入模板，转安全组"},
        )
        r2 = await c.post(
            f"/api/v1/badcases/{abort_id}/status",
            headers={"X-Tenant-Id": T_B},
            json={"status": "aborted"},
        )
    assert r1.status_code == 200 and r1.json()["status"] == "escalated"
    assert r2.status_code == 200 and r2.json()["status"] == "aborted"
    # 无 note 的请求不改动既有 outcome（保持 None），只流转状态
    assert "安全组" in (await _get_row(repo, T_B, esc_id))["outcome"]
    aborted = await _get_row(repo, T_B, abort_id)
    assert aborted["status"] == "aborted" and aborted["outcome"] is None


async def test_cross_tenant_update_returns_404_and_record_untouched():
    """租户 B 处置租户 A 的记录：统一 404（IDOR 防枚举），A 的记录原样保留。"""
    repo = WorkflowRepository()
    case_id = await _mk_quarantined_case(repo, T_A)
    await repo.ensure_tenant(T_B, f"{T_B} Co")
    async with _client() as c:
        res = await c.post(
            f"/api/v1/badcases/{case_id}/status",
            headers={"X-Tenant-Id": T_B},
            json={"status": "escalated", "note": "越权尝试不应生效"},
        )
    assert res.status_code == 404
    assert res.json()["detail"] == "not found"
    row = await _get_row(repo, T_A, case_id)
    assert row["status"] == "quarantined" and row["outcome"] is None


async def test_invalid_target_status_rejected_with_422():
    """非法目标状态（回流态 detected / 乱写值）由 Literal 校验产生 422，库内不变。"""
    repo = WorkflowRepository()
    case_id = await _mk_quarantined_case(repo, T_B)
    async with _client() as c:
        for payload in ({"status": "detected"}, {"status": "purged"}):
            res = await c.post(
                f"/api/v1/badcases/{case_id}/status",
                headers={"X-Tenant-Id": T_B},
                json=payload,
            )
            assert res.status_code == 422
    row = await _get_row(repo, T_B, case_id)
    assert row["status"] == "quarantined" and row["outcome"] is None


async def test_missing_bad_case_returns_404():
    repo = WorkflowRepository()
    await repo.ensure_tenant(T_B, f"{T_B} Co")
    async with _client() as c:
        res = await c.post(
            "/api/v1/badcases/deadbeef00/status",
            headers={"X-Tenant-Id": T_B},
            json={"status": "aborted", "note": "n/a"},
        )
    assert res.status_code == 404
    assert res.json()["detail"] == "not found"


async def test_unknown_tenant_returns_404():
    """租户头指向不存在的租户：tenant_dep 统一 404，防枚举。"""
    repo = WorkflowRepository()
    case_id = await _mk_quarantined_case(repo, T_A)
    async with _client() as c:
        res = await c.post(
            f"/api/v1/badcases/{case_id}/status",
            headers={"X-Tenant-Id": "t_bc_lc_ghost"},
            json={"status": "resolved"},
        )
    assert res.status_code == 404
