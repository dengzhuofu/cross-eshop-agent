"""Workflow 仓储。

约定：
- 每个方法显式携带 tenant_id 并强制过滤——按 id 查询时租户不匹配一律返回 None（上层转 404，防枚举）；
- 方法内自管短会话（每操作一事务），M0 够用；引入工作单元/事务边界时再收敛。
"""

import math
import uuid
from typing import Any

from sqlalchemy import select, update

from app.persistence.db import session_factory
from app.persistence.models import (
    AgentDecision,
    BadCaseRecord,
    FeedbackRecord,
    KnowledgeRecord,
    MemoryRecord,
    Tenant,
    ToolCall,
    Workflow,
    WorkflowStep,
)
from app.rag.retrieval import _rank_desc, bm25_scores, rrf_fuse, rrf_score_map


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
                               query_embedding: list[float], top_k: int = 3,
                               query_text: str | None = None,
                               query_embedding_alt: list[float] | None = None) -> list[dict]:
        """按租户（可选再按 category）取候选后排序取 top_k（M9 混合检索）。

        query_text=None：纯余弦排序（M6 旧行为，旧调用方零感知）；
        query_text 给定：BM25 词面 + 余弦语义双路 → RRF 融合（app.rag.retrieval），
        返回项额外带 bm25 / rrf 两个可解释分数。余弦全零（离线 hash 引擎退化）
        时 BM25 独立支撑排序——这正是双路设计的动机。
        query_embedding_alt（M11 HyDE）：假设性文档的向量，语义路对每篇文档取
        max(cos(主查询, 文档), cos(假设文档, 文档))——假设文档只增强语义召回，
        词面路永远用 query_text 用户原词（LLM 生成的文本不进 BM25）。
        返回 [{id, category, title, content, similarity, ref, created_at[, bm25, rrf]}]，
        按融合质量降序；category=None 查全部类别。
        查询永远带 tenant_id 过滤（多租户铁律）。
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
        # M10：候选知识（status=candidate，来自反馈沉淀）未经审批不进检索池——
        # 语料质量硬保证，Python 侧 meta 过滤与 delete_knowledge_by_source 同款
        rows = [r for r in rows if (r.meta or {}).get("status") != "candidate"]
        def _sem(vec: list[float]) -> float:
            """语义路得分：主查询向量与 HyDE 向量（可选）逐文档取最大余弦。"""
            c = _cosine(query_embedding, vec)
            if query_embedding_alt:
                c = max(c, _cosine(query_embedding_alt, vec))
            return c

        scored = [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "content": r.content,
                "similarity": _sem(r.embedding or []),
                "ref": r.ref,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
        if query_text is None:
            scored.sort(key=lambda item: item["similarity"], reverse=True)
            return scored[:top_k]
        corpus = [item["content"] for item in scored]
        cosines = [item["similarity"] for item in scored]
        bm25 = bm25_scores(query_text, corpus)
        rank_lists = [_rank_desc(bm25), _rank_desc(cosines)]
        fused = rrf_fuse(rank_lists)
        rrf_scores = rrf_score_map(rank_lists)
        fused_rows = []
        for idx in fused[:top_k]:
            row = dict(scored[idx])
            row["bm25"] = round(bm25[idx], 4)
            row["rrf"] = round(rrf_scores[idx], 6)
            fused_rows.append(row)
        return fused_rows

    async def delete_knowledge_by_source(self, *, tenant_id: str, source: str) -> int:
        """删除 meta->>'source' 等于 source 的知识行（爬取语料重灌的幂等前提）。

        JSON 列在 SQLite/PG 间无统一路径操作符，这里取候选后按 Python 过滤，
        与 search_knowledge 同款做法；永远带 tenant_id 过滤（多租户铁律）。
        """
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(KnowledgeRecord).where(KnowledgeRecord.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
            doomed = [r for r in rows if (r.meta or {}).get("source") == source]
            for r in doomed:
                await s.delete(r)
            await s.commit()
        return len(doomed)

    async def review_candidate_knowledge(self, *, tenant_id: str, knowledge_id: str,
                                         action: str) -> bool:
        """候选知识审批（M10 闭环的人工闸门）：approve → status 置 approved 进检索池；
        reject → 直接删除。仅对 origin=feedback 且 status=candidate 的行生效——
        正式语料（种子/爬取）不可经此通道改动。UPDATE 带 tenant_id 过滤，
        跨租户/非候选行返回 False（上层转 404 防枚举）。
        """
        if action not in {"approve", "reject"}:
            return False
        async with self._factory() as s:
            row = (
                await s.execute(
                    select(KnowledgeRecord).where(
                        KnowledgeRecord.id == knowledge_id,
                        KnowledgeRecord.tenant_id == tenant_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return False
            meta = dict(row.meta or {})
            if meta.get("origin") != "feedback" or meta.get("status") != "candidate":
                return False
            if action == "reject":
                await s.delete(row)
            else:
                meta["status"] = "approved"
                row.meta = meta
            await s.commit()
            return True

    async def list_knowledge_candidates(self, *, tenant_id: str,
                                        limit: int = 50) -> list[dict]:
        """待审候选知识（origin=feedback 且 status=candidate），created_at 倒序。"""
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(KnowledgeRecord)
                        .where(KnowledgeRecord.tenant_id == tenant_id)
                        .order_by(KnowledgeRecord.created_at.desc())
                        .limit(limit * 4)
                    )
                )
                .scalars()
                .all()
            )
        cands = [
            r for r in rows
            if (r.meta or {}).get("origin") == "feedback"
            and (r.meta or {}).get("status") == "candidate"
        ]
        return [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "content": r.content,
                "ref": r.ref,
                "meta": r.meta,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in cands[:limit]
        ]

    # ---- feedback_records（反馈-分诊-沉淀闭环，M10）----

    async def insert_feedback(self, *, tenant_id: str, target_type: str, verdict: str,
                              workflow_id: str | None = None, target_key: str | None = None,
                              comment: str | None = None, quote: str | None = None) -> str:
        """写入一条用户反馈（status=pending），返回 feedback_id（uuid4().hex）。"""
        fid = uuid.uuid4().hex
        async with self._factory() as s:
            s.add(
                FeedbackRecord(
                    id=fid,
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    target_type=target_type,
                    target_key=target_key,
                    verdict=verdict,
                    comment=comment,
                    quote=quote,
                    status="pending",
                )
            )
            await s.commit()
        return fid

    async def list_feedback(self, *, tenant_id: str, workflow_id: str | None = None,
                            status: str | None = None, limit: int = 50) -> list[dict]:
        """按租户过滤列出反馈（可再按 workflow/status），created_at 倒序。"""
        filters = [FeedbackRecord.tenant_id == tenant_id]
        if workflow_id:
            filters.append(FeedbackRecord.workflow_id == workflow_id)
        if status:
            filters.append(FeedbackRecord.status == status)
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(FeedbackRecord)
                        .where(*filters)
                        .order_by(FeedbackRecord.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "id": r.id,
                "workflow_id": r.workflow_id,
                "target_type": r.target_type,
                "target_key": r.target_key,
                "verdict": r.verdict,
                "comment": r.comment,
                "quote": r.quote,
                "triage": r.triage,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    async def update_feedback_triage(self, tenant_id: str, feedback_id: str,
                                     triage: dict, status: str) -> bool:
        """分诊完成后回写 triage 结果与状态；带 tenant_id 过滤防 IDOR。"""
        if status not in {"triaged", "dismissed", "pending"}:
            return False
        async with self._factory() as s:
            res = await s.execute(
                update(FeedbackRecord)
                .where(
                    FeedbackRecord.id == feedback_id,
                    FeedbackRecord.tenant_id == tenant_id,
                )
                .values(triage=triage, status=status)
            )
            await s.commit()
            return bool(res.rowcount)

    # ---- bad_cases（红队/Bad Case 闭环，M7）----

    async def insert_bad_case(self, *, tenant_id: str, category: str, severity: str,
                              detector: str, summary: str, workflow_id: str | None = None,
                              evidence: dict | None = None,
                              status: str = "detected") -> str:
        """记录一次 bad case 检测命中，返回 bad_case_id（uuid4().hex）。"""
        bad_case_id = uuid.uuid4().hex
        async with self._factory() as s:
            s.add(
                BadCaseRecord(
                    id=bad_case_id,
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    category=category,
                    severity=severity,
                    detector=detector,
                    summary=summary,
                    evidence=evidence,
                    status=status,
                )
            )
            await s.commit()
        return bad_case_id

    async def list_bad_cases(self, *, tenant_id: str, workflow_id: str | None = None,
                             category: str | None = None,
                             limit: int = 50) -> list[dict]:
        """按租户过滤列出 bad case（可再按 workflow/category），created_at 倒序。"""
        filters = [BadCaseRecord.tenant_id == tenant_id]
        if workflow_id:
            filters.append(BadCaseRecord.workflow_id == workflow_id)
        if category:
            filters.append(BadCaseRecord.category == category)
        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(BadCaseRecord)
                        .where(*filters)
                        .order_by(BadCaseRecord.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "id": r.id,
                "workflow_id": r.workflow_id,
                "category": r.category,
                "severity": r.severity,
                "detector": r.detector,
                "summary": r.summary,
                "evidence": r.evidence,
                "status": r.status,
                "outcome": r.outcome,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    async def update_bad_case_status(
        self,
        tenant_id: str,
        bad_case_id: str,
        status: str,
        outcome_note: str | None = None,
    ) -> bool:
        """Bad Case 状态流转（PRD §20.4）：detected → quarantined → 终态处置。

        允许的目标状态仅 resolved/escalated/aborted（detected/quarantined 只由
        检测链路写入，不允许人工回流）。UPDATE 带 tenant_id 过滤——租户隔离在
        SQL 层完成，跨租户/不存在的记录一律返回 False（上层转 404 防枚举）。
        outcome 为文本列：note 非空时原样写入作处置留痕，空 note 不改动既有值。
        """
        if status not in {"resolved", "escalated", "aborted"}:
            return False
        values: dict[str, Any] = {"status": status}
        if outcome_note:
            values["outcome"] = outcome_note
        async with self._factory() as s:
            res = await s.execute(
                update(BadCaseRecord)
                .where(
                    BadCaseRecord.id == bad_case_id,
                    BadCaseRecord.tenant_id == tenant_id,
                )
                .values(**values)
            )
            await s.commit()
            return bool(res.rowcount)
