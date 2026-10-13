"""HTTP API used by the C# IAS front end ("Send to Cori") and the demo UI."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from cori import service
from cori.config import get_settings
from cori.db import mapping as M
from cori.db import repository as repo

router = APIRouter()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


# ----------------------------------------------------------------------------
# request / response models (drive the OpenAPI spec)
# ----------------------------------------------------------------------------
class BatchRequest(BaseModel):
    invoice_ids: list[int] = Field(..., min_length=1, examples=[[900001, 900002]])
    submitted_by: str = Field(..., min_length=1, max_length=100, examples=["jdoe"])


class BatchAccepted(BaseModel):
    batch_id: str
    status: str
    invoice_count: int


class BatchItem(BaseModel):
    invoice_id: int
    file_name: str | None = None
    status: str
    current_step: str | None = None
    updated_at: datetime | None = None
    decision: str | None = None
    summary: str | None = None


class BatchStatus(BaseModel):
    batch_id: str
    submitted_by: str
    submitted_at: datetime
    finished_at: datetime | None = None
    status: str
    invoice_count: int
    processed: int
    decision_counts: dict[str, int]
    items: list[BatchItem]


class UploadResult(BaseModel):
    invoice_id: int
    file_name: str
    status: str


class InvoiceSummary(BaseModel):
    invoice_id: int
    file_name: str | None
    status: str
    created_at: datetime | None
    decision: str | None = None


class MoneyOut(BaseModel):
    """Amounts are decimal strings (never floats) plus the ISO 4217 code."""
    amount: str | None = Field(None, examples=["27790.31"])
    currency: str | None = Field(None, examples=["EUR"])
    display: str = Field(..., examples=["27,790.31 EUR"])


class ReasonOut(BaseModel):
    code: str = Field(..., examples=["BANK_DETAILS_MISMATCH"])
    source: str = Field(..., description="extraction | consensus | validation | anomaly | guardrail | system")
    severity: str = Field(..., description="LOW | MEDIUM | HIGH | INFO")
    message: str
    blocking: bool = Field(..., description="false = informational note that did not block advancement")


class InvoiceFieldsOut(BaseModel):
    vendor_name: str | None = None
    vendor_id: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    client_number: str | None = None
    matter_number: str | None = None
    currency: str | None = None
    currency_resolution: dict[str, Any] | None = None
    subtotal: MoneyOut
    tax: MoneyOut
    total: MoneyOut
    total_base: MoneyOut
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class FxOut(BaseModel):
    rate: str | None = Field(None, description="Rate used for the base-currency conversion", examples=["1.078860731"])
    rate_date: str | None = Field(None, description="Date of the rate (invoice date or nearest prior date)")
    pair: str | None = Field(None, examples=["EUR->USD"])


class DecisionOut(BaseModel):
    invoice_id: int
    file_name: str | None = None
    status: str = Field(..., description="Current IAS status of the invoice")
    decision: str | None = Field(None, description="ADVANCED_TO_APPROVAL | NEEDS_REVIEW | null if not processed")
    batch_id: str | None = None
    summary: str | None = Field(None, description="Cori's plain-language summary")
    decided_at: datetime | None = None
    reasons: list[ReasonOut] = Field(default_factory=list)
    invoice: InvoiceFieldsOut | None = None
    fx: FxOut | None = None
    consensus: dict[str, Any] | None = Field(None, description="Field status and side-by-side disagreements")
    extractions: list[dict[str, Any]] = Field(default_factory=list)
    validation_checks: list[dict[str, Any]] = Field(default_factory=list)
    anomaly: dict[str, Any] | None = Field(None, description="Flags, baseline (base + native currency), similar invoices")
    feedback: list[dict[str, Any]] = Field(default_factory=list)


class FieldCorrection(BaseModel):
    field_name: str = Field(..., examples=["total_amount"])
    agent_value: str | None = Field(None, examples=["4250.00"])
    corrected_value: str | None = Field(None, examples=["4520.00"])


class FeedbackRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    corrections: list[FieldCorrection] = Field(default_factory=list)
    cleared_flags: list[str] = Field(default_factory=list, examples=[["COLD_START_BASELINE"]])


class FeedbackResult(BaseModel):
    invoice_id: int
    feedback_ids: list[str]


class Health(BaseModel):
    status: str
    database: str
    db_backend: str
    llm_provider: str
    extractors: list[str]


# ----------------------------------------------------------------------------
# endpoints
# ----------------------------------------------------------------------------
@router.get("/health", response_model=Health, tags=["system"])
def health():
    s = get_settings()
    try:
        repo.ping()
        db = "ok"
    except Exception as exc:  # report, don't crash
        db = f"error: {type(exc).__name__}"
    return Health(status="ok" if db == "ok" else "degraded", database=db, db_backend=s.db_backend,
                  llm_provider=s.llm_provider, extractors=list(s.enabled_extractors()))


@router.post("/batches", response_model=BatchAccepted, status_code=202, tags=["batches"],
             summary="Send invoices to Cori (the 'Send to Cori' button)")
def create_batch(req: BatchRequest, background: BackgroundTasks):
    try:
        batch_id = service.submit_batch(req.invoice_ids, req.submitted_by)
    except service.SubmissionError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "invoices": exc.details})
    background.add_task(service.run_batch, batch_id)
    return BatchAccepted(batch_id=batch_id, status="QUEUED", invoice_count=len(set(req.invoice_ids)))


@router.get("/batches", tags=["batches"], summary="Recent batches")
def list_batches(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    return repo.list_batches(limit)


@router.get("/batches/{batch_id}", response_model=BatchStatus, tags=["batches"])
def get_batch(batch_id: str):
    status = service.batch_status(batch_id)
    if not status:
        raise HTTPException(404, "batch not found")
    return status


@router.post("/invoices/upload", response_model=UploadResult, status_code=201, tags=["invoices"],
             summary="Upload a PDF and register it as a PENDING invoice (demo)")
async def upload_invoice(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "only PDF files are accepted")
    invoice_id = service.register_upload(data, file.filename or "invoice.pdf")
    inv = repo.get_invoice(invoice_id)
    return UploadResult(invoice_id=invoice_id, file_name=inv["original_filename"], status=inv["status"])


@router.get("/invoices", response_model=list[InvoiceSummary], tags=["invoices"],
            summary="Uploaded invoices (pending and processed)")
def list_invoices(status: list[str] | None = Query(None)):
    out = []
    for inv in repo.list_invoices(status):
        d = repo.get_latest_decision(inv["invoice_id"]) if inv["status"] not in (M.STATUS_PENDING,) else None
        out.append(InvoiceSummary(invoice_id=inv["invoice_id"], file_name=inv.get("original_filename"),
                                  status=inv["status"], created_at=inv.get("created_at"),
                                  decision=d["decision"] if d else None))
    return out


@router.get("/invoices/{invoice_id}/decision", response_model=DecisionOut, tags=["invoices"],
            summary="Decision, reasons, consensus detail, flags and similar invoices")
def get_decision(invoice_id: int):
    view = service.decision_view(invoice_id)
    if view is None:
        raise HTTPException(404, "invoice not found")
    return view


@router.get("/invoices/{invoice_id}/audit", tags=["invoices"], summary="Full AGENT_RUNS audit trail")
def get_audit(invoice_id: int, batch_id: str | None = None) -> list[dict[str, Any]]:
    return repo.get_agent_runs(invoice_id, batch_id)


@router.get("/invoices/{invoice_id}/pdf", tags=["invoices"], include_in_schema=False)
def get_pdf(invoice_id: int):
    inv = repo.get_invoice(invoice_id)
    if not inv or not inv.get("file_path"):
        raise HTTPException(404, "not found")
    return FileResponse(inv["file_path"], media_type="application/pdf")


@router.post("/invoices/{invoice_id}/feedback", response_model=FeedbackResult, status_code=201, tags=["invoices"],
             summary="Record reviewer corrections and cleared flags")
def post_feedback(invoice_id: int, req: FeedbackRequest):
    try:
        ids = service.record_feedback(invoice_id, req.reviewer, [c.model_dump() for c in req.corrections],
                                      req.cleared_flags)
    except service.SubmissionError as exc:
        raise HTTPException(404, str(exc))
    return FeedbackResult(invoice_id=invoice_id, feedback_ids=ids)
