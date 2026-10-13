"""LangGraph orchestration.

Batch graph (Cori supervisor):
    START -> intake -> [Send(process_invoice) per invoice] -> finalize -> END
    (bounded by ``max_concurrency`` = config max_concurrent_invoices)

Per-invoice subgraph:
    START -> load_document -> extractor_a | extractor_b | extractor_c (parallel)
          -> content_guardrail -> consensus -> validation -> anomaly -> decide -> END

Every node writes an AGENT_RUNS row (agent, model, prompt version, output)
so any decision can be reconstructed.
"""
from __future__ import annotations

import functools
import logging
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from cori.agents import anomaly as anomaly_agent
from cori.agents import cori as cori_agent
from cori.agents import validation as validation_agent
from cori.agents.extractors import run_extractor
from cori.agents.injection import injection_check
from cori.config import Settings, get_settings
from cori.consensus.compare import compare
from cori.db import mapping as M
from cori.db import repository as repo
from cori.graph.state import BatchState, InvoiceState
from cori.models.schemas import DecisionStatus, Reason, Severity
from cori.pdf import load_document

log = logging.getLogger(__name__)
DETERMINISTIC = "deterministic-python"


def audited(agent_name: str, model: str | None = DETERMINISTIC, prompt: str | None = None):
    """Log the node's output to AGENT_RUNS and track the batch item step."""
    def deco(fn: Callable[[InvoiceState], dict]):
        @functools.wraps(fn)
        def wrapper(state: InvoiceState) -> dict:
            started = repo.now()
            repo.update_batch_item(state["batch_id"], state["invoice_id"], status="RUNNING", step=agent_name)
            try:
                out = fn(state)
            except Exception as exc:
                repo.log_agent_run(batch_id=state["batch_id"], invoice_id=state["invoice_id"], agent_name=agent_name,
                                   model_name=model, prompt_version=prompt, raw_output={}, started_at=started,
                                   finished_at=repo.now(), status="ERROR", error=f"{type(exc).__name__}: {exc}")
                raise
            meta = out.pop("_audit", {}) if isinstance(out, dict) else {}
            repo.log_agent_run(batch_id=state["batch_id"], invoice_id=state["invoice_id"], agent_name=agent_name,
                               model_name=meta.get("model", model), prompt_version=meta.get("prompt", prompt),
                               raw_output=meta.get("output", out), started_at=started, finished_at=repo.now(),
                               status=meta.get("status", "OK"), error=meta.get("error"))
            return out
        return wrapper
    return deco


