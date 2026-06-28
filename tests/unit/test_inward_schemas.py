import pytest
from pydantic import ValidationError

from app.schemas.inward import BoxClose, BoxCreate, POLineCreate, ScanCreate
from app.models.enums import InwardCodeTypeEnum


# ── POLineCreate ──────────────────────────────────────────────────────────────

def test_po_line_valid():
    line = POLineCreate(ean="1234567890123", ordered_qty=10)
    assert line.ean == "1234567890123"
    assert line.description is None


def test_po_line_description_optional():
    line = POLineCreate(ean="ABC", ordered_qty=1, description="Widget")
    assert line.description == "Widget"


def test_po_line_zero_qty_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ean="1234567890123", ordered_qty=0)


def test_po_line_negative_qty_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ean="1234567890123", ordered_qty=-1)


def test_po_line_missing_ean_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ordered_qty=5)


# ── BoxCreate ─────────────────────────────────────────────────────────────────

def test_box_create_valid():
    b = BoxCreate(customer_id=42)
    assert b.customer_id == 42


def test_box_create_missing_customer_rejected():
    with pytest.raises(ValidationError):
        BoxCreate()


# ── BoxClose ──────────────────────────────────────────────────────────────────

def test_box_close_valid():
    bc = BoxClose(physical_qty=5)
    assert bc.physical_qty == 5


def test_box_close_zero_allowed():
    bc = BoxClose(physical_qty=0)
    assert bc.physical_qty == 0


def test_box_close_negative_rejected():
    with pytest.raises(ValidationError):
        BoxClose(physical_qty=-1)


# ── ScanCreate ────────────────────────────────────────────────────────────────

def test_scan_create_defaults_to_ean():
    s = ScanCreate(ean="1234567890123")
    assert s.code_type == InwardCodeTypeEnum.ean


def test_scan_create_style_code():
    s = ScanCreate(ean="STYLE-001", code_type=InwardCodeTypeEnum.style_code)
    assert s.code_type == InwardCodeTypeEnum.style_code


def test_scan_create_missing_ean_rejected():
    with pytest.raises(ValidationError):
        ScanCreate()
