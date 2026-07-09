import enum


class UserRoleEnum(str, enum.Enum):
    admin = "admin"
    inward_operator = "inward_operator"
    packer = "packer"


class CustomerStatusEnum(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class InwardReferenceStatusEnum(str, enum.Enum):
    open = "open"
    completed = "completed"


class InwardBoxStatusEnum(str, enum.Enum):
    scanning = "scanning"
    pending_verification = "pending_verification"
    completed = "completed"


class InwardCodeTypeEnum(str, enum.Enum):
    ean = "ean"
    style_code = "style_code"


class OutwardPoStatusEnum(str, enum.Enum):
    open = "open"
    closed = "closed"


class OutwardBoxStatusEnum(str, enum.Enum):
    open = "open"
    in_use = "in_use"
    closed = "closed"


class OutwardScanResultEnum(str, enum.Enum):
    accepted = "accepted"
    rejected = "rejected"
    deleted = "deleted"


class LedgerSourceTypeEnum(str, enum.Enum):
    inward_submission = "inward_submission"
    outward_scan = "outward_scan"
    outward_scan_deletion = "outward_scan_deletion"
    inward_scan_deletion = "inward_scan_deletion"


class AuditModuleEnum(str, enum.Enum):
    shared = "shared"
    inward = "inward"
    outward = "outward"
    reports = "reports"


class CounterTypeEnum(str, enum.Enum):
    outward_box = "outward_box"
    inscan_number = "inscan_number"
    inward_box = "inward_box"
