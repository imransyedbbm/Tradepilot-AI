"""FastAPI service. All endpoints except /health need a bearer token."""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from trading.application import TradingApp
from trading.backtesting.engine import run_backtest, walk_forward
from trading.config import Settings
from trading.domain import ExecutionMode, OrderState
from trading.execution.modes import LIVE_GATES, ModeError
from trading.market_data.feed import IST, SyntheticFeed
from trading.persistence.store import Store
from trading.strategies.library import REGISTRY

class BacktestReq(BaseModel):
    strategy: str
    symbol: str = "INFY"
    days: int = Field(default=5, ge=1, le=60)
    equity: Decimal = Decimal("1000000")
    walk_forward: bool = False
    seed: int = 11


class HaltReq(BaseModel):
    reason: str = Field(min_length=3)


class LiveAuthReq(BaseModel):
    confirmation: str



WEB = Path(__file__).resolve().parents[1] / "web" / "index.html"


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings()
    store = store or Store(settings.database_url)
    core = TradingApp(settings, store)
    app = FastAPI(title="TradePilot AI", version="0.4.0")
    app.state.core = core

    def auth(authorization: str = Header(default="")) -> str:
        tok = authorization.removeprefix("Bearer ").strip()
        if not tok or not hmac.compare_digest(tok, settings.api_token):
            raise HTTPException(401, "invalid or missing API token")
        return "operator"

    @app.get("/")
    def dashboard():
        return FileResponse(WEB)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "mode": core.modes.mode.value}

    @app.get("/api/v1/system/status")
    def status(_=Depends(auth)):
        return core.summary()

    @app.get("/api/v1/system/execution-mode")
    def mode(_=Depends(auth)):
        return {"mode": core.modes.mode.value, "history": [
            {"from": a.value, "to": b.value, "by": by, "at": at.isoformat(), "reason": r}
            for a, b, by, at, r in core.modes.history]}

    @app.get("/api/v1/account/summary")
    def account(_=Depends(auth)):
        return core.summary()

    @app.get("/api/v1/market/watchlist")
    def watch(_=Depends(auth)):
        return core.watchlist()

    @app.post("/api/v1/simulation/step")
    def step(n: int = 1, _=Depends(auth)):
        for _i in range(max(1, min(n, 400))):
            core.step()
        return core.summary()

    @app.get("/api/v1/strategies")
    def strategies(_=Depends(auth)):
        return [{**s.describe(), "enabled_paper": k in core.enabled} for k, s in core.strategies.items()]

    @app.post("/api/v1/strategies/{sid}/enable-paper")
    def enable(sid: str, who=Depends(auth)):
        if sid not in REGISTRY:
            raise HTTPException(404, "unknown strategy")
        core.enabled.add(sid)
        store.audit(core.clock, who, "strategy_enabled_paper", {"strategy": sid})
        return {"enabled": sorted(core.enabled)}

    @app.post("/api/v1/strategies/{sid}/disable")
    def disable(sid: str, who=Depends(auth)):
        core.enabled.discard(sid)
        store.audit(core.clock, who, "strategy_disabled", {"strategy": sid})
        return {"enabled": sorted(core.enabled)}

    @app.get("/api/v1/orders")
    def orders(_=Depends(auth)):
        return sorted(store.intents(), key=lambda r: r["history"][-1][1] if r["history"] else "", reverse=True)[:200]

    @app.get("/api/v1/positions")
    def positions(_=Depends(auth)):
        return core.positions_view()

    @app.get("/api/v1/risk/status")
    def risk(_=Depends(auth)):
        L = settings.risk
        s = core.summary()
        return {"limits": L.model_dump(mode="json"), "halted": core.risk.halted, "halt_reason": core.risk.halt_reason,
                "suspended_strategies": sorted(core.risk.suspended_strategies),
                "usage": {"daily_loss": str(max(Decimal(0), -core.realized)),
                          "daily_loss_limit": str(core.equity() * L.max_daily_loss_pct / 100),
                          "open_risk_pct": s["open_risk_pct"], "gross_exposure_pct": s["gross_exposure_pct"],
                          "positions": len(core.gw.positions), "max_positions": L.max_positions}}

    @app.post("/api/v1/backtests")
    def backtest(req: BacktestReq, who=Depends(auth)):
        if req.strategy not in REGISTRY:
            raise HTTPException(404, "unknown strategy")
        cls = REGISTRY[req.strategy]
        inst = next((i for i in core.universe if i.symbol == req.symbol), None)
        if not inst:
            raise HTTPException(404, "symbol not in universe")
        feed = SyntheticFeed([inst], seed=req.seed)
        start = datetime(2026, 1, 5, tzinfo=IST)
        if cls.style.value == "SWING":
            candles = feed.daily_candles(inst, start, max(req.days, 1) * 30)
        else:
            candles, d = [], start
            while len(candles) < req.days * 375:
                if d.weekday() < 5:
                    candles += feed.minute_candles(inst, d)
                d += timedelta(days=1)
        store.audit(core.clock, who, "backtest_run", req.model_dump(mode="json"))
        if req.walk_forward:
            grid = [{}] + ([{"fast": 5, "slow": 20}, {"fast": 12, "slow": 30}] if req.strategy in ("A", "D") else [])
            return {"synthetic_data": True, "folds": walk_forward(cls, grid, inst, candles, req.equity)}
        r = run_backtest(cls(), inst, candles, req.equity)
        step = max(1, len(r.equity_curve) // 200)
        return {"metrics": r.metrics(), "trades": [
            {"entry_ts": t.entry_ts.isoformat(), "exit_ts": t.exit_ts.isoformat(), "entry": str(t.entry),
             "exit": str(t.exit), "qty": t.qty, "net": str(t.net), "reason": t.reason} for t in r.trades[-50:]],
            "equity_curve": [float(e) for _, e in r.equity_curve[::step]]}

    @app.get("/api/v1/audit-events")
    def audit(limit: int = 100, _=Depends(auth)):
        return store.audit_events(min(limit, 500))

    @app.post("/api/v1/emergency-halt")
    def halt(req: HaltReq, who=Depends(auth)):
        core.emergency_halt(who, req.reason)
        return core.summary()

    @app.post("/api/v1/emergency-halt/clear")
    def clear(who=Depends(auth)):
        try:
            core.resume(who)
        except ModeError as e:
            raise HTTPException(409, str(e))
        return core.summary()

    @app.post("/api/v1/reconciliation/run")
    def reconcile(_=Depends(auth)):
        return core.gw.reconcile(core.clock)

    # --- live readiness (read-only here; authorization exists but gates cannot pass in this build)
    @app.get("/api/v1/live/readiness")
    def readiness(_=Depends(auth)):
        st = core.modes.gate_status()
        return {"gates": [{"gate": g, "passed": st[g]} for g in LIVE_GATES], "missing": core.modes.missing_gates(),
                "live_adapter": "not implemented (Phase 6)", "live_possible": False}

    @app.post("/api/v1/live/authorize")
    def authorize(req: LiveAuthReq, who=Depends(auth)):
        try:
            core.modes.authorize_live(who, core.clock, req.confirmation)
        except ModeError as e:
            store.audit(core.clock, who, "live_authorization_refused", {"reason": str(e)})
            raise HTTPException(409, str(e))
        return {"mode": core.modes.mode.value}

    return app


# Run with: uvicorn apps.api.main:create_app --factory
