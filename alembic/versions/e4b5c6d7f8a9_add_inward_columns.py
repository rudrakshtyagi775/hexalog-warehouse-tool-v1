"""add_inward_columns

Revision ID: e4b5c6d7f8a9
Revises: d3a4b5c6e7f8
Create Date: 2026-07-03 00:03:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e4b5c6d7f8a9'
down_revision: str | None = 'd3a4b5c6e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # inward_scans: add is_manual_entry flag
    op.add_column('inward_scans',
        sa.Column('is_manual_entry', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )

    # inward_boxes: add soft-delete columns
    op.add_column('inward_boxes',
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )
    op.add_column('inward_boxes',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('inward_boxes',
        sa.Column('deleted_by', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_inward_boxes_deleted_by',
        'inward_boxes', 'users',
        ['deleted_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_inward_boxes_deleted_by', 'inward_boxes', type_='foreignkey')
    op.drop_column('inward_boxes', 'deleted_by')
    op.drop_column('inward_boxes', 'deleted_at')
    op.drop_column('inward_boxes', 'is_deleted')
    op.drop_column('inward_scans', 'is_manual_entry')
