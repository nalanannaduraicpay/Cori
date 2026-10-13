"""Seed reference data and ~1,600 historical invoices (admin connection).

Deterministic (fixed RNG seed); dates are relative to today so baselines and
sample invoices always line up.
"""
from __future__ import annotations

import logging
import math
import random
from datetime import date, timedelta
from decimal import Decimal

from cori.currency.fx import FxService, dict_lookup
from cori.currency.parsing import quantize
from cori.currency.reference import CURRENCIES, DEMO_CURRENCIES
from cori.db import mapping as M
from cori.db import repository as repo
from cori.llm.provider import get_embedder
from cori.samples import demo_data as D

log = logging.getLogger(__name__)


def build_fx_table(start: date, end: date, rng: random.Random) -> dict[str, dict[date, Decimal]]:
    """Mean-reverting daily random walk around each currency's centre rate."""
    table: dict[str, dict[date, Decimal]] = {}
    days = (end - start).days + 1
    for ccy in DEMO_CURRENCIES:
        if ccy == "USD":
            continue
        centre = float(D.FX_CENTRES[ccy])
        x = centre
        series = {}
        for i in range(days):
            x = x + 0.05 * (centre - x) + centre * rng.gauss(0, 0.004)
            x = min(max(x, centre * 0.94), centre * 1.06)
            series[start + timedelta(days=i)] = Decimal(f"{x:.10f}")
        table[ccy] = series
    return table


def rate_in(ccy: str, base_rate: Decimal, vendor_ccy: str) -> Decimal:
    """Timekeeper rate expressed in another billing currency (rounded to whole units)."""
    if ccy == vendor_ccy:
        return base_rate
    converted = base_rate * D.FX_CENTRES[vendor_ccy] / D.FX_CENTRES[ccy]
    return converted.quantize(Decimal("1"))


def rate_multiplier(on: date, today: date) -> Decimal:
    return Decimal("1.04") if on >= today - timedelta(days=365) else Decimal("1.00")


def build_lines(rng: random.Random, vendor: D.Vendor, matter: D.Matter, ccy: str, target_subtotal: Decimal,
                on: date, today: date) -> list[dict]:
    pool = D.TRANSLATION_DESCRIPTIONS if vendor.vendor_id == "V015" else D.DESCRIPTIONS[matter.kind]
    k = rng.randint(2, 5)
    weights = [rng.uniform(0.5, 1.5) for _ in range(k)]
    total_w = sum(weights)
    lines = []
    for w in weights:
        tk = rng.choice(vendor.timekeepers)
        unit = quantize(rate_in(ccy, tk.rate, vendor.default_currency) * rate_multiplier(on, today), ccy)
        share = target_subtotal * Decimal(str(w / total_w))
        hours = max(Decimal("0.5"), (share / unit).quantize(Decimal("0.1")))
        lines.append({"description": rng.choice(pool), "timekeeper": tk.name, "quantity": hours,
                      "unit_price": unit, "amount": quantize(hours * unit, ccy)})
    return lines


