"""add_outward_box_and_pos_changes

Revision ID: c2f3a4b5d6e7
Revises: b1e2f3a4c5d6
Create Date: 2026-07-03 00:01:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c2f3a4b5d6e7'
down_revision: str | None = 'b1e2f3a4c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # outward_boxes: add print_count and closed_at
    op.add_column('outward_boxes',
        sa.Column('print_count', sa.Integer(), nullable=False, server_default=sa.text('0'))
    )
    op.add_column('outward_boxes',
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True)
    )

    # outward_boxes: drop old unnamed UNIQUE(box_id) constraint
    op.drop_constraint('outward_boxes_box_id_key', 'outward_boxes', type_='unique')

    # outward_boxes: new composite unique on (organisation_id, box_id)
    op.create_unique_constraint(
        'uq_outward_boxes_org_boxid',
        'outward_boxes',
        ['organisation_id', 'box_id']
    )

    # Resolve existing violations before creating the partial unique index.
    # For any packer with more than one in_use box, keep only the most recent
    # one and revert the rest to open.  In a clean production deployment this
    # UPDATE affects 0 rows; it is a no-op guard, not a data-migration.
    op.execute(sa.text("""
        UPDATE outward_boxes
        SET status = 'open'
        WHERE status = 'in_use'
          AND id NOT IN (
              SELECT DISTINCT ON (created_by) id
              FROM outward_boxes
              WHERE status = 'in_use'
              ORDER BY created_by, created_at DESC
          )
    """))

    # outward_boxes: partial unique index — only one in_use box per packer
    op.create_index(
        'uq_outward_boxes_packer_active',
        'outward_boxes',
        ['created_by'],
        unique=True,
        postgresql_where=sa.text("status = 'in_use'")
    )

    # outward_pos: drop uq_outward_pos_org_po (no replacement)
    op.drop_constraint('uq_outward_pos_org_po', 'outward_pos', type_='unique')


def downgrade() -> None:
    # Restore uq_outward_pos_org_po
    op.create_unique_constraint(
        'uq_outward_pos_org_po',
        'outward_pos',
        ['organisation_id', 'po_number']
    )

    # Remove partial index
    op.drop_index('uq_outward_boxes_packer_active', table_name='outward_boxes')

    # Remove composite unique
    op.drop_constraint('uq_outward_boxes_org_boxid', 'outward_boxes', type_='unique')

    # Restore old simple unique on box_id
    op.create_unique_constraint(
        'outward_boxes_box_id_key',
        'outward_boxes',
        ['box_id']
    )

    # Remove added columns
    op.drop_column('outward_boxes', 'closed_at')
    op.drop_column('outward_boxes', 'print_count')
