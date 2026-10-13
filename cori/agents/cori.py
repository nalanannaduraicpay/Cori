"""Cori - the lead agent.

Routing is a FIXED RULE in code. An invoice is advanced to the approval
section only if:
  1. consensus passed on all critical fields (>= 2 extractors succeeded), and
  2. every validation check passed, and
  3. no anomaly flag has MEDIUM or HIGH severity.
The content guardrail (instruction-like text aimed at AI systems) is reported
as a HIGH validation-level reason and always blocks advancement.
Otherwise it is NEEDS_REVIEW with every reason listed. The LLM only writes
the human-readable summary of reasons; it never influences the decision.
Cori never approves payment.
"""
from __future__ import annotations

import json
import logging

from cori.currency.parsing import format_amount
from cori.llm.provider import ProviderUnavailable, get_provider
from cori.models.schemas import (
    AnomalyResult, CheckResult, ConsensusResult, DecisionStatus, ExtractorResult, Reason, Severity,
    ValidationResult,
)
from cori.prompts.registry import load_prompt

log = logging.getLogger(__name__)
SUMMARY_PROMPT = "cori_summary.v1"
_SEV_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2, Severity.INFO: 3}


def route(extractions: list[ExtractorResult], consensus: ConsensusResult | None,
          validation: ValidationResult | None, anomaly: AnomalyResult | None,
          guardrail: CheckResult | None = None) -> tuple[DecisionStatus, list[Reason]]:
    reasons: list[Reason] = []

    if guardrail is not None and not guardrail.passed:
        reasons.append(Reason(code=guardrail.code or "CONTENT_GUARDRAIL", source="guardrail",
                              severity=guardrail.severity, message=guardrail.message))

    ok = [e for e in extractions if e.status == "ok"]
    if len(ok) < 2:
        failed = "; ".join(f"{e.name}: {e.error}" for e in extractions if e.status != "ok")
        reasons.append(Reason(code="EXTRACTION_INSUFFICIENT", source="extraction", severity=Severity.HIGH,
                              message=f"Only {len(ok)} of {len(extractions)} extractors produced valid output "
                                      f"(need at least 2 to agree). {failed}".strip()))
    if consensus is not None and len(ok) >= 2:
        for d in consensus.disagreements:
            if not d.critical:
                continue
            vals = ", ".join(f"{k}={v}" for k, v in d.values.items())
            code = "LINE_ITEMS_DISAGREEMENT" if d.field == "line_items" else "CONSENSUS_CRITICAL_DISAGREEMENT"
            reasons.append(Reason(code=code, source="consensus", severity=Severity.HIGH,
                                  message=f"Extractors disagree on critical field '{d.field}': {vals}."))
        for note in consensus.notes:
            reasons.append(Reason(code="CONSENSUS_INCOMPLETE", source="consensus", severity=Severity.HIGH,
                                  message=note))
        if not consensus.passed and not any(r.source == "consensus" for r in reasons):
            reasons.append(Reason(code="CONSENSUS_FAILED", source="consensus", severity=Severity.HIGH,
                                  message="Consensus did not pass on all critical fields."))

    if validation is None:
        if not any(r.code == "EXTRACTION_INSUFFICIENT" for r in reasons):
            reasons.append(Reason(code="VALIDATION_NOT_RUN", source="validation", severity=Severity.HIGH,
                                  message="Validation could not run because no agreed record was available."))
    else:
        for c in validation.checks:
            if not c.passed:
                reasons.append(Reason(code=c.code or c.name.upper(), source="validation",
                                      severity=c.severity, message=c.message))

    if anomaly is not None:
        for f in anomaly.flags:
            reasons.append(Reason(code=f.code, source="anomaly", severity=f.severity, message=f.message,
                                  blocking=f.severity in (Severity.MEDIUM, Severity.HIGH)))

    advance = (
        consensus is not None and consensus.passed and len(ok) >= 2
        and validation is not None and validation.passed
        and (guardrail is None or guardrail.passed)
        and not any(r.blocking for r in reasons)
    )
    reasons.sort(key=lambda r: (not r.blocking, _SEV_ORDER[r.severity]))
    return (DecisionStatus.ADVANCED_TO_APPROVAL if advance else DecisionStatus.NEEDS_REVIEW), reasons


