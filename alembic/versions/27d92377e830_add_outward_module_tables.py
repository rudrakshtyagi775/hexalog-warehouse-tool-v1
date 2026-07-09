"""add_outward_module_tables

Revision ID: 27d92377e830
Revises: c0b6c98a6f2d
Create Date: 2026-06-30 11:26:53.331498

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '27d92377e830'
down_revision: Union[str, None] = 'c0b6c98a6f2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the 3 outward enum types — use EXCEPTION WHEN pattern (same as inward migration)
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_po_status_enum AS ENUM ('open', 'closed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_box_status_enum AS ENUM ('open', 'in_use', 'closed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_scan_result_enum AS ENUM ('accepted', 'rejected', 'deleted'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )

    op.create_table('outward_pos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('po_number', sa.Text(), nullable=False),
    sa.Column('status', postgresql.ENUM('open', 'closed', name='outward_po_status_enum', create_type=False), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organisation_id', 'po_number', name='uq_outward_pos_org_po')
    )
    op.create_index('ix_outward_pos_org', 'outward_pos', ['organisation_id'], unique=False)

    op.create_table('outward_po_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('outward_po_id', sa.Integer(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('ean', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('ordered_qty', sa.Integer(), nullable=False),
    sa.Column('packed_qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_po_id'], ['outward_pos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outward_po_lines_ean', 'outward_po_lines', ['organisation_id', 'ean'], unique=False)

    op.create_table('outward_boxes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('box_id', sa.Text(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('status', postgresql.ENUM('open', 'in_use', 'closed', name='outward_box_status_enum', create_type=False), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('closed_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('box_id')
    )
    op.create_index('ix_outward_boxes_org_status', 'outward_boxes', ['organisation_id', 'status'], unique=False)

    op.create_table('outward_scans',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('outward_box_id', sa.Integer(), nullable=False),
    sa.Column('outward_po_line_id', sa.Integer(), nullable=True),
    sa.Column('ean', sa.Text(), nullable=False),
    sa.Column('scan_result', postgresql.ENUM('accepted', 'rejected', 'deleted', name='outward_scan_result_enum', create_type=False), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_box_id'], ['outward_boxes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_po_line_id'], ['outward_po_lines.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_index('ix_outward_boxes_org_status', table_name='outward_boxes')
    op.drop_index('ix_outward_po_lines_ean', table_name='outward_po_lines')
    op.drop_index('ix_outward_pos_org', table_name='outward_pos')
    op.drop_table('outward_scans')
    op.drop_table('outward_boxes')
    op.drop_table('outward_po_lines')
    op.drop_table('outward_pos')
    op.execute("DROP TYPE IF EXISTS outward_scan_result_enum")
    op.execute("DROP TYPE IF EXISTS outward_box_status_enum")
    op.execute("DROP TYPE IF EXISTS outward_po_status_enum")
