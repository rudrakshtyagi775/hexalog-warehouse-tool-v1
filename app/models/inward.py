from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    CounterTypeEnum,
    InwardBoxStatusEnum,
    InwardCodeTypeEnum,
    InwardReferenceStatusEnum,
    LedgerSourceTypeEnum,
)


class Counter(Base):
    """Atomic sequence generator. One row per (type, org, customer_code, date_key).

    date_key is "" for non-date counters (e.g. inward_box) and "YYYYMMDD" for
    inscan_number counters. Updated exclusively via INSERT ... ON CONFLICT DO UPDATE.
    """

    __tablename__ = "counters"
    __table_args__ = (
        UniqueConstraint(
            "counter_type", "organisation_id", "customer_code", "date_key",
            name="uq_counters_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counter_type: Mapped[CounterTypeEnum] = mapped_column(
        PGEnum(CounterTypeEnum, name="counter_type_enum", create_type=False),
        nullable=False,
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_code: Mapped[str] = mapped_column(Text, nullable=False)
    date_key: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class InwardPO(TimestampMixin, Base):
    """One row per PO/invoice number per organisation."""

    __tablename__ = "inward_pos"
    __table_args__ = (
        UniqueConstraint("organisation_id", "po_number", name="uq_inward_pos_org_po"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[InwardReferenceStatusEnum] = mapped_column(
        PGEnum(InwardReferenceStatusEnum, name="inward_reference_status_enum", create_type=False),
        default=InwardReferenceStatusEnum.open,
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lines: Mapped[list["InwardPOLine"]] = relationship(
        back_populates="inward_po", lazy="raise"
    )


class InwardPOLine(Base):
    """One row per EAN per PO. packed_qty tracks FIFO allocation across all boxes."""

    __tablename__ = "inward_po_lines"
    __table_args__ = (
        Index("idx_inward_po_lines_ean_org", "organisation_id", "ean"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inward_po_id: Mapped[int] = mapped_column(
        ForeignKey("inward_pos.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    packed_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    inward_po: Mapped["InwardPO"] = relationship(back_populates="lines", lazy="raise")


class InwardBox(TimestampMixin, Base):
    """One packing box. Box ID (B-CUSTCODE-000001) generated via Counter.

    Status flow: scanning → pending_verification (after close) → completed (after submit).
    scanned_qty invariant: must equal COUNT(*) of non-deleted inward_scans for this box.
    """

    __tablename__ = "inward_boxes"
    __table_args__ = (
        Index("idx_inward_boxes_org_status", "organisation_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    box_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[InwardBoxStatusEnum] = mapped_column(
        PGEnum(InwardBoxStatusEnum, name="inward_box_status_enum", create_type=False),
        default=InwardBoxStatusEnum.scanning,
        nullable=False,
    )
    physical_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scanned_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inscan_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scans: Mapped[list["InwardScan"]] = relationship(
        back_populates="box",
        foreign_keys="[InwardScan.inward_box_id]",
        lazy="raise",
    )


class InwardScan(Base):
    """Append-only. Rows are never hard-deleted; is_deleted=True marks soft-deletion."""

    __tablename__ = "inward_scans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    inward_box_id: Mapped[int] = mapped_column(
        ForeignKey("inward_boxes.id", ondelete="RESTRICT"), nullable=False
    )
    inward_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("inward_po_lines.id", ondelete="SET NULL"), nullable=True
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    code_type: Mapped[InwardCodeTypeEnum] = mapped_column(
        PGEnum(InwardCodeTypeEnum, name="inward_code_type_enum", create_type=False),
        default=InwardCodeTypeEnum.ean,
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    box: Mapped["InwardBox"] = relationship(
        back_populates="scans",
        foreign_keys="[InwardScan.inward_box_id]",
        lazy="raise",
    )


class InventoryLedgerEntry(Base):
    """Append-only. One row per scan (inward_submission = +1) or per deletion reversal."""

    __tablename__ = "inventory_ledger_entries"
    __table_args__ = (
        Index("idx_ledger_org_ean", "organisation_id", "ean"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[LedgerSourceTypeEnum] = mapped_column(
        PGEnum(LedgerSourceTypeEnum, name="ledger_source_type_enum", create_type=False),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
