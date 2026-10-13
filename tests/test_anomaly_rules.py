"""Anomaly agent statistics and rules as pure functions (no DB, no LLM)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from cori.agents.anomaly import build_baseline, check_frequency, check_rates, check_total, choose_level, percentile
from cori.config import AnomalyConfig
from cori.models.schemas import NormalizedLineItem
from tests.conftest import make_invoice

D = Decimal
CFG = AnomalyConfig()


def test_percentile_linear_interpolation():
    vals = [D(v) for v in range(1, 101)]
    assert percentile(vals, 95) == D("95.05")
    assert percentile([D("10")], 95) == D("10")
    with pytest.raises(ValueError):
        percentile([], 95)


def rows(totals_usd, currency="EUR", fx=D("1.1")):
    return [{"total_usd": D(t), "total": (D(t) / fx).quantize(D("0.01")), "currency": currency} for t in totals_usd]


def test_baseline_in_base_currency_with_native_for_display():
    b = build_baseline("matter", 7, rows(["100", "200", "300", "400", "500"]), CFG, "USD", "EUR", ["matter:5"])
    assert b.median_base == D("300.00") and b.mean_base == D("300.00") and b.count == 5
    assert b.p95_base == D("480.00") and b.std_base == D("141.42")
    assert b.native_currency == "EUR" and b.native_count == 5 and b.native_median == D("272.73")


def test_cold_start_falls_back_matter_client_vendor_global():
    few, many = rows(["1"] * 3), rows(["1"] * 8)
    assert choose_level([("matter", 1, many)], 5)[0] == "matter"
    level, key, _, tried = choose_level([("matter", 1, few), ("client", 9, many)], 5)
    assert (level, key) == ("client", 9) and tried == ["matter:3", "client:8"]
    assert choose_level([("matter", 1, few), ("client", 9, few), ("vendor", "V1", many)], 5)[0] == "vendor"
    assert choose_level([("matter", 1, []), ("client", 9, few), ("vendor", "V1", few)], 5)[0] == "global"


def test_cold_start_minimum_is_configurable():
    assert choose_level([("matter", 1, rows(["1"] * 3))], 3)[0] == "matter"


def test_total_within_range_no_flags():
    b = build_baseline("matter", 1, rows([str(1000 + i * 10) for i in range(50)]), CFG, "USD", None, [])
    flags, z = check_total(D("1250"), b, CFG, "matter 1")
    assert flags == [] and z is not None and z < 3


def test_total_above_p95_and_zscore():
    b = build_baseline("matter", 1, rows([str(1000 + i * 10) for i in range(50)]), CFG, "USD", None, [])
    flags, z = check_total(D("9000"), b, CFG, "matter 1")
    assert {f.code for f in flags} == {"TOTAL_ABOVE_P95", "TOTAL_ZSCORE_HIGH"} and z > 3
    assert next(f for f in flags if f.code == "TOTAL_ZSCORE_HIGH").severity.value == "HIGH"
    assert next(f for f in flags if f.code == "TOTAL_ABOVE_P95").severity.value == "MEDIUM"


def test_thresholds_are_configurable():
    b = build_baseline("matter", 1, rows([str(1000 + i * 10) for i in range(50)]), CFG, "USD", None, [])
    lenient = AnomalyConfig(percentile=99.9, z_score=50)
    b_len = build_baseline("matter", 1, rows([str(1000 + i * 10) for i in range(50)]), lenient, "USD", None, [])
    # history tops out at 1,490: 1,480 is above p95 (1,465.50) but below p99.9 (1,489.51)
    assert check_total(D("1480"), b, CFG, "m")[0] and not check_total(D("1480"), b_len, lenient, "m")[0]


def test_global_threshold_when_no_history():
    b = build_baseline("global", None, [], CFG, "USD", None, [])
    assert check_total(D("10000"), b, CFG, "global")[0] == []
    flags, _ = check_total(D("60000"), b, CFG, "global")
    assert flags[0].code == "TOTAL_ABOVE_GLOBAL_THRESHOLD"


def _history(tk, unit_prices_usd):
    return [{"timekeeper": tk, "description": "Work", "unit_price": D(p), "total": D("1000"), "total_usd": D("1000")}
            for p in unit_prices_usd]


def test_rate_spike_flagged_per_timekeeper():
    inv = make_invoice(line_items=[NormalizedLineItem(description="Work", timekeeper="W. Tan",
                                                      unit_price=D("1400.00"), amount=D("1400.00"))])
    flags = check_rates(inv, _history("W. Tan", ["1000", "1000", "1000"]), D("1"), "USD", CFG)
    assert len(flags) == 1 and flags[0].code == "RATE_SPIKE" and flags[0].severity.value == "MEDIUM"


def test_rate_within_threshold_not_flagged():
    inv = make_invoice(line_items=[NormalizedLineItem(timekeeper="W. Tan", unit_price=D("1250.00"))])
    assert check_rates(inv, _history("W. Tan", ["1000"] * 3), D("1"), "USD", CFG) == []


def test_rate_compared_in_base_currency():
    """1,000 SGD at 0.75 = 750 USD, not a spike vs a 700 USD history."""
    inv = make_invoice(line_items=[NormalizedLineItem(timekeeper="W. Tan", unit_price=D("1000.00"))])
    assert check_rates(inv, _history("W. Tan", ["700"] * 3), D("0.75"), "USD", CFG) == []


def test_rate_needs_some_history():
    inv = make_invoice(line_items=[NormalizedLineItem(timekeeper="New Person", unit_price=D("9999"))])
    assert check_rates(inv, _history("W. Tan", ["100"] * 3), D("1"), "USD", CFG) == []


def test_frequency_check():
    today = date(2026, 10, 13)
    monthly = [today - timedelta(days=30 * i) for i in range(1, 24)]  # ~1 per month
    assert check_frequency(monthly, today, CFG) is None
    burst = monthly + [today - timedelta(days=d) for d in (2, 5, 9, 12)]
    flag = check_frequency(burst, today, CFG)
    assert flag is not None and flag.code == "BILLING_FREQUENCY_HIGH" and flag.details["recent"] == 6
