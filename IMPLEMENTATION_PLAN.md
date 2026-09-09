# Phase 1 implementation plan

1. Establish a Python 3.12/FastAPI modular monolith with persistent SQLite and a scheduled scanner.
2. Add the public Bitkub market-data adapter for BTC, ETH, SOL, XRP, and DOGE against THB.
3. Implement deterministic indicators, market-regime classification, strategy scoring, and risk guidance.
4. Implement separate PAPER and REAL portfolio ledgers, manual trades, deposits/withdrawals, positions, P&L, and journal entries.
5. Add a compact web dashboard, notification center, editable scanner settings, and optional DeepSeek/Telegram adapters.
6. Package with Docker Compose and verify with automated tests and startup checks.

Out of scope: private Bitkub APIs, live/automatic execution, futures, margin, leverage, Redis, and PostgreSQL.