def build_invoice_graph(settings: Settings):
    extractors = settings.enabled_extractors()
    with_images = any(c.method == "vision" for c in extractors.values())

    @audited("document_loader")
    def load(state: InvoiceState) -> dict:
        inv = repo.get_invoice(state["invoice_id"])
        if not inv or not inv.get("file_path"):
            raise FileNotFoundError(f"invoice {state['invoice_id']} has no file")
        repo.update_invoice_status(state["invoice_id"], M.STATUS_IN_PROGRESS)
        doc = load_document(inv["file_path"], with_images, settings.pdf.render_dpi, settings.pdf.max_pages)
        return {"document": doc, "extractions": [],
                "_audit": {"output": {"file_path": inv["file_path"], "pages": doc.page_count,
                                      "text_chars": len(doc.text), "images": len(doc.images)}}}

    def make_extractor_node(name: str):
        cfg = extractors[name]

        @audited(name)
        def node(state: InvoiceState) -> dict:
            res = run_extractor(name, cfg, state["document"])
            return {"extractions": [res],
                    "_audit": {"model": f"{res.provider}:{res.model_name}", "prompt": res.prompt_version,
                               "status": "OK" if res.status == "ok" else "FAILED", "error": res.error,
                               "output": {"status": res.status, "attempts": res.attempts,
                                          "duration_ms": res.duration_ms, "raw_model_output": res.raw_text,
                                          "extracted": res.raw.model_dump(mode="json") if res.raw else None,
                                          "normalized": res.normalized.model_dump(mode="json") if res.normalized else None}}}
        return node

    @audited("content_guardrail")
    def guardrail(state: InvoiceState) -> dict:
        res = injection_check(state["document"].text, [e.raw_text for e in state.get("extractions", [])])
        return {"guardrail": res, "_audit": {"output": res.model_dump(mode="json"),
                                             "status": "OK" if res.passed else "FAILED"}}

    @audited("consensus_engine")
    def consensus(state: InvoiceState) -> dict:
        results = sorted(state.get("extractions", []), key=lambda r: r.name)
        res = compare(results, settings.consensus)
        return {"consensus": res, "_audit": {"output": res.model_dump(mode="json"),
                                             "status": "OK" if res.passed else "FAILED"}}

    @audited("validation_agent")
    def validation(state: InvoiceState) -> dict:
        cons = state.get("consensus")
        if not cons or not cons.agreed:
            return {"validation": None, "_audit": {"output": {"skipped": "no agreed record"}, "status": "SKIPPED"}}
        res = validation_agent.run_validation(state["invoice_id"], cons.agreed, settings)
        return {"validation": res, "_audit": {"output": res.model_dump(mode="json"),
                                              "status": "OK" if res.passed else "FAILED"}}

    @audited("anomaly_agent")
    def anomaly(state: InvoiceState) -> dict:
        cons, val = state.get("consensus"), state.get("validation")
        if not cons or not cons.agreed or val is None:
            return {"anomaly": None, "_audit": {"output": {"skipped": "no agreed record"}, "status": "SKIPPED"}}
        res = anomaly_agent.run_anomaly(state["invoice_id"], cons.agreed, val, settings)
        return {"anomaly": res, "_audit": {"output": res.model_dump(mode="json")}}

    def decide(state: InvoiceState) -> dict:
        started = repo.now()
        extractions = sorted(state.get("extractions", []), key=lambda r: r.name)
        cons, val, anom = state.get("consensus"), state.get("validation"), state.get("anomaly")
        guard = state.get("guardrail")
        decision, reasons = cori_agent.route(extractions, cons, val, anom, guard)
        repo.log_agent_run(batch_id=state["batch_id"], invoice_id=state["invoice_id"], agent_name="cori_router",
                           model_name=DETERMINISTIC, prompt_version="routing-rules.v1",
                           raw_output={"decision": decision.value,
                                       "reasons": [r.model_dump(mode="json") for r in reasons]},
                           started_at=started, finished_at=repo.now(), status="OK")
        facts = cori_agent.summary_facts(decision, reasons, cons, val, anom)
        s_started = repo.now()
        summary, model, prompt = cori_agent.summarize(facts, settings.summary.use_llm)
        repo.log_agent_run(batch_id=state["batch_id"], invoice_id=state["invoice_id"], agent_name="cori_summary",
                           model_name=model, prompt_version=prompt, raw_output={"facts": facts, "summary": summary},
                           started_at=s_started, finished_at=repo.now(), status="OK")
        persist_decision(state["batch_id"], state["invoice_id"], decision, reasons, summary, extractions, cons, val,
                         anom, guard)
        return {"decision": decision, "reasons": reasons, "summary": summary}

    g = StateGraph(InvoiceState)
    g.add_node("load_document", load)
    for name in extractors:
        g.add_node(name, make_extractor_node(name))
    g.add_node("content_guardrail", guardrail)
    g.add_node("consensus", consensus)
    g.add_node("validation", validation)
    g.add_node("anomaly", anomaly)
    g.add_node("decide", decide)
    g.add_edge(START, "load_document")
    for name in extractors:
        g.add_edge("load_document", name)
    g.add_edge(list(extractors), "content_guardrail")  # join: wait for every extractor
    g.add_edge("content_guardrail", "consensus")
    g.add_edge("consensus", "validation")
    g.add_edge("validation", "anomaly")
    g.add_edge("anomaly", "decide")
    g.add_edge("decide", END)
    return g.compile()


