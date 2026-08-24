"""memory table (M4)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('memories',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.Column('source_workflow_id', sa.String(length=32), nullable=True),
    sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
    ),
    sa.ForeignKeyConstraint(['source_workflow_id'], ['workflows.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memories_tenant_id'), 'memories', ['tenant_id'], unique=False)
    op.create_index(
        op.f('ix_memories_source_workflow_id'), 'memories', ['source_workflow_id'], unique=False
    )
    op.create_index('ix_memories_tenant_kind', 'memories', ['tenant_id', 'kind'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_memories_tenant_kind', table_name='memories')
    op.drop_index(op.f('ix_memories_source_workflow_id'), table_name='memories')
    op.drop_index(op.f('ix_memories_tenant_id'), table_name='memories')
    op.drop_table('memories')
