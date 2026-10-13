"""Locale-aware amount parsing, minor-unit quantization, currency evidence and
ambiguous-symbol resolution. Pure functions, no I/O."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cori.currency.reference import (
    COUNTRY_CURRENCY, CURRENCIES, SYMBOL_CANDIDATES, country_to_iso2, minor_units,
)
from cori.models.schemas import CurrencyResolution


class AmountParseError(ValueError):
    pass


_SPACES = "    "
_ISO_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")


def parse_amount(text: str | int | Decimal | None, currency: str | None = None) -> Decimal | None:
    """Parse amounts such as ``1,234.56``, ``1.234,56``, ``1 234,56``, ``1'234.56``,
    ``EUR 12.450,00``, ``(500.00)`` or ``¥1,250,000`` into a Decimal.

    Rules for the decimal separator:
    * both ``.`` and ``,`` present -> the right-most one is the decimal separator
    * a single separator kind used more than once -> thousands separator
    * a single separator followed by exactly 3 digits -> thousands separator
      (unless the currency has 3 minor units, e.g. KWD), otherwise decimal.
    """
    if text is None:
        return None
    if isinstance(text, Decimal):
        return text
    if isinstance(text, int):
        return Decimal(text)
    s = str(text).strip()
    if not s:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    # strip everything except digits, separators, sign
    s = s.replace("−", "-")
    if "-" in s:
        negative = True
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".,'" + _SPACES)
    cleaned = cleaned.strip(_SPACES + ".,'")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        raise AmountParseError(f"no digits in amount: {text!r}")

    # apostrophes and spaces are only ever thousands separators
    for ch in "'" + _SPACES:
        cleaned = cleaned.replace(ch, "")

    has_dot, has_comma = "." in cleaned, "," in cleaned
    if has_dot and has_comma:
        dec = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        thou = "," if dec == "." else "."
        number = cleaned.replace(thou, "").replace(dec, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        if cleaned.count(sep) > 1:
            number = cleaned.replace(sep, "")
        else:
            digits_after = len(cleaned) - cleaned.rfind(sep) - 1
            if digits_after == 3 and minor_units(currency) != 3:
                number = cleaned.replace(sep, "")
            else:
                number = cleaned.replace(sep, ".")
    else:
        number = cleaned

    try:
        value = Decimal(number)
    except InvalidOperation as exc:
        raise AmountParseError(f"cannot parse amount: {text!r}") from exc
    return -value if negative else value


def quantize(value: Decimal | None, currency: str | None) -> Decimal | None:
    if value is None:
        return None
    exp = Decimal(1).scaleb(-minor_units(currency))
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def minor_unit_value(currency: str | None) -> Decimal:
    return Decimal(1).scaleb(-minor_units(currency))


def format_amount(value: Decimal | None, currency: str | None) -> str:
    """Display format used everywhere in API/UI: ``4,250.00 EUR``."""
    if value is None:
        return "n/a"
    if currency and currency in CURRENCIES:
        q = quantize(value, currency)
        return f"{q:,.{minor_units(currency)}f} {currency}"
    return f"{value:,} {currency or '(currency unresolved)'}"


# --------------------------------------------------------------------------
# Currency evidence
# --------------------------------------------------------------------------
def currency_from_evidence(evidence: str | None, stated_code: str | None = None) -> tuple[str | None, list[str]]:
    """Return (iso_code_if_unambiguous, candidate_codes) from the raw text an
    extractor saw. An explicit ISO code always wins; a bare symbol may map to
    several candidates (e.g. ``$``)."""
    texts = [t for t in (evidence, stated_code) if t]
    for t in texts:
        for m in _ISO_RE.finditer(t.upper()):
            if m.group(1) in CURRENCIES:
                return m.group(1), [m.group(1)]
    if not evidence:
        return None, []
    ev = evidence.strip().upper()
    # longest symbol first so "R$" wins over "$" and "R"
    for sym in sorted(SYMBOL_CANDIDATES, key=len, reverse=True):
        s = sym.upper()
        if s.replace(".", "").isalpha():
            # alphabetic tokens ("kr", "Rs", "R") must stand alone, not inside a word
            found = re.search(rf"(?<![A-Z]){re.escape(s)}(?![A-Z])", ev) is not None
        else:
            found = s in ev
        if found:
            cands = SYMBOL_CANDIDATES[sym]
            return (cands[0] if len(cands) == 1 else None), list(cands)
    return None, []


def resolve_currency(
    iso_code: str | None,
    candidates: list[str],
    vendor_currencies: list[str] | None,
    vendor_country: str | None,
) -> CurrencyResolution:
    """Priority: explicit ISO code / unambiguous symbol -> vendor's known billing
    currencies -> vendor country. If several of the vendor's known currencies
    match an ambiguous symbol we do NOT guess; the invoice is flagged."""
    if iso_code:
        method = "explicit_iso" if candidates == [iso_code] else "unambiguous_symbol"
        return CurrencyResolution(currency=iso_code, method=method, candidates=[iso_code],
                                  message=f"Currency {iso_code} stated on the invoice.")
    if not candidates:
        return CurrencyResolution(currency=None, method="unresolved", ambiguous=True,
                                  message="No currency code or symbol found on the invoice.")
    known = [c for c in candidates if c in (vendor_currencies or [])]
    if len(known) == 1:
        return CurrencyResolution(currency=known[0], method="vendor_known_currency", candidates=candidates,
                                  message=f"Symbol is ambiguous ({', '.join(candidates)}); vendor bills only "
                                          f"{known[0]} among those, so {known[0]} was used.")
    if len(known) > 1:
        return CurrencyResolution(currency=None, method="unresolved", ambiguous=True, candidates=known,
                                  message=f"Ambiguous currency symbol: vendor bills in {', '.join(known)} "
                                          f"and the invoice does not state an ISO code.")
    iso2 = country_to_iso2(vendor_country)
    country_ccy = COUNTRY_CURRENCY.get(iso2 or "")
    if country_ccy and country_ccy in candidates:
        return CurrencyResolution(currency=country_ccy, method="vendor_country", candidates=candidates,
                                  message=f"Resolved ambiguous symbol to {country_ccy} from vendor country {iso2}.")
    return CurrencyResolution(currency=None, method="unresolved", ambiguous=True, candidates=candidates,
                              message=f"Ambiguous currency symbol; candidates {', '.join(candidates)} "
                                      f"could not be narrowed down.")
