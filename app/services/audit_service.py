import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import AuditModuleEnum

log = structlog.get_logger(__name__)

_SENSITIVE_FIELDS = {"password_hash"}


def _strip_sensitive(data: dict | None) -> dict | None:
    if data is None:
        return None
    return {k: v for k, v in data.items() if k not in _SENSITIVE_FIELDS}


async def write_audit_log(
    session: AsyncSession,
    *,
    module: AuditModuleEnum,
    action: str,
    resource_type: str,
    user_id: int | None = None,
    organisation_id: int | None = None,
    resource_id: int | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Insert an audit row into the current transaction. Never commits — caller owns the tx.

    password_hash is stripped from before_data and after_data unconditionally.
    """
    clean_before = _strip_sensitive(before_data)
    clean_after = _strip_sensitive(after_data)

    entry = AuditLog(
        module=module,
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        organisation_id=organisation_id,
        resource_id=resource_id,
        before_data=clean_before,
        after_data=clean_after,
        ip_address=ip_address,
    )
    session.add(entry)

    log.info(
        action,
        module=module.value,
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        organisation_id=organisation_id,
    )
