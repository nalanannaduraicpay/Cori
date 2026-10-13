"""Consensus engine: deterministic comparison of extractor outputs."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from cori.config import ConsensusConfig
from cori.consensus.compare import compare
from cori.models.schemas import NormalizedLineItem
from tests.conftest import make_invoice, make_result

CFG = ConsensusConfig()


def test_unanimous_extractors_pass(invoice):
    res = compare([make_result("a", invoice), make_result("b", invoice.model_copy())], CFG)
    assert res.passed and not res.disagreements
    assert res.agreed.total_amount == Decimal("1500.00")
    assert res.field_status["total_amount"] == "unanimous" and res.field_status["line_items"] == "unanimous"


def test_vendor_name_fuzzy_match_counts_as_agreement(invoice):
    b = invoice.model_copy(update={"vendor_name": "Harrington and Cole", "vendor_name_key": "harrington and cole"})
    c = invoice.model_copy(update={"vendor_name": "Harington & Cole LLP", "vendor_name_key": "harington and cole"})
    res = compare([make_result("a", invoice), make_result("b", b), make_result("c", c)], CFG)
    assert res.passed and res.field_status["vendor_name"] == "unanimous"


def test_fuzzy_threshold_is_configurable(invoice):
    c = invoice.model_copy(update={"vendor_name": "Harington & Cole LLP", "vendor_name_key": "harington and cole"})
    strict = ConsensusConfig(fuzzy_threshold=100)
    res = compare([make_result("a", invoice), make_result("c", c)], strict)
    assert not res.passed and res.disagreements[0].field == "vendor_name"


def test_critical_field_must_be_unanimous(invoice):
    b = invoice.model_copy(update={"total_amount": Decimal("1600.00")})
    c = invoice.model_copy()
    res = compare([make_result("a", invoice), make_result("b", b), make_result("c", c)], CFG)
    assert not res.passed  # 2 of 3 is not enough for a critical field
    d = next(d for d in res.disagreements if d.field == "total_amount")
    assert d.critical and d.values == {"a": "1500.00", "b": "1600.00", "c": "1500.00"}
    assert res.agreed.total_amount is None


def test_bank_details_are_critical(invoice):
    b = invoice.model_copy(update={"bank_account": "LV10BOCB0000123456789"})
    res = compare([make_result("a", invoice), make_result("b", b)], CFG)
    assert not res.passed and any(d.field == "bank_account" and d.critical for d in res.disagreements)


def test_non_critical_field_majority_is_enough(invoice):
    b = invoice.model_copy(update={"due_date": date(2026, 11, 15)})
    res = compare([make_result("a", invoice), make_result("b", b), make_result("c", invoice.model_copy())], CFG)
    assert res.passed
    d = next(d for d in res.disagreements if d.field == "due_date")
    assert not d.critical and "majority" in d.resolution and res.agreed.due_date == date(2026, 10, 31)


def test_non_critical_tie_uses_highest_confidence_and_still_passes(invoice):
    b = invoice.model_copy(update={"due_date": date(2026, 11, 15)})
    res = compare([make_result("a", invoice, confidence={"due_date": 0.4}),
                   make_result("b", b, confidence={"due_date": 0.95})], CFG)
    assert res.passed and res.agreed.due_date == date(2026, 11, 15)


def test_fewer_than_two_successful_extractors_fails(invoice):
    res = compare([make_result("a", invoice), make_result("b", None, status="failed")], CFG)
    assert not res.passed and res.failed_extractors == ["b"] and "at least 2" in res.notes[0]


def test_line_item_count_mismatch_fails(invoice):
    b = invoice.model_copy(update={"line_items": invoice.line_items[:1]})
    res = compare([make_result("a", invoice), make_result("b", b)], CFG)
    assert not res.passed and any(d.field == "line_items" for d in res.disagreements)


def test_line_item_wording_differences_are_fine(invoice):
    reworded = [li.model_copy(update={"description": (li.description or "") + " (cont.)"}) for li in invoice.line_items]
    res = compare([make_result("a", invoice), make_result("b", invoice.model_copy(update={"line_items": reworded}))], CFG)
    assert res.passed


def test_line_item_amount_mismatch_fails(invoice):
    items = list(invoice.line_items)
    items[1] = NormalizedLineItem(description="Research", amount=Decimal("550.00"))
    res = compare([make_result("a", invoice), make_result("b", invoice.model_copy(update={"line_items": items}))], CFG)
    assert not res.passed


def test_critical_field_missing_everywhere_fails(invoice):
    a = invoice.model_copy(update={"invoice_number": None})
    res = compare([make_result("a", a), make_result("b", a.model_copy())], CFG)
    assert not res.passed and "invoice_number" in res.notes[0]


def test_ambiguous_currency_agrees_but_stays_unresolved():
    a = make_invoice(currency=None, currency_candidates=["USD", "CAD", "AUD"], currency_evidence="$")
    res = compare([make_result("a", a), make_result("b", a.model_copy())], CFG)
    assert res.passed  # extractors agree they saw "$"; currency resolution happens in validation
    assert res.agreed.currency is None and set(res.agreed.currency_candidates) == {"USD", "CAD", "AUD"}


def test_currency_disagreement_is_critical(invoice):
    b = invoice.model_copy(update={"currency": "CAD", "currency_candidates": ["CAD"]})
    res = compare([make_result("a", invoice), make_result("b", b)], CFG)
    assert not res.passed and any(d.field == "currency" for d in res.disagreements)
