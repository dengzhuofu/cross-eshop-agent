"""knowledge_base table (M6 RAG 五类知识集合)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('knowledge_base',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_base_tenant_id'), 'knowledge_base', ['tenant_id'],
                    unique=False)
    op.create_index(
        op.f('ix_knowledge_base_tenant_category'), 'knowledge_base', ['tenant_id', 'category'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_knowledge_base_tenant_category'), table_name='knowledge_base')
    op.drop_index(op.f('ix_knowledge_base_tenant_id'), table_name='knowledge_base')
    op.drop_table('knowledge_base')
