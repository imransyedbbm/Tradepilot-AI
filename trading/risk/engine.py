"""Independent risk engine. Recomputes quantity; never trusts strategy-supplied size."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading.config import RiskLimits
from trading.domain import RiskDecision, TradeProposal, floor_int
from trading.risk.costs import CostModel

H = Decimal(100)


@dataclass
class PortfolioState:
    equity: Decimal
    available_funds: Decimal
    gross_exposure: Decimal
    open_risk: Decimal
    realized_pnl_today: Decimal
    open_position_tokens: set[int]
    open_order_tokens: set[int]
    entries_today: int
    position_count: int
    style_budget_remaining: Decimal  # capital left in this proposal's style bucket


@dataclass
class MarketContext:
    now: datetime
    last_tick_at: datetime | None
    bid: Decimal
    ask: Decimal
    market_open: bool
    universe: set[int]
    broker_state_known: bool = True


@dataclass
class RiskEngine:
    limits: RiskLimits
    costs: CostModel = field(default_factory=CostModel)
    halted: bool = False
    halt_reason: str = ""
    consecutive_losses: dict[str, int] = field(default_factory=dict)
    suspended_strategies: set[str] = field(default_factory=set)

    # --- halts -------------------------------------------------------------
    def latch_halt(self, reason: str) -> None:
        self.halted, self.halt_reason = True, reason

    def clear_halt(self, operator: str) -> None:
        if not operator:
            raise ValueError("operator required to clear a halt")
        self.halted, self.halt_reason = False, ""

    def record_trade_result(self, strategy_id: str, pnl: Decimal) -> None:
        n = self.consecutive_losses.get(strategy_id, 0)
        n = n + 1 if pnl < 0 else 0
        self.consecutive_losses[strategy_id] = n
        if n >= self.limits.max_consecutive_losses:
            self.suspended_strategies.add(strategy_id)

    def daily_loss_breached(self, p: PortfolioState) -> bool:
        return -p.realized_pnl_today >= p.equity * self.limits.max_daily_loss_pct / H

    # --- sizing ------------------------------------------------------------
    def size(self, prop: TradeProposal, p: PortfolioState) -> tuple[int, Decimal, Decimal, list[str]]:
        L, entry, stop = self.limits, prop.entry_price, prop.stop_price
        reasons: list[str] = []
        if entry <= stop:
            return 0, Decimal(0), Decimal(0), ["entry must be above stop (long-only)"]
        per_share_cost = self.costs.round_trip_per_share(entry, prop.signal.style)
        risk_per_share = entry - stop + per_share_cost
        daily_left = p.equity * L.max_daily_loss_pct / H + min(p.realized_pnl_today, Decimal(0))
        open_left = p.equity * L.max_open_risk_pct / H - p.open_risk
        budget = min(p.equity * L.per_trade_risk_pct / H, daily_left, open_left)
        if budget <= 0:
            return 0, Decimal(0), per_share_cost, ["no risk budget remaining"]
        q = floor_int(budget / risk_per_share)
        caps = {
            "available funds": floor_int(p.available_funds / entry),
            "single-position cap": floor_int(p.equity * L.max_single_position_pct / H / entry),
            "gross exposure cap": floor_int((p.equity * L.max_gross_exposure_pct / H - p.gross_exposure) / entry),
            "style allocation": floor_int(p.style_budget_remaining / entry),
        }
        for name, cap in caps.items():
            if cap < q:
                q = max(cap, 0)
                reasons.append(f"capped by {name}")
        lot = prop.signal.instrument.lot_size
        q -= q % lot
        return q, Decimal(q) * risk_per_share, per_share_cost * q, reasons

    # --- evaluation --------------------------------------------------------
    def evaluate(self, prop: TradeProposal, p: PortfolioState, m: MarketContext) -> RiskDecision:
        s, L, rej = prop.signal, self.limits, []
        tok = s.instrument.token
        if self.halted:
            rej.append(f"execution halt active: {self.halt_reason}")
        if not m.broker_state_known:
            rej.append("broker/account state unknown")
        if m.last_tick_at is None or (m.now - m.last_tick_at).total_seconds() > L.stale_data_seconds:
            rej.append("market data stale")
        if not prop.data_quality_ok:
            rej.append("data quality check failed")
        if not m.market_open:
            rej.append("market closed")
        if tok not in m.universe or not s.instrument.tradable:
            rej.append("instrument not in permitted universe")
        if s.strategy_id in self.suspended_strategies:
            rej.append("strategy suspended after consecutive losses")
        if self.daily_loss_breached(p):
            rej.append("daily loss limit breached")
        if p.position_count >= L.max_positions:
            rej.append("max concurrent positions reached")
        if p.entries_today >= L.max_entries_per_day:
            rej.append("max entries per day reached")
        if tok in p.open_position_tokens or tok in p.open_order_tokens:
            rej.append("conflicting position or open order")
        if prop.stop_price is None or prop.stop_price <= 0:
            rej.append("missing or invalid stop")
        mid = (m.bid + m.ask) / 2
        if mid > 0 and (m.ask - m.bid) / mid * H > L.max_spread_pct:
            rej.append("spread exceeds threshold")
        if s.style.value == "INTRADAY" and m.now.strftime("%H:%M") >= L.intraday_entry_cutoff:
            rej.append("past intraday entry cutoff")
        if prop.valid_until and m.now > prop.valid_until:
            rej.append("proposal expired")

        qty, risk_amt, costs, notes = self.size(prop, p)
        if qty < 1:
            rej.append("computed quantity is zero" + (f" ({'; '.join(notes)})" if notes else ""))
        if rej:
            return RiskDecision(False, 0, tuple(rej))
        return RiskDecision(True, qty, tuple(notes), risk_amt, costs)
