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
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, OutwardScanResultEnum


class OutwardPO(TimestampMixin, Base):
    """One row per outward PO/invoice per organisation."""

    __tablename__ = "outward_pos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OutwardPoStatusEnum] = mapped_column(
        PGEnum(OutwardPoStatusEnum, name="outward_po_status_enum", create_type=False),
        default=OutwardPoStatusEnum.open,
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lines: Mapped[list["OutwardPOLine"]] = relationship(
        back_populates="outward_po", lazy="raise"
    )


class OutwardPOLine(Base):
    """One row per EAN per outward PO. packed_qty tracks FIFO allocation."""

    __tablename__ = "outward_po_lines"
    __table_args__ = (
        Index("ix_outward_po_lines_ean", "organisation_id", "ean"),
        UniqueConstraint("outward_po_id", "ean", name="uq_po_lines_po_ean"),
        Index(
            "idx_po_lines_org_ean_remaining",
            "organisation_id",
            "ean",
            postgresql_where=text("packed_qty < ordered_qty"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outward_po_id: Mapped[int] = mapped_column(
        ForeignKey("outward_pos.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    packed_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    outward_po: Mapped["OutwardPO"] = relationship(back_populates="lines", lazy="raise")


class OutwardBox(TimestampMixin, Base):
    """One outward packing box. Box ID (OB-CUSTCODE-000001) generated via Counter."""

    __tablename__ = "outward_boxes"
    __table_args__ = (
        Index("ix_outward_boxes_org_status", "organisation_id", "status"),
        UniqueConstraint("organisation_id", "box_id", name="uq_outward_boxes_org_boxid"),
        Index(
            "uq_outward_boxes_packer_active",
            "created_by",
            unique=True,
            postgresql_where=text("status = 'in_use'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    box_id: Mapped[str] = mapped_column(Text, nullable=False)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[OutwardBoxStatusEnum] = mapped_column(
        PGEnum(OutwardBoxStatusEnum, name="outward_box_status_enum", create_type=False),
        default=OutwardBoxStatusEnum.open,
        nullable=False,
    )
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scans: Mapped[list["OutwardScan"]] = relationship(
        back_populates="box",
        foreign_keys="[OutwardScan.outward_box_id]",
        lazy="raise",
    )


class OutwardScan(Base):
    """Append-only. scan_result=deleted marks soft-deletion."""

    __tablename__ = "outward_scans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    outward_box_id: Mapped[int] = mapped_column(
        ForeignKey("outward_boxes.id", ondelete="RESTRICT"), nullable=False
    )
    outward_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("outward_po_lines.id", ondelete="SET NULL"), nullable=True
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    scan_result: Mapped[OutwardScanResultEnum] = mapped_column(
        PGEnum(OutwardScanResultEnum, name="outward_scan_result_enum", create_type=False),
        nullable=False,
    )
    stock_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    box: Mapped["OutwardBox"] = relationship(
        back_populates="scans",
        foreign_keys="[OutwardScan.outward_box_id]",
        lazy="raise",
    )
