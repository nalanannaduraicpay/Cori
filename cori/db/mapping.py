"""Single mapping layer between Cori's logical names and physical table/column
names. To point Cori at the real IAS schema, change the physical names here
(right-hand side) - repository.py only ever uses the logical names.

Every query aliases columns back to their logical names (``PHYS AS logical``),
so result dictionaries are stable regardless of the physical schema.
"""
from __future__ import annotations


class Table:
    def __init__(self, physical: str, **columns: str):
        self._physical = physical
        self._columns = columns

    def __str__(self) -> str:
        return self._physical

    def __getattr__(self, logical: str) -> str:
        try:
            return self.__dict__["_columns"][logical]
        except KeyError:
            raise AttributeError(f"{self._physical} has no mapped column {logical!r}") from None

    def col(self, logical: str, alias: str | None = None) -> str:
        prefix = f"{alias}." if alias else ""
        return f"{prefix}{self._columns[logical]}"

    def select(self, *logical: str, alias: str | None = None) -> str:
        """``PHYS AS logical, ...`` for the given (or all) logical columns."""
        names = logical or tuple(self._columns)
        return ", ".join(f"{self.col(n, alias)} AS {n}" for n in names)

    @property
    def columns(self) -> dict[str, str]:
        return dict(self._columns)


CURRENCIES = Table("CURRENCIES", currency_code="CURRENCY_CODE", name="NAME",
                   minor_units="MINOR_UNITS", symbol="SYMBOL")

FX_RATES = Table("FX_RATES", rate_date="RATE_DATE", from_currency="FROM_CURRENCY",
                 to_currency="TO_CURRENCY", rate="RATE")

VENDORS = Table("VENDORS", vendor_id="VENDOR_ID", name="NAME", country="COUNTRY",
                default_currency="DEFAULT_CURRENCY", status="STATUS")

VENDOR_CURRENCIES = Table("VENDOR_CURRENCIES", vendor_id="VENDOR_ID", currency_code="CURRENCY_CODE")

VENDOR_BANK_ACCOUNTS = Table("VENDOR_BANK_ACCOUNTS", account_id="ACCOUNT_ID", vendor_id="VENDOR_ID",
                             bank_name="BANK_NAME", iban_or_account="IBAN_OR_ACCOUNT",
                             swift_bic="SWIFT_BIC", is_active="IS_ACTIVE")

CLIENTS = Table("CLIENTS", client_id="CLIENT_ID", client_number="CLIENT_NUMBER", name="NAME")

MATTERS = Table("MATTERS", matter_id="MATTER_ID", client_id="CLIENT_ID", matter_number="MATTER_NUMBER",
                description="DESCRIPTION", status="STATUS")

INVOICES = Table("INVOICES", invoice_id="INVOICE_ID", vendor_id="VENDOR_ID", matter_id="MATTER_ID",
                 invoice_number="INVOICE_NUMBER", invoice_date="INVOICE_DATE", currency="CURRENCY",
                 subtotal="SUBTOTAL", tax="TAX", total="TOTAL", total_usd="TOTAL_USD", status="STATUS",
                 source="SOURCE", file_path="FILE_PATH", original_filename="ORIGINAL_FILENAME",
                 created_at="CREATED_AT")

INVOICE_LINE_ITEMS = Table("INVOICE_LINE_ITEMS", line_id="LINE_ID", invoice_id="INVOICE_ID",
                           description="DESCRIPTION", timekeeper="TIMEKEEPER", quantity="QUANTITY",
                           unit_price="UNIT_PRICE", amount="AMOUNT")

INVOICE_EMBEDDINGS = Table("INVOICE_EMBEDDINGS", invoice_id="INVOICE_ID", embedding="EMBEDDING",
                           model_name="MODEL_NAME", created_at="CREATED_AT")

AGENT_BATCHES = Table("AGENT_BATCHES", batch_id="BATCH_ID", submitted_by="SUBMITTED_BY",
                      submitted_at="SUBMITTED_AT", status="STATUS", invoice_count="INVOICE_COUNT",
                      finished_at="FINISHED_AT")

AGENT_BATCH_ITEMS = Table("AGENT_BATCH_ITEMS", batch_id="BATCH_ID", invoice_id="INVOICE_ID",
                          status="STATUS", current_step="CURRENT_STEP", updated_at="UPDATED_AT")

AGENT_RUNS = Table("AGENT_RUNS", run_id="RUN_ID", batch_id="BATCH_ID", invoice_id="INVOICE_ID",
                   agent_name="AGENT_NAME", model_name="MODEL_NAME", prompt_version="PROMPT_VERSION",
                   raw_output="RAW_OUTPUT", started_at="STARTED_AT", finished_at="FINISHED_AT",
                   status="STATUS", error_message="ERROR_MESSAGE")

AGENT_DECISIONS = Table("AGENT_DECISIONS", decision_id="DECISION_ID", invoice_id="INVOICE_ID",
                        batch_id="BATCH_ID", decision="DECISION", summary="SUMMARY", reasons="REASONS",
                        consensus="CONSENSUS", flags="FLAGS", detail="DETAIL",
                        fx_rate_used="FX_RATE_USED", fx_rate_date="FX_RATE_DATE", decided_at="DECIDED_AT")

AGENT_INVOICE_EXTRACTS = Table("AGENT_INVOICE_EXTRACTS", invoice_id="INVOICE_ID", batch_id="BATCH_ID",
                               vendor_id="VENDOR_ID", matter_id="MATTER_ID", invoice_number="INVOICE_NUMBER",
                               invoice_date="INVOICE_DATE", currency="CURRENCY", total="TOTAL",
                               total_usd="TOTAL_USD", decision="DECISION", updated_at="UPDATED_AT")

REVIEW_FEEDBACK = Table("REVIEW_FEEDBACK", feedback_id="FEEDBACK_ID", invoice_id="INVOICE_ID",
                        field_name="FIELD_NAME", agent_value="AGENT_VALUE",
                        corrected_value="CORRECTED_VALUE", reviewer="REVIEWER",
                        reviewed_at="REVIEWED_AT", flag_cleared="FLAG_CLEARED")

ALL_TABLES = [
    REVIEW_FEEDBACK, AGENT_INVOICE_EXTRACTS, AGENT_DECISIONS, AGENT_RUNS, AGENT_BATCH_ITEMS, AGENT_BATCHES,
    INVOICE_EMBEDDINGS, INVOICE_LINE_ITEMS, INVOICES, MATTERS, CLIENTS,
    VENDOR_BANK_ACCOUNTS, VENDOR_CURRENCIES, VENDORS, FX_RATES, CURRENCIES,
]  # drop order (children first)

# Invoice status values used by Cori (map to IAS workflow states here).
STATUS_PENDING = "PENDING"
STATUS_IN_PROGRESS = "CORI_PROCESSING"
STATUS_ADVANCED = "ADVANCED_TO_APPROVAL"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_HISTORICAL = "APPROVED"
