"""Real LLM extraction. Skipped unless the provider's key is set.

    LLM_PROVIDER=azure (or anthropic) with keys in .env, then: pytest -m llm
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from cori.agents.extractors import run_extractor
from cori.config import ExtractorConfig
from cori.pdf import load_document

pytestmark = pytest.mark.llm

PROVIDERS = [
    pytest.param("azure", marks=pytest.mark.skipif(not (os.getenv("AZURE_OPENAI_API_KEY")
                                                        and os.getenv("AZURE_OPENAI_ENDPOINT")),
                                                   reason="AZURE_OPENAI_API_KEY / ENDPOINT not set")),
    pytest.param("anthropic", marks=pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"),
                                                       reason="ANTHROPIC_API_KEY not set")),
]


@pytest.fixture
def live(monkeypatch, request):
    monkeypatch.setenv("LLM_PROVIDER", request.param)
    from cori.llm.provider import _cached_langchain
    _cached_langchain.cache_clear()
    return request.param


@pytest.mark.parametrize("live", PROVIDERS, indirect=True)
@pytest.mark.parametrize("method, prompt", [("vision", "extractor_vision.v1"), ("text", "extractor_text.v1")])
def test_live_extraction_european_format(live, method, prompt, samples_dir):
    doc = load_document(str(samples_dir / "10_european_number_format_eur.pdf"), with_images=method == "vision")
    res = run_extractor("live", ExtractorConfig(method=method, provider=live, prompt=prompt), doc)
    assert res.status == "ok", res.error
    n = res.normalized
    assert n.currency == "EUR" and isinstance(n.total_amount, Decimal)
    assert n.subtotal + n.tax_amount == n.total_amount


@pytest.mark.parametrize("live", PROVIDERS, indirect=True)
def test_live_extractor_ignores_injected_instructions(live, samples_dir):
    doc = load_document(str(samples_dir / "13_prompt_injection.pdf"), with_images=False)
    res = run_extractor("live", ExtractorConfig(method="text", provider=live, prompt="extractor_text.v1"), doc)
    assert res.status == "ok" and res.normalized.total_amount is not None
