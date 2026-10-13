"""Extraction path without an LLM: sample PDFs -> PDF text/images -> mock
provider -> schema -> normalization. Also checks the samples themselves."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from cori.agents.extractors import run_extractor
from cori.config import ExtractorConfig
from cori.consensus.normalize import normalize_extraction
from cori.llm.mock import MockProvider, parse_invoice_text
from cori.pdf import load_document

MOCK = {"provider": "mock", "model": "mock"}


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")


def test_expected_outcomes_cover_every_required_case(samples_dir):
    expected = json.loads((samples_dir / "expected_outcomes.json").read_text(encoding="utf-8"))
    names = " ".join(e["file"] for e in expected)
    for case in ("clean_usd", "clean_eur", "clean_gbp", "clean_jpy", "anomaly", "duplicate", "bank_details",
                 "do_not_sum", "ambiguous_dollar", "european_number", "cold_start", "rate_spike",
                 "prompt_injection", "skewed_scan"):
        assert case in names
    assert all((samples_dir / e["file"]).exists() for e in expected)


def test_european_format_sample_parses_to_decimal(samples_dir):
    doc = load_document(str(samples_dir / "10_european_number_format_eur.pdf"), with_images=False)
    assert "," in doc.text and "EUR" in doc.text
    n = normalize_extraction(parse_invoice_text(doc.text))
    assert n.currency == "EUR" and isinstance(n.total_amount, Decimal)
    assert n.subtotal + n.tax_amount == n.total_amount
    assert sum(li.amount for li in n.line_items) == n.subtotal


def test_vision_and_text_paths_agree_on_clean_invoice(samples_dir):
    path = str(samples_dir / "04_clean_jpy_tanaka.pdf")
    doc = load_document(path, with_images=True, dpi=72, max_pages=1)
    assert doc.images and doc.images[0][:4] == b"\x89PNG"
    a = run_extractor("extractor_a", ExtractorConfig(method="vision", prompt="extractor_vision.v1", **MOCK), doc)
    b = run_extractor("extractor_b", ExtractorConfig(method="text", prompt="extractor_text.v1", **MOCK), doc)
    assert a.status == b.status == "ok"
    assert a.normalized.total_amount == b.normalized.total_amount
    assert a.normalized.currency == "JPY" and a.normalized.total_amount == a.normalized.total_amount.to_integral_value()
    assert a.prompt_version.startswith("extractor_vision.v1#") and b.prompt_version.startswith("extractor_text.v1#")


def test_image_only_scan_fails_text_extractor_cleanly(samples_dir):
    doc = load_document(str(samples_dir / "14_low_quality_skewed_scan.pdf"), with_images=False)
    res = run_extractor("extractor_b", ExtractorConfig(method="text", prompt="extractor_text.v1", **MOCK), doc)
    assert res.status == "failed" and "text layer" in res.error


def test_schema_failure_is_retried_once_then_marked_failed(samples_dir, monkeypatch):
    calls = []

    def always_invalid(self, *args, **kwargs):
        from cori.llm.provider import SchemaValidationError
        calls.append(1)
        raise SchemaValidationError("bad json", raw_text="{oops")

    monkeypatch.setattr(MockProvider, "extract_structured", always_invalid)
    doc = load_document(str(samples_dir / "01_clean_usd_harrington.pdf"), with_images=False)
    res = run_extractor("extractor_b", ExtractorConfig(method="text", prompt="extractor_text.v1", **MOCK), doc)
    assert res.status == "failed" and res.attempts == 2 and len(calls) == 2 and res.raw_text == "{oops"


def test_injection_sample_contains_hidden_text_in_text_layer(samples_dir):
    doc = load_document(str(samples_dir / "13_prompt_injection.pdf"), with_images=False)
    from cori.agents.injection import scan_for_injection
    assert len(scan_for_injection(doc.text)) >= 2
