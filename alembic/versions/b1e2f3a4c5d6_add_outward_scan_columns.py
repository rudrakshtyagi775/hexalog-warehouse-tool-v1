"""add_outward_scan_columns

Revision ID: b1e2f3a4c5d6
Revises: 27d92377e830
Create Date: 2026-07-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1e2f3a4c5d6'
down_revision: str | None = '27d92377e830'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('outward_scans',
        sa.Column('stock_flagged', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )
    op.add_column('outward_scans',
        sa.Column('reject_reason', sa.Text(), nullable=True)
    )
    op.add_column('outward_scans',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('outward_scans',
        sa.Column('deleted_by', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_outward_scans_deleted_by',
        'outward_scans', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_outward_scans_deleted_by', 'outward_scans', type_='foreignkey')
    op.drop_column('outward_scans', 'deleted_by')
    op.drop_column('outward_scans', 'deleted_at')
    op.drop_column('outward_scans', 'reject_reason')
    op.drop_column('outward_scans', 'stock_flagged')
