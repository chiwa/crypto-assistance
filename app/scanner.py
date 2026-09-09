import asyncio

from app.db import Database
from app.market import PAIR_TO_SYMBOL, BitkubMarketData
from app.strategy import analyze


class Scanner:
    def __init__(self, db: Database, market: BitkubMarketData):
        self.db, self.market = db, market
        self._lock = asyncio.Lock()
        self._last_setup_state: dict[tuple[str, str], dict] = {}

    async def run(self) -> dict:
        if self._lock.locked():
            return {"status": "skipped", "reason": "scan already running"}
        settings = self.db.settings()
        if not settings["scanner_enabled"]:
            return {"status": "disabled"}
        completed, errors = 0, []
        material_delta = settings.get("notification_score_material_delta", 10)

        async with self._lock:
            for pair in PAIR_TO_SYMBOL:
                for timeframe in settings["timeframes"]:
                    try:
                        candles = await self.market.candles(pair, timeframe)
                        # The last TradingView candle may still be forming. Decisions use closed candles only.
                        result = analyze(candles["highs"][:-1], candles["lows"][:-1], candles["closes"][:-1], candles["volumes"][:-1])
                        self.db.save_signal(pair, timeframe, result)
                        completed += 1

                        # State-change deduplication by symbol + timeframe + signal + strategy + score bucket
                        key = (pair, timeframe)
                        signal = result["signal"]
                        score = result["score"]
                        regime = result["regime"]
                        strategy = result.get("strategy", "")
                        prev = self._last_setup_state.get(key)

                        if prev is None:
                            self._last_setup_state[key] = {
                                "signal": signal,
                                "score": score,
                                "regime": regime,
                                "strategy": strategy,
                            }
                            if signal != "HOLD":
                                self.db.notify(
                                    "INFO",
                                    f"{signal} setup: {pair} {timeframe}",
                                    f"Score {score} · {regime}",
                                )
                        else:
                            prev_signal = prev["signal"]
                            prev_score = prev["score"]
                            prev_strategy = prev.get("strategy", "")

                            if signal != prev_signal:
                                if signal != "HOLD":
                                    self.db.notify(
                                        "INFO",
                                        f"{signal} setup: {pair} {timeframe}",
                                        f"Score {score} · {regime}",
                                    )
                                elif prev_signal == "BUY":
                                    self.db.notify(
                                        "INFO",
                                        f"Setup no longer valid: {pair} {timeframe}",
                                        f"สัญญาณซื้อ {pair} {timeframe} สิ้นสุดลง (กลับสู่ HOLD, Score {score})",
                                    )
                                self._last_setup_state[key] = {
                                    "signal": signal,
                                    "score": score,
                                    "regime": regime,
                                    "strategy": strategy,
                                }
                            elif signal != "HOLD" and strategy != prev_strategy:
                                self.db.notify(
                                    "INFO",
                                    f"{signal} setup updated: {pair} {timeframe}",
                                    f"เปลี่ยนกลยุทธ์เป็น {strategy} (Score {score}) · {regime}",
                                )
                                self._last_setup_state[key] = {
                                    "signal": signal,
                                    "score": score,
                                    "regime": regime,
                                    "strategy": strategy,
                                }
                            elif signal != "HOLD" and abs(score - prev_score) >= material_delta:
                                self.db.notify(
                                    "INFO",
                                    f"{signal} setup score updated: {pair} {timeframe}",
                                    f"คะแนนเปลี่ยน {prev_score} -> {score} (Δ{abs(score - prev_score)}) · {regime}",
                                )
                                self._last_setup_state[key]["score"] = score
                                self._last_setup_state[key]["regime"] = regime
                            # Otherwise: routine scan identical result, do NOT notify repeatedly

                    except Exception as exc:  # isolate external failures per market/timeframe
                        errors.append(f"{pair} {timeframe}: {exc}")
            if errors:
                self.db.notify("WARNING", "Scanner completed with errors", "; ".join(errors[:3]))
        return {"status": "completed", "analyses": completed, "errors": errors}
