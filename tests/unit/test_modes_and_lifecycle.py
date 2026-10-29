import pytest

from tests.conftest import INFY, NOW
from trading.domain import ExecutionMode as M, InvalidTransition, OrderIntent, OrderState, TradeStyle
from trading.execution.modes import LIVE_GATES, ModeController, ModeError


def test_defaults_to_paper():
    assert ModeController().mode == M.PAPER


def test_live_enabled_unreachable_via_transition():
    m = ModeController()
    with pytest.raises(ModeError):
        m.transition(M.LIVE_ENABLED, "op", NOW)


def test_live_requires_all_gates_and_phrase():
    m = ModeController()
    m.transition(M.LIVE_PENDING_VALIDATION, "op", NOW)
    m.transition(M.LIVE_DISABLED, "op", NOW)
    for g in LIVE_GATES[:-1]:
        m.record_gate(g, True, "evidence-doc", "op", NOW)
    with pytest.raises(ModeError, match="missing"):
        m.authorize_live("op", NOW, "I AUTHORIZE LIVE TRADING")
    m.record_gate(LIVE_GATES[-1], True, "runbook.md", "op", NOW)
    with pytest.raises(ModeError, match="phrase"):
        m.authorize_live("op", NOW, "yes")
    m.authorize_live("op", NOW, "I AUTHORIZE LIVE TRADING")
    assert m.mode == M.LIVE_ENABLED


def test_passing_gate_needs_evidence():
    with pytest.raises(ModeError):
        ModeController().record_gate("static_ip_registered", True, "  ", "op", NOW)


def test_transition_needs_operator():
    with pytest.raises(ModeError):
        ModeController().transition(M.RESEARCH, "", NOW)


def test_order_state_machine():
    i = OrderIntent("p", "k", INFY, "A", "1", TradeStyle.INTRADAY, "BUY", 1, None, None)
    with pytest.raises(InvalidTransition):
        i.transition(OrderState.FILLED, NOW)
    for s in (OrderState.RISK_APPROVED, OrderState.PERSISTED, OrderState.SUBMISSION_PENDING,
              OrderState.BROKER_ACKNOWLEDGED, OrderState.PARTIALLY_FILLED, OrderState.FILLED):
        i.transition(s, NOW)
    with pytest.raises(InvalidTransition):
        i.transition(OrderState.CANCELLED, NOW)
