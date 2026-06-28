# Re-export all ORM models so that `alembic revision --autogenerate`
# detects every table. Import order matters for FK resolution.
from app.models.base import Base, TimestampMixin
from app.models.enums import (
    AuditModuleEnum,
    CounterTypeEnum,
    CustomerStatusEnum,
    InwardBoxStatusEnum,
    InwardCodeTypeEnum,
    InwardReferenceStatusEnum,
    LedgerSourceTypeEnum,
    OutwardBoxStatusEnum,
    OutwardPoStatusEnum,
    OutwardScanResultEnum,
    UserRoleEnum,
)
from app.models.organisation import Organisation
from app.models.user import LoginAttempt, Session, User, UserOrganisation, UserRole
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.inward import (
    Counter,
    InwardPO,
    InwardPOLine,
    InwardBox,
    InwardScan,
    InventoryLedgerEntry,
)

__all__ = [
    "Base",
    "TimestampMixin",
    # Enums
    "AuditModuleEnum",
    "CounterTypeEnum",
    "CustomerStatusEnum",
    "InwardBoxStatusEnum",
    "InwardCodeTypeEnum",
    "InwardReferenceStatusEnum",
    "LedgerSourceTypeEnum",
    "OutwardBoxStatusEnum",
    "OutwardPoStatusEnum",
    "OutwardScanResultEnum",
    "UserRoleEnum",
    # Models
    "Organisation",
    "User",
    "UserOrganisation",
    "UserRole",
    "Session",
    "LoginAttempt",
    "AuditLog",
    "Customer",
    "Counter",
    "InwardPO",
    "InwardPOLine",
    "InwardBox",
    "InwardScan",
    "InventoryLedgerEntry",
]
