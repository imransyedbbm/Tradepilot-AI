"""The ten safety invariants from the master prompt, plus failure injection."""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from tests.conftest import INFY, NOW, make_proposal, market, portfolio
from trading.domain import ExecutionMode as M, OrderState
from trading.execution.gateway import ExecutionGateway, LiveSubmissionBlocked
from trading.execution.interfaces import Broker, BrokerOrderStatus, BrokerPosition, SubmissionTimeout
from trading.execution.modes import ModeController
from trading.execution.paper import PaperBroker
from trading.persistence.store import Store


class RecordingLiveBroker(Broker):
    """Stands in for a live adapter; records any call that would reach a real broker."""
    is_live = True

    def __init__(self):
        self.submitted = []

    def available_funds(self):
        return D("1000000")

    def submit(self, intent):
        self.submitted.append(intent)
        return "LIVE-1"

    def orders(self):
        return []

    def positions(self):
        return []

    def cancel(self, _):
        pass


class TimeoutBroker(PaperBroker):
    """Accepts the order at the 'exchange' but the response is lost."""
    def submit(self, intent):
        super().submit(intent)
        raise SubmissionTimeout()


def gw_with(broker, risk, mode=M.PAPER):
    m = ModeController()
    m.mode = mode
    return ExecutionGateway(broker, risk, m, Store())


# 1
def test_paper_mode_never_submits_to_live_broker(risk):
    b = RecordingLiveBroker()
    g = gw_with(b, risk)
    with pytest.raises(LiveSubmissionBlocked):
        g.submit_entry(make_proposal(), portfolio(), market(), NOW)
    assert b.submitted == []


# 2
@pytest.mark.parametrize("mode", [M.LIVE_PENDING_VALIDATION, M.LIVE_DISABLED, M.LIVE_ENABLED, M.RESEARCH])
def test_no_live_order_without_gates(risk, mode):
    b = RecordingLiveBroker()
    g = gw_with(b, risk, mode)  # LIVE_ENABLED forced directly, but gates have no evidence
    try:
        g.submit_entry(make_proposal(), portfolio(), market(), NOW)
    except LiveSubmissionBlocked:
        pass
    assert b.submitted == []


# 3
def test_strategy_cannot_bypass_risk(gateway):
    r = gateway.submit_entry(make_proposal(qty=10**6), portfolio(position_count=5), market(), NOW)
    assert not r.accepted and r.intent.state == OrderState.RISK_REJECTED
    assert gateway.broker.orders() == []


# 4
def test_duplicate_signal_single_order(gateway):
    p1, p2 = make_proposal(), make_proposal()  # same strategy/version/instrument/timestamp
    assert gateway.submit_entry(p1, portfolio(), market(), NOW).accepted
    r2 = gateway.submit_entry(p2, portfolio(), market(), NOW)
    assert not r2.accepted
    assert len(gateway.broker.orders()) == 1


# 5
def test_ambiguous_submission_reconciled_before_retry(risk):
    b = TimeoutBroker(cash=D("1000000"))
    b.mark(INFY.token, D("1452.40"))
    g = gw_with(b, risk)
    r = g.submit_entry(make_proposal(), portfolio(), market(), NOW)
    assert r.intent.state == OrderState.RECONCILIATION_REQUIRED
    # a fresh signal on the same instrument is blocked until reconciled
    later = make_proposal(ts=NOW + timedelta(minutes=1))
    assert g.submit_entry(later, portfolio(), market(NOW + timedelta(minutes=1), NOW + timedelta(minutes=1)), NOW).reason \
        == "instrument awaiting reconciliation"
    assert len(b.orders()) == 1
    out = g.reconcile(NOW)
    assert out["resolved"] == [r.intent.intent_id]
    assert g.store.intents({OrderState.FILLED})[0]["intent_id"] == r.intent.intent_id
    assert INFY.token not in g.blocked_tokens


# 6
def test_risk_halt_blocks_entries(gateway):
    gateway.risk.latch_halt("manual")
    r = gateway.submit_entry(make_proposal(), portfolio(), market(), NOW)
    assert not r.accepted and any("halt" in x for x in r.decision.reasons)


def test_emergency_mode_blocks_entries(gateway):
    gateway.modes.halt("op", NOW, "test")
    assert not gateway.submit_entry(make_proposal(), portfolio(), market(), NOW).accepted


# 7
def test_stale_data_blocks_orders(gateway):
    r = gateway.submit_entry(make_proposal(), portfolio(), market(last=NOW - timedelta(minutes=2)), NOW)
    assert not r.accepted and gateway.broker.orders() == []


# 8
def test_restart_never_replays_pending(risk):
    store = Store()
    b = PaperBroker(cash=D("1000000"))
    b.mark(INFY.token, D("1452.40"))

    class CrashBroker(PaperBroker):
        def submit(self, intent):
            raise KeyboardInterrupt  # process dies after intent persisted

    g = ExecutionGateway(CrashBroker(cash=D("1000000")), risk, ModeController(), store)
    with pytest.raises(KeyboardInterrupt):
        g.submit_entry(make_proposal(), portfolio(), market(), NOW)
    assert store.intents({OrderState.SUBMISSION_PENDING})
    g2 = ExecutionGateway(b, risk, ModeController(), store)
    flagged = g2.recover_after_restart(NOW)
    assert len(flagged) == 1 and b.orders() == []
    assert store.intents({OrderState.RECONCILIATION_REQUIRED})


# 9
def test_broker_positions_override_local(gateway):
    assert gateway.submit_entry(make_proposal(), portfolio(), market(), NOW).accepted
    gateway.positions[INFY.token].quantity = 1  # local drift
    out = gateway.reconcile(NOW)
    assert INFY.token in out["position_discrepancies"]
    assert gateway.positions[INFY.token].quantity == gateway.broker.positions()[0].quantity


# 10
def test_missing_evidence_prevents_live():
    m = ModeController()
    m.transition(M.LIVE_PENDING_VALIDATION, "op", NOW)
    m.transition(M.LIVE_DISABLED, "op", NOW)
    m.record_gate("critical_tests_passing", False, "pytest failed", "ci", NOW)
    with pytest.raises(Exception):
        m.authorize_live("op", NOW, "I AUTHORIZE LIVE TRADING")
    assert not m.can_submit_real_orders


def test_insufficient_funds_rejected(risk):
    b = PaperBroker(cash=D("100"))
    b.mark(INFY.token, D("1452.40"))
    g = gw_with(b, risk)
    r = g.submit_entry(make_proposal(), portfolio(available_funds=D("100")), market(), NOW)
    assert not r.accepted


def test_kite_adapter_is_inert():
    from trading.execution.zerodha_kite import KiteBroker, LiveAdapterNotImplemented
    with pytest.raises(LiveAdapterNotImplemented):
        KiteBroker()
