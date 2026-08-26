"""FastAPI 入口（M0 单文件；M1 拆分 routes/ 与 dependencies.py）。

租户注入铁律（PRD §19.4）：tenant_id 由系统注入、业务参数里的 tenant_id 一律忽略。
M0 用 X-Tenant-Id 头模拟（dev）；接真实鉴权后只改这个依赖函数，业务代码不感知。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.config import get_settings
from app.feedback.triage import triage_and_route
from app.graphs.product_launch.agent import build_graph
from app.graphs.product_launch.state import initial_state
from app.multitenancy.context import TenantContext, reset_current_tenant, set_current_tenant
from app.observability.recorder import RunRecorder
from app.persistence.db import adispose_database
from app.persistence.migrations import upgrade_head
from app.persistence.repositories.workflow_repo import WorkflowRepository

logger = logging.getLogger("cesa.api")


_GRAPH = build_graph()  # lifespan 内替换为带 checkpointer 的实例（interrupt/resume 用）


@asynccontextmanager
async def lifespan(_: FastAPI):
    # M1 起 schema 由 alembic 管理（create_all 仅用于测试的 hermetic 库）
    await upgrade_head()
    global _GRAPH
    settings = get_settings()
    # M5：checkpointer 只管断点恢复，不是状态真源（v1.4 §2.3 规则2）；
    # 独立 sqlite 文件与业务库解耦，进程重启后待审工作流仍可批
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        _GRAPH = build_graph(checkpointer=saver)
        yield
    _GRAPH = build_graph()
    await adispose_database()


app = FastAPI(title="Cross Eshop Agent", version="0.1.0", lifespan=lifespan)


# ---- schemas ----


class WorkflowCreate(BaseModel):
    product_idea: str = Field(min_length=2)
    marketplaces: list[str] = ["amazon", "shopify", "tiktok_shop"]
    target_market: str = "US"
    risk_preference: str = "balanced"
    title: Optional[str] = None
    # M5：工作流级 HITL 开关（缺省跟全局 AUTO_APPROVE）；false 时发布前挂起等人工审批
    auto_approve: Optional[bool] = None


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


async def _finalize(
    repo: WorkflowRepository, tenant_id: str, workflow_id: str, final_state: dict
) -> None:
    """终态落库。只有走完 retrospective 的完整链路才算 completed；
    halted 路径（gate=abort/revise、人工驳回）的终态已由 recorder 落库，不得覆盖。"""
    if not final_state.get("retrospective"):
        return
    await repo.update_status(
        tenant_id,
        workflow_id,
        "completed",
        result_json={
            "retrospective": final_state.get("retrospective"),
            "published": final_state.get("published"),
        },
    )


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
        # thread_id=workflow_id：同一工作流的 resume 必须落在同一 checkpoint 线程
        final_state = await _GRAPH.ainvoke(
            initial_state(workflow_id, tenant_id, task_input),
            config={"configurable": {"recorder": rec, "thread_id": workflow_id}},
        )
        intr = final_state.get("__interrupt__")
        if intr:
            # M5 HITL：图在 approval_check 挂起，把审批材料快照进唯一真源供审批中心展示
            payload = dict(intr[0].value or {})
            await repo.update_status(
                tenant_id,
                workflow_id,
                "awaiting_approval",
                result_json={"pending_approval": payload},
            )
            await rec.status("awaiting_approval")
            await rec.step(
                "approval_check",
                status="blocked",
                detail={
                    "mode": "manual_pending",
                    "margin_pct": payload.get("margin_pct"),
                    "platforms": [
                        d.get("marketplace") for d in (payload.get("listings") or [])
                    ],
                },
            )
            logger.info("workflow %s awaiting human approval", workflow_id)
            return
        await _finalize(repo, tenant_id, workflow_id, final_state)
    except Exception as exc:  # noqa: BLE001 —— 失败必须落状态机，不能静默
        logger.exception("workflow %s failed", workflow_id)
        await rec.status("failed", error=str(exc))


async def resume_workflow(
    workflow_id: str, tenant_id: str, *, approved: bool, comment: str
) -> None:
    """人工决定后从 approval_check 断点续跑（M5）。"""
    repo = WorkflowRepository()
    rec = RunRecorder(repo, workflow_id, tenant_id)
    try:
        await repo.update_status(tenant_id, workflow_id, "running")
        final_state = await _GRAPH.ainvoke(
            Command(resume={"approved": approved, "comment": comment}),
            # recorder 必须带上：恢复段的步骤/决策/发布审计全靠它落库
            config={"configurable": {"recorder": rec, "thread_id": workflow_id}},
        )
        await _finalize(repo, tenant_id, workflow_id, final_state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("workflow %s resume failed", workflow_id)
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
                # M13：步骤完成时间——前端据此与决策/工具调用交错成统一活动流
                "created_at": str(s.created_at),
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
                # M13：审计里本就存了输入/输出摘要（executor._summarize），此前没吐给前端——
                # 对话式活动流的「工具调用卡」靠它展示每次调用传了什么、拿回了什么
                "input_summary": c.input_summary,
                "output_summary": c.output_summary,
                "created_at": str(c.created_at),
            }
            for c in calls
        ],
    }


# ---- M5：Approval Center（人工审批） ----


class ApprovalBody(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=500)


@app.get("/api/v1/approvals")
async def list_approvals(limit: int = 20, tenant: TenantContext = Depends(tenant_dep)) -> dict:
    """本租户待人工审批队列（挂起时快照存在 workflow 唯一真源里）。"""
    repo = WorkflowRepository()
    rows = await repo.list_for_tenant(tenant.tenant_id, limit=100)
    items = []
    for w in rows:
        if w.status != "awaiting_approval":
            continue
        items.append(
            {
                "id": w.id,
                "title": w.title,
                "product_idea": w.product_idea,
                "marketplaces": w.marketplaces,
                "created_at": str(w.created_at),
                "pending_approval": (w.result_json or {}).get("pending_approval") or {},
            }
        )
        if len(items) >= limit:
            break
    return {"items": items}


@app.post("/api/v1/workflows/{workflow_id}/approval")
async def submit_approval(
    workflow_id: str, body: ApprovalBody, tenant: TenantContext = Depends(tenant_dep)
) -> dict:
    repo = WorkflowRepository()
    wf = await repo.get(tenant.tenant_id, workflow_id)  # 跨租户统一 404（IDOR 策略）
    if wf is None:
        raise HTTPException(status_code=404, detail="not found")
    if wf.status != "awaiting_approval":
        raise HTTPException(
            status_code=409, detail=f"not awaiting approval (status={wf.status})"
        )
    task = asyncio.create_task(
        resume_workflow(
            workflow_id,
            tenant.tenant_id,
            approved=body.decision == "approve",
            comment=body.comment,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"id": workflow_id, "status": "running"}


@app.get("/api/v1/badcases")
async def list_badcases(
    limit: int = 50,
    workflow_id: Optional[str] = None,
    category: Optional[str] = None,
    tenant: TenantContext = Depends(tenant_dep),
) -> dict:
    """本租户 Bad Case 列表（M7 红队沉淀；跨工作流可按 workflow_id/category 过滤）。"""
    repo = WorkflowRepository()
    items = await repo.list_bad_cases(
        tenant_id=tenant.tenant_id, workflow_id=workflow_id, category=category, limit=limit
    )
    return {"items": items}


class BadCaseStatusBody(BaseModel):
    """Bad Case 处置请求（PRD §20.4）：目标状态仅限终态。

    detected/quarantined 由检测链路写入，人工只能流转到 resolved/escalated/aborted；
    Literal 之外的取值由 pydantic 校验自然产生 422。
    """

    status: Literal["resolved", "escalated", "aborted"]
    note: str = Field(default="", max_length=500)


@app.post("/api/v1/badcases/{bad_case_id}/status")
async def update_badcase_status(
    bad_case_id: str, body: BadCaseStatusBody, tenant: TenantContext = Depends(tenant_dep)
) -> dict:
    """Bad Case 处置闭环（PRD §20.4）：quarantined → retry/reroute/escalate/abort → resolved。

    租户不存在由 tenant_dep 统一 404；跨租户/不存在的记录在 SQL 层过滤后同样
    404（IDOR 策略，防枚举）；note 写入 outcome 文本列作处置留痕。
    """
    repo = WorkflowRepository()
    ok = await repo.update_bad_case_status(
        tenant_id=tenant.tenant_id,
        bad_case_id=bad_case_id,
        status=body.status,
        outcome_note=body.note or None,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"id": bad_case_id, "status": body.status, "outcome": body.note or None}


# ---- 反馈-分诊-沉淀闭环（M10）----


class FeedbackBody(BaseModel):
    """用户反馈请求：对任一 agent 产物 👍/👎 + 可选评论与引用片段。

    comment/quote 是不可信输入——分诊模块内先过 scrub_untrusted 再进任何沉淀通道。
    """

    workflow_id: Optional[str] = None
    target_type: Literal["support_draft", "listing_copy", "plan", "research_brief", "other"]
    target_key: Optional[str] = Field(default=None, max_length=128)
    verdict: Literal["helpful", "unhelpful"]
    comment: str = Field(default="", max_length=1000)
    quote: str = Field(default="", max_length=1000)


@app.post("/api/v1/feedback", status_code=201)
async def create_feedback(body: FeedbackBody, tenant: TenantContext = Depends(tenant_dep)) -> dict:
    """反馈入口：落账本 → 分诊子 agent 归类归因 → 沉淀路由（同步完成，返回分诊结果）。

    分诊的 LLM 增强/知识草稿失败都自动降级规则结果——接口永不 500 于 LLM 故障。
    """
    repo = WorkflowRepository()
    fid = await repo.insert_feedback(
        tenant_id=tenant.tenant_id,
        workflow_id=body.workflow_id,
        target_type=body.target_type,
        target_key=body.target_key,
        verdict=body.verdict,
        comment=body.comment or None,
        quote=body.quote or None,
    )
    triage = await triage_and_route(
        repo,
        tenant_id=tenant.tenant_id,
        feedback_id=fid,
        workflow_id=body.workflow_id,
        target_type=body.target_type,
        verdict=body.verdict,
        comment=body.comment or "",
        quote=body.quote or "",
    )
    return {"id": fid, **triage}


@app.get("/api/v1/feedback")
async def list_feedback_route(
    limit: int = 50,
    status: Optional[str] = None,
    workflow_id: Optional[str] = None,
    tenant: TenantContext = Depends(tenant_dep),
) -> dict:
    """本租户反馈列表（含 triage 结果），供前端反馈面板/闭环观测。"""
    items = await WorkflowRepository().list_feedback(
        tenant_id=tenant.tenant_id, workflow_id=workflow_id, status=status, limit=limit
    )
    return {"items": items}


class KnowledgeReviewBody(BaseModel):
    """候选知识审批请求（M10）：approve 进检索池 / reject 删除。"""

    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=300)


@app.post("/api/v1/knowledge/{knowledge_id}/review")
async def review_candidate_knowledge(
    knowledge_id: str, body: KnowledgeReviewBody, tenant: TenantContext = Depends(tenant_dep)
) -> dict:
    """候选知识审批闸门：反馈沉淀的知识条目 status=candidate 不进检索池，
    人工 approve 后才生效（语料质量硬保证）。仅 origin=feedback 的候选行可动，
    正式语料不可经此通道改动；跨租户/非候选行一律 404 防枚举。
    """
    ok = await WorkflowRepository().review_candidate_knowledge(
        tenant_id=tenant.tenant_id, knowledge_id=knowledge_id, action=body.action
    )
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"id": knowledge_id, "action": body.action}


@app.get("/api/v1/knowledge/candidates")
async def list_candidate_knowledge(
    limit: int = 50, tenant: TenantContext = Depends(tenant_dep)
) -> dict:
    """待审候选知识列表（M10：反馈沉淀、status=candidate，不含正式语料）。"""
    repo = WorkflowRepository()
    rows = await repo.list_knowledge_candidates(tenant_id=tenant.tenant_id, limit=limit)
    return {"items": rows}
