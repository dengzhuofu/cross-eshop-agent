"""feedback_records table (M10 反馈-分诊-沉淀闭环)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str]] = None


def upgrade() -> None:
    op.create_table('feedback_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('workflow_id', sa.String(length=32), nullable=True),
    # target_type: support_draft / listing_copy / plan / research_brief ...
    # target_key: 定位被反馈产物（如步骤 seq 或草稿标识）
    sa.Column('target_type', sa.String(length=32), nullable=False),
    sa.Column('target_key', sa.String(length=128), nullable=True),
    # verdict: helpful | unhelpful（helpful 也留痕——正反馈同样进分诊统计）
    sa.Column('verdict', sa.String(length=16), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('quote', sa.Text(), nullable=True),
    # 分诊产出：{category, root_cause, sink, sink_ref, source: rule|llm|llm_fallback}
    sa.Column('triage', sa.JSON(), nullable=True),
    # pending → triaged → dismissed / applied
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_feedback_records_tenant_id'), 'feedback_records', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_feedback_records_workflow_id'), 'feedback_records', ['workflow_id'], unique=False)
    op.create_index(
        op.f('ix_feedback_records_tenant_status'), 'feedback_records', ['tenant_id', 'status'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_feedback_records_tenant_status'), table_name='feedback_records')
    op.drop_index(op.f('ix_feedback_records_workflow_id'), table_name='feedback_records')
    op.drop_index(op.f('ix_feedback_records_tenant_id'), table_name='feedback_records')
    op.drop_table('feedback_records')
