# India retail algo-trading checklist

Status key: **CONFIRMED** = verified from an official source on the date shown · **TO VERIFY** = must be confirmed with Zerodha, the exchange, or a professional before live use.

Having this checklist does not make TradePilot compliant. Live mode stays locked until every row below has evidence attached to the matching gate.

| # | Requirement | Source | Status | Gate |
|---|---|---|---|---|
| 1 | SEBI framework for safer retail participation in algo trading (Feb 2025 circular) and its implementation timeline | https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html | TO VERIFY: current applicability, order-per-second threshold for registration, and the deadline as extended | regulatory_algo_approval |
| 2 | Exchange (NSE/BSE) algo registration / strategy ID and order tagging under the broker's implementation standards | https://www.nseindia.com/static/trade/empanelled-algo-providers-exchange | TO VERIFY with Zerodha: whether a self-built algo used on your own account needs registration at your order rate | regulatory_algo_approval |
| 3 | Static IP whitelisting for API order placement | Zerodha Kite Connect docs / support | TO VERIFY current requirement and how to register | static_ip_registered |
| 4 | API rate limits (orders/second, per-endpoint limits, daily order caps) | https://kite.trade/docs/connect/v3/ | TO VERIFY at deployment; the 10 orders/sec figure is unconfirmed | broker_credentials_verified |
| 5 | No Kite sandbox; paper trading must be internal | Kite Connect docs | Implemented internally (PaperBroker) | — |
| 6 | Daily session/access-token expiry and interactive re-login | Kite Connect docs | TO VERIFY; adapter is Phase 6 | broker_credentials_verified |
| 7 | Market-data licensing: historical/WebSocket data used only under Kite terms | Kite Connect terms | TO VERIFY | — |
| 8 | Record-keeping and audit-trail retention period | SEBI / broker | TO VERIFY retention period; audit table exists | monitoring_active |
| 9 | Hosting location and security expectations for algo infrastructure | Broker implementation standards | TO VERIFY | rollback_runbook |
| 10 | Tax and charges (STT, stamp duty, exchange, SEBI, GST) used in the cost model | Zerodha charges page | TO VERIFY current rates; defaults are estimates in `trading/risk/costs.py` | — |

Last reviewed: not yet reviewed against live sources. Add a date and reviewer for each row as it is confirmed.
