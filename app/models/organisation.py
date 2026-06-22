from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Organisation(TimestampMixin, Base):
    __tablename__ = "organisations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user_organisations: Mapped[list["UserOrganisation"]] = relationship(  # noqa: F821
        back_populates="organisation"
    )
