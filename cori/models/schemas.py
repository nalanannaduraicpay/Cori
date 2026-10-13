"""Pydantic v2 data models shared across agents, graph, and API.

Money is always ``decimal.Decimal``. LLM extractors return amounts as the raw
printed text (e.g. "12.450,00"); deterministic code parses them, so locale
handling never depends on a model.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DecisionStatus(str, Enum):
    ADVANCED_TO_APPROVAL = "ADVANCED_TO_APPROVAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# --------------------------------------------------------------------------
# Extraction (what an LLM returns; validated via structured output)
# --------------------------------------------------------------------------
class ExtractedLineItem(BaseModel):
    description: str | None = Field(None, description="Line item description as printed")
    quantity: str | None = Field(None, description="Quantity or hours exactly as printed")
    unit_price: str | None = Field(None, description="Unit price or hourly rate exactly as printed")
    amount: str | None = Field(None, description="Line amount exactly as printed")
    timekeeper: str | None = Field(None, description="Timekeeper / fee earner name, if shown")


class FieldConfidence(BaseModel):
    field: str = Field(description="Name of an ExtractedInvoice field")
    confidence: float = Field(ge=0, le=1, description="0 to 1 confidence for that field")


class ExtractedInvoice(BaseModel):
    """Fields an extraction agent pulls from one invoice document."""
    vendor_name: str | None = Field(None, description="Legal name of the vendor / law firm issuing the invoice")
    vendor_id: str | None = Field(None, description="Vendor ID printed on the invoice, if any")
    vendor_country: str | None = Field(None, description="Vendor country (ISO 3166 alpha-2 or name)")
    invoice_number: str | None = None
    invoice_date: str | None = Field(None, description="Invoice date, ISO 8601 (YYYY-MM-DD) if you can determine it")
    due_date: str | None = Field(None, description="Due date, ISO 8601 if you can determine it")
    currency: str | None = Field(None, description="ISO 4217 code ONLY if printed explicitly; otherwise null")
    currency_evidence: str | None = Field(None, description="The raw currency symbol or text seen next to the total, e.g. '$', 'EUR', '€'")
    subtotal: str | None = Field(None, description="Subtotal exactly as printed, without currency symbol")
    tax_amount: str | None = Field(None, description="Tax / VAT amount exactly as printed")
    total_amount: str | None = Field(None, description="Total amount due exactly as printed")
    client_number: str | None = None
    matter_number: str | None = None
    bank_name: str | None = Field(None, description="Beneficiary bank name")
    bank_account: str | None = Field(None, description="Beneficiary IBAN or account number")
    swift_bic: str | None = Field(None, description="Beneficiary SWIFT/BIC code")
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    field_confidence: list[FieldConfidence] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Normalized record used by consensus / validation / anomaly
# --------------------------------------------------------------------------
class NormalizedLineItem(BaseModel):
    description: str | None = None
    timekeeper: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class NormalizedInvoice(BaseModel):
    vendor_name: str | None = None
    vendor_name_key: str | None = None  # lowercase, no punctuation / legal suffix
    vendor_id: str | None = None
    vendor_country: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None  # ISO code if unambiguous from evidence
    currency_candidates: list[str] = Field(default_factory=list)
    currency_evidence: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    client_number: str | None = None
    matter_number: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    swift_bic: str | None = None
    line_items: list[NormalizedLineItem] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


class ExtractorResult(BaseModel):
    name: str
    method: str
    provider: str
    model_name: str
    prompt_version: str
    status: str  # ok | failed
    error: str | None = None
    attempts: int = 0
    raw: ExtractedInvoice | None = None
    normalized: NormalizedInvoice | None = None
    raw_text: str | None = None  # model raw response, for the audit trail
    duration_ms: int = 0


# --------------------------------------------------------------------------
# Consensus
# --------------------------------------------------------------------------
class Disagreement(BaseModel):
    field: str
    critical: bool
    values: dict[str, str | None]  # extractor name -> normalized value (as text)
    resolution: str  # "no agreement" | "majority: X" | ...


class ConsensusResult(BaseModel):
    passed: bool
    successful_extractors: list[str]
    failed_extractors: list[str]
    agreed: NormalizedInvoice | None = None
    field_status: dict[str, str] = Field(default_factory=dict)  # unanimous|majority|disagree|missing
    disagreements: list[Disagreement] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Validation / anomaly
# --------------------------------------------------------------------------
class CheckResult(BaseModel):
    name: str
    passed: bool
    code: str | None = None  # stable reason code when the check fails
    severity: Severity = Severity.INFO
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CurrencyResolution(BaseModel):
    currency: str | None
    method: str  # explicit_iso | unambiguous_symbol | vendor_known_currency | vendor_country | unresolved
    ambiguous: bool = False
    candidates: list[str] = Field(default_factory=list)
    message: str = ""


class FxRateUsed(BaseModel):
    from_currency: str
    to_currency: str
    rate: Decimal
    rate_date: date
    requested_date: date


class ValidationResult(BaseModel):
    passed: bool
    checks: list[CheckResult]
    currency: CurrencyResolution | None = None
    vendor: dict[str, Any] | None = None
    client: dict[str, Any] | None = None
    matter: dict[str, Any] | None = None
    fx: FxRateUsed | None = None
    total_base: Decimal | None = None  # total in base currency (USD)


class AnomalyFlag(BaseModel):
    code: str
    severity: Severity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Baseline(BaseModel):
    level: str  # matter | client | vendor | global
    level_key: str | None = None
    count: int = 0
    median_base: Decimal | None = None
    mean_base: Decimal | None = None
    std_base: Decimal | None = None
    p95_base: Decimal | None = None
    base_currency: str = "USD"
    native_currency: str | None = None
    native_count: int = 0
    native_median: Decimal | None = None
    native_p95: Decimal | None = None
    levels_tried: list[str] = Field(default_factory=list)


class SimilarInvoice(BaseModel):
    invoice_id: int
    invoice_number: str | None
    vendor_name: str | None
    invoice_date: date | None
    currency: str | None
    total: Decimal | None
    total_base: Decimal | None
    distance: float


class AnomalyResult(BaseModel):
    flags: list[AnomalyFlag] = Field(default_factory=list)
    baseline: Baseline | None = None
    z_score: float | None = None
    similar_invoices: list[SimilarInvoice] = Field(default_factory=list)
    similar_avg_base: Decimal | None = None
    skipped_reason: str | None = None


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------
class Reason(BaseModel):
    code: str
    source: str  # extraction | consensus | validation | anomaly | guardrail | system
    severity: Severity
    message: str
    blocking: bool = True  # False = informational (e.g. LOW anomaly notes)


class Money(BaseModel):
    amount: Decimal | None
    currency: str | None
    display: str


class InvoiceDecision(BaseModel):
    invoice_id: int
    batch_id: str
    decision: DecisionStatus
    summary: str
    reasons: list[Reason]
    decided_at: datetime
