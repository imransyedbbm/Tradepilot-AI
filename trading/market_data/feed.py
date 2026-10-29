"""Market-data utilities: deterministic synthetic feed, bar builder, integrity checks."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading.domain import Candle, Instrument, MarketQuote, money, round_to_tick

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_UNIVERSE = [
    Instrument("RELIANCE", 738561), Instrument("HDFCBANK", 341249), Instrument("INFY", 408065),
    Instrument("TCS", 2953217), Instrument("ICICIBANK", 1270529), Instrument("LT", 2939649),
]


def session_open(day: datetime) -> datetime:
    return day.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0)


@dataclass
class SyntheticFeed:
    """Seeded random-walk quotes. Every object it emits is flagged synthetic=True."""
    instruments: list[Instrument]
    seed: int = 42
    start_prices: dict[int, Decimal] | None = None
    drift: float = 0.0
    vol: float = 0.0015
    _rng: random.Random = field(init=False)
    _px: dict[int, Decimal] = field(init=False)

    def __post_init__(self):
        self._rng = random.Random(self.seed)
        base = {i.token: Decimal(str(round(500 + self._rng.random() * 3500, 2))) for i in self.instruments}
        self._px = dict(self.start_prices or base)

    def quote(self, inst: Instrument, ts: datetime) -> MarketQuote:
        p = self._px[inst.token]
        step = Decimal(str(self._rng.gauss(self.drift, self.vol)))
        p = max(round_to_tick(p * (1 + step), inst.tick_size), inst.tick_size)
        self._px[inst.token] = p
        half = round_to_tick(p * Decimal("0.0002"), inst.tick_size) or inst.tick_size
        return MarketQuote(inst.token, ts, p, p - half, p + half, synthetic=True)

    def minute_candles(self, inst: Instrument, day: datetime, n: int = 375) -> list[Candle]:
        out, t = [], session_open(day)
        for _ in range(n):
            ticks = [self.quote(inst, t).ltp for _ in range(6)]
            out.append(Candle(inst.token, t, ticks[0], max(ticks), min(ticks), ticks[-1],
                              self._rng.randint(2_000, 40_000), True, True))
            t += timedelta(minutes=1)
        return out

    def daily_candles(self, inst: Instrument, start: datetime, days: int) -> list[Candle]:
        out, t = [], start.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        while len(out) < days:
            if t.weekday() < 5:
                o = self._px[inst.token]
                path = [self.quote(inst, t).ltp for _ in range(20)]
                out.append(Candle(inst.token, t, o, max([o, *path]), min([o, *path]), path[-1],
                                  self._rng.randint(500_000, 5_000_000), True, True))
            t += timedelta(days=1)
        return out


def vwap(candles: list[Candle]) -> Decimal | None:
    done = [c for c in candles if c.complete]
    vol = sum(c.volume for c in done)
    if not vol:
        return None
    return money(sum((c.high + c.low + c.close) / 3 * c.volume for c in done) / vol)


@dataclass
class IntegrityReport:
    duplicates: int = 0
    out_of_order: int = 0
    gaps: int = 0

    @property
    def ok(self) -> bool:
        return not (self.duplicates or self.out_of_order or self.gaps)


def check_candles(candles: list[Candle], interval: timedelta) -> IntegrityReport:
    r, seen = IntegrityReport(), set()
    for prev, cur in zip(candles, candles[1:]):
        if cur.ts in seen or cur.ts == prev.ts:
            r.duplicates += 1
        elif cur.ts < prev.ts:
            r.out_of_order += 1
        elif interval < timedelta(days=1) and cur.ts - prev.ts > interval:
            r.gaps += 1
        seen.add(prev.ts)
    return r


def is_stale(last: datetime | None, now: datetime, threshold_s: int) -> bool:
    return last is None or (now - last).total_seconds() > threshold_s
