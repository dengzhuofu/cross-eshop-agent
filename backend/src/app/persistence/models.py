"""ORM 模型（M0 子集 + M4 长期记忆）。

规则（v1.4 §2.3）：workflow 状态唯一真源在这里（Postgres）；LangGraph checkpoint 只负责断点恢复。
所有业务表含 tenant_id；按 id 查询必须同时校验 tenant_id（IDOR 返 404，见 repository 层）。
"""

import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    # MVP=shared_db；预留 schema_per_tenant / db_per_tenant（PRD §13.5）
    isolation_mode: Mapped[str] = mapped_column(String(32), default="shared_db")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (Index("ix_workflows_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    product_idea: Mapped[str] = mapped_column(Text)
    marketplaces: Mapped[list] = mapped_column(JSON, default=list)
    target_market: Mapped[str] = mapped_column(String(64), default="US")
    risk_preference: Mapped[str] = mapped_column(String(32), default="balanced")

    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_node: Mapped[str | None] = mapped_column(String(64))
    input_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (Index("ix_steps_wf", "workflow_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    detail: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentDecision(Base):
    """自主决策记录（PRD §8.3/§13.2）。每个决策点必须有理由与备选项。"""

    __tablename__ = "agent_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    decision_type: Mapped[str] = mapped_column(String(64))
    reasoning: Mapped[str] = mapped_column(Text)
    chosen_option: Mapped[str] = mapped_column(String(64))
    alternatives: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCall(Base):
    """工具调用审计（PRD §7.2）。ok/error/replayed 全留痕；幂等键按租户索引。"""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_tenant_idem", "tenant_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id"), index=True
    )
    tool: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    input_summary: Mapped[dict | None] = mapped_column(JSON)
    output_summary: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))  # ok | error | replayed
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryRecord(Base):
    """长期记忆（M4）。kind 为业务分类（supplier_risk / launch_lesson 等），按租户隔离。

    embedding 存 JSON float 数组：本机 PG 无 pgvector 扩展，JSONB + Python 余弦是等价实现；
    换 pgvector 时此列改 VECTOR(1024)，search_memories 改 SQL 内积查询即可。
    """

    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_tenant_kind", "tenant_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON)
    meta: Mapped[dict | None] = mapped_column(JSON)
    source_workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id"), index=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeRecord(Base):
    """RAG 知识库（M6 起五类客服知识 + M8 ops_playbook 运营打法，PRD §7.11）。
    category: policy / platform_rule / product_info / faq / script / ops_playbook，
    按租户隔离。ref 为文档引用编号（如 POL-RTN-07 v2.1），客服回复草稿的来源
    引用即引用它；ops_playbook 供主链路 planner/listing 经 search_knowledge 检索。
    embedding 存储与检索契约同 MemoryRecord。
    """

    __tablename__ = "knowledge_base"
    __table_args__ = (Index("ix_knowledge_base_tenant_category", "tenant_id", "category"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON)
    ref: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BadCaseRecord(Base):
    """Bad Case 记录（M7，PRD §20）。八类分类 + 状态机；进 dataset 的用例在 eval
    CI 门禁里作黄金回归——新代码导致旧 bad case 复现即阻断。
    """

    __tablename__ = "bad_cases"
    __table_args__ = (Index("ix_bad_cases_tenant_category", "tenant_id", "category"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflows.id"), index=True)
    category: Mapped[str] = mapped_column(String(32))  # BadCaseCategory
    severity: Mapped[str] = mapped_column(String(16))  # high | medium | low
    detector: Mapped[str] = mapped_column(String(64))  # 注册的 detector 名
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="detected")
    outcome: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedbackRecord(Base):
    """用户反馈（M10 反馈-分诊-沉淀闭环）。

    用户对任一 agent 产物（客服草稿/Listing 文案/研究结论）给 👍/👎 + 评论/引用，
    分诊子 agent 归类归因后把 triage 结果写回本行；沉淀路由按 category 把改进
    信号送到合适组件（候选知识 / 黄金查询候选集 / bad_cases / 长期记忆）。
    本表是闭环的入口账本：feedback → triage → sink 的每一步都可追溯。
    """

    __tablename__ = "feedback_records"
    __table_args__ = (Index("ix_feedback_records_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflows.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))  # support_draft / listing_copy / plan ...
    target_key: Mapped[str | None] = mapped_column(String(128))
    verdict: Mapped[str] = mapped_column(String(16))  # helpful | unhelpful
    comment: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)  # 用户选中的问题文本片段
    triage: Mapped[dict | None] = mapped_column(JSON)  # {category, root_cause, sink, ...}
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/triaged/dismissed
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
