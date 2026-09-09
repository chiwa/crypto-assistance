# Crypto Assistance — Phase 1

A local decision-support MVP for five Bitkub THB spot pairs. It scans public market data, produces deterministic signals and risk guidance, and tracks paper or manually recorded real positions. It cannot place trades.

REST history drives the scheduled full strategy evaluation. Bitkub's public ticker and trade WebSocket streams continuously update prices and perform lightweight checks for `BUY_NOW`, `SELL_NOW`, `STOP_LOSS`, and `TAKE_PROFIT`. Critical alerts are saved to the web notification center and pushed to an open dashboard over Server-Sent Events, then sent to Telegram immediately when configured. DeepSeek is never in this alert path.

Graceful application starts and stops also create `APP_STARTED` and `APP_STOPPING` web notifications and send the same messages to Telegram when enabled.

## Run with Docker

```bash
docker compose up --build
```

Open <http://localhost:8010>. SQLite is retained in the `crypto_data` Docker volume. Port `8010` is used on the host to avoid colliding with AutoClip; the service remains on port `8000` inside the container.

## Local development

Requires Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload
```

## Defaults and scope

- Pairs: BTC/THB, ETH/THB, SOL/THB, XRP/THB, DOGE/THB
- Scanner interval: 60 seconds
- WebSocket ticker/trade monitoring: enabled, with reconnect/backoff and 5-minute duplicate-alert cooldown
- Entry confirmation: closed candles only, at least 2 of 3 timeframes aligned, no opposing 4h signal, and no alert after price extends more than 1% beyond the confirmation reference
- Timeframes: 15m, 1h, 4h
- Risk per setup: 1% (configurable, capped at 5%)
- Separate PAPER and REAL ledgers
- Deposits and withdrawals do not reset performance
- Optional DeepSeek second opinion requires `DEEP_SEEK_API_KEY`
- Telegram adapter is inactive unless both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exist

The app deliberately has no private Bitkub access, live orders, futures, margin, leverage, Redis, or PostgreSQL.

## API highlights

- `GET /api/dashboard` — portfolio, signals, journal, and notifications
- `POST /api/cash` — deposit/withdrawal
- `POST /api/trades` — manual paper/real trade record
- `POST /api/scan` — on-demand public-data scan
- `PUT /api/settings` — scanner settings
- `POST /api/second-opinion` — optional DeepSeek review
- `GET /docs` — complete interactive API reference
- `GET /api/backtest/BTC-THB/15m?fee_percent=0.25&slippage_percent=0.1` — deterministic historical replay with trade statistics and equity curve

## Phase 1.5 lifecycle

The ranked scanner exposes one Best Candidate only when no position is open. Entry states are `WAIT`, `WATCH`, and `BUY_NOW`. Recording an actual PAPER or REAL buy creates a persisted exit plan and changes monitoring to `IN_POSITION`. The real-time exit engine then reports `HOLD`, `EXIT_WATCH`, `TAKE_PROFIT`, `STOP_LOSS`, or `SELL_NOW` until an actual sell closes the position. A second position is rejected while one is open.

For REAL positions, every signal is recommendation-only. Signals can update the recommended action and protective levels but never create, resize, partially close, or close a position. Only an actual execution recorded by the user changes quantity, cash, fees, or realized P/L. Unrealized P/L uses the current Bitkub price against actual average cost and subtracts the configured estimated exit fee (default 0.25%). Realized P/L uses actual recorded sell executions and actual fees.

Market history uses Bitkub's public `GET /tradingview/history` endpoint; no API key is sent.
