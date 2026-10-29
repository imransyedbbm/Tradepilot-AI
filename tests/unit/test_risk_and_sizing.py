from datetime import timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from tests.conftest import INFY, NOW, make_proposal, market, portfolio
from trading.config import CapitalAllocation, RiskLimits
from trading.domain import TradeStyle, money, round_to_tick
from trading.risk.costs import CostModel


def test_sizing_formula(risk):
    prop = make_proposal(stop="1400.00")
    d = risk.evaluate(prop, portfolio(), market())
    assert d.approved
    cost = CostModel().round_trip_per_share(D("1452.40"), TradeStyle.INTRADAY)
    expected = int(D("2500") / (D("1452.40") - D("1400.00") + cost))
    assert d.quantity == expected
    assert d.risk_amount <= D("2500")


def test_strategy_quantity_is_ignored(risk):
    d = risk.evaluate(make_proposal(qty=99999), portfolio(), market())
    assert d.quantity < 99999


def test_single_position_cap(risk):
    d = risk.evaluate(make_proposal(entry="1000", stop="999.5"), portfolio(), market(bid="999.9", ask="1000.1"))
    assert d.quantity == 150  # 15% of 10L / 1000
    assert any("single-position" in r for r in d.reasons)


def test_style_allocation_cap(risk):
    d = risk.evaluate(make_proposal(entry="1000", stop="999.5"), portfolio(style_budget_remaining=D("50000")),
                      market(bid="999.9", ask="1000.1"))
    assert d.quantity == 50


@pytest.mark.parametrize("entry,stop", [("100", "100"), ("100", "101")])
def test_invalid_stop_rejected(risk, entry, stop):
    d = risk.evaluate(make_proposal(entry=entry, stop=stop), portfolio(), market(bid="99.95", ask="100.05"))
    assert not d.approved


@pytest.mark.parametrize("kw,mk,needle", [
    ({"realized_pnl_today": D("-10000")}, {}, "daily loss"),
    ({"position_count": 5}, {}, "concurrent positions"),
    ({"entries_today": 10}, {}, "entries per day"),
    ({"open_position_tokens": {INFY.token}}, {}, "conflicting"),
    ({}, {"last": NOW - timedelta(seconds=30)}, "stale"),
    ({}, {"open_": False}, "market closed"),
    ({}, {"universe": {1}}, "universe"),
    ({}, {"known": False}, "broker"),
    ({}, {"bid": "1440", "ask": "1460"}, "spread"),
    ({}, {"now": NOW.replace(hour=14, minute=50), "last": NOW.replace(hour=14, minute=50)}, "cutoff"),
])
def test_hard_rejections(risk, kw, mk, needle):
    d = risk.evaluate(make_proposal(), portfolio(**kw), market(**mk))
    assert not d.approved and any(needle in r for r in d.reasons), d.reasons


def test_consecutive_losses_suspend_strategy(risk):
    for _ in range(3):
        risk.record_trade_result("A", D("-100"))
    d = risk.evaluate(make_proposal(), portfolio(), market())
    assert not d.approved and "A" in risk.suspended_strategies


def test_open_risk_budget_reduces_size(risk):
    full = risk.evaluate(make_proposal(), portfolio(), market()).quantity
    part = risk.evaluate(make_proposal(), portfolio(open_risk=D("19000")), market()).quantity
    assert 0 < part < full


def test_money_rounding():
    assert money("0.105") == D("0.11")
    assert round_to_tick(D("100.03")) == D("100.05")
    assert D("0.1") + D("0.2") == D("0.3")


def test_costs_positive_and_delivery_higher():
    c = CostModel()
    intr = c.charges("BUY", 100, D("1000"), TradeStyle.INTRADAY) + c.charges("SELL", 100, D("1000"), TradeStyle.INTRADAY)
    deliv = c.charges("BUY", 100, D("1000"), TradeStyle.SWING) + c.charges("SELL", 100, D("1000"), TradeStyle.SWING)
    assert D(0) < intr < deliv


@pytest.mark.parametrize("kw", [
    {"per_trade_risk_pct": D("2"), "max_daily_loss_pct": D("1")},
    {"max_single_position_pct": D("90"), "max_gross_exposure_pct": D("80")},
    {"max_gross_exposure_pct": D("150")},
    {"per_trade_risk_pct": D("0")},
    {"intraday_entry_cutoff": "15:20", "intraday_squareoff": "15:10"},
])
def test_inconsistent_limits_rejected(kw):
    with pytest.raises(ValidationError):
        RiskLimits(**kw)


def test_no_double_allocation():
    with pytest.raises(ValidationError):
        CapitalAllocation(intraday_pct=D("60"), swing_pct=D("50"))
    assert CapitalAllocation().reserve_pct == D("10")
