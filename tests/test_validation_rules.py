"""Each validation rule as a pure function (no DB, no LLM)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from cori.agents.validation import (
    check_arithmetic, check_bank, check_client_matter, check_currency, check_dates, check_duplicates,
    check_required_fields, check_vendor, match_vendor,
)
from cori.currency.parsing import resolve_currency
from cori.models.schemas import NormalizedLineItem
from tests.conftest import make_invoice

VENDORS = [
    {"vendor_id": "V001", "name": "Harrington & Cole LLP", "country": "US", "default_currency": "USD",
     "status": "ACTIVE"},
    {"vendor_id": "V003", "name": "Kessler & Partner GmbH", "country": "DE", "default_currency": "EUR",
     "status": "ACTIVE"},
    {"vendor_id": "V099", "name": "Defunct Partners LLP", "country": "US", "default_currency": "USD",
     "status": "INACTIVE"},
]
ACCOUNTS = [{"bank_name": "First Meridian Bank", "iban_or_account": "026 009 5934471", "swift_bic": "FMBKUS33XXX"}]


# --- required fields ---------------------------------------------------------
def test_required_fields(invoice):
    assert check_required_fields(invoice).passed
    r = check_required_fields(invoice.model_copy(update={"total_amount": None, "matter_number": None}))
    assert not r.passed and r.code == "REQUIRED_FIELD_MISSING" and set(r.details["missing"]) == {
        "total_amount", "matter_number"}


# --- vendor master --------------------------------------------------------------
def test_vendor_matched_by_fuzzy_name(invoice):
    v, score = match_vendor(invoice.model_copy(update={"vendor_name_key": "harrington and cole"}), VENDORS, 90)
    assert v["vendor_id"] == "V001" and score >= 90
    assert check_vendor(invoice, v, score).passed


def test_vendor_not_found():
    inv = make_invoice(vendor_name="Unknown Law Group", vendor_name_key="unknown law group")
    v, score = match_vendor(inv, VENDORS, 90)
    r = check_vendor(inv, v, score)
    assert v is None and not r.passed and r.code == "VENDOR_NOT_FOUND"


def test_vendor_inactive():
    inv = make_invoice(vendor_name="Defunct Partners LLP", vendor_name_key="defunct partners")
    v, score = match_vendor(inv, VENDORS, 90)
    assert check_vendor(inv, v, score).code == "VENDOR_INACTIVE"


def test_vendor_id_on_invoice_must_match(invoice):
    v, score = match_vendor(invoice, VENDORS, 90)
    r = check_vendor(invoice.model_copy(update={"vendor_id": "V003"}), v, score)
    assert r.code == "VENDOR_ID_MISMATCH"


# --- arithmetic -------------------------------------------------------------------
def test_arithmetic_ok(invoice):
    assert all(c.passed for c in check_arithmetic(invoice, "USD"))


def test_lines_do_not_sum_to_subtotal(invoice):
    bad = invoice.model_copy(update={"subtotal": Decimal("2000.00"), "total_amount": Decimal("2000.00")})
    lines, total = check_arithmetic(bad, "USD")
    assert lines.code == "ARITHMETIC_LINES_MISMATCH" and lines.details["difference"] == Decimal("500.00")
    assert total.passed


def test_subtotal_plus_tax_must_equal_total(invoice):
    _, total = check_arithmetic(invoice.model_copy(update={"tax_amount": Decimal("10.00")}), "USD")
    assert total.code == "ARITHMETIC_TOTAL_MISMATCH"


def test_tolerance_is_one_minor_unit(invoice):
    off_by_one_cent = invoice.model_copy(update={"total_amount": Decimal("1500.01")})
    assert all(c.passed for c in check_arithmetic(off_by_one_cent, "USD"))
    off_by_two = invoice.model_copy(update={"total_amount": Decimal("1500.02")})
    assert not check_arithmetic(off_by_two, "USD")[1].passed


def test_tolerance_follows_currency_minor_units():
    jpy = make_invoice(currency="JPY", subtotal=Decimal("100000"), tax_amount=Decimal("10000"),
                       total_amount=Decimal("110001"),
                       line_items=[NormalizedLineItem(description="x", amount=Decimal("100000"))])
    assert check_arithmetic(jpy, "JPY")[1].passed           # 1 yen off = 1 minor unit
    assert not check_arithmetic(jpy.model_copy(update={"total_amount": Decimal("110002")}), "JPY")[1].passed


# --- client / matter ------------------------------------------------------------
CLIENT = {"client_id": 10, "client_number": "C1010"}
MATTER = {"matter_id": 30, "client_id": 10, "matter_number": "2025-1003", "status": "OPEN"}


def test_client_matter_ok(invoice):
    assert check_client_matter(invoice, CLIENT, MATTER).passed


def test_matter_not_found(invoice):
    assert check_client_matter(invoice, CLIENT, None).code == "MATTER_NOT_FOUND"


def test_matter_closed(invoice):
    assert check_client_matter(invoice, CLIENT, {**MATTER, "status": "CLOSED"}).code == "MATTER_CLOSED"


def test_matter_belongs_to_other_client(invoice):
    assert check_client_matter(invoice, CLIENT, {**MATTER, "client_id": 3}).code == "MATTER_CLIENT_MISMATCH"


def test_client_not_found(invoice):
    assert check_client_matter(invoice, None, MATTER).code == "CLIENT_NOT_FOUND"


# --- duplicates -------------------------------------------------------------------
def test_duplicate_by_vendor_and_number(invoice):
    hist = [{"invoice_id": 7, "invoice_date": date(2026, 9, 1), "total": Decimal("1500.00"), "currency": "USD",
             "origin": "IAS"}]
    r = check_duplicates(invoice, hist, [], 30)
    assert r.code == "DUPLICATE_INVOICE_NUMBER" and r.details["matches"] == [7]


def test_duplicate_of_earlier_cori_submission_says_so(invoice):
    prev = [{"invoice_id": 900001, "invoice_date": date(2026, 10, 1), "total": Decimal("1500.00"),
             "currency": "USD", "origin": "CORI", "status": "ADVANCED_TO_APPROVAL"}]
    assert "submitted to Cori earlier" in check_duplicates(invoice, prev, [], 30).message


def test_possible_duplicate_same_total_within_window(invoice):
    near = [{"invoice_id": 8, "invoice_number": "HC-OTHER", "invoice_date": date(2026, 9, 20),
             "total": Decimal("1500.00"), "currency": "USD"}]
    assert check_duplicates(invoice, [], near, 30).code == "POSSIBLE_DUPLICATE_AMOUNT"


def test_same_total_in_other_currency_is_not_a_duplicate(invoice):
    near = [{"invoice_id": 8, "invoice_date": date(2026, 9, 20), "total": Decimal("1500.00"), "currency": "EUR"}]
    assert check_duplicates(invoice, [], near, 30).passed


def test_no_duplicate(invoice):
    assert check_duplicates(invoice, [], [], 30).passed


# --- bank details -----------------------------------------------------------------
def test_bank_matches_account_on_file_after_normalization(invoice):
    assert check_bank(invoice, ACCOUNTS).passed  # spaces and SWIFT XXX suffix ignored


def test_bank_account_mismatch_is_high_severity(invoice):
    r = check_bank(invoice.model_copy(update={"bank_account": "LV10BOCB0000123456789", "swift_bic": "BOCBLV22"}),
                   ACCOUNTS)
    assert r.code == "BANK_DETAILS_MISMATCH" and r.severity.value == "HIGH" and "fraud" in r.message
    assert "0000123456789" not in r.message  # account numbers are masked in messages


def test_bank_swift_mismatch(invoice):
    r = check_bank(invoice.model_copy(update={"swift_bic": "OTHRUS33"}), ACCOUNTS)
    assert r.code == "BANK_DETAILS_MISMATCH"


def test_bank_details_missing(invoice):
    assert check_bank(invoice.model_copy(update={"bank_account": None}), ACCOUNTS).code == "BANK_DETAILS_MISSING"


def test_inactive_accounts_are_not_offered():
    assert not check_bank(make_invoice(), []).passed  # repository passes active accounts only


# --- currency -----------------------------------------------------------------------
def test_currency_ambiguous_is_flagged():
    res = resolve_currency(None, ["USD", "CAD", "AUD"], ["CAD", "USD"], "CA")
    checks = check_currency(res, ["CAD", "USD"], "Maple North Legal LLP")
    assert len(checks) == 1 and checks[0].code == "CURRENCY_AMBIGUOUS"


def test_currency_not_a_vendor_billing_currency():
    res = resolve_currency("JPY", ["JPY"], ["EUR"], "DE")
    checks = check_currency(res, ["EUR"], "Kessler & Partner GmbH")
    assert checks[0].passed and checks[1].code == "CURRENCY_NOT_VENDOR_BILLING"


def test_currency_ok():
    res = resolve_currency("EUR", ["EUR"], ["GBP", "EUR"], "GB")
    assert all(c.passed for c in check_currency(res, ["GBP", "EUR"], "Whitmore"))


# --- dates ---------------------------------------------------------------------------
def test_dates():
    today = date(2026, 10, 13)
    assert check_dates(date(2026, 10, 1), today, 365).passed
    assert check_dates(today + timedelta(days=1), today, 365).code == "INVOICE_DATE_FUTURE"
    assert check_dates(today - timedelta(days=400), today, 365).code == "INVOICE_DATE_TOO_OLD"
    assert check_dates(None, today, 365).code == "INVOICE_DATE_MISSING"
