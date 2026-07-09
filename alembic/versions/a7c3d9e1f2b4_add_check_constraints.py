"""add_check_constraints

Revision ID: a7c3d9e1f2b4
Revises: f5c6d7e8a9b0
Create Date: 2026-07-06 00:00:00.000000

Adds the DB-level CHECK constraints called for in database-schema-v1.md that
were never created:
  - outward_po_lines.ordered_qty > 0
  - outward_po_lines.packed_qty >= 0 AND packed_qty <= ordered_qty (NFR-4,
    the second of the two documented over-packing-prevention layers — the
    conditional UPDATE was already in place, this is the DB-level backstop)
  - inward_boxes.scanned_qty >= 0 (schema doc BUG-9)

These mirror invariants already enforced at the application layer (Pydantic
`Field(gt=0)`, the conditional `UPDATE ... WHERE packed_qty < ordered_qty`),
so no existing row can violate them — this is a pure safety net, not a
behavior change.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3d9e1f2b4'
down_revision: str | None = 'f5c6d7e8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        'ck_outward_po_lines_ordered_qty_positive',
        'outward_po_lines',
        'ordered_qty > 0',
    )
    op.create_check_constraint(
        'ck_outward_po_lines_packed_qty_range',
        'outward_po_lines',
        'packed_qty >= 0 AND packed_qty <= ordered_qty',
    )
    op.create_check_constraint(
        'ck_inward_boxes_scanned_qty_nonneg',
        'inward_boxes',
        'scanned_qty >= 0',
    )


def downgrade() -> None:
    op.drop_constraint('ck_inward_boxes_scanned_qty_nonneg', 'inward_boxes', type_='check')
    op.drop_constraint(
        'ck_outward_po_lines_packed_qty_range', 'outward_po_lines', type_='check'
    )
    op.drop_constraint(
        'ck_outward_po_lines_ordered_qty_positive', 'outward_po_lines', type_='check'
    )
