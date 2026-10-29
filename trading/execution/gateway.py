"""Execution gateway: risk-check → persist intent → controlled submission → reconciliation.

The DB and broker cannot share a transaction, so we design for ambiguity:
an intent is persisted before submission, a timeout moves it to
RECONCILIATION_REQUIRED, and nothing is retried blindly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading.domain import (
    ExecutionMode, OrderIntent, OrderState, Position, RiskDecision, TradeProposal,
)
from trading.execution.interfaces import Broker, BrokerRejected, SubmissionTimeout
from trading.execution.modes import ModeController
from trading.persistence.store import DuplicateIntent, Store
from trading.risk.engine import MarketContext, PortfolioState, RiskEngine


class LiveSubmissionBlocked(Exception):
    pass


@dataclass
class SubmitResult:
    accepted: bool
    intent: OrderIntent | None
    decision: RiskDecision | None
    reason: str = ""


@dataclass
class ExecutionGateway:
    broker: Broker
    risk: RiskEngine
    modes: ModeController
    store: Store
    positions: dict[int, Position] = field(default_factory=dict)
    blocked_tokens: set[int] = field(default_factory=set)  # unresolved ambiguity per instrument

    def _allowed(self) -> tuple[bool, str]:
        m = self.modes.mode
        if m == ExecutionMode.RESEARCH:
            return False, "RESEARCH mode: no submissions"
        if m == ExecutionMode.EMERGENCY_HALT:
            return False, "emergency halt active"
        if self.broker.is_live:
            if not self.modes.can_submit_real_orders:
                return False, "live gates not satisfied"
        elif not self.modes.can_submit_paper_orders:
            return False, f"paper broker cannot run in {m.value}"
        if self.modes.mode == ExecutionMode.PAPER and self.broker.is_live:
            return False, "PAPER mode never uses a live broker"
        return True, ""

    def submit_entry(self, prop: TradeProposal, port: PortfolioState, mkt: MarketContext, now: datetime) -> SubmitResult:
        ok, why = self._allowed()
        if not ok:
            self.store.audit(now, "gateway", "submission_blocked", {"proposal": prop.proposal_id, "reason": why})
            if self.broker.is_live and "live" in why:
                raise LiveSubmissionBlocked(why)
            return SubmitResult(False, None, None, why)
        if prop.signal.instrument.token in self.blocked_tokens:
            return SubmitResult(False, None, None, "instrument awaiting reconciliation")

        decision = self.risk.evaluate(prop, port, mkt)  # strategy quantity ignored
        s = prop.signal
        intent = OrderIntent(prop.proposal_id, prop.dedupe_key, s.instrument, s.strategy_id, s.strategy_version,
                             s.style, "BUY", decision.quantity, prop.entry_price, prop.stop_price,
                             simulated=not self.broker.is_live)
        intent.history.append((OrderState.PROPOSED, now, s.explanation))
        if not decision.approved:
            intent.transition(OrderState.RISK_REJECTED, now, "; ".join(decision.reasons))
            self.store.audit(now, "risk", "risk_rejected", {"proposal": prop.proposal_id, "symbol": s.instrument.symbol,
                                                            "strategy": s.strategy_id, "reasons": decision.reasons})
            return SubmitResult(False, intent, decision, "; ".join(decision.reasons))

        intent.transition(OrderState.RISK_APPROVED, now, f"qty {decision.quantity}")
        intent.transition(OrderState.PERSISTED, now)
        try:
            self.store.insert_intent(intent)
        except DuplicateIntent:
            self.store.audit(now, "gateway", "duplicate_signal", {"dedupe_key": prop.dedupe_key})
            return SubmitResult(False, None, decision, "duplicate signal")

        intent.transition(OrderState.SUBMISSION_PENDING, now)
        self.store.save_intent(intent)
        return self._send(intent, now, decision)

    def _send(self, intent: OrderIntent, now: datetime, decision: RiskDecision | None) -> SubmitResult:
        try:
            oid = self.broker.submit(intent)
        except SubmissionTimeout:
            intent.transition(OrderState.RECONCILIATION_REQUIRED, now, "submission timeout — outcome unknown")
            self.blocked_tokens.add(intent.instrument.token)
            self.store.save_intent(intent)
            self.store.audit(now, "gateway", "ambiguous_submission", {"intent": intent.intent_id})
            return SubmitResult(False, intent, decision, "reconciliation required")
        except BrokerRejected as e:
            intent.transition(OrderState.REJECTED, now, str(e))
            self.store.save_intent(intent)
            self.store.audit(now, "broker", "order_rejected", {"intent": intent.intent_id, "reason": str(e)})
            return SubmitResult(False, intent, decision, str(e))
        intent.broker_order_id = oid
        intent.transition(OrderState.BROKER_ACKNOWLEDGED, now, oid)
        self._apply_status(intent, now)
        return SubmitResult(True, intent, decision)

    def _apply_status(self, intent: OrderIntent, now: datetime) -> None:
        st = next((o for o in self.broker.orders() if o.broker_order_id == intent.broker_order_id), None)
        if st and st.status == "COMPLETE":
            intent.filled_qty, intent.avg_fill_price = st.filled_qty, st.avg_price
            if intent.state != OrderState.FILLED:
                intent.transition(OrderState.FILLED, now, f"{st.filled_qty} @ {st.avg_price}")
            if intent.side == "BUY":
                self.positions[intent.instrument.token] = Position(
                    intent.instrument, intent.strategy_id, intent.style, st.filled_qty, st.avg_price,
                    intent.stop_price, None, now)
        self.store.save_intent(intent)
        self.store.audit(now, "broker", "order_update", {"intent": intent.intent_id, "state": intent.state.value,
                                                         "simulated": intent.simulated})

    def exit_position(self, token: int, now: datetime, reason: str) -> Decimal | None:
        pos = self.positions.get(token)
        if not pos:
            return None
        i = OrderIntent(f"EXIT-{token}-{now.timestamp()}", f"exit:{token}:{now.isoformat()}", pos.instrument,
                        pos.strategy_id, "exit", pos.style, "SELL", pos.quantity, None, pos.stop_price,
                        simulated=not self.broker.is_live)
        for s in (OrderState.RISK_APPROVED, OrderState.PERSISTED):
            i.history.append((s, now, reason))
        i.state = OrderState.PERSISTED
        self.store.insert_intent(i)
        i.transition(OrderState.SUBMISSION_PENDING, now)
        res = self._send(i, now, None)
        if not res.accepted:
            return None
        self.positions.pop(token, None)
        pnl = (i.avg_fill_price - pos.avg_price) * pos.quantity
        self.risk.record_trade_result(pos.strategy_id, pnl)
        self.store.audit(now, "gateway", "position_closed", {"symbol": pos.instrument.symbol, "reason": reason,
                                                             "gross_pnl": str(pnl)})
        return pnl

    # --- recovery & reconciliation ---------------------------------------
    def recover_after_restart(self, now: datetime) -> list[str]:
        """Pending submissions are never replayed; they are marked for reconciliation."""
        flagged = []
        for r in self.store.intents({OrderState.SUBMISSION_PENDING}):
            hist = r["history"] + [[OrderState.RECONCILIATION_REQUIRED.value, now.isoformat(), "found pending on restart"]]
            from trading.persistence.store import IntentRow
            from sqlalchemy.orm import Session
            with Session(self.store.engine) as s, s.begin():
                row = s.get(IntentRow, r["intent_id"])
                row.state, row.history = OrderState.RECONCILIATION_REQUIRED.value, hist
            self.blocked_tokens.add(r["token"])
            flagged.append(r["intent_id"])
        if flagged:
            self.store.audit(now, "gateway", "restart_recovery", {"flagged": flagged})
        return flagged

    def reconcile(self, now: datetime) -> dict:
        """Match ambiguous intents to broker orders by tag; broker positions are authoritative."""
        from sqlalchemy.orm import Session
        from trading.persistence.store import IntentRow
        by_tag = {o.tag: o for o in self.broker.orders()}
        resolved, unresolved = [], []
        with Session(self.store.engine) as s, s.begin():
            for r in s.query(IntentRow).filter(IntentRow.state == OrderState.RECONCILIATION_REQUIRED.value):
                o = by_tag.get(r.intent_id)
                if o is None:
                    r.state = OrderState.REJECTED.value
                    r.history = r.history + [["REJECTED", now.isoformat(), "not found at broker"]]
                    resolved.append(r.intent_id)
                elif o.status == "COMPLETE":
                    r.state, r.broker_order_id = OrderState.FILLED.value, o.broker_order_id
                    r.filled_qty, r.avg_fill_price = o.filled_qty, str(o.avg_price)
                    r.history = r.history + [["FILLED", now.isoformat(), "confirmed by broker"]]
                    resolved.append(r.intent_id)
                else:
                    unresolved.append(r.intent_id)
                    continue
                self.blocked_tokens.discard(r.token)
        broker_pos = {p.token: p for p in self.broker.positions()}
        discrepancies = []
        for tok, bp in broker_pos.items():
            local = self.positions.get(tok)
            if not local or local.quantity != bp.quantity:
                discrepancies.append(tok)
                if local:
                    local.quantity, local.avg_price = bp.quantity, bp.avg_price
        for tok in list(self.positions):
            if tok not in broker_pos:
                discrepancies.append(tok)
                self.positions.pop(tok)
        out = {"resolved": resolved, "unresolved": unresolved, "position_discrepancies": discrepancies}
        self.store.audit(now, "reconciler", "reconciliation", out)
        return out
