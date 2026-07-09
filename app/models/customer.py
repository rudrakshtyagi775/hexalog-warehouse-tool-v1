from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import CustomerStatusEnum


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organisation_id", "code", name="uq_customers_org_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CustomerStatusEnum] = mapped_column(
        PGEnum(CustomerStatusEnum, name="customer_status_enum", create_type=False),
        default=CustomerStatusEnum.active,
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
