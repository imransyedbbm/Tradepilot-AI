"""Deterministic strategy interface. Strategies produce proposals; they never place orders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from trading.domain import Candle, Instrument, Position, TradeProposal, TradeStyle, TradingSignal


@dataclass(frozen=True)
class StrategyContext:
    instrument: Instrument
    candles: tuple[Candle, ...]  # completed candles only, oldest first


def sma(vals: list[Decimal], n: int) -> Decimal | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def completed(candles) -> tuple[Candle, ...]:
    """Drop any unfinished bar so no strategy can read a forming candle."""
    return tuple(c for c in candles if c.complete)


class Strategy(ABC):
    id: str
    name: str
    version: str
    style: TradeStyle
    timeframe: str

    def __init__(self, **params):
        self.params = {**self.default_params(), **params}

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @abstractmethod
    def generate_signal(self, ctx: StrategyContext) -> TradingSignal | None: ...

    def build_trade_proposal(self, sig: TradingSignal) -> TradeProposal:
        return TradeProposal(signal=sig, entry_price=sig.reference_price, stop_price=sig.stop_price)

    def manage_open_position(self, pos: Position, ctx: StrategyContext) -> str | None:
        """Return an exit reason, or None to hold."""
        last = ctx.candles[-1]
        if last.low <= pos.stop_price:
            return "stop"
        if pos.target_price and last.high >= pos.target_price:
            return "target"
        return None

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version,
                "style": self.style.value, "timeframe": self.timeframe, "params": self.params}
