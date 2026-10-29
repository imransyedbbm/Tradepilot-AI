"""Typed domain objects. Money and prices use Decimal throughout."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import Enum

PAISE = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(PAISE, rounding=ROUND_HALF_UP)


def round_to_tick(price: Decimal, tick: Decimal = Decimal("0.05")) -> Decimal:
    return (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick


def floor_int(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_DOWN))


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class Direction(str, Enum):
    LONG = "LONG"  # initial release is long-only


class TradeStyle(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"


class ExecutionMode(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE_PENDING_VALIDATION = "LIVE_PENDING_VALIDATION"
    LIVE_DISABLED = "LIVE_DISABLED"
    LIVE_ENABLED = "LIVE_ENABLED"
    EMERGENCY_HALT = "EMERGENCY_HALT"


class OrderState(str, Enum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    PERSISTED = "PERSISTED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    BROKER_ACKNOWLEDGED = "BROKER_ACKNOWLEDGED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TERMINAL_STATES = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.RISK_REJECTED}

ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PROPOSED: {OrderState.RISK_APPROVED, OrderState.RISK_REJECTED},
    OrderState.RISK_APPROVED: {OrderState.PERSISTED},
    OrderState.PERSISTED: {OrderState.SUBMISSION_PENDING, OrderState.CANCELLED},
    OrderState.SUBMISSION_PENDING: {
        OrderState.BROKER_ACKNOWLEDGED, OrderState.REJECTED, OrderState.RECONCILIATION_REQUIRED,
    },
    OrderState.BROKER_ACKNOWLEDGED: {
        OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED,
    },
    OrderState.OPEN: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED},
    OrderState.RECONCILIATION_REQUIRED: {
        OrderState.BROKER_ACKNOWLEDGED, OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED,
    },
}


class InvalidTransition(Exception):
    pass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    token: int
    exchange: Exchange = Exchange.NSE
    tick_size: Decimal = Decimal("0.05")
    lot_size: int = 1
    tradable: bool = True


@dataclass(frozen=True)
class Candle:
    token: int
    ts: datetime  # candle open time, tz-aware IST
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    complete: bool = True
    synthetic: bool = False


@dataclass(frozen=True)
class MarketQuote:
    token: int
    ts: datetime
    ltp: Decimal
    bid: Decimal
    ask: Decimal
    synthetic: bool = False


@dataclass(frozen=True)
class TradingSignal:
    strategy_id: str
    strategy_version: str
    instrument: Instrument
    ts: datetime
    timeframe: str
    direction: Direction
    reference_price: Decimal
    stop_price: Decimal
    style: TradeStyle
    explanation: str
    target_price: Decimal | None = None
    invalidation: str = ""


@dataclass
class TradeProposal:
    signal: TradingSignal
    entry_price: Decimal
    stop_price: Decimal
    proposal_id: str = field(default_factory=lambda: new_id("TP"))
    suggested_quantity: int | None = None  # never trusted; risk engine recomputes
    valid_until: datetime | None = None
    data_quality_ok: bool = True

    @property
    def dedupe_key(self) -> str:
        s = self.signal
        return f"{s.strategy_id}:{s.strategy_version}:{s.instrument.token}:{s.ts.isoformat()}"


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: int
    reasons: tuple[str, ...]
    risk_amount: Decimal = Decimal("0")
    estimated_costs: Decimal = Decimal("0")


@dataclass
class Position:
    instrument: Instrument
    strategy_id: str
    style: TradeStyle
    quantity: int
    avg_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None = None
    opened_at: datetime | None = None


@dataclass
class OrderIntent:
    proposal_id: str
    dedupe_key: str
    instrument: Instrument
    strategy_id: str
    strategy_version: str
    style: TradeStyle
    side: str  # BUY / SELL
    quantity: int
    limit_price: Decimal | None
    stop_price: Decimal
    intent_id: str = field(default_factory=lambda: new_id("OI"))
    state: OrderState = OrderState.PROPOSED
    broker_order_id: str | None = None
    filled_qty: int = 0
    avg_fill_price: Decimal | None = None
    simulated: bool = True
    history: list[tuple[OrderState, datetime, str]] = field(default_factory=list)

    def transition(self, new: OrderState, at: datetime, note: str = "") -> None:
        if new not in ALLOWED_TRANSITIONS.get(self.state, set()):
            raise InvalidTransition(f"{self.state} -> {new}")
        self.state = new
        self.history.append((new, at, note))


@dataclass(frozen=True)
class OrderFill:
    intent_id: str
    quantity: int
    price: Decimal
    ts: datetime
    charges: Decimal
    simulated: bool = True
