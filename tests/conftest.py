"""Shared fixtures.

Unit tests are pure (no DB, no LLM). Integration tests get a throwaway SQLite
database seeded with the demo dataset, so a plain ``pytest`` run never touches
the Oracle schema configured in .env. Oracle tests are opt-in.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cori.models.schemas import (
    ExtractedInvoice, ExtractorResult, FieldConfidence, NormalizedInvoice, NormalizedLineItem,
)


def pytest_collection_modifyitems(config, items):
    run_oracle = os.getenv("CORI_TEST_ORACLE") == "1"
    for item in items:
        if "oracle" in item.keywords and not run_oracle:
            item.add_marker(pytest.mark.skip(reason="set CORI_TEST_ORACLE=1 to run against Oracle (reseeds schema)"))


# ----------------------------------------------------------------------------
# builders for consensus / routing tests
# ----------------------------------------------------------------------------
def make_invoice(**overrides) -> NormalizedInvoice:
    base = dict(
        vendor_name="Harrington & Cole LLP", vendor_name_key="harrington and cole", vendor_country="US",
        invoice_number="HC-2026-5001", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
        currency="USD", currency_candidates=["USD"], currency_evidence="USD",
        subtotal=Decimal("1500.00"), tax_amount=Decimal("0.00"), total_amount=Decimal("1500.00"),
        client_number="C1010", matter_number="2025-1003", bank_name="First Meridian Bank",
        bank_account="0260095934471", swift_bic="FMBKUS33",
        line_items=[
            NormalizedLineItem(description="Draft motion", timekeeper="A. Harrington", quantity=Decimal("1.0"),
                               unit_price=Decimal("1000.00"), amount=Decimal("1000.00")),
            NormalizedLineItem(description="Research", timekeeper="J. Patel", quantity=Decimal("1.0"),
                               unit_price=Decimal("500.00"), amount=Decimal("500.00")),
        ],
    )
    base.update(overrides)
    return NormalizedInvoice(**base)


def make_result(name: str, inv: NormalizedInvoice | None, status: str = "ok",
                confidence: dict[str, float] | None = None) -> ExtractorResult:
    raw = ExtractedInvoice(field_confidence=[FieldConfidence(field=k, confidence=v)
                                             for k, v in (confidence or {}).items()])
    return ExtractorResult(name=name, method="text", provider="test", model_name="test", prompt_version="t.v1",
                           status=status, error=None if status == "ok" else "boom",
                           raw=raw if status == "ok" else None, normalized=inv if status == "ok" else None)


@pytest.fixture
def invoice():
    return make_invoice()


@pytest.fixture(scope="session", autouse=True)
def _isolated_samples_dir(tmp_path_factory):
    """Demo setup regenerates the sample PDFs (dates are relative to today).
    Point it at a temp dir so test runs never modify the committed
    sample_invoices/ and always use samples generated for today."""
    import cori.demo as demo
    original = demo.SAMPLES_DIR
    demo.SAMPLES_DIR = tmp_path_factory.mktemp("sample_invoices")
    try:
        yield demo.SAMPLES_DIR
    finally:
        demo.SAMPLES_DIR = original


# ----------------------------------------------------------------------------
# integration: throwaway SQLite database + generated sample PDFs
# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def sqlite_demo(tmp_path_factory):
    """Seeded SQLite DB with the 14 sample invoices registered as PENDING.
    Yields {file name: invoice_id}."""
    tmp = tmp_path_factory.mktemp("cori")
    saved = {k: os.environ.get(k) for k in ("DB_BACKEND", "SQLITE_PATH", "UPLOAD_DIR", "LLM_PROVIDER")}
    os.environ.update(DB_BACKEND="sqlite", SQLITE_PATH=str(tmp / "cori_test.db"), UPLOAD_DIR=str(tmp / "uploads"),
                      LLM_PROVIDER="mock")
    from cori.demo import setup
    try:
        yield setup(register=True)["registered"]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory) -> Path:
    from cori.samples.generate import generate
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return out