def summary_facts(decision: DecisionStatus, reasons: list[Reason], consensus: ConsensusResult | None,
                  validation: ValidationResult | None, anomaly: AnomalyResult | None) -> dict:
    agreed = consensus.agreed if consensus else None
    ccy = validation.currency.currency if validation and validation.currency else None
    b = anomaly.baseline if anomaly else None
    return {
        "decision": decision.value,
        "extractors_succeeded": len(consensus.successful_extractors) if consensus else 0,
        "vendor": (validation.vendor or {}).get("name") if validation and validation.vendor else (agreed.vendor_name if agreed else None),
        "invoice_number": agreed.invoice_number if agreed else None,
        "matter_number": agreed.matter_number if agreed else None,
        "total": format_amount(agreed.total_amount, ccy) if agreed and agreed.total_amount is not None else None,
        "total_base": format_amount(validation.total_base, b.base_currency if b else "USD")
        if validation and validation.total_base is not None else None,
        "baseline_level": b.level if b else None,
        "baseline_median": format_amount(b.median_base, b.base_currency) if b and b.median_base else None,
        "baseline_p95": format_amount(b.p95_base, b.base_currency) if b and b.p95_base else None,
        "similar_invoices_avg": format_amount(anomaly.similar_avg_base, "USD") if anomaly and anomaly.similar_avg_base else None,
        "similar_invoices_count": len(anomaly.similar_invoices) if anomaly else 0,
        "reasons": [{"code": r.code, "severity": r.severity.value, "blocking": r.blocking, "message": r.message}
                    for r in reasons],
    }


def template_summary(facts: dict) -> str:
    blocking = [r for r in facts["reasons"] if r["blocking"]]
    notes = [r for r in facts["reasons"] if not r["blocking"]]
    if facts["decision"] == DecisionStatus.ADVANCED_TO_APPROVAL.value:
        level = facts.get("baseline_level")
        where = f"matter {facts['matter_number']}" if level == "matter" else f"the {level}-level baseline"
        s = (f"Advanced: {facts['extractors_succeeded']} extractors agreed on all critical fields, validation passed, "
             f"total of {facts['total']} is within the normal range for {where}")
        if facts.get("baseline_median"):
            s += f" (median {facts['baseline_median']})"
        s += "."
        if facts.get("similar_invoices_avg"):
            scope = "matter" if level == "matter" else f"{level} (level used for the baseline)"
            s += (f" The closest {facts['similar_invoices_count']} invoices for this {scope} averaged "
                  f"{facts['similar_invoices_avg']}.")
        if notes:
            s += " Note: " + " ".join(r["message"] for r in notes)
        return s
    parts = [f"{r['message']}" for r in blocking]
    return f"Needs review ({len(blocking)} issue{'s' if len(blocking) != 1 else ''}): " + " ".join(parts)


def summarize(facts: dict, use_llm: bool) -> tuple[str, str, str]:
    """Return (summary, model_name, prompt_version)."""
    prompt = load_prompt(SUMMARY_PROMPT)
    if use_llm:
        try:
            provider = get_provider("default", "default")
            text = provider.complete(prompt.text, json.dumps(facts, ensure_ascii=False, indent=2)).strip()
            expected = "Advanced" if facts["decision"] == DecisionStatus.ADVANCED_TO_APPROVAL.value else "Needs review"
            if text and text.startswith(expected):  # guard: summary must match the fixed decision
                return text, provider.model_name, prompt.version_tag
            log.warning("LLM summary did not match decision; using template")
        except ProviderUnavailable:
            pass
        except Exception:
            log.exception("summary LLM failed; using template")
    return template_summary(facts), "template", prompt.version_tag
