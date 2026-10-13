"""Currency parsing, minor units, ambiguous symbols and FX (no DB, no LLM)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cori.currency.fx import FxRateNotFound, FxService, dict_lookup
from cori.currency.parsing import (
    AmountParseError, currency_from_evidence, format_amount, minor_unit_value, parse_amount, quantize,
    resolve_currency,
)
from cori.currency.reference import DEMO_CURRENCIES, minor_units

D = Decimal


# ----------------------------------------------------------------------------
# locale number formats
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("1,234.56", D("1234.56")),        # US / UK
    ("1.234,56", D("1234.56")),        # German / most of Europe
    ("1 234,56", D("1234.56")),        # French, regular space
    ("1 234,56", D("1234.56")),   # no-break space
    ("1 234,56", D("1234.56")),   # narrow no-break space (French typography)
    ("1 234,56", D("1234.56")),   # thin space
    ("1'234.56", D("1234.56")),        # Swiss
    ("12.450,00", D("12450.00")),      # the spec example
    ("1,234,567.89", D("1234567.89")),
    ("1.234.567,89", D("1234567.89")),
    ("1234.56", D("1234.56")),
    ("1234,56", D("1234.56")),
    ("1,250,000", D("1250000")),
    ("0.50", D("0.50")),
    ("42", D("42")),
])
def test_parse_locale_formats(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("EUR 12.450,00", D("12450.00")),
    ("12.450,00 EUR", D("12450.00")),
    ("$1,234.56", D("1234.56")),
    ("CHF 1'234.50", D("1234.50")),
    ("¥1,250,000", D("1250000")),
    ("R$ 2.400,00", D("2400.00")),
])
def test_parse_strips_currency_tokens(text, expected):
    assert parse_amount(text) == expected


def test_parse_negative_forms():
    assert parse_amount("(500.00)") == D("-500.00")
    assert parse_amount("-1.234,50") == D("-1234.50")


def test_single_separator_with_three_digits_is_thousands_unless_three_minor_units():
    assert parse_amount("1.234", "EUR") == D("1234")
    assert parse_amount("1,234", "USD") == D("1234")
    assert parse_amount("1.234", "KWD") == D("1.234")  # KWD has 3 decimals


def test_parse_none_and_empty():
    assert parse_amount(None) is None
    assert parse_amount("  ") is None
    assert parse_amount(D("1.5")) == D("1.5")


def test_parse_garbage_raises():
    with pytest.raises(AmountParseError):
        parse_amount("n/a")


def test_never_returns_float():
    assert isinstance(parse_amount("1,234.56"), Decimal)


# ----------------------------------------------------------------------------
# minor units
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("ccy, units", [("USD", 2), ("EUR", 2), ("JPY", 0), ("KWD", 3), ("BHD", 3), ("CLF", 4),
                                        ("INR", 2), ("BRL", 2)])
def test_minor_units(ccy, units):
    assert minor_units(ccy) == units


def test_demo_currencies_seeded():
    for c in ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "INR", "SGD", "MXN", "BRL"):
        assert c in DEMO_CURRENCIES


def test_quantize_respects_minor_units():
    assert quantize(D("1234.5"), "JPY") == D("1235")          # half up, 0 decimals
    assert quantize(D("1.23456"), "KWD") == D("1.235")
    assert quantize(D("10.005"), "USD") == D("10.01")
    assert str(quantize(D("7"), "EUR")) == "7.00"


def test_minor_unit_value():
    assert minor_unit_value("JPY") == D("1")
    assert minor_unit_value("USD") == D("0.01")
    assert minor_unit_value("KWD") == D("0.001")


def test_format_amount_always_shows_code():
    assert format_amount(D("4250"), "EUR") == "4,250.00 EUR"
    assert format_amount(D("3135704"), "JPY") == "3,135,704 JPY"
    assert format_amount(D("1.5"), "KWD") == "1.500 KWD"
    assert "unresolved" in format_amount(D("10"), None)


# ----------------------------------------------------------------------------
# currency evidence + ambiguity resolution
# ----------------------------------------------------------------------------
def test_explicit_iso_code_wins_over_symbol():
    assert currency_from_evidence("$ 1,000.00 CAD") == ("CAD", ["CAD"])
    assert currency_from_evidence("$", stated_code="USD") == ("USD", ["USD"])


def test_unambiguous_symbols():
    assert currency_from_evidence("€")[0] == "EUR"
    assert currency_from_evidence("£")[0] == "GBP"
    assert currency_from_evidence("R$")[0] == "BRL"     # longest symbol first, not "$" or "R"
    assert currency_from_evidence("US$")[0] == "USD"
    assert currency_from_evidence("S$")[0] == "SGD"


def test_ambiguous_symbols_return_candidates():
    iso, cands = currency_from_evidence("$")
    assert iso is None and {"USD", "CAD", "AUD", "SGD", "MXN"} <= set(cands)
    iso, cands = currency_from_evidence("kr")
    assert iso is None and {"SEK", "NOK", "DKK"} <= set(cands)


def test_no_evidence():
    assert currency_from_evidence(None) == (None, [])


def test_resolve_explicit_iso():
    r = resolve_currency("EUR", ["EUR"], ["GBP"], "GB")
    assert r.currency == "EUR" and r.method == "explicit_iso" and not r.ambiguous


def test_resolve_via_single_vendor_billing_currency():
    _, cands = currency_from_evidence("$")
    r = resolve_currency(None, cands, ["AUD"], "AU")
    assert r.currency == "AUD" and r.method == "vendor_known_currency"


def test_resolve_flags_when_vendor_bills_in_several_matching_currencies():
    _, cands = currency_from_evidence("$")
    r = resolve_currency(None, cands, ["CAD", "USD"], "CA")
    assert r.currency is None and r.ambiguous and set(r.candidates) == {"CAD", "USD"}


def test_resolve_falls_back_to_vendor_country():
    _, cands = currency_from_evidence("$")
    r = resolve_currency(None, cands, [], "Canada")
    assert r.currency == "CAD" and r.method == "vendor_country"


def test_resolve_unresolved_when_nothing_matches():
    _, cands = currency_from_evidence("kr")
    r = resolve_currency(None, cands, ["EUR"], "DE")
    assert r.currency is None and r.ambiguous


def test_resolve_no_symbol_at_all():
    r = resolve_currency(None, [], ["USD"], "US")
    assert r.currency is None and r.ambiguous


# ----------------------------------------------------------------------------
# FX
# ----------------------------------------------------------------------------
@pytest.fixture
def fx():
    table = {
        "EUR": {date(2026, 1, 1): D("1.10"), date(2026, 1, 5): D("1.12")},
        "JPY": {date(2026, 1, 1): D("0.0067")},
        "GBP": {date(2026, 1, 1): D("1.25")},
    }
    return FxService(dict_lookup(table))


def test_fx_exact_date(fx):
    amount, used = fx.convert(D("100.00"), "EUR", "USD", date(2026, 1, 5))
    assert amount == D("112.00") and used.rate == D("1.12") and used.rate_date == date(2026, 1, 5)


def test_fx_uses_nearest_prior_date(fx):
    _, used = fx.convert(D("100.00"), "EUR", "USD", date(2026, 1, 4))
    assert used.rate == D("1.10") and used.rate_date == date(2026, 1, 1)
    assert used.requested_date == date(2026, 1, 4)


def test_fx_same_currency_is_identity(fx):
    amount, used = fx.convert(D("10.00"), "USD", "USD", date(2026, 1, 1))
    assert amount == D("10.00") and used.rate == 1


def test_fx_cross_rate_via_usd_and_target_minor_units(fx):
    amount, used = fx.convert(D("100.00"), "GBP", "JPY", date(2026, 1, 2))
    assert used.rate == D("1.25") / D("0.0067")
    assert amount == quantize(D("100.00") * used.rate, "JPY") and amount == amount.to_integral_value()


def test_fx_missing_rate_raises(fx):
    with pytest.raises(FxRateNotFound):
        fx.convert(D("1"), "EUR", "USD", date(2025, 12, 31))  # before the first rate
    with pytest.raises(FxRateNotFound):
        fx.convert(D("1"), "CHF", "USD", date(2026, 1, 1))
