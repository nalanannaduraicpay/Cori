"""Anomaly agent: compares the invoice with historical baselines.

* Total vs baseline (median / mean / std / p95 in base currency USD, plus the
  native-currency baseline for display). Cold start falls back
  matter -> client -> vendor -> global threshold.
* Rate check: unit price per timekeeper (or description) vs the matter's
  historical average in base currency.
* Frequency check: invoices from this vendor on this matter in the window.
* Similar invoices: VECTOR_DISTANCE over line-item description embeddings.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, median, pstdev

from cori.config import AnomalyConfig, Settings
from cori.currency.parsing import format_amount, quantize
from cori.db import repository as repo
from cori.llm.provider import get_embedder
from cori.models.schemas import (
    AnomalyFlag, AnomalyResult, Baseline, NormalizedInvoice, Severity, SimilarInvoice, ValidationResult,
)

TWO = Decimal("0.01")


def percentile(values: list[Decimal], pct: float) -> Decimal:
    """Linear-interpolated percentile (same as numpy's default)."""
    s = sorted(values)
    if not s:
        raise ValueError("no values")
    k = Decimal(len(s) - 1) * Decimal(str(pct)) / 100  # Decimal, so no float error leaks into money
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def build_baseline(level: str, key, rows: list[dict], cfg: AnomalyConfig, base_ccy: str,
                   native_ccy: str | None, tried: list[str]) -> Baseline:
    vals = [r["total_usd"] for r in rows]
    native = [r["total"] for r in rows if native_ccy and r.get("currency") == native_ccy]
    b = Baseline(level=level, level_key=str(key) if key is not None else None, count=len(vals),
                 base_currency=base_ccy, native_currency=native_ccy, native_count=len(native), levels_tried=tried)
    if vals:
        b.median_base = quantize(Decimal(median(vals)), base_ccy)
        b.mean_base = quantize(Decimal(mean(vals)), base_ccy)
        b.std_base = quantize(pstdev(vals), base_ccy)
        b.p95_base = quantize(percentile(vals, cfg.percentile), base_ccy)
    if native:
        b.native_median = quantize(Decimal(median(native)), native_ccy)
        b.native_p95 = quantize(percentile(native, cfg.percentile), native_ccy)
    return b


def choose_level(candidates: list[tuple[str, object, list[dict]]], min_count: int) -> tuple[str, object, list[dict], list[str]]:
    """First level with at least ``min_count`` invoices; else ``global``."""
    tried = []
    for level, key, rows in candidates:
        tried.append(f"{level}:{len(rows)}")
        if key is not None and len(rows) >= min_count:
            return level, key, rows, tried
    return "global", None, [], tried


def check_total(total_base: Decimal, b: Baseline, cfg: AnomalyConfig, label: str) -> tuple[list[AnomalyFlag], float | None]:
    flags: list[AnomalyFlag] = []
    z = None
    bc = b.base_currency
    if b.level == "global":
        limit = Decimal(str(cfg.global_max_total_usd))
        if total_base > limit:
            flags.append(AnomalyFlag(code="TOTAL_ABOVE_GLOBAL_THRESHOLD", severity=Severity.MEDIUM,
                                     message=f"No usable history; total {format_amount(total_base, bc)} exceeds the "
                                             f"global threshold of {format_amount(limit, bc)}."))
        return flags, None
    if b.std_base and b.std_base > 0:
        z = float((total_base - b.mean_base) / b.std_base)
    if z is not None and z > cfg.z_score:
        flags.append(AnomalyFlag(code="TOTAL_ZSCORE_HIGH", severity=Severity.HIGH,
                                 message=f"Total {format_amount(total_base, bc)} is {z:.1f} standard deviations above "
                                         f"the {label} mean of {format_amount(b.mean_base, bc)}.",
                                 details={"z_score": round(z, 2), "threshold": cfg.z_score}))
    if b.p95_base is not None and total_base > b.p95_base:
        flags.append(AnomalyFlag(code="TOTAL_ABOVE_P95", severity=Severity.MEDIUM,
                                 message=f"Total {format_amount(total_base, bc)} is above the {label} "
                                         f"{cfg.percentile:g}th percentile of {format_amount(b.p95_base, bc)} "
                                         f"(median {format_amount(b.median_base, bc)}, n={b.count}).",
                                 details={"p95": str(b.p95_base), "median": str(b.median_base)}))
    return flags, z


def check_rates(inv: NormalizedInvoice, history: list[dict], fx_rate: Decimal, base_ccy: str,
                cfg: AnomalyConfig, min_history: int = 3) -> list[AnomalyFlag]:
    by_tk: dict[str, list[Decimal]] = defaultdict(list)
    by_desc: dict[str, list[Decimal]] = defaultdict(list)
    for h in history:
        if h.get("unit_price") is None or not h.get("total") or h.get("total_usd") is None:
            continue
        unit_base = h["unit_price"] * h["total_usd"] / h["total"]  # invoice-level FX
        if h.get("timekeeper"):
            by_tk[h["timekeeper"].strip().lower()].append(unit_base)
        if h.get("description"):
            by_desc[h["description"].strip().lower()].append(unit_base)
    flags = []
    limit = Decimal(1) + Decimal(str(cfg.rate_spike_pct)) / 100
    for li in inv.line_items:
        if li.unit_price is None:
            continue
        key, series = None, []
        if li.timekeeper and len(by_tk.get(li.timekeeper.lower(), [])) >= min_history:
            key, series = f"timekeeper {li.timekeeper}", by_tk[li.timekeeper.lower()]
        elif li.description and len(by_desc.get(li.description.lower(), [])) >= min_history:
            key, series = f"'{li.description}'", by_desc[li.description.lower()]
        if not series:
            continue
        avg = sum(series, Decimal(0)) / len(series)
        current = li.unit_price * fx_rate
        if current > avg * limit:
            pct = (current / avg - 1) * 100
            sev = Severity.HIGH if pct > Decimal(str(cfg.rate_spike_pct * 3)) else Severity.MEDIUM
            flags.append(AnomalyFlag(code="RATE_SPIKE", severity=sev,
                                     message=f"Rate for {key} is {format_amount(current, base_ccy)}/unit, "
                                             f"{pct:.0f}% above the matter's historical average of "
                                             f"{format_amount(avg, base_ccy)} (threshold {cfg.rate_spike_pct:g}%).",
                                     details={"current_base": str(quantize(current, base_ccy)),
                                              "historical_avg_base": str(quantize(avg, base_ccy)),
                                              "history_count": len(series)}))
    return flags


def check_frequency(history_dates: list[date], invoice_date: date, cfg: AnomalyConfig) -> AnomalyFlag | None:
    window = timedelta(days=cfg.frequency_window_days)
    recent = sum(1 for d in history_dates if invoice_date - window <= d <= invoice_date) + 1  # + this invoice
    if history_dates:
        span = max((max(history_dates) - min(history_dates)).days, cfg.frequency_window_days)
        expected = len(history_dates) * cfg.frequency_window_days / span
    else:
        expected = 0.0
    threshold = max(cfg.frequency_min_count, math.ceil(cfg.frequency_multiplier * expected))
    if recent > threshold:
        return AnomalyFlag(code="BILLING_FREQUENCY_HIGH", severity=Severity.MEDIUM,
                           message=f"{recent} invoices from this vendor on this matter within "
                                   f"{cfg.frequency_window_days} days (usual about {expected:.1f}; threshold {threshold}).",
                           details={"recent": recent, "expected": round(expected, 2), "threshold": threshold})
    return None


def embedding_text(inv: NormalizedInvoice) -> str:
    return "; ".join(li.description for li in inv.line_items if li.description)


def run_anomaly(invoice_id: int, inv: NormalizedInvoice, validation: ValidationResult,
                settings: Settings) -> AnomalyResult:
    cfg, base_ccy = settings.anomaly, settings.base_currency
    vendor, matter = validation.vendor, validation.matter
    result = AnomalyResult()
    native_ccy = validation.currency.currency if validation.currency else None

    candidates = [
        ("matter", matter["matter_id"] if matter else None,
         repo.get_historical_totals("matter", matter["matter_id"], invoice_id) if matter else []),
    ]
    client_id = matter["client_id"] if matter else (validation.client or {}).get("client_id")
    level, key, rows, tried = choose_level(candidates, cfg.cold_start_min)
    if level == "global" and client_id is not None:
        candidates.append(("client", client_id, repo.get_historical_totals("client", client_id, invoice_id)))
        level, key, rows, tried = choose_level(candidates, cfg.cold_start_min)
    if level == "global" and vendor:
        candidates.append(("vendor", vendor["vendor_id"],
                           repo.get_historical_totals("vendor", vendor["vendor_id"], invoice_id)))
        level, key, rows, tried = choose_level(candidates, cfg.cold_start_min)
    baseline = build_baseline(level, key, rows, cfg, base_ccy, native_ccy, tried)
    result.baseline = baseline
    label = {"matter": f"matter {matter['matter_number']}" if matter else "matter",
             "client": "client-level", "vendor": "vendor-level", "global": "global"}[level]

    if level != "matter":
        result.flags.append(AnomalyFlag(
            code="COLD_START_BASELINE", severity=Severity.LOW,
            message=f"Matter has fewer than {cfg.cold_start_min} historical invoices; "
                    f"used {label} baseline ({baseline.count} invoices).",
            details={"levels_tried": tried}))

    default_ccy = (vendor or {}).get("default_currency")
    if native_ccy and default_ccy and native_ccy != default_ccy:
        # not-a-billing-currency is a validation failure; this note covers allowed-but-unusual
        result.flags.append(AnomalyFlag(
            code="CURRENCY_NOT_VENDOR_DEFAULT", severity=Severity.LOW,
            message=f"Billed in {native_ccy}; {vendor['name']} normally bills in {default_ccy}.",
            details={"invoice_currency": native_ccy, "vendor_default_currency": default_ccy}))

    if validation.total_base is None:
        result.skipped_reason = "Total could not be converted to base currency (currency unresolved or no FX rate)."
    else:
        flags, z = check_total(validation.total_base, baseline, cfg, label)
        result.flags.extend(flags)
        result.z_score = round(z, 2) if z is not None else None
        if matter and validation.fx:
            result.flags.extend(check_rates(inv, repo.get_line_item_history(matter["matter_id"]),
                                            validation.fx.rate, base_ccy, cfg))
    if vendor and matter and inv.invoice_date:
        f = check_frequency(repo.get_vendor_matter_invoice_dates(vendor["vendor_id"], matter["matter_id"], invoice_id),
                            inv.invoice_date, cfg)
        if f:
            result.flags.append(f)

    # similar invoices via vector search
    text = embedding_text(inv)
    if text:
        embedder = get_embedder()
        vec = embedder.embed([text])[0]
        repo.upsert_invoice_embedding(invoice_id, vec, embedder.model_name)
        sim_level = level if level != "global" else ("vendor" if vendor else "global")
        sim_key = key if level != "global" else (vendor["vendor_id"] if vendor else None)
        rows = repo.find_similar_invoices(vec, sim_level, sim_key, invoice_id, cfg.similar_invoices_k)
        result.similar_invoices = [SimilarInvoice(invoice_id=r["invoice_id"], invoice_number=r.get("invoice_number"),
                                                  vendor_name=r.get("vendor_name"), invoice_date=r.get("invoice_date"),
                                                  currency=r.get("currency"), total=r.get("total"),
                                                  total_base=r.get("total_usd"), distance=round(r["distance"], 4))
                                   for r in rows]
        bases = [s.total_base for s in result.similar_invoices if s.total_base is not None]
        if bases:
            result.similar_avg_base = quantize(sum(bases, Decimal(0)) / len(bases), base_ccy)
    return result
