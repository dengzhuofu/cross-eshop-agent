"""bad_cases table (M7 红队/Bad Case 闭环)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('bad_cases',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('workflow_id', sa.String(length=32), nullable=True),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('detector', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
    ),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bad_cases_tenant_id'), 'bad_cases', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_bad_cases_workflow_id'), 'bad_cases', ['workflow_id'], unique=False)
    op.create_index(
        op.f('ix_bad_cases_tenant_category'), 'bad_cases', ['tenant_id', 'category'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_bad_cases_tenant_category'), table_name='bad_cases')
    op.drop_index(op.f('ix_bad_cases_workflow_id'), table_name='bad_cases')
    op.drop_index(op.f('ix_bad_cases_tenant_id'), table_name='bad_cases')
    op.drop_table('bad_cases')
