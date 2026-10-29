"""Application service: wires feed → strategies → risk → gateway for a paper session."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from trading.config import Settings
from trading.domain import Candle, ExecutionMode, TradeStyle, money
from trading.execution.gateway import ExecutionGateway
from trading.execution.modes import ModeController
from trading.execution.paper import PaperBroker
from trading.market_data.feed import DEFAULT_UNIVERSE, IST, SyntheticFeed, session_open, vwap
from trading.persistence.store import Store
from trading.risk.engine import MarketContext, PortfolioState, RiskEngine
from trading.strategies.base import StrategyContext
from trading.strategies.library import REGISTRY

H = Decimal(100)


@dataclass
class TradingApp:
    settings: Settings
    store: Store
    universe: list = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    enabled: set[str] = field(default_factory=lambda: {"A", "B", "C"})
    seed: int = 7

    def __post_init__(self):
        cap = self.settings.capital
        self.modes = ModeController()
        self.risk = RiskEngine(self.settings.risk)
        if self.store.get("halt") == "1":  # persistent latch survives restarts
            self.risk.latch_halt(self.store.get("halt_reason", "restored"))
            self.modes.mode = ExecutionMode.EMERGENCY_HALT
        self.broker = PaperBroker(cash=cap.research_capital)
        self.gw = ExecutionGateway(self.broker, self.risk, self.modes, self.store)
        self.strategies = {k: cls() for k, cls in REGISTRY.items()}
        self.feed = SyntheticFeed(self.universe, seed=self.seed)
        self.clock = session_open(datetime.now(IST))
        self.bars: dict[int, list[Candle]] = {i.token: [] for i in self.universe}
        self.last_tick: datetime | None = None
        self.realized = Decimal(0)
        self.entries_today = 0
        self.quotes: dict[int, tuple[Decimal, Decimal, Decimal]] = {}

    # --- capital ---------------------------------------------------------
    def style_budget(self, style: TradeStyle) -> Decimal:
        cap = self.settings.capital
        pct = cap.intraday_pct if style == TradeStyle.INTRADAY else cap.swing_pct
        used = sum(p.avg_price * p.quantity for p in self.gw.positions.values() if p.style == style)
        return cap.research_capital * pct / H - used

    def equity(self) -> Decimal:
        return self.broker.cash + sum(self._ltp(p.instrument.token) * p.quantity for p in self.gw.positions.values())

    def _ltp(self, tok: int) -> Decimal:
        return self.quotes.get(tok, (Decimal(0),))[0] or Decimal(0)

    def portfolio(self, style: TradeStyle) -> PortfolioState:
        pos = self.gw.positions.values()
        return PortfolioState(
            equity=self.equity(), available_funds=self.broker.available_funds(),
            gross_exposure=sum(self._ltp(p.instrument.token) * p.quantity for p in pos),
            open_risk=sum(max(Decimal(0), (self._ltp(p.instrument.token) - p.stop_price) * p.quantity) for p in pos),
            realized_pnl_today=self.realized, open_position_tokens={p.instrument.token for p in pos},
            open_order_tokens=set(self.gw.blocked_tokens), entries_today=self.entries_today,
            position_count=len(self.gw.positions), style_budget_remaining=self.style_budget(style))

    # --- one simulated minute -------------------------------------------
    def step(self) -> None:
        now = self.clock
        for inst in self.universe:
            ticks = [self.feed.quote(inst, now) for _ in range(6)]
            px = [t.ltp for t in ticks]
            c = Candle(inst.token, now, px[0], max(px), min(px), px[-1], self.feed._rng.randint(2000, 40000), True, True)
            self.bars[inst.token].append(c)
            self.broker.mark(inst.token, c.close)
            self.quotes[inst.token] = (c.close, ticks[-1].bid, ticks[-1].ask)
        self.last_tick = now
        self._manage(now)
        if self.risk.daily_loss_breached(self.portfolio(TradeStyle.INTRADAY)) and not self.risk.halted:
            self.emergency_halt("risk-engine", "daily loss limit breached")
        self._scan(now)
        self.clock = now + timedelta(minutes=1)
        if self.clock.strftime("%H:%M") >= "15:30":
            nxt = self.clock + timedelta(days=1)
            while nxt.weekday() >= 5:  # TODO(Phase 5): replace with verified NSE holiday calendar
                nxt += timedelta(days=1)
            self.clock = session_open(nxt)
            for k in self.bars:
                self.bars[k] = []
            self.entries_today, self.realized = 0, Decimal(0)

    def _manage(self, now):
        for tok, pos in list(self.gw.positions.items()):
            ctx = StrategyContext(pos.instrument, tuple(self.bars[tok]))
            why = self.strategies[pos.strategy_id].manage_open_position(pos, ctx)
            if not why and pos.style == TradeStyle.INTRADAY and now.strftime("%H:%M") >= self.settings.risk.intraday_squareoff:
                why = "intraday square-off"
            if why and self.modes.mode == ExecutionMode.PAPER:
                pnl = self.gw.exit_position(tok, now, why)
                if pnl is not None:
                    self.realized += pnl

    def _scan(self, now):
        if self.modes.mode != ExecutionMode.PAPER:
            return
        for sid in sorted(self.enabled):
            st = self.strategies[sid]
            if st.style != TradeStyle.INTRADAY:
                continue  # swing strategies evaluate on daily bars via the backtest lab in this build
            for inst in self.universe:
                sig = st.generate_signal(StrategyContext(inst, tuple(self.bars[inst.token])))
                if not sig:
                    continue
                prop = st.build_trade_proposal(sig)
                ltp, bid, ask = self.quotes[inst.token]
                mkt = MarketContext(now, self.last_tick, bid, ask, True, {i.token for i in self.universe})
                res = self.gw.submit_entry(prop, self.portfolio(sig.style), mkt, now)
                if res.accepted:
                    self.entries_today += 1
                    self.gw.positions[inst.token].target_price = sig.target_price

    # --- operator actions -----------------------------------------------
    def emergency_halt(self, by: str, reason: str) -> None:
        self.risk.latch_halt(reason)
        self.modes.halt(by, self.clock, reason)
        self.store.put("halt", "1")
        self.store.put("halt_reason", reason)
        self.store.audit(self.clock, by, "emergency_halt", {"reason": reason})

    def resume(self, by: str) -> None:
        self.risk.clear_halt(by)
        self.modes.transition(ExecutionMode.PAPER, by, self.clock, "resume after halt")
        self.store.put("halt", "0")
        self.store.audit(self.clock, by, "halt_cleared", {})

    # --- views -----------------------------------------------------------
    def summary(self) -> dict:
        eq = self.equity()
        p = self.portfolio(TradeStyle.INTRADAY)
        cap = self.settings.capital
        return {
            "simulated": True, "mode": self.modes.mode.value, "clock": self.clock.isoformat(),
            "configured_capital": str(cap.research_capital), "paper_equity": str(money(eq)),
            "cash": str(money(self.broker.cash)), "realized_pnl_today": str(money(self.realized)),
            "unrealized_pnl": str(money(sum((self._ltp(x.instrument.token) - x.avg_price) * x.quantity for x in self.gw.positions.values()))),
            "charges_paid": str(money(self.broker.charges_paid)),
            "gross_exposure_pct": round(float(p.gross_exposure / eq * 100), 2) if eq else 0,
            "open_risk_pct": round(float(p.open_risk / eq * 100), 3) if eq else 0,
            "entries_today": self.entries_today, "max_entries": self.settings.risk.max_entries_per_day,
            "allocation": {"intraday_pct": str(cap.intraday_pct), "swing_pct": str(cap.swing_pct), "reserve_pct": str(cap.reserve_pct)},
            "halted": self.risk.halted, "halt_reason": self.risk.halt_reason,
            "broker": "PaperBroker (Zerodha adapter not connected)",
        }

    def watchlist(self) -> list[dict]:
        out = []
        for inst in self.universe:
            b = self.bars[inst.token]
            if not b:
                continue
            v = vwap(b)
            out.append({"symbol": inst.symbol, "token": inst.token, "ltp": str(b[-1].close),
                        "chg_pct": round(float((b[-1].close / b[0].open - 1) * 100), 2), "vwap": str(v),
                        "volume": sum(x.volume for x in b), "synthetic": True,
                        "closes": [float(x.close) for x in b[-120:]]})
        return out

    def positions_view(self) -> list[dict]:
        return [{"symbol": p.instrument.symbol, "strategy": p.strategy_id, "style": p.style.value, "qty": p.quantity,
                 "avg": str(p.avg_price), "stop": str(p.stop_price), "target": str(p.target_price) if p.target_price else None,
                 "ltp": str(self._ltp(t)), "pnl": str(money((self._ltp(t) - p.avg_price) * p.quantity)), "simulated": True}
                for t, p in self.gw.positions.items()]
