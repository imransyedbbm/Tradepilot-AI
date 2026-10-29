"""Paper broker: simulated fills, slippage, charges. Never touches the network."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading.domain import OrderIntent, money, round_to_tick
from trading.execution.interfaces import Broker, BrokerOrderStatus, BrokerPosition, BrokerRejected
from trading.risk.costs import CostModel


@dataclass
class PaperBroker(Broker):
    cash: Decimal
    slippage_bps: Decimal = Decimal("5")
    costs: CostModel = field(default_factory=CostModel)
    is_live: bool = False
    marks: dict[int, Decimal] = field(default_factory=dict)
    _orders: dict[str, BrokerOrderStatus] = field(default_factory=dict)
    _pos: dict[int, BrokerPosition] = field(default_factory=dict)
    charges_paid: Decimal = Decimal("0")
    _seq: int = 0

    def available_funds(self) -> Decimal:
        return self.cash

    def mark(self, token: int, price: Decimal) -> None:
        self.marks[token] = price

    def submit(self, intent: OrderIntent) -> str:
        px = self.marks.get(intent.instrument.token) or intent.limit_price
        if px is None:
            raise BrokerRejected("no price available")
        slip = px * self.slippage_bps / Decimal(10_000)
        fill = round_to_tick(px + slip if intent.side == "BUY" else px - slip, intent.instrument.tick_size)
        charges = self.costs.charges(intent.side, intent.quantity, fill, intent.style)
        value = fill * intent.quantity
        if intent.side == "BUY" and value + charges > self.cash:
            raise BrokerRejected("insufficient funds")
        self._seq += 1
        oid = f"PAPER-{self._seq:06d}"
        tok = intent.instrument.token
        cur = self._pos.get(tok, BrokerPosition(tok, 0, Decimal(0)))
        if intent.side == "BUY":
            q = cur.quantity + intent.quantity
            avg = (cur.avg_price * cur.quantity + value) / q
            self._pos[tok] = BrokerPosition(tok, q, money(avg))
            self.cash -= value + charges
        else:
            q = cur.quantity - intent.quantity
            if q < 0:
                raise BrokerRejected("short selling is disabled")
            self.cash += value - charges
            if q:
                self._pos[tok] = BrokerPosition(tok, q, cur.avg_price)
            else:
                self._pos.pop(tok, None)
        self.charges_paid += charges
        self._orders[oid] = BrokerOrderStatus(oid, intent.intent_id, "COMPLETE", intent.quantity, fill)
        return oid

    def orders(self) -> list[BrokerOrderStatus]:
        return list(self._orders.values())

    def positions(self) -> list[BrokerPosition]:
        return list(self._pos.values())

    def cancel(self, broker_order_id: str) -> None:
        o = self._orders.get(broker_order_id)
        if o and o.status == "OPEN":
            self._orders[broker_order_id] = BrokerOrderStatus(o.broker_order_id, o.tag, "CANCELLED", o.filled_qty, o.avg_price)
