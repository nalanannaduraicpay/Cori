"""Field normalization before comparison (plain Python, no LLM)."""
from __future__ import annotations

import re
import string
from datetime import date

from cori.currency.parsing import AmountParseError, currency_from_evidence, parse_amount, quantize
from cori.currency.reference import country_to_iso2, minor_units
from cori.models.schemas import ExtractedInvoice, NormalizedInvoice, NormalizedLineItem

LEGAL_SUFFIXES = {
    "llp", "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co", "company", "plc",
    "gmbh", "mbh", "ag", "kg", "sa", "sas", "sarl", "srl", "spa", "sl", "bv", "nv", "pty", "pte", "kk",
    "sc", "oy", "ab", "as", "aps", "lp", "pc", "pllc", "partg", "partnerschaft",
}
_PUNCT = str.maketrans({ch: " " for ch in string.punctuation if ch not in ".&"})
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def normalize_vendor_name(name: str | None) -> str | None:
    """'Dubois Avocats S.A.' -> 'dubois avocats'; 'Harrington & Cole, LLP' -> 'harrington and cole'."""
    if not name:
        return None
    s = name.lower().replace("&", " and ")
    s = s.replace(".", "")  # S.A. -> sa, K.K. -> kk
    s = s.translate(_PUNCT)
    tokens = [t for t in s.split() if t]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or None


def normalize_date(value: str | None) -> date | None:
    if not value:
        return None
    s = value.strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        # day-first (international default) unless that is impossible
        day, month = (b, a) if b > 12 else (a, b)
        return date(y, month, day)
    m = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", s)  # October 10, 2026
    if m and m[1].lower() in MONTHS:
        return date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+)\.?,? (\d{4})", s)  # 10 October 2026
    if m and m[2].lower() in MONTHS:
        return date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    raise ValueError(f"unrecognised date: {value!r}")


def normalize_account(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[\s\-./]", "", value).upper() or None


def normalize_swift(value: str | None) -> str | None:
    if not value:
        return None
    s = re.sub(r"\s", "", value).upper()
    return s[:8] if len(s) == 11 and s.endswith("XXX") else s  # primary office equivalence


def normalize_invoice_number(value: str | None) -> str | None:
    if not value:
        return None
    s = re.sub(r"^(no\.?|nr\.?|#)\s*", "", value.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", "", s).upper() or None


def normalize_reference(value: str | None) -> str | None:
    """Client / matter numbers: first token only, e.g. 'C1001 (Northwind)' -> 'C1001'."""
    if not value:
        return None
    token = value.strip().split()[0].strip(",;()")
    return token.upper() or None


def _amount(value, ccy, errors, label):
    try:
        v = parse_amount(value, ccy)
    except AmountParseError as exc:
        errors.append(f"{label}: {exc}")
        return None
    return quantize(v, ccy) if (v is not None and ccy) else v


def normalize_extraction(raw: ExtractedInvoice) -> NormalizedInvoice:
    errors: list[str] = []
    iso, candidates = currency_from_evidence(raw.currency_evidence, raw.currency)
    # quantize by the resolved currency, or by the candidates' shared minor units
    q_ccy = iso
    if not q_ccy and candidates and len({minor_units(c) for c in candidates}) == 1:
        q_ccy = candidates[0]

    def d(v, label):
        try:
            return normalize_date(v)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None

    items = []
    for i, li in enumerate(raw.line_items):
        items.append(NormalizedLineItem(
            description=(li.description or "").strip() or None,
            timekeeper=(li.timekeeper or "").strip() or None,
            quantity=_amount(li.quantity, None, errors, f"line {i + 1} quantity"),
            unit_price=_amount(li.unit_price, q_ccy, errors, f"line {i + 1} unit_price"),
            amount=_amount(li.amount, q_ccy, errors, f"line {i + 1} amount"),
        ))
    return NormalizedInvoice(
        vendor_name=(raw.vendor_name or "").strip() or None,
        vendor_name_key=normalize_vendor_name(raw.vendor_name),
        vendor_id=(raw.vendor_id or "").strip().upper() or None,
        vendor_country=country_to_iso2(raw.vendor_country),
        invoice_number=normalize_invoice_number(raw.invoice_number),
        invoice_date=d(raw.invoice_date, "invoice_date"),
        due_date=d(raw.due_date, "due_date"),
        currency=iso,
        currency_candidates=candidates,
        currency_evidence=raw.currency_evidence,
        subtotal=_amount(raw.subtotal, q_ccy, errors, "subtotal"),
        tax_amount=_amount(raw.tax_amount, q_ccy, errors, "tax_amount"),
        total_amount=_amount(raw.total_amount, q_ccy, errors, "total_amount"),
        client_number=normalize_reference(raw.client_number),
        matter_number=normalize_reference(raw.matter_number),
        bank_name=(raw.bank_name or "").strip() or None,
        bank_account=normalize_account(raw.bank_account),
        swift_bic=normalize_swift(raw.swift_bic),
        line_items=items,
        parse_errors=errors,
    )
