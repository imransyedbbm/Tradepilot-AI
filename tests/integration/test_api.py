from fastapi.testclient import TestClient

from apps.api.main import create_app
from trading.config import Settings
from trading.persistence.store import Store

TOKEN = "test-token-123"
H = {"Authorization": f"Bearer {TOKEN}"}


def client(store=None):
    return TestClient(create_app(Settings(api_token=TOKEN, database_url="sqlite:///:memory:"), store or Store()))


def test_health_open_rest_protected():
    c = client()
    assert c.get("/api/v1/health").json()["mode"] == "PAPER"
    assert c.get("/api/v1/system/status").status_code == 401
    assert c.get("/api/v1/system/status", headers={"Authorization": "Bearer wrong-token"}).status_code == 401


def test_paper_session_runs_and_trades_are_simulated():
    c = client()
    s = c.post("/api/v1/simulation/step?n=300", headers=H).json()
    assert s["simulated"] and s["mode"] == "PAPER"
    orders = c.get("/api/v1/orders", headers=H).json()
    assert all(o["simulated"] == 1 for o in orders)
    assert len(c.get("/api/v1/market/watchlist", headers=H).json()) == 6
    assert c.get("/api/v1/audit-events", headers=H).json()


def test_secrets_never_returned():
    c = TestClient(create_app(Settings(api_token=TOKEN, kite_api_secret="SUPERSECRET", database_url="sqlite:///:memory:"), Store()))
    for path in ("/api/v1/system/status", "/api/v1/risk/status", "/api/v1/live/readiness"):
        assert "SUPERSECRET" not in c.get(path, headers=H).text


def test_halt_persists_across_restart():
    store = Store()
    c = client(store)
    c.post("/api/v1/emergency-halt", json={"reason": "operator test"}, headers=H)
    c2 = client(store)  # simulated restart, same database
    s = c2.get("/api/v1/system/status", headers=H).json()
    assert s["halted"] and s["mode"] == "EMERGENCY_HALT"
    c2.post("/api/v1/simulation/step?n=50", headers=H)
    assert c2.get("/api/v1/positions", headers=H).json() == []
    assert c2.post("/api/v1/emergency-halt/clear", headers=H).json()["mode"] == "PAPER"


def test_live_authorization_refused():
    c = client()
    r = c.post("/api/v1/live/authorize", json={"confirmation": "I AUTHORIZE LIVE TRADING"}, headers=H)
    assert r.status_code == 409
    rd = c.get("/api/v1/live/readiness", headers=H).json()
    assert rd["live_possible"] is False and len(rd["missing"]) == 10


def test_backtest_endpoint():
    c = client()
    r = c.post("/api/v1/backtests", json={"strategy": "A", "days": 3}, headers=H).json()
    assert r["metrics"]["synthetic_data"] is True
    wf = c.post("/api/v1/backtests", json={"strategy": "D", "days": 10, "walk_forward": True}, headers=H).json()
    assert len(wf["folds"]) == 3
