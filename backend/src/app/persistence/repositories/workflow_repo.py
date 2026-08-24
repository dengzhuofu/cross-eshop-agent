"""Workflow 仓储。

约定：
- 每个方法显式携带 tenant_id 并强制过滤——按 id 查询时租户不匹配一律返回 None（上层转 404，防枚举）；
- 方法内自管短会话（每操作一事务），M0 够用；引入工作单元/事务边界时再收敛。
"""

import math
import uuid
from typing import Any

from sqlalchemy import select

from app.persistence.db import session_factory
from app.persistence.models import (
    AgentDecision,
    KnowledgeRecord,
    MemoryRecord,
    Tenant,
    ToolCall,
    Workflow,
    WorkflowStep,
)


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量/空向量（分母为 0）安全返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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

    # ---- tool calls（审计 + 幂等回放）----

    async def record_tool_call(
        self,
        tenant_id: str,
        tool: str,
        status: str,
        call_id: str,
        workflow_id: str | None = None,
        risk_level: str = "low",
        idempotency_key: str | None = None,
        input_summary: dict | None = None,
        output_summary: dict | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        async with self._factory() as s:
            s.add(
                ToolCall(
                    id=call_id,
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    tool=tool,
                    risk_level=risk_level,
                    idempotency_key=idempotency_key,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    status=status,
                    error=error,
                    latency_ms=latency_ms,
                )
            )
            await s.commit()

    async def find_tool_output_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict | None:
        """幂等回放：返回同租户下该键最近一次成功调用的输出。"""
        async with self._factory() as s:
            row = (
                await s.execute(
                    select(ToolCall)
                    .where(
                        ToolCall.tenant_id == tenant_id,
                        ToolCall.idempotency_key == idempotency_key,
                        ToolCall.status == "ok",
                    )
                    .order_by(ToolCall.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row.output_summary if row else None

    async def tool_calls(self, tenant_id: str, workflow_id: str) -> list[ToolCall]:
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(ToolCall)
                        .where(
                            ToolCall.tenant_id == tenant_id,
                            ToolCall.workflow_id == workflow_id,
                        )
                        .order_by(ToolCall.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    # ---- memories（长期记忆，M4）----

    async def insert_memory(self, *, tenant_id: str, kind: str, content: str,
                            embedding: list[float], source_workflow_id: str | None = None,
                            meta: dict | None = None) -> str:
        """写入一条记忆，返回 memory_id（uuid4().hex）。"""
        memory_id = uuid.uuid4().hex
        async with self._factory() as s:
            s.add(
                MemoryRecord(
                    id=memory_id,
                    tenant_id=tenant_id,
                    kind=kind,
                    content=content,
                    embedding=embedding,
                    source_workflow_id=source_workflow_id,
                    meta=meta,
                )
            )
            await s.commit()
        return memory_id

    async def search_memories(self, *, tenant_id: str, kind: str,
                              query_embedding: list[float], top_k: int = 3) -> list[dict]:
        """按租户+kind 取候选后在 Python 里算余弦相似度排序取 top_k。

        返回 [{id, kind, content, similarity(float), source_workflow_id, created_at}]，
        similarity 降序；零向量安全处理（分母为 0 时 similarity=0.0）；
        created_at 输出 isoformat 字符串。查询永远带 tenant_id 过滤（多租户铁律）。
        """
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(MemoryRecord).where(
                            MemoryRecord.tenant_id == tenant_id,
                            MemoryRecord.kind == kind,
                        )
                    )
                )
                .scalars()
                .all()
            )
        scored = [
            {
                "id": r.id,
                "kind": r.kind,
                "content": r.content,
                "similarity": _cosine(query_embedding, r.embedding or []),
                "source_workflow_id": r.source_workflow_id,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:top_k]

    # ---- knowledge_base（RAG 五类知识，M6）----

    async def insert_knowledge(self, *, tenant_id: str, category: str, title: str,
                               content: str, embedding: list[float],
                               ref: str | None = None, meta: dict | None = None) -> str:
        """写入一条知识文档，返回 knowledge_id（uuid4().hex）。"""
        knowledge_id = uuid.uuid4().hex
        async with self._factory() as s:
            s.add(
                KnowledgeRecord(
                    id=knowledge_id,
                    tenant_id=tenant_id,
                    category=category,
                    title=title,
                    content=content,
                    embedding=embedding,
                    ref=ref,
                    meta=meta,
                )
            )
            await s.commit()
        return knowledge_id

    async def search_knowledge(self, *, tenant_id: str, category: str | None,
                               query_embedding: list[float], top_k: int = 3) -> list[dict]:
        """按租户（可选再按 category）取候选后 Python 余弦排序取 top_k。

        返回 [{id, category, title, content, similarity, ref, created_at}]，similarity 降序；
        category=None 查全部五类。查询永远带 tenant_id 过滤（多租户铁律）。
        """
        filters = [KnowledgeRecord.tenant_id == tenant_id]
        if category:
            filters.append(KnowledgeRecord.category == category)
        async with self._factory() as s:
            rows = (
                (await s.execute(select(KnowledgeRecord).where(*filters)))
                .scalars()
                .all()
            )
        scored = [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "content": r.content,
                "similarity": _cosine(query_embedding, r.embedding or []),
                "ref": r.ref,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:top_k]
