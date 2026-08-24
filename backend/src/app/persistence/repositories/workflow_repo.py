"""Workflow 仓储。

约定：
- 每个方法显式携带 tenant_id 并强制过滤——按 id 查询时租户不匹配一律返回 None（上层转 404，防枚举）；
- 方法内自管短会话（每操作一事务），M0 够用；引入工作单元/事务边界时再收敛。
"""

from typing import Any

from sqlalchemy import select

from app.persistence.db import session_factory
from app.persistence.models import AgentDecision, Tenant, Workflow, WorkflowStep


class WorkflowRepository:
    def __init__(self, factory=None) -> None:
        self._factory = factory or session_factory()

    # ---- tenant ----

    async def ensure_tenant(self, tenant_id: str, name: str) -> Tenant:
        async with self._factory() as s:
            t = await s.get(Tenant, tenant_id)
            if t is None:
                t = Tenant(id=tenant_id, name=name)
                s.add(t)
                await s.commit()
            return t

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        async with self._factory() as s:
            return await s.get(Tenant, tenant_id)

    # ---- workflow ----

    async def create_workflow(self, tenant_id: str, **fields: Any) -> Workflow:
        wf = Workflow(tenant_id=tenant_id, **fields)
        async with self._factory() as s:
            s.add(wf)
            await s.commit()
            await s.refresh(wf)
            return wf

    async def get(self, tenant_id: str, workflow_id: str) -> Workflow | None:
        async with self._factory() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None or wf.tenant_id != tenant_id:  # IDOR：不匹配等同不存在
                return None
            return wf

    async def list_for_tenant(self, tenant_id: str, limit: int = 50) -> list[Workflow]:
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(Workflow)
                        .where(Workflow.tenant_id == tenant_id)
                        .order_by(Workflow.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def update_status(
        self,
        tenant_id: str,
        workflow_id: str,
        status: str,
        current_node: str | None = None,
        error: str | None = None,
        result_json: dict | None = None,
    ) -> None:
        async with self._factory() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None or wf.tenant_id != tenant_id:
                return
            wf.status = status
            if current_node is not None:
                wf.current_node = current_node
            if error is not None:
                wf.error = error
            if result_json is not None:
                wf.result_json = result_json
            await s.commit()

    # ---- steps / decisions ----

    async def add_step(
        self,
        tenant_id: str,
        workflow_id: str,
        node: str,
        status: str = "completed",
        detail: dict | None = None,
        latency_ms: int | None = None,
    ) -> None:
        async with self._factory() as s:
            seq_row = (
                await s.execute(
                    select(WorkflowStep.seq)
                    .where(WorkflowStep.workflow_id == workflow_id)
                    .order_by(WorkflowStep.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            s.add(
                WorkflowStep(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    seq=(seq_row or 0) + 1,
                    node=node,
                    status=status,
                    detail=detail,
                    latency_ms=latency_ms,
                )
            )
            await s.commit()

    async def steps(self, tenant_id: str, workflow_id: str) -> list[WorkflowStep]:
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(WorkflowStep)
                        .where(
                            WorkflowStep.workflow_id == workflow_id,
                            WorkflowStep.tenant_id == tenant_id,
                        )
                        .order_by(WorkflowStep.seq)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def add_decision(
        self,
        tenant_id: str,
        workflow_id: str,
        agent: str,
        decision_type: str,
        reasoning: str,
        chosen_option: str,
        alternatives: list | None = None,
    ) -> None:
        async with self._factory() as s:
            s.add(
                AgentDecision(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    agent=agent,
                    decision_type=decision_type,
                    reasoning=reasoning,
                    chosen_option=chosen_option,
                    alternatives=alternatives or [],
                )
            )
            await s.commit()

    async def decisions(self, tenant_id: str, workflow_id: str) -> list[AgentDecision]:
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(AgentDecision)
                        .where(
                            AgentDecision.workflow_id == workflow_id,
                            AgentDecision.tenant_id == tenant_id,
                        )
                        .order_by(AgentDecision.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)
