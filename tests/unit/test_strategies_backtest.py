from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D

from tests.conftest import INFY
from trading.backtesting.engine import run_backtest, walk_forward
from trading.domain import Candle
from trading.market_data.feed import IST, SyntheticFeed, check_candles, vwap
from trading.strategies.base import StrategyContext, completed
from trading.strategies.library import REGISTRY, OpeningRangeBreakout, TrendMomentum, TrendSwing

DAY = datetime(2026, 3, 2, tzinfo=IST)


def candles(n_days=3, seed=3):
    f, out, d = SyntheticFeed([INFY], seed=seed), [], DAY
    while len(out) < n_days * 375:
        if d.weekday() < 5:
            out += f.minute_candles(INFY, d)
        d += timedelta(days=1)
    return out


def test_feed_is_deterministic_and_labelled():
    a, b = candles(1, 9), candles(1, 9)
    assert [c.close for c in a] == [c.close for c in b]
    assert all(c.synthetic for c in a)


def test_orb_no_entry_before_range_complete():
    c = candles(1)
    s = OpeningRangeBreakout()
    for i in range(15):
        assert s.generate_signal(StrategyContext(INFY, tuple(c[: i + 1]))) is None


def test_unfinished_bars_filtered():
    c = candles(1)[:30]
    forming = replace(c[-1], complete=False)
    assert completed([*c[:-1], forming]) == tuple(c[:-1])


def test_vwap_ignores_incomplete():
    c = candles(1)[:20]
    assert vwap(c) == vwap([*c, replace(c[-1], high=D("99999"), complete=False)])


def test_no_lookahead_signal_unchanged_by_future_bars():
    c = candles(2)
    s = TrendMomentum()
    for i in range(30, 300, 7):
        hist = tuple(c[: i + 1])
        assert s.generate_signal(StrategyContext(INFY, hist)) == s.generate_signal(StrategyContext(INFY, hist))
        # appending a future bar must not change the decision for bar i
        mutated = list(c)
        mutated[i + 1] = replace(c[i + 1], close=c[i + 1].close * 2)
        assert s.generate_signal(StrategyContext(INFY, tuple(mutated[: i + 1]))) == s.generate_signal(StrategyContext(INFY, hist))


def test_backtest_entries_fill_next_bar_and_costs_counted():
    c = candles(5)
    r = run_backtest(TrendMomentum(), INFY, c, D("1000000"))
    by_ts = {x.ts: i for i, x in enumerate(c)}
    for t in r.trades:
        assert t.costs > 0
        assert by_ts[t.entry_ts] > 0
        assert t.entry_ts.date() == t.exit_ts.date()  # intraday never carried overnight
    m = r.metrics()
    assert m["synthetic_data"] is True and m["trades"] == len(r.trades)


def test_gap_stop_fills_at_open():
    from trading.domain import Direction, TradeStyle, TradingSignal
    from trading.strategies.base import Strategy

    class Once(Strategy):
        id, name, version, style, timeframe = "T", "t", "0", TradeStyle.SWING, "1d"
        fired = False
        def generate_signal(self, ctx):
            if not self.fired:
                self.fired = True
                l = ctx.candles[-1]
                return TradingSignal("T", "0", INFY, l.ts, "1d", Direction.LONG, l.close, D("95"), TradeStyle.SWING, "x")

    mk = lambda i, o, h, l, cl: Candle(INFY.token, DAY + timedelta(days=i), D(o), D(h), D(l), D(cl), 1000)
    bars = [mk(0, 100, 101, 99, 100), mk(1, 100, 101, 99, 100), mk(2, 90, 92, 88, 91), mk(3, 91, 92, 90, 91)]
    r = run_backtest(Once(), INFY, bars, D("1000000"))
    assert r.trades[0].reason == "stop (gap)" and r.trades[0].exit < D("95")


def test_swing_and_walk_forward_run():
    f = SyntheticFeed([INFY], seed=5)
    daily = f.daily_candles(INFY, DAY, 400)
    assert check_candles(daily, timedelta(days=1)).ok
    assert run_backtest(TrendSwing(), INFY, daily, D("1000000"), max_hold_bars=15).metrics()["trades"] >= 0
    folds = walk_forward(TrendSwing, [{}, {"fast": 10, "slow": 30}], INFY, daily, D("1000000"))
    assert len(folds) == 3 and all("out_of_sample" in x for x in folds)


def test_integrity_checks_detect_problems():
    c = candles(1)[:10]
    assert not check_candles([*c[:5], c[4], *c[5:]], timedelta(minutes=1)).ok
    assert check_candles([c[0], c[2]], timedelta(minutes=1)).gaps == 1
    assert check_candles([c[3], c[1]], timedelta(minutes=1)).out_of_order == 1


def test_registry_has_five_versioned_strategies():
    assert set(REGISTRY) == set("ABCDE")
    assert all(cls().version for cls in REGISTRY.values())
