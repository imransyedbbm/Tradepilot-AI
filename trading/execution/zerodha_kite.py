"""Zerodha Kite Connect adapter — Phase 6 placeholder.

Deliberately inert. Every method raises until Phase 6 implements the documented
Kite Connect v3 flows (https://kite.trade/docs/connect/v3/) with contract tests.
Kite has no sandbox; all simulation lives in PaperBroker.
"""
from __future__ import annotations

from trading.execution.interfaces import Broker


class LiveAdapterNotImplemented(RuntimeError):
    pass


class KiteBroker(Broker):
    is_live = True

    def __init__(self, *_, **__):
        raise LiveAdapterNotImplemented(
            "Kite adapter is not implemented yet (Phase 6). Live execution is unavailable."
        )

    def available_funds(self):  # pragma: no cover
        raise LiveAdapterNotImplemented

    def submit(self, intent):  # pragma: no cover
        raise LiveAdapterNotImplemented

    def orders(self):  # pragma: no cover
        raise LiveAdapterNotImplemented

    def positions(self):  # pragma: no cover
        raise LiveAdapterNotImplemented

    def cancel(self, broker_order_id):  # pragma: no cover
        raise LiveAdapterNotImplemented
