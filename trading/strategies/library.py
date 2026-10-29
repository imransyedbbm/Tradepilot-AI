"""Strategies A–E. Research hypotheses only — not recommendations or proven sources of return."""
from __future__ import annotations

from decimal import Decimal

from trading.domain import Direction, TradeStyle, TradingSignal, money
from trading.market_data.feed import vwap
from trading.strategies.base import Strategy, StrategyContext, sma

D = Decimal


def _sig(s: Strategy, ctx: StrategyContext, stop: Decimal, why: str, target: Decimal | None = None, inv: str = ""):
    last = ctx.candles[-1]
    return TradingSignal(s.id, s.version, ctx.instrument, last.ts, s.timeframe, Direction.LONG,
                         last.close, money(stop), s.style, why, money(target) if target else None, inv)


class TrendMomentum(Strategy):
    id, name, version, style, timeframe = "A", "Trend & momentum", "1.0.0", TradeStyle.INTRADAY, "1m"

    @classmethod
    def default_params(cls):
        return {"fast": 9, "slow": 21, "mom": 5, "stop_pct": "0.6", "rr": "2"}

    def generate_signal(self, ctx):
        p, c = self.params, ctx.candles
        closes = [x.close for x in c]
        if len(closes) < p["slow"] + 1:
            return None
        f, s = sma(closes, p["fast"]), sma(closes, p["slow"])
        f_prev, s_prev = sma(closes[:-1], p["fast"]), sma(closes[:-1], p["slow"])
        mom = closes[-1] - closes[-1 - p["mom"]]
        avg_vol = sum(x.volume for x in c[-20:]) / min(len(c), 20)
        if f_prev <= s_prev and f > s and mom > 0 and c[-1].volume >= avg_vol:
            stop = closes[-1] * (1 - D(p["stop_pct"]) / 100)
            return _sig(self, ctx, stop, f"SMA{p['fast']} crossed above SMA{p['slow']} with positive momentum",
                        closes[-1] + (closes[-1] - stop) * D(p["rr"]), "close below slow SMA")
        return None


class VwapReclaim(Strategy):
    id, name, version, style, timeframe = "B", "VWAP reclaim", "1.0.0", TradeStyle.INTRADAY, "1m"

    @classmethod
    def default_params(cls):
        return {"min_bars": 15, "stop_buffer_pct": "0.15", "rr": "2"}

    def generate_signal(self, ctx):
        c = ctx.candles
        if len(c) < self.params["min_bars"]:
            return None
        v_prev, v = vwap(list(c[:-1])), vwap(list(c))
        if v is None or v_prev is None:
            return None
        if c[-2].close < v_prev and c[-1].close > v and c[-1].volume > c[-2].volume:
            stop = min(c[-1].low, v) * (1 - D(self.params["stop_buffer_pct"]) / 100)
            if stop >= c[-1].close:
                return None
            return _sig(self, ctx, stop, f"Reclaimed VWAP {v} on rising volume",
                        c[-1].close + (c[-1].close - stop) * D(self.params["rr"]), "close back below VWAP")
        return None


class OpeningRangeBreakout(Strategy):
    id, name, version, style, timeframe = "C", "Opening-range breakout", "1.0.0", TradeStyle.INTRADAY, "1m"

    @classmethod
    def default_params(cls):
        return {"range_minutes": 15, "rr": "1.5"}

    def generate_signal(self, ctx):
        n, c = self.params["range_minutes"], ctx.candles
        if len(c) <= n:  # no entries before the range is complete
            return None
        rng = c[:n]
        hi, lo = max(x.high for x in rng), min(x.low for x in rng)
        if c[-2].close <= hi < c[-1].close:
            return _sig(self, ctx, lo, f"Broke {n}-min opening range high {hi}",
                        c[-1].close + (c[-1].close - lo) * D(self.params["rr"]), f"close back inside range below {hi}")
        return None


class TrendSwing(Strategy):
    id, name, version, style, timeframe = "D", "Trend-following swing", "1.0.0", TradeStyle.SWING, "1d"

    @classmethod
    def default_params(cls):
        return {"fast": 20, "slow": 50, "atr": 14, "atr_mult": "2", "max_hold_days": 15}

    def generate_signal(self, ctx):
        p, c = self.params, ctx.candles
        closes = [x.close for x in c]
        if len(c) < p["slow"] + 1:
            return None
        f, s = sma(closes, p["fast"]), sma(closes, p["slow"])
        trs = [max(x.high - x.low, abs(x.high - y.close), abs(x.low - y.close)) for y, x in zip(c[-p["atr"] - 1:], c[-p["atr"]:])]
        atr = sum(trs) / len(trs)
        if f > s and closes[-1] > f and closes[-2] <= sma(closes[:-1], p["fast"]):
            stop = closes[-1] - atr * D(p["atr_mult"])
            return _sig(self, ctx, stop, f"Pullback resolved above SMA{p['fast']} in SMA{p['fast']}>SMA{p['slow']} uptrend",
                        None, f"close below SMA{p['slow']} or {p['max_hold_days']} sessions elapsed")
        return None


class BreakoutPullback(Strategy):
    id, name, version, style, timeframe = "E", "Breakout & pullback", "1.0.0", TradeStyle.SWING, "1d"

    @classmethod
    def default_params(cls):
        return {"lookback": 20, "retest_pct": "1.5", "rr": "2"}

    def generate_signal(self, ctx):
        n, c = self.params["lookback"], ctx.candles
        if len(c) < n + 5:
            return None
        base = c[-n - 5:-5]
        res = max(x.high for x in base)
        broke = any(x.close > res for x in c[-5:-1])
        near = abs(c[-1].low - res) / res * 100 <= D(self.params["retest_pct"])
        if broke and near and c[-1].close > res:
            stop = min(x.low for x in c[-5:]) * D("0.995")
            return _sig(self, ctx, stop, f"Retest of broken {n}-day resistance {res} held",
                        c[-1].close + (c[-1].close - stop) * D(self.params["rr"]), f"daily close below {res}")
        return None


REGISTRY: dict[str, type[Strategy]] = {
    s.id: s for s in (TrendMomentum, VwapReclaim, OpeningRangeBreakout, TrendSwing, BreakoutPullback)
}
