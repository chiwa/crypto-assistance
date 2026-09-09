import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import websockets

from app.db import Database
from app.market import PAIR_TO_SYMBOL
from app.engines import PositionExitEngine


PAIR_TO_WS = {pair: f"thb_{pair.split('/')[0].lower()}" for pair in PAIR_TO_SYMBOL}


class RealtimeMonitor:
    """Public market watcher only. It creates alerts and can never execute orders."""

    def __init__(self, db: Database, telegram_send: Callable[[str], Awaitable[None]] | None = None):
        self.db = db
        self.telegram_send = telegram_send
        self.connected = False
        self.last_event_at: float | None = None
        self._last_alert: dict[str, float] = {}
        self._stop = asyncio.Event()
        self.exit_engine = PositionExitEngine()

    @property
    def url(self) -> str:
        streams = [f"market.{kind}.{symbol}" for symbol in PAIR_TO_WS.values() for kind in ("ticker", "trade")]
        return "wss://api.bitkub.com/websocket-api/" + ",".join(streams)

    async def run_forever(self):
        backoff = 1
        while not self._stop.is_set():
            if not self.db.settings().get("websocket_enabled", True):
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20, close_timeout=5) as socket:
                    self.connected, backoff = True, 1
                    async for raw in socket:
                        # Bitkub may batch newline-delimited JSON objects in one frame.
                        for line in str(raw).splitlines():
                            if line.strip():
                                await self.handle_event(json.loads(line))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.db.notify("WARNING", "Bitkub WebSocket disconnected", f"Retrying in {backoff}s: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                self.connected = False

    def stop(self):
        self._stop.set()

    async def handle_event(self, event: dict):
        stream = str(event.get("stream", "")).lower()
        pair = next((pair for pair, symbol in PAIR_TO_WS.items() if stream.endswith(symbol)), None)
        raw_price = event.get("last", event.get("close", event.get("rat")))
        if not pair or raw_price is None:
            return
        price = float(raw_price)
        if price <= 0:
            return
        self.last_event_at = time.time()
        self.db.update_price(pair, price)
        await self._evaluate(pair, price)

    async def _evaluate(self, pair: str, price: float):
        context = self.db.realtime_context(pair)
        signals = context["signals"]
        candidates: list[tuple[str, str]] = []
        if signals:
            buys = [signal for signal in signals if signal["signal"] == "BUY"]
            sells = [signal for signal in signals if signal["signal"] == "SELL"]
            four_hour = next((signal for signal in signals if signal["timeframe"] == "4h"), None)
            if len(buys) >= 2 and (not four_hour or four_hour["signal"] != "SELL"):
                reference = sum(signal["details"]["close"] for signal in buys) / len(buys)
                if reference <= price <= reference * 1.01:
                    frames = ", ".join(signal["timeframe"] for signal in buys)
                    candidates.append(("BUY_NOW", f"{pair} confirmed by {len(buys)}/3 timeframes ({frames}) above {reference:,.4f}; price not extended >1%"))
            if len(sells) >= 2 and (not four_hour or four_hour["signal"] != "BUY"):
                reference = sum(signal["details"]["close"] for signal in sells) / len(sells)
                if reference * 0.99 <= price <= reference:
                    frames = ", ".join(signal["timeframe"] for signal in sells)
                    candidates.append(("SELL_NOW", f"{pair} confirmed by {len(sells)}/3 timeframes ({frames}) below {reference:,.4f}; price not extended >1%"))
            # Protective exits use the slowest available analysis and never wait for consensus.
            protective = four_hour or signals[-1]
            details = protective["details"]
            for position in context["positions"]:
                if price <= details["stop_loss"]:
                    candidates.append(("STOP_LOSS", f"{position['mode']} {pair} price {price:,.4f} <= stop {details['stop_loss']:,.4f}"))
                elif price >= details["take_profit"]:
                    candidates.append(("TAKE_PROFIT", f"{position['mode']} {pair} price {price:,.4f} >= target {details['take_profit']:,.4f}"))
            for plan in context["plans"]:
                action, reason, effective_stop = self.exit_engine.evaluate(price, plan, signals)
                self.db.update_position_plan(plan["mode"], action, reason, price, effective_stop)
                if action in {"EXIT_WATCH","TAKE_PROFIT","STOP_LOSS","SELL_NOW"}:
                    candidates.append((action, f"{plan['mode']} {pair} · {reason} · current {price:,.4f}"))
        for kind, message in candidates:
            await self._alert(kind, pair, message)

    async def _alert(self, kind: str, pair: str, message: str):
        key, current = f"{kind}:{pair}", time.monotonic()
        cooldown = self.db.settings().get("realtime_alert_cooldown_seconds", 300)
        previous = self._last_alert.get(key)
        if previous is not None and current - previous < cooldown:
            return
        self._last_alert[key] = current
        title = f"{kind} · {pair}"
        self.db.notify("CRITICAL", title, message)
        if self.telegram_send and kind in {"BUY_NOW", "SELL_NOW", "STOP_LOSS", "TAKE_PROFIT"}:
            try:
                await self.telegram_send(f"🚨 {title}\n{message}\nDecision support only — no order executed.")
            except Exception as exc:
                self.db.notify("WARNING", "Telegram delivery failed", str(exc))
