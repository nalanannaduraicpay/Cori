"""Offline mock provider (LLM_PROVIDER=mock).

Not an LLM. It lets the full pipeline run deterministically without API keys:
* strategy "A" (vision stand-in): rebuilds text lines from PyMuPDF word
  coordinates of the source PDF - a different text path than pdfplumber.
* strategy "B": parses the pdfplumber text passed in the prompt.
* strategy "C": same text, but a different total-selection heuristic.

An image-only scan has no text layer, so mock extractors fail on it - the
same outcome a weak OCR path would produce. The mock knows the label
vocabulary of the three demo templates plus common synonyms.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from cori.currency.reference import COUNTRY_NAMES
from cori.llm.provider import LLMProvider, ProviderUnavailable, SchemaValidationError
from cori.models.schemas import ExtractedInvoice, ExtractedLineItem, FieldConfidence

LABELS: dict[str, list[str]] = {
    "vendor_name": ["From", "Issued by", "Supplier", "Vendor"],
    "vendor_id": ["Vendor ID", "Supplier ID", "Vendor No\\.?"],
    "invoice_number": ["Invoice Number", "Invoice No\\.?", "Invoice #", "Invoice Ref"],
    "invoice_date": ["Invoice Date", "Date of issue", "Issue Date"],
    "due_date": ["Due Date", "Payment due", "Due"],
    "client_number": ["Client Number", "Client No\\.?", "Client"],
    "matter_number": ["Matter Number", "Matter No\\.?", "Matter"],
    "bank_name": ["Bank Name", "Bank"],
    "bank_account": ["IBAN", "Account Number", "Account No\\.?", "Account"],
    "swift_bic": ["SWIFT/BIC", "SWIFT", "BIC"],
    "subtotal": ["Subtotal", "Sub-total", "Net amount", "Fees subtotal"],
    "tax_amount": ["VAT(?: \\([\\d.,]+ ?%\\))?", "GST(?: \\([\\d.,]+ ?%\\))?", "Tax(?: \\([\\d.,]+ ?%\\))?",
                   "IVA(?: \\([\\d.,]+ ?%\\))?", "ISS(?: \\([\\d.,]+ ?%\\))?", "HST(?: \\([\\d.,]+ ?%\\))?"],
    "total_amount": ["Total Amount Due", "Total Due", "Amount Due", "Invoice Total", "Balance Due", "Total"],
    "currency": ["Currency"],
}
_ALL = sorted(((f, l) for f, ls in LABELS.items() for l in ls), key=lambda x: -len(x[1]))
_LABEL_RE = re.compile("(?<![A-Za-z#-])(" + "|".join(f"(?:{l})" for _, l in _ALL) + r")\s*:", re.IGNORECASE)
_NUM_TOKEN = re.compile(r"^[\d.,']+$")
_TK_RE = re.compile(r"^(.*?)\s+([A-Z]\.\s?[A-Z][A-Za-z'\-]+)$")


def _field_for_label(label: str) -> str | None:
    for f, pat in _ALL:
        if re.fullmatch(pat, label.strip(), re.IGNORECASE):
            return f
    return None


def split_labeled(line: str) -> list[tuple[str, str]]:
    """'From: X  Invoice Number: Y' -> [('vendor_name','X'), ('invoice_number','Y')]"""
    out = []
    matches = list(_LABEL_RE.finditer(line))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        field = _field_for_label(m.group(1))
        if field:
            out.append((field, line[m.end():end].strip()))
    return out


def _amount_and_evidence(value: str) -> tuple[str | None, str | None]:
    m = re.search(r"\(?-?[\d][\d.,' ]*\d\)?|\d", value)
    if not m:
        return None, value.strip() or None
    amount = m.group(0).strip()
    evidence = (value[:m.start()] + " " + value[m.end():]).strip() or None
    return amount, evidence


def parse_invoice_text(text: str, strategy: str = "B") -> ExtractedInvoice:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    found: dict[str, list[str]] = defaultdict(list)
    vendor_line_idx = None
    for idx, line in enumerate(lines):
        for field, value in split_labeled(line):
            if value:
                found[field].append(value)
                if field == "vendor_name" and vendor_line_idx is None:
                    vendor_line_idx = idx

    def first(f):
        return found[f][0] if found.get(f) else None

    if not found.get("vendor_name"):
        # no "From:"-style label: letterhead convention, first non-title line
        for idx, line in enumerate(lines):
            if ":" not in line and not re.fullmatch(r"(tax\s+)?invoice", line, re.IGNORECASE):
                found["vendor_name"].append(line)
                vendor_line_idx = idx
                break

    data: dict = {f: first(f) for f in ("vendor_name", "vendor_id", "invoice_number", "invoice_date", "due_date",
                                        "client_number", "matter_number", "bank_name", "bank_account", "swift_bic")}
    # country: first known country name in the lines just after the vendor name
    if vendor_line_idx is not None:
        for line in lines[vendor_line_idx:vendor_line_idx + 4]:
            low = line.lower()
            hit = next((iso for name, iso in sorted(COUNTRY_NAMES.items(), key=lambda x: -len(x[0]))
                        if re.search(rf"\b{re.escape(name)}\b", low)), None)
            if hit:
                data["vendor_country"] = hit
                break

    evidence_parts = []
    for f in ("subtotal", "tax_amount", "total_amount"):
        vals = found.get(f) or []
        if not vals:
            continue
        chosen = vals[-1] if (f == "total_amount" and strategy == "C") else vals[0]
        amt, ev = _amount_and_evidence(chosen)
        data[f] = amt
        if f == "total_amount" and ev:
            evidence_parts.append(ev)
    cur_line = first("currency")
    if cur_line:
        evidence_parts.append(cur_line)
        m = re.search(r"\b([A-Z]{3})\b", cur_line)
        if m:
            data["currency"] = m.group(1)
    if not data.get("currency"):
        for ev in evidence_parts:
            m = re.search(r"\b([A-Z]{3})\b", ev)
            if m:
                data["currency"] = m.group(1)
                break
    data["currency_evidence"] = " | ".join(evidence_parts) or None

    # line items: rows between the table header and the subtotal line
    items = []
    in_table = False
    for line in lines:
        low = line.lower()
        if "description" in low and ("amount" in low or "rate" in low):
            in_table = True
            continue
        if in_table:
            if split_labeled(line) and any(f in ("subtotal", "total_amount", "tax_amount")
                                           for f, _ in split_labeled(line)):
                break
            toks = line.split()
            if len(toks) >= 4 and all(_NUM_TOKEN.match(t) for t in toks[-3:]):
                head = " ".join(toks[:-3])
                m = _TK_RE.match(head)
                desc, tk = (m.group(1), m.group(2)) if m else (head, None)
                items.append(ExtractedLineItem(description=desc.strip(), timekeeper=tk, quantity=toks[-3],
                                               unit_price=toks[-2], amount=toks[-1]))
    data["line_items"] = items
    present = [k for k, v in data.items() if v not in (None, [], "")]
    data["field_confidence"] = [FieldConfidence(field=k, confidence=0.9) for k in present if k != "line_items"]
    return ExtractedInvoice.model_validate(data)


def text_from_pdf_words(path: str, max_pages: int = 5) -> str:
    """Rebuild lines from PyMuPDF word boxes (independent of pdfplumber)."""
    import pymupdf as fitz

    out_lines = []
    with fitz.open(path) as doc:
        for page in list(doc)[:max_pages]:
            rows: dict[int, list] = defaultdict(list)
            for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                key = round(y1 / 3)
                rows[key].append((x0, word))
            for key in sorted(rows):
                out_lines.append(" ".join(w for _, w in sorted(rows[key])))
    return "\n".join(out_lines)


_DOC_RE = re.compile(r"<invoice_document>(.*)</invoice_document>", re.DOTALL)


class MockProvider(LLMProvider):
    provider_name = "mock"
    supports_vision = True

    def __init__(self, strategy: str = "B"):
        self.strategy = strategy
        self.model_name = f"mock-parser-{strategy}"

    def extract_structured(self, system, user_text, schema, images=None, source_path=None):
        if schema is not ExtractedInvoice:
            raise SchemaValidationError(f"mock provider cannot produce {schema.__name__}")
        if self.strategy == "A":
            if not source_path:
                raise SchemaValidationError("mock vision needs the source PDF path")
            text = text_from_pdf_words(source_path)
        else:
            m = _DOC_RE.search(user_text)
            text = m.group(1) if m else user_text
        if len(text.strip()) < 20:
            raise SchemaValidationError("no readable text in document (image-only or blank page)", raw_text="")
        parsed = parse_invoice_text(text, self.strategy)
        if not parsed.total_amount and not parsed.invoice_number:
            raise SchemaValidationError("could not locate invoice fields", raw_text=text[:500])
        return parsed, json.dumps(parsed.model_dump(mode="json"))

    def complete(self, system, user):
        raise ProviderUnavailable("mock provider does not generate prose; template summary is used")
