import pytest
from pydantic import ValidationError

from app.models.enums import CustomerStatusEnum
from app.schemas.customer import CustomerCreate, CustomerUpdate

# ── CustomerCreate: code validation ──────────────────────────────────────────

def test_create_valid_2_letter_code():
    c = CustomerCreate(name="Nimai", code="NM")
    assert c.code == "NM"


def test_create_valid_3_letter_code():
    c = CustomerCreate(name="Kiran Enterprises", code="KIR")
    assert c.code == "KIR"


def test_create_code_too_short():
    with pytest.raises(ValidationError, match="2"):
        CustomerCreate(name="X", code="K")


def test_create_code_too_long():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="KIRN")


def test_create_code_lowercase():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="kir")


def test_create_code_mixed_case():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="KiR")


def test_create_code_with_digit():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="K1R")


def test_create_code_with_space():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="K R")


# ── CustomerCreate: name validation ──────────────────────────────────────────

def test_create_name_empty_string():
    with pytest.raises(ValidationError):
        CustomerCreate(name="", code="KIR")


def test_create_name_whitespace_only():
    with pytest.raises(ValidationError):
        CustomerCreate(name="   ", code="KIR")


def test_create_name_stripped():
    c = CustomerCreate(name="  Kiran  ", code="KIR")
    assert c.name == "Kiran"


# ── CustomerUpdate: field validation ─────────────────────────────────────────

def test_update_code_field_rejected():
    """code must not be patchable — extra="forbid" on CustomerUpdate."""
    with pytest.raises(ValidationError):
        CustomerUpdate.model_validate({"code": "KIR"})


def test_update_name_only():
    u = CustomerUpdate(name="New Name")
    assert u.name == "New Name"
    assert u.status is None


def test_update_status_only():
    u = CustomerUpdate(status=CustomerStatusEnum.inactive)
    assert u.status == CustomerStatusEnum.inactive
    assert u.name is None


def test_update_empty_name_rejected():
    with pytest.raises(ValidationError):
        CustomerUpdate(name="")


def test_update_whitespace_name_rejected():
    with pytest.raises(ValidationError):
        CustomerUpdate(name="   ")


def test_update_both_fields():
    u = CustomerUpdate(name="Renamed", status=CustomerStatusEnum.active)
    assert u.name == "Renamed"
    assert u.status == CustomerStatusEnum.active


def test_update_all_none_is_valid():
    u = CustomerUpdate()
    assert u.name is None
    assert u.status is None
