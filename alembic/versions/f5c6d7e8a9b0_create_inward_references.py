"""create_inward_references

Revision ID: f5c6d7e8a9b0
Revises: e4b5c6d7f8a9
Create Date: 2026-07-03 00:04:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5c6d7e8a9b0'
down_revision: str | None = 'e4b5c6d7f8a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create inward_references table
    op.create_table(
        'inward_references',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organisation_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('po_number', sa.Text(), nullable=True),
        sa.Column('invoice_number', sa.Text(), nullable=True),
        sa.Column(
            'status',
            postgresql.ENUM(
                'open', 'completed',
                name='inward_reference_status_enum',
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes on inward_references
    op.create_index(
        'idx_inward_refs_customer', 'inward_references', ['customer_id'], unique=False
    )
    op.create_index(
        'idx_inward_refs_org_status',
        'inward_references',
        ['organisation_id', 'status'],
        unique=False,
    )
    op.create_index(
        'idx_inward_refs_customer_po',
        'inward_references',
        ['customer_id', 'po_number'],
        unique=False,
    )
    op.create_index(
        'idx_inward_refs_customer_inv',
        'inward_references',
        ['customer_id', 'invoice_number'],
        unique=False,
    )

    # inward_boxes: add inward_reference_id and box_number columns
    op.add_column('inward_boxes',
        sa.Column('inward_reference_id', sa.Integer(), nullable=True)
    )
    op.add_column('inward_boxes',
        sa.Column('box_number', sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        'fk_inward_boxes_inward_reference_id',
        'inward_boxes', 'inward_references',
        ['inward_reference_id'], ['id'],
        ondelete='RESTRICT'
    )

    # Partial unique index: one box_number per inward_reference (when both are set)
    op.create_index(
        'uq_inward_boxes_ref_boxnum',
        'inward_boxes',
        ['inward_reference_id', 'box_number'],
        unique=True,
        postgresql_where=sa.text('inward_reference_id IS NOT NULL AND box_number IS NOT NULL')
    )

    # inventory_ledger_entries: replace idx_ledger_org_ean with idx_ledger_org_customer_ean
    op.drop_index('idx_ledger_org_ean', table_name='inventory_ledger_entries')
    op.create_index(
        'idx_ledger_org_customer_ean',
        'inventory_ledger_entries',
        ['organisation_id', 'customer_id', 'ean'],
        unique=False
    )


def downgrade() -> None:
    # Reverse ledger index change
    op.drop_index('idx_ledger_org_customer_ean', table_name='inventory_ledger_entries')
    op.create_index(
        'idx_ledger_org_ean',
        'inventory_ledger_entries',
        ['organisation_id', 'ean'],
        unique=False,
    )

    # Remove inward_boxes additions
    op.drop_index('uq_inward_boxes_ref_boxnum', table_name='inward_boxes')
    op.drop_constraint('fk_inward_boxes_inward_reference_id', 'inward_boxes', type_='foreignkey')
    op.drop_column('inward_boxes', 'box_number')
    op.drop_column('inward_boxes', 'inward_reference_id')

    # Drop inward_references indexes then table
    op.drop_index('idx_inward_refs_customer_inv', table_name='inward_references')
    op.drop_index('idx_inward_refs_customer_po', table_name='inward_references')
    op.drop_index('idx_inward_refs_org_status', table_name='inward_references')
    op.drop_index('idx_inward_refs_customer', table_name='inward_references')
    op.drop_table('inward_references')
