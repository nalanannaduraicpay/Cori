"""Service layer shared by the API and scripts."""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from cori.config import get_settings
from cori.currency.parsing import format_amount
from cori.db import mapping as M
from cori.db import repository as repo
from cori.graph.build_graph import run_batch_graph

log = logging.getLogger(__name__)
SUBMITTABLE = {M.STATUS_PENDING, M.STATUS_NEEDS_REVIEW}


class SubmissionError(ValueError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def register_upload(src: Path | bytes, original_filename: str) -> int:
    upload_dir = get_settings().upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(original_filename).name)[:120] or "invoice.pdf"
    dest = upload_dir / f"{uuid.uuid4().hex[:12]}_{safe}"
    if isinstance(src, (bytes, bytearray)):
        dest.write_bytes(src)
    else:
        shutil.copyfile(src, dest)
    return repo.register_pending_invoice(str(dest.resolve()), safe)


def submit_batch(invoice_ids: list[int], submitted_by: str) -> str:
    ids = list(dict.fromkeys(int(i) for i in invoice_ids))  # dedupe, keep order
    if not ids:
        raise SubmissionError("invoice_ids must not be empty")
    problems = {}
    for i in ids:
        inv = repo.get_invoice(i)
        if inv is None:
            problems[i] = "not found"
        elif inv["status"] not in SUBMITTABLE:
            problems[i] = f"status {inv['status']} cannot be sent to Cori"
        elif not inv.get("file_path"):
            problems[i] = "no document attached"
    if problems:
        raise SubmissionError("Some invoices cannot be submitted", {str(k): v for k, v in problems.items()})
    batch_id = str(uuid.uuid4())
    repo.create_batch(batch_id, submitted_by, ids)
    for i in ids:  # claim them now so the same invoice cannot be queued in two batches
        repo.update_invoice_status(i, M.STATUS_IN_PROGRESS)
    return batch_id


def run_batch(batch_id: str) -> list[dict]:
    items = repo.get_batch_items(batch_id)
    try:
        return run_batch_graph(batch_id, [it["invoice_id"] for it in items])
    except Exception:
        log.exception("batch %s failed", batch_id)
        repo.close_unfinished_batch(batch_id, "FAILED")
        raise


def batch_status(batch_id: str) -> dict | None:
    batch = repo.get_batch(batch_id)
    if not batch:
        return None
    items = []
    counts = {"ADVANCED_TO_APPROVAL": 0, "NEEDS_REVIEW": 0}
    for it in repo.get_batch_items(batch_id):
        entry = {"invoice_id": it["invoice_id"], "status": it["status"], "current_step": it["current_step"],
                 "updated_at": it["updated_at"]}
        if it["status"] == "DONE":
            d = repo.get_latest_decision(it["invoice_id"])
            if d and d["batch_id"] == batch_id:
                entry["decision"] = d["decision"]
                entry["summary"] = d["summary"]
                counts[d["decision"]] = counts.get(d["decision"], 0) + 1
        inv = repo.get_invoice(it["invoice_id"])
        entry["file_name"] = inv.get("original_filename") if inv else None
        items.append(entry)
    done = sum(1 for i in items if i["status"] in ("DONE", "ERROR"))
    return {**batch, "processed": done, "decision_counts": counts, "items": items}


def money(amount: Decimal | str | None, currency: str | None) -> dict:
    amt = Decimal(str(amount)) if amount is not None else None
    return {"amount": str(amt) if amt is not None else None, "currency": currency,
            "display": format_amount(amt, currency)}


def decision_view(invoice_id: int) -> dict | None:
    inv = repo.get_invoice(invoice_id)
    if inv is None:
        return None
    d = repo.get_latest_decision(invoice_id)
    view = {"invoice_id": invoice_id, "file_name": inv.get("original_filename"), "status": inv["status"],
            "decision": None}
    if not d:
        return view
    detail = d.get("detail") or {}
    validation = detail.get("validation") or {}
    anomaly = detail.get("anomaly") or {}
    consensus = d.get("consensus") or {}
    agreed = consensus.get("agreed") or {}
    ccy = (validation.get("currency") or {}).get("currency")
    base = (anomaly.get("baseline") or {}).get("base_currency", get_settings().base_currency)
    baseline = anomaly.get("baseline")
    if baseline:
        baseline = {**baseline,
                    "median": money(baseline.get("median_base"), base), "mean": money(baseline.get("mean_base"), base),
                    "std": money(baseline.get("std_base"), base), "p95": money(baseline.get("p95_base"), base),
                    "native_median": money(baseline.get("native_median"), baseline.get("native_currency")),
                    "native_p95": money(baseline.get("native_p95"), baseline.get("native_currency"))}
    view.update({
        "batch_id": d["batch_id"], "decision": d["decision"], "summary": d["summary"],
        "decided_at": d["decided_at"], "reasons": d.get("reasons") or [],
        "invoice": {
            "vendor_name": agreed.get("vendor_name"), "vendor_id": (validation.get("vendor") or {}).get("vendor_id"),
            "invoice_number": agreed.get("invoice_number"), "invoice_date": agreed.get("invoice_date"),
            "client_number": agreed.get("client_number"), "matter_number": agreed.get("matter_number"),
            "currency": ccy, "currency_resolution": validation.get("currency"),
            "subtotal": money(agreed.get("subtotal"), ccy), "tax": money(agreed.get("tax_amount"), ccy),
            "total": money(agreed.get("total_amount"), ccy),
            "total_base": money(validation.get("total_base"), base),
            "line_items": [{**li, "unit_price": money(li.get("unit_price"), ccy), "amount": money(li.get("amount"), ccy)}
                           for li in agreed.get("line_items", [])],
        },
        "fx": {"rate": str(d["fx_rate_used"]) if d.get("fx_rate_used") is not None else None,
               "rate_date": d["fx_rate_date"].isoformat() if d.get("fx_rate_date") else None,
               "pair": f"{ccy}->{base}" if ccy else None},
        "consensus": consensus,
        "extractions": detail.get("extractions", []),
        "validation_checks": ([detail["guardrail"]] if detail.get("guardrail") else []) + validation.get("checks", []),
        "anomaly": {"flags": anomaly.get("flags", []), "baseline": baseline, "z_score": anomaly.get("z_score"),
                    "skipped_reason": anomaly.get("skipped_reason"),
                    "similar_avg": money(anomaly.get("similar_avg_base"), base),
                    "similar_invoices": [{**s, "total": money(s.get("total"), s.get("currency")),
                                          "total_base": money(s.get("total_base"), base)}
                                         for s in anomaly.get("similar_invoices", [])]},
        "feedback": repo.get_feedback(invoice_id),
    })
    return view


def record_feedback(invoice_id: int, reviewer: str, corrections: list[dict], cleared_flags: list[str]) -> list[str]:
    if repo.get_invoice(invoice_id) is None:
        raise SubmissionError("invoice not found")
    rows = [{"invoice_id": invoice_id, "field_name": c["field_name"], "agent_value": c.get("agent_value"),
             "corrected_value": c.get("corrected_value"), "reviewer": reviewer, "flag_cleared": None}
            for c in corrections]
    rows += [{"invoice_id": invoice_id, "field_name": "flag", "agent_value": None, "corrected_value": None,
              "reviewer": reviewer, "flag_cleared": code} for code in cleared_flags]
    return repo.insert_feedback(rows) if rows else []
