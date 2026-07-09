"""Unit tests for app.services.audit_service.

write_audit_log only calls session.add() (synchronous) — no DB is needed.
A plain MagicMock is sufficient as the session stand-in.
"""
from unittest.mock import MagicMock

from app.models.audit_log import AuditLog
from app.models.enums import AuditModuleEnum
from app.services.audit_service import write_audit_log


def _mock_session() -> MagicMock:
    """Minimal mock for AsyncSession. Only session.add() is called by write_audit_log."""
    return MagicMock()


def _added_entry(session: MagicMock) -> AuditLog:
    """Extract the AuditLog object passed to session.add()."""
    session.add.assert_called_once()
    return session.add.call_args[0][0]


# ── Row insertion ─────────────────────────────────────────────────────────────

async def test_audit_row_is_added_to_session():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        user_id=1,
        organisation_id=7,
        resource_id=1,
        ip_address="192.168.1.1",
    )
    entry = _added_entry(session)
    assert isinstance(entry, AuditLog)
    assert entry.action == "auth.login"
    assert entry.module == AuditModuleEnum.shared
    assert entry.resource_type == "users"
    assert entry.user_id == 1
    assert entry.organisation_id == 7
    assert entry.resource_id == 1
    assert entry.ip_address == "192.168.1.1"


async def test_write_audit_log_does_not_commit():
    """write_audit_log must never commit — the caller owns the transaction."""
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.logout",
        resource_type="sessions",
    )
    session.commit.assert_not_called()


# ── password_hash redaction ───────────────────────────────────────────────────

async def test_password_hash_stripped_from_before_data():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="user.updated",
        resource_type="users",
        before_data={
            "email": "alice@hexalog.in",
            "password_hash": "bcrypt$should_be_removed",
            "full_name": "Alice",
        },
    )
    entry = _added_entry(session)
    assert "password_hash" not in entry.before_data
    assert entry.before_data["email"] == "alice@hexalog.in"
    assert entry.before_data["full_name"] == "Alice"


async def test_password_hash_stripped_from_after_data():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="user.updated",
        resource_type="users",
        after_data={
            "email": "alice@hexalog.in",
            "password_hash": "bcrypt$should_be_removed",
        },
    )
    entry = _added_entry(session)
    assert "password_hash" not in entry.after_data
    assert entry.after_data["email"] == "alice@hexalog.in"


async def test_password_hash_stripped_from_both_dicts():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="user.password_changed",
        resource_type="users",
        before_data={"password_hash": "old_bcrypt_hash", "updated_at": "2026-01-01"},
        after_data={"password_hash": "new_bcrypt_hash", "updated_at": "2026-06-22"},
    )
    entry = _added_entry(session)
    assert "password_hash" not in entry.before_data
    assert "password_hash" not in entry.after_data
    assert entry.before_data["updated_at"] == "2026-01-01"
    assert entry.after_data["updated_at"] == "2026-06-22"


async def test_none_before_data_passes_through_as_none():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        before_data=None,
    )
    entry = _added_entry(session)
    assert entry.before_data is None


async def test_none_after_data_passes_through_as_none():
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        after_data=None,
    )
    entry = _added_entry(session)
    assert entry.after_data is None


# ── organisation_id NULL handling ─────────────────────────────────────────────

async def test_null_organisation_id_is_accepted():
    """Login-time audit events have no org context yet — NULL must be accepted."""
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        user_id=42,
        organisation_id=None,
    )
    entry = _added_entry(session)
    assert entry.organisation_id is None
    assert entry.user_id == 42


async def test_null_user_id_is_accepted():
    """Logout events for unknown/expired sessions have no user_id."""
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.logout",
        resource_type="sessions",
        user_id=None,
        organisation_id=None,
    )
    entry = _added_entry(session)
    assert entry.user_id is None
    assert entry.organisation_id is None


# ── Login event audit pattern ─────────────────────────────────────────────────

async def test_login_event_matches_auth_service_pattern():
    """Verify the exact call pattern that auth_service.login() uses."""
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        resource_id=42,
        user_id=42,
        organisation_id=7,
        after_data={"email": "user@hexalog.in", "organisation_id": 7},
        ip_address="10.0.0.1",
    )
    entry = _added_entry(session)
    assert entry.module == AuditModuleEnum.shared
    assert entry.action == "auth.login"
    assert entry.resource_type == "users"
    assert entry.resource_id == 42
    assert entry.user_id == 42
    assert entry.organisation_id == 7
    assert entry.after_data == {"email": "user@hexalog.in", "organisation_id": 7}
    assert entry.before_data is None
    assert entry.ip_address == "10.0.0.1"


async def test_login_event_after_data_never_leaks_password_hash():
    """Even if a caller accidentally includes password_hash, it is always stripped."""
    session = _mock_session()
    await write_audit_log(
        session,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        after_data={
            "email": "user@hexalog.in",
            "password_hash": "this_must_never_reach_the_db",
        },
    )
    entry = _added_entry(session)
    assert "password_hash" not in entry.after_data
    assert entry.after_data["email"] == "user@hexalog.in"
