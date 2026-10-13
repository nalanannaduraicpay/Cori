"""Guardrail: flag invoice text that looks like instructions aimed at AI
systems. Invoice content is always untrusted; this check makes attempts
visible and forces human review.

It runs as its own graph step (after the extractors, before consensus) so the
flag is reported even when extraction or consensus fails. It scans the PDF
text layer (which includes white / tiny "hidden" text) plus every
extractor's raw output, so instructions that only exist in a page image are
caught once a vision model transcribes them."""
from __future__ import annotations

import re

from cori.models.schemas import CheckResult, Severity

_PATTERNS = [
    r"\b(ai|a\.i\.|llm|gpt|claude|chatbot|assistant|language model)\b.{0,40}\b(ignore|disregard|override|approve|skip|bypass)",
    r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|all|above|earlier)\b.{0,30}\b(instructions?|checks?|rules?|prompts?)",
    r"\bsystem\s*(prompt|override|message|instruction)",
    r"\b(approve|validate|mark)\b.{0,40}\b(this invoice|for payment|as (valid|validated|approved))",
    r"\bdo not (flag|review|check)\b",
    r"\byou (are|must|should) now\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
]
_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def scan_for_injection(text: str) -> list[str]:
    hits = []
    flat = re.sub(r"\s+", " ", text or "")
    for rx in _RE:
        for m in rx.finditer(flat):
            hits.append(m.group(0)[:160])
    return sorted(set(hits))


def injection_check(text: str, extractor_outputs: list[str] | None = None) -> CheckResult:
    hits = scan_for_injection("\n".join([text or "", *(o for o in extractor_outputs or [] if o)]))
    if hits:
        return CheckResult(name="content_safety", passed=False, code="PROMPT_INJECTION_SUSPECTED",
                           severity=Severity.HIGH,
                           message=f"Invoice contains {len(hits)} instruction-like passage(s) aimed at AI systems; "
                                   f"content was treated as data only.",
                           details={"matches": hits})
    return CheckResult(name="content_safety", passed=True, message="No instruction-like text aimed at AI systems.")
