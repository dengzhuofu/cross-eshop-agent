"""ORM 模型（M0 子集）。

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