def seed(n_per_matter: int = D.INVOICES_PER_MATTER, rng_seed: int = 42) -> dict:
    rng = random.Random(rng_seed)
    today = D.today()
    start = today - timedelta(days=D.HISTORY_DAYS + 10)

    # --- currencies + FX ---------------------------------------------------
    repo.admin_insert_many(M.CURRENCIES, [
        {"currency_code": c.code, "name": c.name, "minor_units": c.minor_units, "symbol": c.symbol}
        for c in CURRENCIES.values()])
    fx_table = build_fx_table(start, today, rng)
    repo.admin_insert_many(M.FX_RATES, [
        {"rate_date": d, "from_currency": ccy, "to_currency": "USD", "rate": r}
        for ccy, series in fx_table.items() for d, r in series.items()])
    fx = FxService(dict_lookup(fx_table))

    # --- vendors -------------------------------------------------------------
    repo.admin_insert_many(M.VENDORS, [
        {"vendor_id": v.vendor_id, "name": v.name, "country": v.country,
         "default_currency": v.default_currency, "status": "ACTIVE"} for v in D.VENDORS])
    repo.admin_insert_many(M.VENDOR_CURRENCIES, [
        {"vendor_id": v.vendor_id, "currency_code": c} for v in D.VENDORS for c in v.currencies])
    accounts = [{"account_id": i + 1, "vendor_id": v.vendor_id, "bank_name": v.bank_name,
                 "iban_or_account": v.account, "swift_bic": v.swift, "is_active": 1}
                for i, v in enumerate(D.VENDORS)]
    # an old, deactivated account for realism (must not be accepted)
    accounts.append({"account_id": 100, "vendor_id": "V002", "bank_name": "Old Street Savings Bank",
                     "iban_or_account": D.make_iban("GB", "OSSB40051299887766"), "swift_bic": "OSSBGB21",
                     "is_active": 0})
    repo.admin_insert_many(M.VENDOR_BANK_ACCOUNTS, accounts)

    # --- clients / matters -------------------------------------------------------
    repo.admin_insert_many(M.CLIENTS, [{"client_id": cid, "client_number": num, "name": name}
                                       for cid, num, name in D.CLIENTS])
    repo.admin_insert_many(M.MATTERS, [
        {"matter_id": m.matter_id, "client_id": D.CLIENT_BY_NUMBER[m.client_number][0],
         "matter_number": m.matter_number, "description": m.description, "status": m.status}
        for m in D.MATTERS])

    # --- historical invoices -------------------------------------------------
    invoices, lines_rows, embed_jobs = [], [], []
    seq: dict[str, int] = {v.vendor_id: 1000 for v in D.VENDORS}
    drafts = []
    for matter in D.MATTERS:
        if not matter.has_history:
            continue
        end = today - timedelta(days=D.HISTORY_GAP_DAYS)
        if matter.status == "CLOSED":
            end = today - timedelta(days=400)
        span = (end - (today - timedelta(days=D.HISTORY_DAYS))).days
        for _ in range(n_per_matter):
            on = end - timedelta(days=rng.randint(0, span))
            drafts.append((on, matter))
    drafts.sort(key=lambda x: x[0])

    def add_invoice(inv_id: int, vendor: D.Vendor, matter: D.Matter, ccy: str, on: date, number: str,
                    lines: list[dict]):
        subtotal = sum((l["amount"] for l in lines), Decimal(0))
        tax = quantize(subtotal * vendor.tax_rate, ccy)
        total = subtotal + tax
        total_usd, _ = fx.convert(total, ccy, "USD", on)
        invoices.append({"invoice_id": inv_id, "vendor_id": vendor.vendor_id, "matter_id": matter.matter_id,
                         "invoice_number": number, "invoice_date": on, "currency": ccy, "subtotal": subtotal,
                         "tax": tax, "total": total, "total_usd": total_usd, "status": M.STATUS_HISTORICAL,
                         "source": "HISTORICAL", "file_path": None, "original_filename": None,
                         "created_at": repo.now()})
        for l in lines:
            lines_rows.append({"line_id": len(lines_rows) + 1, "invoice_id": inv_id, **l})
        embed_jobs.append((inv_id, "; ".join(l["description"] for l in lines)))

    for inv_id, (on, matter) in enumerate(drafts, start=1):
        vendor = D.VENDOR_BY_ID[rng.choice(matter.vendors)]
        alternates = [c for c in vendor.currencies if c != vendor.default_currency]
        ccy = vendor.default_currency
        if alternates and rng.random() < 0.12:
            ccy = rng.choice(alternates)
        target_usd = matter.target_usd * Decimal(str(math.exp(rng.gauss(0, 0.3))))
        target_total = target_usd / fx.get_rate(ccy, "USD", on).rate
        target_subtotal = target_total / (1 + vendor.tax_rate)
        lines = build_lines(rng, vendor, matter, ccy, target_subtotal, on, today)
        seq[vendor.vendor_id] += 1
        add_invoice(inv_id, vendor, matter, ccy, on, f"{vendor.prefix}-{on.year}-{seq[vendor.vendor_id]:04d}", lines)

    # --- anchor invoice for the duplicate test --------------------------------
    a = D.ANCHOR_DUPLICATE
    av, am = D.VENDOR_BY_ID[a["vendor_id"]], D.MATTER_BY_NUMBER[a["matter_number"]]
    aon = today - timedelta(days=a["days_ago"])
    alines = []
    for desc, tk_name, hours in a["lines"]:
        tk = next(t for t in av.timekeepers if t.name == tk_name)
        unit = quantize(tk.rate * rate_multiplier(aon, today), a["currency"])
        alines.append({"description": desc, "timekeeper": tk_name, "quantity": hours, "unit_price": unit,
                       "amount": quantize(hours * unit, a["currency"])})
    add_invoice(len(invoices) + 1, av, am, a["currency"], aon, a["invoice_number"], alines)

    repo.admin_insert_many(M.INVOICES, invoices)
    repo.admin_insert_many(M.INVOICE_LINE_ITEMS, lines_rows)

    # --- embeddings ------------------------------------------------------------
    embedder = get_embedder()
    for i in range(0, len(embed_jobs), 200):
        chunk = embed_jobs[i:i + 200]
        vectors = embedder.embed([t for _, t in chunk])
        repo.admin_upsert_embeddings([(inv_id, vec) for (inv_id, _), vec in zip(chunk, vectors)],
                                     embedder.model_name)

    stats = {"currencies": len(CURRENCIES), "fx_rates": sum(len(s) for s in fx_table.values()),
             "vendors": len(D.VENDORS), "clients": len(D.CLIENTS), "matters": len(D.MATTERS),
             "invoices": len(invoices), "line_items": len(lines_rows), "embeddings": len(embed_jobs)}
    log.info("seeded %s", stats)
    return stats
