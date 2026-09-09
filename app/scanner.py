import asyncio

from app.db import Database
from app.market import PAIR_TO_SYMBOL, BitkubMarketData
from app.strategy import analyze


class Scanner:
    def __init__(self, db: Database, market: BitkubMarketData):
        self.db, self.market = db, market
        self._lock = asyncio.Lock()

    async def run(self) -> dict:
        if self._lock.locked():
            return {"status": "skipped", "reason": "scan already running"}
        settings = self.db.settings()
        if not settings["scanner_enabled"]:
            return {"status": "disabled"}
        completed, errors = 0, []
        async with self._lock:
            for pair in PAIR_TO_SYMBOL:
                for timeframe in settings["timeframes"]:
                    try:
                        candles = await self.market.candles(pair, timeframe)
                        # The last TradingView candle may still be forming. Decisions use closed candles only.
                        result = analyze(candles["highs"][:-1], candles["lows"][:-1], candles["closes"][:-1], candles["volumes"][:-1])
                        self.db.save_signal(pair, timeframe, result)
                        completed += 1
                        if result["signal"] != "HOLD":
                            self.db.notify("INFO", f"{result['signal']} setup: {pair} {timeframe}", f"Score {result['score']} · {result['regime']}")
                    except Exception as exc:  # isolate external failures per market/timeframe
                        errors.append(f"{pair} {timeframe}: {exc}")
            if errors:
                self.db.notify("WARNING", "Scanner completed with errors", "; ".join(errors[:3]))
        return {"status": "completed", "analyses": completed, "errors": errors}
