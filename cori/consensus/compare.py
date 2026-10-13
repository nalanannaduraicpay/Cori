"""Consensus engine: deterministic field-by-field comparison of extractor
outputs. Critical fields must be unanimous; non-critical need a majority."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Callable

from rapidfuzz import fuzz

from cori.config import ConsensusConfig
from cori.consensus.normalize import normalize_vendor_name
from cori.models.schemas import (
    ConsensusResult, Disagreement, ExtractorResult, NormalizedInvoice,
)

SCALAR_FIELDS = [
    "vendor_name", "vendor_id", "vendor_country", "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "tax_amount", "total_amount", "client_number", "matter_number", "bank_name", "bank_account",
    "swift_bic",
]

def currency_key(n: NormalizedInvoice) -> str | None:
    """ISO code when unambiguous, otherwise the ambiguous candidate set, e.g. '?CAD/USD/...'."""
    if n.currency:
        return n.currency
    if n.currency_candidates:
        return "?" + "/".join(sorted(n.currency_candidates))
    return None


def _value(n: NormalizedInvoice, field: str) -> Any:
    if field == "currency":
        return currency_key(n)
    return getattr(n, field)


def _display(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _equivalent(field: str, a: Any, b: Any, ra: NormalizedInvoice, rb: NormalizedInvoice, threshold: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if field == "vendor_name":
        ka, kb = ra.vendor_name_key or "", rb.vendor_name_key or ""
        return ka == kb or fuzz.token_sort_ratio(ka, kb) >= threshold
    if field == "bank_name":
        return fuzz.token_sort_ratio(str(a).lower(), str(b).lower()) >= threshold
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b
    return a == b


def _cluster(field: str, records: dict[str, NormalizedInvoice], threshold: int) -> list[list[str]]:
    clusters: list[list[str]] = []
    for name, rec in records.items():
        v = _value(rec, field)
        for cl in clusters:
            rep = records[cl[0]]
            if _equivalent(field, _value(rep, field), v, rep, rec, threshold):
                cl.append(name)
                break
        else:
            clusters.append([name])
    return sorted(clusters, key=len, reverse=True)


def _confidence(result: ExtractorResult, field: str) -> float:
    if result.raw:
        for fc in result.raw.field_confidence:
            if fc.field == field:
                return fc.confidence
    return 0.5


def _line_signature(n: NormalizedInvoice) -> tuple:
    return tuple(sorted((li.amount if li.amount is not None else Decimal(-1)) for li in n.line_items))


def compare(results: list[ExtractorResult], cfg: ConsensusConfig) -> ConsensusResult:
    ok = [r for r in results if r.status == "ok" and r.normalized is not None]
    failed = [r.name for r in results if r not in ok]
    records = {r.name: r.normalized for r in ok}
    by_name = {r.name: r for r in ok}
    critical = set(cfg.critical_fields)

    if len(ok) < 2:
        return ConsensusResult(
            passed=False, successful_extractors=[r.name for r in ok], failed_extractors=failed,
            agreed=ok[0].normalized if ok else None,
            notes=[f"Only {len(ok)} of {len(results)} extractors produced valid output; at least 2 must agree."])

    agreed_values: dict[str, Any] = {}
    field_status: dict[str, str] = {}
    disagreements: list[Disagreement] = []
    passed = True
    n = len(ok)

    for field in SCALAR_FIELDS:
        clusters = _cluster(field, records, cfg.fuzzy_threshold)
        values = {name: _display(_value(rec, field)) for name, rec in records.items()}
        top = clusters[0]
        top_value = _value(records[top[0]], field)
        is_critical = field in critical
        if len(top) == n:
            field_status[field] = "missing" if top_value is None else "unanimous"
            agreed_values[field] = top_value
            continue
        if not is_critical and len(top) * 2 > n:
            field_status[field] = "majority"
            agreed_values[field] = top_value
            disagreements.append(Disagreement(field=field, critical=False, values=values,
                                              resolution=f"majority ({len(top)} of {n}): {_display(top_value)}"))
            continue
        # no agreement
        field_status[field] = "disagree"
        if is_critical:
            passed = False
            agreed_values[field] = None
            resolution = "critical field not unanimous - needs human review"
        else:
            best = max(ok, key=lambda r: _confidence(r, field))
            agreed_values[field] = _value(best.normalized, field)
            resolution = f"no majority; used highest-confidence value from {best.name} (low confidence)"
        disagreements.append(Disagreement(field=field, critical=is_critical, values=values, resolution=resolution))

    # line items: count + amounts must match; wording differences are fine
    sigs = {name: _line_signature(rec) for name, rec in records.items()}
    sig_counts = Counter(sigs.values())
    top_sig, top_count = sig_counts.most_common(1)[0]
    if top_count == n:
        field_status["line_items"] = "unanimous"
    else:
        field_status["line_items"] = "disagree"
        if cfg.line_items_critical:
            passed = False
        disagreements.append(Disagreement(
            field="line_items", critical=cfg.line_items_critical,
            values={name: f"{len(rec.line_items)} lines; amounts {', '.join(str(a) for a in sigs[name])}"
                    for name, rec in records.items()},
            resolution="line item count/amounts differ"))
    line_source = next(name for name, s in sigs.items() if s == top_sig)

    # build the agreed record
    first = records[ok[0].name]
    cur_src = next((rec for rec in records.values() if currency_key(rec) == agreed_values.get("currency")), first)
    agreed = NormalizedInvoice(
        **{f: agreed_values.get(f) for f in SCALAR_FIELDS if f not in ("currency", "vendor_name")},
        vendor_name=agreed_values.get("vendor_name"),
        vendor_name_key=normalize_vendor_name(agreed_values.get("vendor_name")),
        currency=cur_src.currency if agreed_values.get("currency") else None,
        currency_candidates=cur_src.currency_candidates if agreed_values.get("currency") else [],
        currency_evidence=cur_src.currency_evidence,
        line_items=records[line_source].line_items,
        parse_errors=sorted({e for rec in records.values() for e in rec.parse_errors}),
    )
    missing_critical = [f for f in critical if field_status.get(f) == "missing"
                        and f not in ("bank_account", "swift_bic")]
    notes = []
    if missing_critical:
        notes.append("Critical fields not found by any extractor: " + ", ".join(sorted(missing_critical)))
    return ConsensusResult(passed=passed and not missing_critical,
                           successful_extractors=[r.name for r in ok], failed_extractors=failed,
                           agreed=agreed, field_status=field_status, disagreements=disagreements, notes=notes)
