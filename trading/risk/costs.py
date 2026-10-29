"""Indian cash-equity transaction-cost model.

Rates are configurable estimates and must be verified against the broker's current
charge sheet before relying on them. Defaults approximate a discount broker in 2025-26.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading.domain import TradeStyle, money


@dataclass(frozen=True)
class CostModel:
    brokerage_pct_intraday: Decimal = Decimal("0.03")   # % of turnover, capped
    brokerage_cap: Decimal = Decimal("20")              # INR per executed order
    brokerage_delivery: Decimal = Decimal("0")
    stt_intraday_sell_pct: Decimal = Decimal("0.025")
    stt_delivery_pct: Decimal = Decimal("0.1")          # both sides
    exchange_txn_pct: Decimal = Decimal("0.00297")
    sebi_per_crore: Decimal = Decimal("10")
    stamp_intraday_buy_pct: Decimal = Decimal("0.003")
    stamp_delivery_buy_pct: Decimal = Decimal("0.015")
    gst_pct: Decimal = Decimal("18")

    def charges(self, side: str, qty: int, price: Decimal, style: TradeStyle) -> Decimal:
        turnover = Decimal(qty) * price
        pct = lambda p: turnover * p / Decimal(100)  # noqa: E731
        if style == TradeStyle.INTRADAY:
            brokerage = min(pct(self.brokerage_pct_intraday), self.brokerage_cap)
            stt = pct(self.stt_intraday_sell_pct) if side == "SELL" else Decimal(0)
            stamp = pct(self.stamp_intraday_buy_pct) if side == "BUY" else Decimal(0)
        else:
            brokerage = self.brokerage_delivery
            stt = pct(self.stt_delivery_pct)
            stamp = pct(self.stamp_delivery_buy_pct) if side == "BUY" else Decimal(0)
        exch = pct(self.exchange_txn_pct)
        sebi = turnover * self.sebi_per_crore / Decimal(10_000_000)
        gst = (brokerage + exch + sebi) * self.gst_pct / Decimal(100)
        return money(brokerage + stt + stamp + exch + sebi + gst)

    def round_trip_per_share(self, price: Decimal, style: TradeStyle) -> Decimal:
        qty = 100
        total = self.charges("BUY", qty, price, style) + self.charges("SELL", qty, price, style)
        return total / Decimal(qty)
