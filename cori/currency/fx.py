"""FX conversion using the FX_RATES table (mock rates for the demo).

FX_RATES stores ``from_currency -> USD`` rates per date (units of USD per 1
unit of the currency). Other pairs are derived through USD. The rate is taken
from the invoice date, or the nearest prior date when that day is missing.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from cori.currency.parsing import quantize
from cori.models.schemas import FxRateUsed

PIVOT = "USD"

# (currency, on_date) -> (rate_to_usd, actual_rate_date) or None
RateLookup = Callable[[str, date], Optional[tuple[Decimal, date]]]


class FxRateNotFound(LookupError):
    pass


class FxService:
    def __init__(self, lookup: RateLookup):
        self._lookup = lookup

    def _to_pivot(self, currency: str, on_date: date) -> tuple[Decimal, date]:
        if currency == PIVOT:
            return Decimal(1), on_date
        found = self._lookup(currency, on_date)
        if not found:
            raise FxRateNotFound(f"No FX rate {currency}->{PIVOT} on or before {on_date}")
        return found

    def get_rate(self, from_ccy: str, to_ccy: str, on_date: date) -> FxRateUsed:
        from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
        if from_ccy == to_ccy:
            return FxRateUsed(from_currency=from_ccy, to_currency=to_ccy, rate=Decimal(1),
                              rate_date=on_date, requested_date=on_date)
        f_rate, f_date = self._to_pivot(from_ccy, on_date)
        t_rate, t_date = self._to_pivot(to_ccy, on_date)
        rate = f_rate / t_rate
        # date of the oldest real (non-pivot) rate involved
        rate_date = min(d for ccy, d in ((from_ccy, f_date), (to_ccy, t_date)) if ccy != PIVOT)
        return FxRateUsed(from_currency=from_ccy, to_currency=to_ccy, rate=rate,
                          rate_date=rate_date, requested_date=on_date)

    def convert(self, amount: Decimal, from_ccy: str, to_ccy: str, on_date: date) -> tuple[Decimal, FxRateUsed]:
        fx = self.get_rate(from_ccy, to_ccy, on_date)
        return quantize(amount * fx.rate, to_ccy), fx


def dict_lookup(table: dict[str, dict[date, Decimal]]) -> RateLookup:
    """In-memory lookup (tests, seeding): nearest prior date wins."""
    def lookup(currency: str, on_date: date):
        series = table.get(currency)
        if not series:
            return None
        prior = [d for d in series if d <= on_date]
        if not prior:
            return None
        d = max(prior)
        return series[d], d
    return lookup
