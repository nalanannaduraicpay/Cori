"""Validation agent: deterministic rule checks against Oracle (via the
repository). Each rule is a pure function so it can be unit-tested without a
database or an LLM; ``run_validation`` fetches the data and applies them."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from rapidfuzz import fuzz

from cori.config import Settings
from cori.consensus.normalize import normalize_account, normalize_swift, normalize_vendor_name
from cori.currency.fx import FxRateNotFound, FxService
from cori.currency.parsing import format_amount, minor_unit_value, resolve_currency
from cori.db import repository as repo
from cori.models.schemas import (
    CheckResult, CurrencyResolution, NormalizedInvoice, Severity, ValidationResult,
)


def _fail(name, code, severity, message, **details) -> CheckResult:
    return CheckResult(name=name, passed=False, code=code, severity=severity, message=message, details=details)


def _ok(name, message, **details) -> CheckResult:
    return CheckResult(name=name, passed=True, message=message, details=details)


# ----------------------------------------------------------------------------
# pure rules
# ----------------------------------------------------------------------------
def check_required_fields(inv: NormalizedInvoice) -> CheckResult:
    missing = [f for f in ("vendor_name", "invoice_number", "invoice_date", "total_amount", "matter_number")
               if getattr(inv, f) in (None, "")]
    if missing:
        return _fail("required_fields", "REQUIRED_FIELD_MISSING", Severity.HIGH,
                     f"Required fields missing or not agreed: {', '.join(missing)}.", missing=missing)
    return _ok("required_fields", "All required fields present.")


def match_vendor(inv: NormalizedInvoice, vendors: list[dict], threshold: int) -> tuple[dict | None, float]:
    key = inv.vendor_name_key or normalize_vendor_name(inv.vendor_name) or ""
    best, best_score = None, 0.0
    for v in vendors:
        score = fuzz.token_sort_ratio(key, normalize_vendor_name(v["name"]) or "")
        if score > best_score:
            best, best_score = v, score
    if best and best_score >= threshold:
        return best, best_score
    if inv.vendor_id:
        by_id = next((v for v in vendors if v["vendor_id"] == inv.vendor_id), None)
        if by_id:
            return by_id, best_score
    return None, best_score


def check_vendor(inv: NormalizedInvoice, vendor: dict | None, score: float) -> CheckResult:
    if vendor is None:
        return _fail("vendor_master", "VENDOR_NOT_FOUND", Severity.HIGH,
                     f"Vendor '{inv.vendor_name}' not found in the vendor master.", best_score=score)
    if inv.vendor_id and inv.vendor_id != vendor["vendor_id"]:
        return _fail("vendor_master", "VENDOR_ID_MISMATCH", Severity.HIGH,
                     f"Vendor ID {inv.vendor_id} on the invoice does not match {vendor['name']} "
                     f"({vendor['vendor_id']}).", vendor_id=vendor["vendor_id"])
    if vendor.get("status") != "ACTIVE":
        return _fail("vendor_master", "VENDOR_INACTIVE", Severity.HIGH,
                     f"Vendor {vendor['name']} is {vendor.get('status')}.", vendor_id=vendor["vendor_id"])
    return _ok("vendor_master", f"Vendor matched: {vendor['name']} ({vendor['vendor_id']}).",
               vendor_id=vendor["vendor_id"], match_score=score)


def check_arithmetic(inv: NormalizedInvoice, currency: str | None, tolerance_units: int = 1) -> list[CheckResult]:
    tol = minor_unit_value(currency) * tolerance_units
    out = []
    amounts = [li.amount for li in inv.line_items]
    if not inv.line_items:
        out.append(_ok("arithmetic_lines", "No line items to reconcile."))
    elif any(a is None for a in amounts) or inv.subtotal is None:
        out.append(_fail("arithmetic_lines", "ARITHMETIC_LINES_MISMATCH", Severity.MEDIUM,
                         "Line items or subtotal could not be read, so they cannot be reconciled."))
    else:
        line_sum = sum(amounts, Decimal(0))
        diff = inv.subtotal - line_sum
        if abs(diff) > tol:
            out.append(_fail("arithmetic_lines", "ARITHMETIC_LINES_MISMATCH", Severity.HIGH,
                             f"Line items sum to {format_amount(line_sum, currency)} but the subtotal is "
                             f"{format_amount(inv.subtotal, currency)} (difference {format_amount(diff, currency)}).",
                             line_sum=line_sum, subtotal=inv.subtotal, difference=diff))
        else:
            out.append(_ok("arithmetic_lines", f"Line items sum to the subtotal ({format_amount(line_sum, currency)})."))
    if inv.total_amount is None:
        out.append(_fail("arithmetic_total", "ARITHMETIC_TOTAL_MISMATCH", Severity.HIGH, "Total amount missing."))
    else:
        sub = inv.subtotal if inv.subtotal is not None else (sum(amounts, Decimal(0)) if inv.line_items else None)
        tax = inv.tax_amount or Decimal(0)
        if sub is None:
            out.append(_ok("arithmetic_total", "No subtotal printed; total taken as stated."))
        elif abs(sub + tax - inv.total_amount) > tol:
            out.append(_fail("arithmetic_total", "ARITHMETIC_TOTAL_MISMATCH", Severity.HIGH,
                             f"Subtotal {format_amount(sub, currency)} + tax {format_amount(tax, currency)} "
                             f"does not equal total {format_amount(inv.total_amount, currency)}.",
                             subtotal=sub, tax=tax, total=inv.total_amount))
        else:
            out.append(_ok("arithmetic_total", "Subtotal + tax equals total."))
    return out


def check_client_matter(inv: NormalizedInvoice, client: dict | None, matter: dict | None) -> CheckResult:
    if inv.matter_number and matter is None:
        return _fail("client_matter", "MATTER_NOT_FOUND", Severity.HIGH, f"Matter {inv.matter_number} not found.")
    if inv.client_number and client is None:
        return _fail("client_matter", "CLIENT_NOT_FOUND", Severity.HIGH, f"Client {inv.client_number} not found.")
    if matter is None:
        return _fail("client_matter", "MATTER_NOT_FOUND", Severity.HIGH, "No matter number on the invoice.")
    if client is not None and matter["client_id"] != client["client_id"]:
        return _fail("client_matter", "MATTER_CLIENT_MISMATCH", Severity.HIGH,
                     f"Matter {matter['matter_number']} does not belong to client {client['client_number']}.")
    if matter.get("status") != "OPEN":
        return _fail("client_matter", "MATTER_CLOSED", Severity.HIGH,
                     f"Matter {matter['matter_number']} is {matter.get('status')}.")
    return _ok("client_matter", f"Matter {matter['matter_number']} is open"
                                + (f" for client {client['client_number']}." if client else "."))


def _where(d: dict) -> str:
    if d.get("origin") == "CORI":
        return f"submitted to Cori earlier as invoice {d['invoice_id']}, status {d.get('status')}"
    return f"invoice {d['invoice_id']}"


def check_duplicates(inv: NormalizedInvoice, by_number: list[dict], in_window: list[dict],
                     window_days: int) -> CheckResult:
    if by_number:
        d = by_number[0]
        return _fail("duplicates", "DUPLICATE_INVOICE_NUMBER", Severity.HIGH,
                     f"Invoice number {inv.invoice_number} already exists for this vendor "
                     f"({_where(d)}, {d.get('invoice_date')}, "
                     f"{format_amount(d.get('total'), d.get('currency'))}).",
                     matches=[m["invoice_id"] for m in by_number])
    same_amount = [m for m in in_window
                   if m.get("total") is not None and inv.total_amount is not None
                   and m["total"] == inv.total_amount and (m.get("currency") == inv.currency or not inv.currency)]
    if same_amount:
        d = same_amount[0]
        return _fail("duplicates", "POSSIBLE_DUPLICATE_AMOUNT", Severity.HIGH,
                     f"Same vendor and total ({format_amount(inv.total_amount, inv.currency)}) within "
                     f"{window_days} days: {_where(d)} ({d.get('invoice_number')}) dated {d.get('invoice_date')}.",
                     matches=[m["invoice_id"] for m in same_amount])
    return _ok("duplicates", "No duplicate found (vendor + number, or vendor + total within "
                             f"{window_days} days).")


def check_bank(inv: NormalizedInvoice, accounts: list[dict]) -> CheckResult:
    if not inv.bank_account:
        return _fail("bank_details", "BANK_DETAILS_MISSING", Severity.HIGH,
                     "No beneficiary bank account could be agreed from the invoice.")
    acct = normalize_account(inv.bank_account)
    swift = normalize_swift(inv.swift_bic)
    for a in accounts:
        if normalize_account(a["iban_or_account"]) == acct:
            if swift and a.get("swift_bic") and normalize_swift(a["swift_bic"]) != swift:
                return _fail("bank_details", "BANK_DETAILS_MISMATCH", Severity.HIGH,
                             f"Account matches the vendor's file but SWIFT/BIC {swift} differs from "
                             f"{a['swift_bic']} on file.")
            return _ok("bank_details", f"Bank details match the vendor's active account at {a['bank_name']}.")
    on_file = ", ".join(f"{a['bank_name']} {mask(a['iban_or_account'])}" for a in accounts) or "none"
    return _fail("bank_details", "BANK_DETAILS_MISMATCH", Severity.HIGH,
                 f"Beneficiary account {mask(acct)} ({inv.bank_name or 'unknown bank'}, {swift or 'no SWIFT'}) does "
                 f"not match any active account on file ({on_file}). Possible payment fraud - verify with the "
                 f"vendor through a known contact.", invoice_account=mask(acct), invoice_swift=swift)


def mask(account: str | None) -> str:
    if not account:
        return "n/a"
    a = account.replace(" ", "")
    return f"{a[:4]}...{a[-4:]}" if len(a) > 8 else a


def check_currency(resolution: CurrencyResolution, vendor_currencies: list[str], vendor_name: str | None) -> list[CheckResult]:
    out = []
    if resolution.ambiguous or not resolution.currency:
        out.append(_fail("currency_resolution", "CURRENCY_AMBIGUOUS", Severity.HIGH, resolution.message,
                         candidates=resolution.candidates))
        return out
    out.append(_ok("currency_resolution", resolution.message, method=resolution.method))
    if vendor_currencies and resolution.currency not in vendor_currencies:
        out.append(_fail("vendor_currency", "CURRENCY_NOT_VENDOR_BILLING", Severity.MEDIUM,
                         f"Invoice currency {resolution.currency} is not one of {vendor_name or 'the vendor'}'s "
                         f"billing currencies ({', '.join(vendor_currencies)}).",
                         vendor_currencies=vendor_currencies))
    else:
        out.append(_ok("vendor_currency", f"{resolution.currency} is a known billing currency for this vendor."))
    return out


def check_dates(invoice_date: date | None, today: date, max_age_days: int) -> CheckResult:
    if invoice_date is None:
        return _fail("invoice_date", "INVOICE_DATE_MISSING", Severity.HIGH, "Invoice date missing or unreadable.")
    if invoice_date > today:
        return _fail("invoice_date", "INVOICE_DATE_FUTURE", Severity.HIGH,
                     f"Invoice date {invoice_date} is in the future.")
    if invoice_date < today - timedelta(days=max_age_days):
        return _fail("invoice_date", "INVOICE_DATE_TOO_OLD", Severity.MEDIUM,
                     f"Invoice date {invoice_date} is older than {max_age_days} days.")
    return _ok("invoice_date", f"Invoice date {invoice_date} is within the allowed range.")


# ----------------------------------------------------------------------------
# orchestrator
# ----------------------------------------------------------------------------
def run_validation(invoice_id: int, inv: NormalizedInvoice, settings: Settings,
                   today: date | None = None) -> ValidationResult:
    today = today or date.today()
    vcfg = settings.validation
    checks: list[CheckResult] = [check_required_fields(inv)]

    vendor, score = match_vendor(inv, repo.list_vendors(), settings.consensus.fuzzy_threshold)
    checks.append(check_vendor(inv, vendor, score))
    vendor_currencies = repo.get_vendor_currencies(vendor["vendor_id"]) if vendor else []

    resolution = resolve_currency(inv.currency, inv.currency_candidates, vendor_currencies,
                                  vendor["country"] if vendor else inv.vendor_country)
    checks.extend(check_currency(resolution, vendor_currencies, vendor["name"] if vendor else None))
    ccy = resolution.currency or (inv.currency_candidates[0] if inv.currency_candidates else None)

    checks.extend(check_arithmetic(inv, ccy, vcfg.amount_tolerance_minor_units))

    client = repo.get_client_by_number(inv.client_number) if inv.client_number else None
    matter = repo.get_matter_by_number(inv.matter_number) if inv.matter_number else None
    if client is None and matter is not None and not inv.client_number:
        client = repo.get_client(matter["client_id"])
    checks.append(check_client_matter(inv, client, matter))

    if vendor and inv.invoice_number:
        by_number = repo.find_invoices_by_vendor_number(vendor["vendor_id"], inv.invoice_number, invoice_id)
        window = []
        if inv.invoice_date:
            w = timedelta(days=vcfg.duplicate_window_days)
            window = repo.find_invoices_by_vendor_in_window(vendor["vendor_id"], inv.invoice_date - w,
                                                            inv.invoice_date + w, invoice_id)
        checks.append(check_duplicates(inv.model_copy(update={"currency": resolution.currency}),
                                       by_number, window, vcfg.duplicate_window_days))

    if vendor:
        checks.append(check_bank(inv, repo.get_vendor_bank_accounts(vendor["vendor_id"])))

    checks.append(check_dates(inv.invoice_date, today, vcfg.max_invoice_age_days))

    fx_used, total_base = None, None
    if resolution.currency and inv.total_amount is not None and inv.invoice_date:
        try:
            fx = FxService(repo.get_fx_rate_to_usd)
            total_base, fx_used = fx.convert(inv.total_amount, resolution.currency, settings.base_currency,
                                             inv.invoice_date)
            checks.append(_ok("fx_rate", f"FX {resolution.currency}->{settings.base_currency} "
                                         f"{fx_used.rate:.6f} (rate date {fx_used.rate_date}).",
                              rate=fx_used.rate, rate_date=fx_used.rate_date))
        except FxRateNotFound as exc:
            checks.append(_fail("fx_rate", "FX_RATE_MISSING", Severity.MEDIUM, str(exc)))

    return ValidationResult(passed=all(c.passed for c in checks), checks=checks, currency=resolution,
                            vendor=vendor, client=client, matter=matter, fx=fx_used, total_base=total_base)
