"""LangGraph state definitions."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from cori.models.schemas import (
    AnomalyResult, CheckResult, ConsensusResult, DecisionStatus, ExtractorResult, Reason, ValidationResult,
)
from cori.pdf import DocumentContent


class InvoiceState(TypedDict, total=False):
    batch_id: str
    invoice_id: int
    document: Optional[DocumentContent]
    extractions: Annotated[list[ExtractorResult], operator.add]  # fan-in from parallel extractors
    guardrail: Optional[CheckResult]  # instruction-like text aimed at AI systems
    consensus: Optional[ConsensusResult]
    validation: Optional[ValidationResult]
    anomaly: Optional[AnomalyResult]
    decision: Optional[DecisionStatus]
    reasons: list[Reason]
    summary: str


class BatchState(TypedDict, total=False):
    batch_id: str
    invoice_ids: list[int]
    results: Annotated[list[dict[str, Any]], operator.add]  # fan-in from per-invoice subgraphs
