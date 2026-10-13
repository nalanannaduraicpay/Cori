"""Shared, deterministic demo dataset: vendors, clients, matters, FX centres,
the anchor invoice used by the duplicate test, and the sample-invoice specs.

Both ``db/seed.py`` and ``samples/generate.py`` read from here so the PDFs
always agree with the seeded vendor master and history. All companies, banks
and account numbers are fictitious.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal


# ----------------------------------------------------------------------------
# IBAN helper (valid mod-97 check digits, fictitious BBANs)
# ----------------------------------------------------------------------------
def make_iban(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"


def pretty_iban(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


@dataclass(frozen=True)
class Timekeeper:
    name: str
    rate: Decimal  # hourly rate in the vendor's default currency


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    country: str
    country_name: str
    city: str
    default_currency: str
    currencies: tuple[str, ...]
    tax_rate: Decimal
    bank_name: str
    account: str  # IBAN or local account number (no spaces)
    swift: str
    prefix: str
    timekeepers: tuple[Timekeeper, ...]


def _tk(*pairs) -> tuple[Timekeeper, ...]:
    return tuple(Timekeeper(n, Decimal(str(r))) for n, r in pairs)


VENDORS: list[Vendor] = [
    Vendor("V001", "Harrington & Cole LLP", "US", "United States", "New York, NY", "USD", ("USD",), Decimal("0"),
           "First Meridian Bank", "0260095934471", "FMBKUS33", "HC",
           _tk(("A. Harrington", 850), ("M. Cole", 620), ("J. Patel", 340))),
    Vendor("V002", "Whitmore Barristers Ltd", "GB", "United Kingdom", "London", "GBP", ("GBP", "EUR"), Decimal("0.20"),
           "Thames Commercial Bank", make_iban("GB", "TCBK40051231234567"), "TCBKGB2L", "WB",
           _tk(("E. Whitmore", 720), ("R. Ashby", 480), ("L. Grant", 260))),
    Vendor("V003", "Kessler & Partner GmbH", "DE", "Germany", "Frankfurt am Main", "EUR", ("EUR",), Decimal("0.19"),
           "Rheinland Handelsbank AG", make_iban("DE", "370400440532013000"), "RHBKDEFF", "KP",
           _tk(("K. Kessler", 540), ("S. Vogel", 410), ("T. Brandt", 230))),
    Vendor("V004", "Dubois Avocats S.A.", "FR", "France", "Paris", "EUR", ("EUR",), Decimal("0.20"),
           "Banque du Littoral", make_iban("FR", "20041010050500013M02606"), "BDLTFRPP", "DA",
           _tk(("C. Dubois", 600), ("A. Laurent", 420), ("N. Moreau", 210))),
    Vendor("V005", "Tanaka Legal Services K.K.", "JP", "Japan", "Tokyo", "JPY", ("JPY",), Decimal("0.10"),
           "Sakura Harbor Bank", "0012345678", "SHBKJPJT", "TL",
           _tk(("H. Tanaka", 65000), ("Y. Sato", 45000), ("K. Ito", 25000))),
    Vendor("V006", "Maple North Legal LLP", "CA", "Canada", "Toronto, ON", "CAD", ("CAD", "USD"), Decimal("0.13"),
           "Northern Prairie Bank", "003120041234567", "NPBKCATT", "MN",
           _tk(("D. MacLeod", 780), ("S. Tremblay", 520), ("P. Singh", 300))),
    Vendor("V007", "Southern Cross Litigation Pty Ltd", "AU", "Australia", "Sydney, NSW", "AUD", ("AUD",), Decimal("0.10"),
           "Coral Coast Bank", "06200012345678", "CCBKAU2S", "SC",
           _tk(("B. Walsh", 820), ("O. Nguyen", 560), ("I. Brooks", 310))),
    Vendor("V008", "Alpenrecht AG", "CH", "Switzerland", "Zurich", "CHF", ("CHF", "EUR"), Decimal("0.081"),
           "Bergbank Zurich AG", make_iban("CH", "00762011623852957"), "BGBKCHZZ", "AR",
           _tk(("M. Huber", 750), ("L. Keller", 520), ("F. Meier", 280))),
    Vendor("V009", "Mehta & Rao Advocates", "IN", "India", "Mumbai", "INR", ("INR", "USD"), Decimal("0.18"),
           "Deccan Mercantile Bank", "50100234567890", "DMBKINBB", "MR",
           _tk(("R. Mehta", 28000), ("P. Rao", 18000), ("V. Iyer", 9000))),
    Vendor("V010", "Lion City Legal Pte Ltd", "SG", "Singapore", "Singapore", "SGD", ("SGD", "USD"), Decimal("0.09"),
           "Straits Merchant Bank", "7012345678", "SMBKSGSG", "LC",
           _tk(("W. Tan", 900), ("J. Lim", 600), ("A. Rahman", 320))),
    Vendor("V011", "Bufete Ortega S.C.", "MX", "Mexico", "Mexico City", "MXN", ("MXN", "USD"), Decimal("0.16"),
           "Banco del Valle Central", "012180001234567891", "BVCMMXMM", "BO",
           _tk(("L. Ortega", 9500), ("G. Ramirez", 6500), ("E. Flores", 3500))),
    Vendor("V012", "Silva Pereira Advogados", "BR", "Brazil", "Sao Paulo", "BRL", ("BRL",), Decimal("0.05"),
           "Banco Atlantico Sul", "0012345678901", "BASLBRSP", "SP",
           _tk(("F. Silva", 2400), ("M. Pereira", 1600), ("C. Souza", 800))),
    Vendor("V013", "Van der Berg Advocaten B.V.", "NL", "Netherlands", "Amsterdam", "EUR", ("EUR",), Decimal("0.21"),
           "Polder Bank N.V.", make_iban("NL", "PLDR0417164300"), "PLDRNL2A", "VB",
           _tk(("J. Vandenberg", 580), ("S. Devries", 400), ("M. Jansen", 220))),
    Vendor("V014", "Expert Witness Partners LLC", "US", "United States", "Chicago, IL", "USD", ("USD",), Decimal("0"),
           "Liberty Plains Bank", "1210003589902", "LPBKUS44", "EW",
           _tk(("R. Hayes", 950), ("C. Morgan", 600))),
    Vendor("V015", "Global Translation Services Ltd", "GB", "United Kingdom", "Manchester", "GBP", ("GBP", "EUR", "USD"),
           Decimal("0.20"), "Albion Trade Bank", make_iban("GB", "ALTB20000055779911"), "ALTBGB22", "GT",
           _tk(("N. Clarke", 95), ("P. Evans", 75))),
]
VENDOR_BY_ID = {v.vendor_id: v for v in VENDORS}

CLIENTS = [
    (1, "C1001", "Northwind Pharmaceuticals Inc."), (2, "C1002", "Bluewater Shipping Co."),
    (3, "C1003", "Helios Energy Group"), (4, "C1004", "Aurora Software Ltd"),
    (5, "C1005", "Granite Capital Partners"), (6, "C1006", "Evergreen Foods SA"),
    (7, "C1007", "Orion Aerospace Corp"), (8, "C1008", "Sterling Retail Holdings"),
    (9, "C1009", "Pacific Rim Mining Ltd"), (10, "C1010", "Crescent Hotels Group"),
]
CLIENT_BY_NUMBER = {c[1]: c for c in CLIENTS}


@dataclass(frozen=True)
class Matter:
    matter_id: int
    client_number: str
    matter_number: str
    description: str
    kind: str
    vendors: tuple[str, ...]
    target_usd: Decimal  # typical invoice total in USD
    status: str = "OPEN"
    has_history: bool = True


def _m(mid, client, num, desc, kind, vendors, target, status="OPEN", history=True):
    return Matter(mid, client, num, desc, kind, tuple(vendors), Decimal(target), status, history)


MATTERS: list[Matter] = [
    _m(1, "C1001", "2024-0101", "Patent infringement litigation - EU", "patent", ["V003", "V013", "V015"], 18000),
    _m(2, "C1001", "2024-0102", "US FDA regulatory advice", "regulatory", ["V001", "V014"], 12000),
    _m(3, "C1001", "2025-0103", "Licensing agreements - Japan", "commercial", ["V005"], 9000),
    _m(4, "C1002", "2024-0201", "Cargo damage arbitration - London", "arbitration", ["V002", "V015"], 22000),
    _m(5, "C1002", "2024-0202", "Port concession dispute - Brazil", "litigation", ["V012"], 8000),
    _m(6, "C1002", "2025-0203", "Fleet financing - Singapore", "finance", ["V010"], 15000),
    _m(7, "C1003", "2024-0301", "Wind farm acquisition - Germany", "mna", ["V003", "V004"], 30000),
    _m(8, "C1003", "2024-0302", "Mining royalty dispute - Australia", "litigation", ["V007"], 16000),
    _m(9, "C1003", "2025-0303", "Solar project permits - India", "regulatory", ["V009"], 6000),
    _m(10, "C1004", "2024-0401", "Employment claims - UK", "employment", ["V002"], 7000),
    _m(11, "C1004", "2024-0402", "Data privacy compliance - EU", "regulatory", ["V004", "V013"], 13500),
    _m(12, "C1004", "2025-0403", "Series C financing", "finance", ["V001"], 20000),
    _m(13, "C1005", "2024-0501", "Fund formation - Switzerland", "finance", ["V008"], 25000),
    _m(14, "C1005", "2024-0502", "Cross-border tax structuring", "commercial", ["V008", "V002"], 14000),
    _m(15, "C1005", "2026-0503", "Acquisition of Mexican logistics group", "mna", ["V011"], 15000, history=False),
    _m(16, "C1006", "2024-0601", "Trademark portfolio - Latin America", "ip", ["V011", "V012"], 5000),
    _m(17, "C1006", "2024-0602", "Supply chain contracts - Mexico", "commercial", ["V011"], 6500),
    _m(18, "C1006", "2024-0603", "Product liability defense - Canada", "litigation", ["V006"], 19000),
    _m(19, "C1007", "2024-0701", "Export control investigation", "regulatory", ["V001", "V014"], 26000),
    _m(20, "C1007", "2024-0702", "Joint venture - Japan", "mna", ["V005"], 21000),
    _m(21, "C1007", "2024-0703", "Satellite contract arbitration", "arbitration", ["V002", "V008"], 28000),
    _m(22, "C1008", "2024-0801", "Store lease portfolio - Canada", "real_estate", ["V006"], 8000),
    _m(23, "C1008", "2024-0802", "Consumer class action - Australia", "litigation", ["V007"], 24000),
    _m(24, "C1008", "2023-0803", "Franchise restructuring", "commercial", ["V001"], 10000, status="CLOSED"),
    _m(25, "C1009", "2024-0901", "Environmental permits - Australia", "regulatory", ["V007"], 11000),
    _m(26, "C1009", "2024-0902", "Offtake agreement - India", "commercial", ["V009"], 7000),
    _m(27, "C1009", "2025-0903", "Shareholder dispute - Singapore", "litigation", ["V010"], 17000),
    _m(28, "C1010", "2024-1001", "Hotel acquisitions - Europe", "mna", ["V004", "V013", "V003"], 23000),
    _m(29, "C1010", "2024-1002", "Management agreements - Brazil", "commercial", ["V012"], 6000),
    _m(30, "C1010", "2025-1003", "Guest injury claims - US", "litigation", ["V001", "V014"], 13000),
]
MATTER_BY_NUMBER = {m.matter_number: m for m in MATTERS}

DESCRIPTIONS: dict[str, list[str]] = {
    "patent": ["Review prior art references", "Draft claim construction brief", "Prepare expert declaration on infringement",
               "Attend Markman hearing preparation", "Analyse patent file history", "Correspondence with opposing counsel re discovery"],
    "regulatory": ["Review agency guidance and draft memo", "Prepare submission to regulator", "Call with client compliance team",
                   "Draft response to information request", "Research regulatory precedent", "Attend meeting with agency staff"],
    "commercial": ["Draft master services agreement", "Negotiate licence terms with counterparty", "Review term sheet comments",
                   "Prepare contract markup", "Call with client on commercial terms", "Finalise schedules and exhibits"],
    "arbitration": ["Prepare statement of claim", "Review tribunal procedural order", "Witness statement interviews",
                    "Draft reply submission", "Attend case management conference", "Document production review"],
    "litigation": ["Draft motion and supporting brief", "Deposition preparation", "Review document production",
                   "Attend court hearing", "Legal research on liability issues", "Correspondence with opposing counsel"],
    "finance": ["Review facility agreement draft", "Prepare closing checklist", "Negotiate security documents",
                "Draft legal opinion", "Call with lenders counsel", "Review fund offering memorandum"],
    "mna": ["Due diligence review of target contracts", "Draft share purchase agreement", "Negotiate disclosure schedules",
            "Prepare regulatory merger filing", "Call with client deal team", "Review data room documents"],
    "employment": ["Review employment tribunal claim", "Draft witness statements", "Advise on settlement options",
                   "Prepare response to grievance", "Call with HR director"],
    "ip": ["File trademark applications", "Respond to office action", "Trademark clearance search",
           "Prepare opposition filing", "Portfolio renewal review"],
    "real_estate": ["Review lease renewal terms", "Negotiate landlord consent", "Draft lease amendment",
                    "Title and zoning review", "Call with property manager"],
}
TRANSLATION_DESCRIPTIONS = ["Certified translation of exhibits", "Interpretation at hearing", "Translation of witness statements"]

# USD per 1 unit of currency (centre of the mock random walk)
FX_CENTRES: dict[str, Decimal] = {
    "USD": Decimal("1"), "EUR": Decimal("1.08"), "GBP": Decimal("1.27"), "JPY": Decimal("0.0067"),
    "CAD": Decimal("0.73"), "AUD": Decimal("0.66"), "CHF": Decimal("1.13"), "INR": Decimal("0.012"),
    "SGD": Decimal("0.75"), "MXN": Decimal("0.055"), "BRL": Decimal("0.19"), "KWD": Decimal("3.25"),
}

HISTORY_DAYS = 750      # history spans ~2 years before today
HISTORY_GAP_DAYS = 21   # newest historical invoice is 3 weeks old
INVOICES_PER_MATTER = 56


def today() -> date:
    return date.today()


# ----------------------------------------------------------------------------
# Anchor historical invoice duplicated by sample #3
# ----------------------------------------------------------------------------
ANCHOR_DUPLICATE = {
    "vendor_id": "V008",
    "matter_number": "2024-0501",
    "invoice_number": "AR-2026-0412",
    "days_ago": 45,
    "currency": "CHF",
    "lines": [  # description, timekeeper, hours
        ("Review fund offering memorandum", "M. Huber", Decimal("9.5")),
        ("Negotiate security documents", "L. Keller", Decimal("14.0")),
        ("Prepare closing checklist", "F. Meier", Decimal("12.0")),
    ],
}


# ----------------------------------------------------------------------------
# Sample invoice specifications (one PDF each)
# ----------------------------------------------------------------------------
@dataclass
class SampleSpec:
    file_stem: str
    vendor_id: str
    matter_number: str
    currency: str
    template: str                 # classic | modern | european
    number_style: str = "us"      # us | eu | ch
    scale: Decimal = Decimal("1.0")  # multiple of the matter's typical invoice
    days_ago: int = 3
    currency_display: str = "code"   # code | symbol_and_code | symbol_only
    rate_overrides: dict[str, Decimal] = field(default_factory=dict)  # timekeeper -> rate multiplier
    bank_override: tuple[str, str, str] | None = None  # bank name, account, swift
    printed_subtotal_offset: Decimal = Decimal("0")
    injection: bool = False
    scan: bool = False
    fixed_lines: list | None = None
    invoice_number: str | None = None
    expected_decision: str = "ADVANCED_TO_APPROVAL"
    expected_reasons: list[str] = field(default_factory=list)
    note: str = ""


SAMPLES: list[SampleSpec] = [
    SampleSpec("01_clean_usd_harrington", "V001", "2025-1003", "USD", "classic",
               note="Clean USD invoice; all checks should pass."),
    SampleSpec("02_clean_eur_kessler", "V003", "2024-0301", "EUR", "modern", currency_display="symbol_and_code",
               note="Clean EUR invoice with euro symbol and ISO code."),
    SampleSpec("03_clean_gbp_whitmore", "V002", "2024-0401", "GBP", "classic",
               note="Clean GBP invoice with 20% VAT."),
    SampleSpec("04_clean_jpy_tanaka", "V005", "2024-0702", "JPY", "modern", currency_display="symbol_and_code",
               note="Clean JPY invoice (0 minor units)."),
    SampleSpec("05_anomaly_total_far_above_range", "V007", "2024-0802", "AUD", "classic", scale=Decimal("7.5"),
               expected_decision="NEEDS_REVIEW", expected_reasons=["TOTAL_ABOVE_P95", "TOTAL_ZSCORE_HIGH"],
               note="Total about 7.5x the matter's typical invoice."),
    SampleSpec("06_duplicate_of_historical", "V008", "2024-0501", "CHF", "european", number_style="ch",
               fixed_lines=ANCHOR_DUPLICATE["lines"], invoice_number=ANCHOR_DUPLICATE["invoice_number"],
               days_ago=ANCHOR_DUPLICATE["days_ago"],
               expected_decision="NEEDS_REVIEW", expected_reasons=["DUPLICATE_INVOICE_NUMBER"],
               note="Same vendor + invoice number as a historical invoice (Swiss 1'234.56 format)."),
    SampleSpec("07_bank_details_mismatch", "V003", "2024-0101", "EUR", "modern", currency_display="code",
               bank_override=("Baltic Offshore Credit Bank", make_iban("LV", "BOCB0000123456789"), "BOCBLV22"),
               expected_decision="NEEDS_REVIEW", expected_reasons=["BANK_DETAILS_MISMATCH"],
               note="Beneficiary IBAN/SWIFT differ from the vendor's account on file (fraud pattern)."),
    SampleSpec("08_line_items_do_not_sum", "V004", "2024-1001", "EUR", "european", number_style="eu",
               printed_subtotal_offset=Decimal("500.00"),
               expected_decision="NEEDS_REVIEW", expected_reasons=["ARITHMETIC_LINES_MISMATCH"],
               note="Printed subtotal/total are 500.00 EUR higher than the sum of the line items."),
    SampleSpec("09_ambiguous_dollar_symbol", "V006", "2024-0603", "CAD", "classic", currency_display="symbol_only",
               expected_decision="NEEDS_REVIEW", expected_reasons=["CURRENCY_AMBIGUOUS"],
               note="Only '$' is shown; vendor bills in both CAD and USD."),
    SampleSpec("10_european_number_format_eur", "V013", "2024-0402", "EUR", "european", number_style="eu",
               fixed_lines=[("Review agency guidance and draft memo", "J. Vandenberg", Decimal("10.0")),
                            ("Prepare submission to regulator", "S. Devries", Decimal("6.0")),
                            ("Research regulatory precedent", "M. Jansen", Decimal("3.6"))],
               note="European number format, e.g. 12.450,00 EUR; should parse and advance."),
    SampleSpec("11_cold_start_new_matter", "V011", "2026-0503", "MXN", "modern", currency_display="code",
               expected_reasons=["COLD_START_BASELINE"],
               note="New matter with no history; baseline falls back to client level (LOW note only)."),
    SampleSpec("12_rate_spike_timekeeper", "V010", "2025-0903", "SGD", "classic",
               rate_overrides={"W. Tan": Decimal("1.6")}, scale=Decimal("0.9"),
               expected_decision="NEEDS_REVIEW", expected_reasons=["RATE_SPIKE"],
               note="Partner W. Tan billed ~60% above the matter's historical rate."),
    SampleSpec("13_prompt_injection", "V009", "2024-0902", "INR", "modern", currency_display="code", injection=True,
               expected_decision="NEEDS_REVIEW", expected_reasons=["PROMPT_INJECTION_SUSPECTED"],
               note="Contains visible and hidden text instructing AI systems to approve."),
    SampleSpec("14_low_quality_skewed_scan", "V012", "2024-1002", "BRL", "classic", scan=True,
               expected_decision="NEEDS_REVIEW", expected_reasons=["EXTRACTION_INSUFFICIENT"],
               note="Rotated, noisy image-only scan; extractors fail or disagree."),
]


def sample_dates(spec: SampleSpec) -> tuple[date, date]:
    d = today() - timedelta(days=spec.days_ago)
    return d, d + timedelta(days=30)
