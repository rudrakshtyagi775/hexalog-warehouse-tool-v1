"""add_po_line_constraints

Revision ID: d3a4b5c6e7f8
Revises: c2f3a4b5d6e7
Create Date: 2026-07-03 00:02:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3a4b5c6e7f8'
down_revision: str | None = 'c2f3a4b5d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # outward_po_lines: unique constraint on (outward_po_id, ean)
    op.create_unique_constraint(
        'uq_po_lines_po_ean',
        'outward_po_lines',
        ['outward_po_id', 'ean']
    )

    # outward_po_lines: partial index for FIFO lookups — only lines with remaining qty
    op.create_index(
        'idx_po_lines_org_ean_remaining',
        'outward_po_lines',
        ['organisation_id', 'ean'],
        unique=False,
        postgresql_where=sa.text('packed_qty < ordered_qty')
    )


def downgrade() -> None:
    op.drop_index('idx_po_lines_org_ean_remaining', table_name='outward_po_lines')
    op.drop_constraint('uq_po_lines_po_ean', 'outward_po_lines', type_='unique')
