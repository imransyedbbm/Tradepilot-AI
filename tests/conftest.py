from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading.config import RiskLimits
from trading.domain import Direction, Instrument, TradeProposal, TradeStyle, TradingSignal
from trading.execution.gateway import ExecutionGateway
from trading.execution.modes import ModeController
from trading.execution.paper import PaperBroker
from trading.market_data.feed import IST
from trading.persistence.store import Store
from trading.risk.engine import MarketContext, PortfolioState, RiskEngine

NOW = datetime(2026, 10, 29, 10, 30, tzinfo=IST)
INFY = Instrument("INFY", 408065)
D = Decimal


@pytest.fixture
def now():
    return NOW


def make_proposal(entry="1452.40", stop="1438.00", inst=INFY, ts=NOW, sid="A", qty=None, style=TradeStyle.INTRADAY):
    sig = TradingSignal(sid, "1.0.0", inst, ts, "1m", Direction.LONG, D(entry), D(stop), style, "test signal")
    return TradeProposal(signal=sig, entry_price=D(entry), stop_price=D(stop), suggested_quantity=qty)


def portfolio(**kw):
    base = dict(equity=D("1000000"), available_funds=D("1000000"), gross_exposure=D(0), open_risk=D(0),
                realized_pnl_today=D(0), open_position_tokens=set(), open_order_tokens=set(), entries_today=0,
                position_count=0, style_budget_remaining=D("400000"))
    base.update(kw)
    return PortfolioState(**base)


def market(now=NOW, last=NOW, bid="1452.30", ask="1452.50", open_=True, universe=None, known=True):
    return MarketContext(now, last, D(bid), D(ask), open_, universe or {INFY.token}, known)


@pytest.fixture
def risk():
    return RiskEngine(RiskLimits())


@pytest.fixture
def gateway(risk):
    b = PaperBroker(cash=D("1000000"))
    b.mark(INFY.token, D("1452.40"))
    return ExecutionGateway(b, risk, ModeController(), Store())
