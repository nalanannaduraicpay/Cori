"""Field normalization before consensus (no DB, no LLM)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cori.consensus.normalize import (
    normalize_account, normalize_date, normalize_extraction, normalize_invoice_number, normalize_reference,
    normalize_swift, normalize_vendor_name,
)
from cori.models.schemas import ExtractedInvoice, ExtractedLineItem


@pytest.mark.parametrize("raw, key", [
    ("Harrington & Cole LLP", "harrington and cole"),
    ("Harrington and Cole, L.L.P.", "harrington and cole"),
    ("Whitmore Barristers Ltd.", "whitmore barristers"),
    ("Kessler & Partner GmbH", "kessler and partner"),
    ("Dubois Avocats S.A.", "dubois avocats"),
    ("Tanaka Legal Services K.K.", "tanaka legal services"),
    ("Van der Berg Advocaten B.V.", "van der berg advocaten"),
    ("Lion City Legal Pte Ltd", "lion city legal"),
])
def test_vendor_name_strips_case_punctuation_and_legal_suffixes(raw, key):
    assert normalize_vendor_name(raw) == key


def test_vendor_name_empty():
    assert normalize_vendor_name(None) is None and normalize_vendor_name("  ") is None


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-13", date(2026, 10, 13)),
    ("2026/10/13", date(2026, 10, 13)),
    ("13.10.2026", date(2026, 10, 13)),   # European day-first
    ("13/10/2026", date(2026, 10, 13)),
    ("10/13/2026", date(2026, 10, 13)),   # day > 12 in 2nd position -> US month-first
    ("October 13, 2026", date(2026, 10, 13)),
    ("Oct 13, 2026", date(2026, 10, 13)),
    ("13 October 2026", date(2026, 10, 13)),
])
def test_dates_to_iso(raw, expected):
    assert normalize_date(raw) == expected


def test_unrecognised_date_raises():
    with pytest.raises(ValueError):
        normalize_date("next Tuesday")


def test_account_and_iban_normalization():
    assert normalize_account("de89 3704 0044 0532 0130 00") == "DE89370400440532013000"
    assert normalize_account("026-009-593.4471") == "0260095934471"


def test_swift_primary_office_equivalence():
    assert normalize_swift("rhbk deff") == "RHBKDEFF"
    assert normalize_swift("RHBKDEFFXXX") == "RHBKDEFF"
    assert normalize_swift("RHBKDEFF100") == "RHBKDEFF100"


def test_invoice_number_and_references():
    assert normalize_invoice_number("No. hc-2026-5001") == "HC-2026-5001"
    assert normalize_invoice_number("# KP 2026 5002") == "KP20265002"
    assert normalize_reference("C1001 (Northwind)") == "C1001"


def test_normalize_extraction_european_format():
    raw = ExtractedInvoice(
        vendor_name="Van der Berg Advocaten B.V.", vendor_country="Netherlands", invoice_number="VB-1",
        invoice_date="03.10.2026", currency="EUR", currency_evidence="EUR",
        subtotal="9.351,68", tax_amount="1.963,85", total_amount="11.315,53",
        line_items=[ExtractedLineItem(description="Memo", quantity="10,00", unit_price="603,20", amount="6.032,00")],
    )
    n = normalize_extraction(raw)
    assert n.total_amount == Decimal("11315.53") and n.subtotal == Decimal("9351.68")
    assert n.invoice_date == date(2026, 10, 3) and n.vendor_country == "NL"
    assert n.line_items[0].quantity == Decimal("10.00") and n.line_items[0].amount == Decimal("6032.00")
    assert n.parse_errors == []


def test_normalize_extraction_quantizes_jpy_to_zero_decimals():
    n = normalize_extraction(ExtractedInvoice(currency_evidence="¥ JPY", total_amount="3,135,704"))
    assert n.currency == "JPY" and n.total_amount == Decimal("3135704")


def test_normalize_extraction_keeps_ambiguous_currency_candidates():
    n = normalize_extraction(ExtractedInvoice(currency_evidence="$", total_amount="1,000.00"))
    assert n.currency is None and "CAD" in n.currency_candidates and n.total_amount == Decimal("1000.00")


def test_normalize_extraction_records_parse_errors_instead_of_crashing():
    n = normalize_extraction(ExtractedInvoice(currency="USD", total_amount="TBD", invoice_date="someday"))
    assert n.total_amount is None and n.invoice_date is None and len(n.parse_errors) == 2