def persist_decision(batch_id, invoice_id, decision, reasons, summary, extractions, cons, val, anom, guard=None):
    flags = ([guard.model_dump(mode="json")] if guard and not guard.passed else []) + \
            ([c.model_dump(mode="json") for c in val.checks if not c.passed] if val else []) + \
            ([f.model_dump(mode="json") for f in anom.flags] if anom else [])
    detail = {
        "guardrail": guard.model_dump(mode="json") if guard else None,
        "extractions": [{"name": e.name, "method": e.method, "provider": e.provider, "model": e.model_name,
                         "prompt_version": e.prompt_version, "status": e.status, "error": e.error,
                         "normalized": e.normalized.model_dump(mode="json") if e.normalized else None}
                        for e in extractions],
        "validation": val.model_dump(mode="json") if val else None,
        "anomaly": anom.model_dump(mode="json") if anom else None,
    }
    repo.insert_decision(invoice_id=invoice_id, batch_id=batch_id, decision=decision.value, summary=summary,
                         reasons=[r.model_dump(mode="json") for r in reasons],
                         consensus=cons.model_dump(mode="json") if cons else None, flags=flags, detail=detail,
                         fx_rate_used=val.fx.rate if val and val.fx else None,
                         fx_rate_date=val.fx.rate_date if val and val.fx else None)
    agreed = cons.agreed if cons else None
    if agreed is not None:  # lets later duplicate checks see this submission
        repo.upsert_invoice_extract(
            invoice_id=invoice_id, batch_id=batch_id, vendor_id=(val.vendor or {}).get("vendor_id") if val else None,
            matter_id=(val.matter or {}).get("matter_id") if val else None, invoice_number=agreed.invoice_number,
            invoice_date=agreed.invoice_date, currency=val.currency.currency if val and val.currency else None,
            total=agreed.total_amount, total_usd=val.total_base if val else None, decision=decision.value)
    repo.update_invoice_status(invoice_id, M.STATUS_ADVANCED if decision == DecisionStatus.ADVANCED_TO_APPROVAL
                               else M.STATUS_NEEDS_REVIEW)
    repo.update_batch_item(batch_id, invoice_id, status="DONE", step="decided")


def build_batch_graph(settings: Settings | None = None):
    settings = settings or get_settings()
    invoice_graph = build_invoice_graph(settings)

    def intake(state: BatchState) -> dict:
        repo.set_batch_status(state["batch_id"], "RUNNING")
        repo.log_agent_run(batch_id=state["batch_id"], invoice_id=None, agent_name="cori_supervisor",
                           model_name=DETERMINISTIC, prompt_version=None,
                           raw_output={"invoice_ids": state["invoice_ids"],
                                       "extractors": list(settings.enabled_extractors()),
                                       "max_concurrency": settings.max_concurrent_invoices},
                           started_at=repo.now(), finished_at=repo.now(), status="OK")
        return {}

    def fan_out(state: BatchState):
        return [Send("process_invoice", {"batch_id": state["batch_id"], "invoice_id": i})
                for i in state["invoice_ids"]]

    def process_invoice(payload: dict) -> dict:
        batch_id, invoice_id = payload["batch_id"], payload["invoice_id"]
        try:
            out = invoice_graph.invoke({"batch_id": batch_id, "invoice_id": invoice_id, "extractions": []})
            return {"results": [{"invoice_id": invoice_id, "decision": out["decision"].value}]}
        except Exception as exc:
            log.exception("invoice %s failed", invoice_id)
            reason = Reason(code="PROCESSING_ERROR", source="system", severity=Severity.HIGH,
                            message=f"Cori could not finish processing: {type(exc).__name__}: {exc}")
            try:
                persist_decision(batch_id, invoice_id, DecisionStatus.NEEDS_REVIEW, [reason],
                                 f"Needs review: {reason.message}", [], None, None, None)
            except Exception:
                log.exception("could not persist error decision for %s", invoice_id)
                repo.update_batch_item(batch_id, invoice_id, status="ERROR", step="error")
            return {"results": [{"invoice_id": invoice_id, "decision": DecisionStatus.NEEDS_REVIEW.value,
                                 "error": str(exc)}]}

    def finalize(state: BatchState) -> dict:
        repo.set_batch_status(state["batch_id"], "COMPLETED", finished=True)
        return {}

    g = StateGraph(BatchState)
    g.add_node("intake", intake)
    g.add_node("process_invoice", process_invoice)
    g.add_node("finalize", finalize)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", fan_out, ["process_invoice"])
    g.add_edge("process_invoice", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run_batch_graph(batch_id: str, invoice_ids: list[int], settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    graph = build_batch_graph(settings)
    out = graph.invoke({"batch_id": batch_id, "invoice_ids": invoice_ids, "results": []},
                       config={"max_concurrency": settings.max_concurrent_invoices})
    return out.get("results", [])
