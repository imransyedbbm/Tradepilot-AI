"""Chronological backtester.

Look-ahead protection: at bar i the strategy sees candles[0..i] (all completed);
entries fill at bar i+1's OPEN plus slippage. Stops that gap through fill at the
open, not the stop price. Costs come from the same CostModel as paper trading.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading.domain import Candle, Instrument, TradeStyle, floor_int, money
from trading.risk.costs import CostModel
from trading.strategies.base import Strategy, StrategyContext


@dataclass
class BTTrade:
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    entry: Decimal
    exit: Decimal
    qty: int
    costs: Decimal
    reason: str

    @property
    def net(self) -> Decimal:
        return (self.exit - self.entry) * self.qty - self.costs


@dataclass
class BacktestResult:
    strategy: str
    version: str
    start_equity: Decimal
    trades: list[BTTrade]
    equity_curve: list[tuple[datetime, Decimal]]
    synthetic_data: bool
    notes: list[str] = field(default_factory=list)

    def metrics(self) -> dict:
        t, eq = self.trades, [e for _, e in self.equity_curve] or [self.start_equity]
        wins = [x.net for x in t if x.net > 0]
        losses = [x.net for x in t if x.net <= 0]
        peak, mdd = eq[0], Decimal(0)
        for e in eq:
            peak = max(peak, e)
            mdd = max(mdd, (peak - e) / peak)
        rets = [float(b / a - 1) for a, b in zip(eq, eq[1:]) if a]
        mean = sum(rets) / len(rets) if rets else 0.0
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
        dsd = math.sqrt(sum(min(r, 0) ** 2 for r in rets) / len(rets)) if rets else 0.0
        ann = math.sqrt(252)
        gross_l = -sum(losses) if losses else Decimal(0)
        return {
            "trades": len(t),
            "net_pnl": str(money(sum((x.net for x in t), Decimal(0)))),
            "net_return_pct": round(float((eq[-1] / self.start_equity - 1) * 100), 3),
            "max_drawdown_pct": round(float(mdd * 100), 3),
            "win_rate_pct": round(len(wins) / len(t) * 100, 1) if t else None,
            "avg_win": str(money(sum(wins) / len(wins))) if wins else None,
            "avg_loss": str(money(sum(losses) / len(losses))) if losses else None,
            "profit_factor": round(float(sum(wins) / gross_l), 2) if wins and gross_l else None,
            "expectancy": str(money(sum((x.net for x in t), Decimal(0)) / len(t))) if t else None,
            "total_costs": str(money(sum((x.costs for x in t), Decimal(0)))),
            "sharpe_per_bar_annualised_252": round(mean / sd * ann, 2) if sd else None,
            "sortino_per_bar_annualised_252": round(mean / dsd * ann, 2) if dsd else None,
            "synthetic_data": self.synthetic_data,
            "sample_size_warning": len(t) < 30,
        }


def run_backtest(strategy: Strategy, inst: Instrument, candles: list[Candle], equity: Decimal,
                 risk_pct: Decimal = Decimal("0.25"), slippage_bps: Decimal = Decimal("5"),
                 costs: CostModel | None = None, max_hold_bars: int | None = None) -> BacktestResult:
    costs = costs or CostModel()
    trades, curve, pos = [], [], None
    cash = equity
    slip = slippage_bps / Decimal(10_000)
    intraday = strategy.style == TradeStyle.INTRADAY
    pending = None
    for i, c in enumerate(candles):
        # 1) fill pending entry at this bar's open (signal came from previous completed bar)
        if pending and pos is None:
            entry = money(c.open * (1 + slip))
            stop, target = pending.stop_price, pending.target_price
            if entry > stop:
                rps = entry - stop + costs.round_trip_per_share(entry, strategy.style)
                qty = min(floor_int(cash * risk_pct / 100 / rps), floor_int(cash * Decimal("0.15") / entry))
                if qty > 0:
                    pos = {"entry": entry, "stop": stop, "target": target, "qty": qty, "ts": c.ts, "i": i,
                           "buy_cost": costs.charges("BUY", qty, entry, strategy.style)}
        pending = None
        # 2) manage open position within this bar
        if pos:
            exit_px, why = None, ""
            if c.open <= pos["stop"]:
                exit_px, why = c.open, "stop (gap)"
            elif c.low <= pos["stop"]:
                exit_px, why = pos["stop"], "stop"
            elif pos["target"] and c.high >= pos["target"]:
                exit_px, why = pos["target"], "target"
            elif max_hold_bars and i - pos["i"] >= max_hold_bars:
                exit_px, why = c.close, "time"
            elif intraday and (i + 1 == len(candles) or candles[i + 1].ts.date() != c.ts.date()):
                exit_px, why = c.close, "session end"
            if exit_px is not None:
                exit_px = money(exit_px * (1 - slip))
                cst = pos["buy_cost"] + costs.charges("SELL", pos["qty"], exit_px, strategy.style)
                tr = BTTrade(inst.symbol, pos["ts"], c.ts, pos["entry"], exit_px, pos["qty"], cst, why)
                trades.append(tr)
                cash += tr.net
                pos = None
        mtm = cash + ((c.close - pos["entry"]) * pos["qty"] if pos else 0)
        curve.append((c.ts, money(mtm)))
        # 3) evaluate strategy on completed history (no future bars)
        if pos is None and i + 1 < len(candles):
            sig = strategy.generate_signal(StrategyContext(inst, tuple(candles[: i + 1])))
            if sig:
                pending = sig
    return BacktestResult(strategy.id, strategy.version, equity, trades, curve,
                          any(c.synthetic for c in candles))


def walk_forward(strategy_cls, param_grid: list[dict], inst: Instrument, candles: list[Candle], equity: Decimal,
                 folds: int = 3, train_frac: float = 0.7) -> list[dict]:
    """Chronological folds: choose params on train window, evaluate once on the following test window."""
    out, n = [], len(candles)
    size = n // folds
    for f in range(folds):
        seg = candles[f * size:(f + 1) * size]
        cut = int(len(seg) * train_frac)
        train, test = seg[:cut], seg[cut:]
        scored = []
        for params in param_grid:
            r = run_backtest(strategy_cls(**params), inst, train, equity)
            scored.append((float(r.equity_curve[-1][1]) if r.equity_curve else 0, params))
        best = max(scored, key=lambda x: x[0])[1]
        res = run_backtest(strategy_cls(**best), inst, test, equity)
        out.append({"fold": f + 1, "params": best, "train_bars": len(train), "test_bars": len(test),
                    "out_of_sample": res.metrics()})
    return out
