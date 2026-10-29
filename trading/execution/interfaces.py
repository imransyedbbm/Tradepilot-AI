"""Broker-neutral interface. Adapters must not leak vendor objects."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from trading.domain import OrderIntent


class SubmissionTimeout(Exception):
    """Outcome unknown — the broker may or may not have accepted the order."""


class BrokerRejected(Exception):
    pass


@dataclass(frozen=True)
class BrokerOrderStatus:
    broker_order_id: str
    tag: str  # carries our intent_id so ambiguous submissions can be matched
    status: str  # OPEN / COMPLETE / CANCELLED / REJECTED
    filled_qty: int
    avg_price: Decimal | None


@dataclass(frozen=True)
class BrokerPosition:
    token: int
    quantity: int
    avg_price: Decimal


class Broker(ABC):
    is_live: bool

    @abstractmethod
    def available_funds(self) -> Decimal: ...

    @abstractmethod
    def submit(self, intent: OrderIntent) -> str: ...

    @abstractmethod
    def orders(self) -> list[BrokerOrderStatus]: ...

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def cancel(self, broker_order_id: str) -> None: ...
