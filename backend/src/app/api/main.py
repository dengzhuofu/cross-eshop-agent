"""FastAPI 入口（M0 单文件；M1 拆分 routes/ 与 dependencies.py）。

租户注入铁律（PRD §19.4）：tenant_id 由系统注入、业务参数里的 tenant_id 一律忽略。
M0 用 X-Tenant-Id 头模拟（dev）；接真实鉴权后只改这个依赖函数，业务代码不感知。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.graphs.product_launch.agent import graph
from app.graphs.product_launch.state import initial_state
from app.multitenancy.context import TenantContext, reset_current_tenant, set_current_tenant
from app.observability.recorder import RunRecorder
from app.persistence.db import adispose_database
from app.persistence.migrations import upgrade_head
from app.persistence.repositories.workflow_repo import WorkflowRepository

logger = logging.getLogger("cesa.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # M1 起 schema 由 alembic 管理（create_all 仅用于测试的 hermetic 库）
    await upgrade_head()
    yield
    await adispose_database()


app = FastAPI(title="Cross Eshop Agent", version="0.1.0", lifespan=lifespan)


# ---- schemas ----


class WorkflowCreate(BaseModel):
    product_idea: str = Field(min_length=2)
    marketplaces: list[str] = ["amazon", "shopify", "tiktok_shop"]
    target_market: str = "US"
    risk_preference: str = "balanced"
    title: Optional[str] = None


# ---- tenant 注入 ----


async def tenant_dep(x_tenant_id: str = Header(alias="X-Tenant-Id")) -> TenantContext:
    repo = WorkflowRepository()
    tenant = await repo.get_tenant(x_tenant_id)
    if tenant is None:
        # IDOR 策略（PRD §13.6）：不存在的租户与不归属的资源统一 404，防枚举
        raise HTTPException(status_code=404, detail="not found")
    ctx = TenantContext(tenant_id=tenant.id)
    token = set_current_tenant(ctx)
    try:
        yield ctx
    finally:
        reset_current_tenant(token)


# ---- workflow 运行器 ----

_background_tasks: set[asyncio.Task] = set()


async def run_workflow(workflow_id: str, tenant_id: str) -> None:
    repo = WorkflowRepository()
    wf = await repo.get(tenant_id, workflow_id)
    if wf is None:
        logger.error("run_workflow: workflow %s not found for tenant %s", workflow_id, tenant_id)
        return
    rec = RunRecorder(repo, workflow_id, tenant_id)
    task_input = wf.input_json or {
        "product_idea": wf.product_idea,
        "marketplaces": wf.marketplaces,
        "target_market": wf.target_market,
        "risk_preference": wf.risk_preference,
    }
    try:
        await repo.update_status(tenant_id, workflow_id, "running")
        final_state = await graph.ainvoke(
            initial_state(workflow_id, tenant_id, task_input),
            config={"configurable": {"recorder": rec}},
        )
        await repo.update_status(
            tenant_id,
            workflow_id,
            "completed",
            result_json={
                "retrospective": final_state.get("retrospective"),
                "published": final_state.get("published"),
            },
        )
    except Exception as exc:  # noqa: BLE001 —— 失败必须落状态机，不能静默
        logger.exception("workflow %s failed", workflow_id)
        await rec.status("failed", error=str(exc))


# ---- routes ----


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/workflows", status_code=201)
async def create_workflow(
    body: WorkflowCreate, tenant: TenantContext = Depends(tenant_dep)
) -> dict:
    repo = WorkflowRepository()
    wf = await repo.create_workflow(
        tenant_id=tenant.tenant_id,
        title=body.title or body.product_idea[:60],
        product_idea=body.product_idea,
        marketplaces=body.marketplaces,
        target_market=body.target_market,
        risk_preference=body.risk_preference,
        status="queued",
        input_json=body.model_dump(),
    )
    task = asyncio.create_task(run_workflow(wf.id, tenant.tenant_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"id": wf.id, "status": wf.status, "title": wf.title}


@app.get("/api/v1/workflows")
async def list_workflows(limit: int = 20, tenant: TenantContext = Depends(tenant_dep)) -> dict:
    rows = await WorkflowRepository().list_for_tenant(tenant.tenant_id, limit=limit)
    return {"items": [{"id": w.id, "title": w.title, "status": w.status} for w in rows]}


@app.get("/api/v1/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, tenant: TenantContext = Depends(tenant_dep)) -> dict:
    repo = WorkflowRepository()
    wf = await repo.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="not found")
    steps = await repo.steps(tenant.tenant_id, workflow_id)
    decisions = await repo.decisions(tenant.tenant_id, workflow_id)
    return {
        "id": wf.id,
        "title": wf.title,
        "status": wf.status,
        "current_node": wf.current_node,
        "error": wf.error,
        "product_idea": wf.product_idea,
        "marketplaces": wf.marketplaces,
        "step_count": len(steps),
        "decision_count": len(decisions),
    }


@app.get("/api/v1/workflows/{workflow_id}/trace")
async def get_trace(workflow_id: str, tenant: TenantContext = Depends(tenant_dep)) -> dict:
    """决策时间线 + 步骤 trace + 工具调用审计（PRD §16.2 / §7.2）。"""
    repo = WorkflowRepository()
    wf = await repo.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="not found")
    steps = await repo.steps(tenant.tenant_id, workflow_id)
    decisions = await repo.decisions(tenant.tenant_id, workflow_id)
    calls = await repo.tool_calls(tenant.tenant_id, workflow_id)
    return {
        "workflow": {"id": wf.id, "status": wf.status, "error": wf.error},
        "steps": [
            {
                "seq": s.seq,
                "node": s.node,
                "status": s.status,
                "detail": s.detail,
                "latency_ms": s.latency_ms,
            }
            for s in steps
        ],
        "decisions": [
            {
                "agent": d.agent,
                "decision_type": d.decision_type,
                "reasoning": d.reasoning,
                "chosen_option": d.chosen_option,
                "alternatives": d.alternatives,
                "created_at": str(d.created_at),
            }
            for d in decisions
        ],
        "tool_calls": [
            {
                "id": c.id,
                "tool": c.tool,
                "risk_level": c.risk_level,
                "status": c.status,
                "idempotency_key": c.idempotency_key,
                "error": c.error,
                "latency_ms": c.latency_ms,
            }
            for c in calls
        ],
    }
