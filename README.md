# TradePilot AI

TradePilot AI is a paper-first automated trading platform for NSE cash equities. Phases 1–4 of the build plan are done: foundation, quantitative research, risk engine and paper execution. **Live trading is impossible in this build.** The Zerodha adapter is an inert placeholder until Phase 6.

## Run it

```bash
cp .env.example .env              # set TP_API_TOKEN
./scripts/run_local.sh            # installs, runs tests, starts the API on 127.0.0.1:8000
```
Open http://127.0.0.1:8000 and enter your token. Press **Run session** to stream the synthetic feed through the strategies.

To run with Postgres instead: `docker compose up --build`.

Tests only: `pip install -e ".[dev]" && pytest` (64 tests).

## What's built

| Area | Module | Notes |
|---|---|---|
| Domain model | `trading/domain.py` | Decimal money, tick rounding, order state machine with enforced transitions |
| Config | `trading/config.py` | Validated risk limits and capital allocation. Rejects inconsistent limits, leverage and double allocation |
| Execution modes | `trading/execution/modes.py` | RESEARCH → PAPER → LIVE_PENDING → LIVE_DISABLED → LIVE_ENABLED. LIVE_ENABLED is reachable only through `authorize_live()` with all 10 gates evidenced plus a confirmation phrase |
| Risk engine | `trading/risk/engine.py` | Risk-based sizing, capped by funds, single-position, gross exposure and style budget. 13 hard rejections. Consecutive-loss suspension. Persistent halt latch |
| Costs | `trading/risk/costs.py` | Indian cash-equity charges (brokerage, STT, stamp, exchange, SEBI, GST). Estimates to verify against Zerodha's charge sheet |
| Market data | `trading/market_data/feed.py` | Seeded synthetic feed (every object flagged `synthetic`), VWAP, gap/duplicate/out-of-order detection, staleness |
| Strategies | `trading/strategies/library.py` | A trend-momentum, B VWAP reclaim, C opening-range breakout (intraday); D trend swing, E breakout-pullback (swing). Versioned, long-only |
| Paper broker | `trading/execution/paper.py` | Slippage, charges, cash and position accounting. No network access |
| Gateway | `trading/execution/gateway.py` | Risk check → persist intent → submit → fill. Dedupe on signal key. Timeouts go to RECONCILIATION_REQUIRED and lock the instrument. Restart recovery never replays a submission. Broker positions are authoritative |
| Persistence | `trading/persistence/store.py` | SQLAlchemy: intents with full history, audit events, halt latch. SQLite locally, Postgres in Docker |
| Backtester | `trading/backtesting/engine.py` | Next-bar-open fills (no look-ahead), gap-through stops, costs, intraday square-off, metrics, walk-forward |
| API | `apps/api/main.py` | Bearer-token auth. Endpoints from the spec, plus `/simulation/step`, `/live/readiness`, `/live/authorize` (refuses) |
| Dashboard | `apps/web/index.html` | Monitor, positions, orders, strategies, risk bars, halt, gates, backtest lab, audit log |

## Safety invariants (tested in `tests/failure_scenarios/`)
1. PAPER never submits to a live broker. 2. No live order without all gates. 3. Strategies can't bypass risk. 4. Duplicate signals produce one order. 5. Ambiguous submissions are reconciled before any retry. 6. Halts block entries. 7. Stale data blocks orders. 8. Restart never replays a pending submission. 9. Broker positions override local state. 10. Missing evidence blocks live.

## Known limitations
- Swing strategies D and E run in the backtest lab only. The live paper loop runs intraday strategies on 1-minute bars.
- The session calendar skips weekends but has no NSE holiday calendar yet (Phase 5).
- Strategies suspended after consecutive losses have no "un-suspend" endpoint yet.
- Single-operator token auth. RBAC, TLS and Alembic migrations (currently `create_all`) are Phase 5/8.
- All results use synthetic data. Nothing here is evidence that any strategy has an edge.

## Next phases
5. Scheduler with an NSE calendar, notifications, daily reports, Alembic. 6. Kite Connect adapter with contract tests against mocked responses. 7. Persistent gate evidence and compliance sign-off (`docs/compliance/`). 8. Failure drills, security review, backup/restore.
