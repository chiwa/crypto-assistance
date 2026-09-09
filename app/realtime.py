import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets

from app.db import Database
from app.engines import PositionExitEngine
from app.market import PAIR_TO_SYMBOL
from app.translations import to_thai_status

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
                        for line in str(raw).splitlines():
                            if line.strip():
                                await self.handle_event(json.loads(line))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.db.notify("WARNING", "Bitkub WebSocket disconnected", f"ตัดการเชื่อมต่อ WebSocket Bitkub: กำลังลองใหม่ใน {backoff}s ({exc})")
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
        candidates: list[tuple[str, str, dict]] = []
        now_bkk = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")

        if signals:
            buys = [signal for signal in signals if signal["signal"] == "BUY"]
            sells = [signal for signal in signals if signal["signal"] == "SELL"]
            four_hour = next((signal for signal in signals if signal["timeframe"] == "4h"), None)

            # Check BUY_NOW condition: >= 2 timeframes aligned, 4h not selling, price in entry zone
            if len(buys) >= 2 and (not four_hour or four_hour["signal"] != "SELL"):
                reference = sum(signal["details"]["close"] for signal in buys) / len(buys)
                entry_low = reference * 0.995
                entry_high = reference * 1.01
                if reference <= price <= entry_high:
                    details = buys[0]["details"]
                    score = round(sum(s["score"] for s in signals) / len(signals))
                    msg = (
                        f"🚨 ซื้อได้ตอนนี้ · {pair}\n\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท\n"
                        f"โซนเข้า: {entry_low:,.4f} - {entry_high:,.4f} บาท\n"
                        f"Stop Loss: {details.get('stop_loss', reference * 0.98):,.4f} บาท\n"
                        f"TP1: {details.get('take_profit_1', reference * 1.03):,.4f} บาท\n"
                        f"TP2: {details.get('take_profit_2', reference * 1.05):,.4f} บาท\n"
                        f"คะแนน: {score}\n"
                        f"เหตุผล: ยืนยันสัญญาณซื้อ {len(buys)}/3 Timeframes ราคาอยู่ในโซนเข้า\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"⚠️ ระบบเพื่อการตัดสินใจเท่านั้น — ไม่มีการส่งคำสั่งเทรดอัตโนมัติ"
                    )
                    candidates.append(("BUY_NOW", msg, {"price": price, "score": score}))

            if len(sells) >= 2 and (not four_hour or four_hour["signal"] != "BUY"):
                reference = sum(signal["details"]["close"] for signal in sells) / len(sells)
                if reference * 0.99 <= price <= reference:
                    msg = (
                        f"🚨 ขายตอนนี้ · {pair}\n\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท\n"
                        f"เหตุผล: ยืนยันสัญญาณขาย {len(sells)}/3 Timeframes ต่ำกว่าราคาอ้างอิง {reference:,.4f}\n"
                        f"เวลา: {now_bkk}"
                    )
                    candidates.append(("SELL_NOW", msg, {"price": price}))

            # Protective exits for open positions
            protective = four_hour or signals[-1]
            details = protective["details"]
            for position in context["positions"]:
                pos_mode = position["mode"]
                avg_cost = float(position["average_cost"])
                pnl_thb = float(position["quantity"]) * (price - avg_cost)
                pnl_pct = ((price - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0

                if price <= details["stop_loss"]:
                    msg = (
                        f"🚨 ตัดขาดทุน (STOP_LOSS) · {pair}\n\n"
                        f"พอร์ต: {pos_mode}\n"
                        f"ต้นทุนจริง: {avg_cost:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท (หลุด Stop: {details['stop_loss']:,.4f})\n"
                        f"กำไร/ขาดทุนโดยประมาณ: {pnl_thb:+,.2f} บาท ({pnl_pct:+.2f}%)\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"คำแนะนำ: หากเป็นพอร์ตจริง ให้ดำเนินการขายตัดขาดทุนบน Bitkub แล้วบันทึกในระบบ"
                    )
                    candidates.append(("STOP_LOSS", msg, {"price": price}))
                elif price >= details["take_profit"]:
                    msg = (
                        f"🎯 ทำกำไร (TAKE_PROFIT) · {pair}\n\n"
                        f"พอร์ต: {pos_mode}\n"
                        f"ต้นทุนจริง: {avg_cost:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท (แตะเป้าหมาย: {details['take_profit']:,.4f})\n"
                        f"กำไร/ขาดทุนโดยประมาณ: {pnl_thb:+,.2f} บาท ({pnl_pct:+.2f}%)\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"คำแนะนำ: หากเป็นพอร์ตจริง ให้ดำเนินการแบ่งทำกำไรบน Bitkub แล้วบันทึกในระบบ"
                    )
                    candidates.append(("TAKE_PROFIT", msg, {"price": price}))

            for plan in context["plans"]:
                action, reason, effective_stop = self.exit_engine.evaluate(price, plan, signals)
                self.db.update_position_plan(plan["mode"], action, reason, price, effective_stop)
                if action in {"EXIT_WATCH", "TAKE_PROFIT", "STOP_LOSS", "SELL_NOW"}:
                    th_action = to_thai_status(action)
                    msg = (
                        f"🚨 {th_action} ({action}) · {pair}\n\n"
                        f"พอร์ต: {plan['mode']}\n"
                        f"ต้นทุนจริง: {plan['entry_price']:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท\n"
                        f"เหตุผล: {reason}\n"
                        f"เวลา: {now_bkk}\n"
                        f"คำแนะนำ: การตัดสินใจดำเนินการขึ้นอยู่กับผู้ใช้ โดยบันทึกผลการเทรดในระบบ"
                    )
                    candidates.append((action, msg, {"price": price}))

        for kind, message, _ in candidates:
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
                await self.telegram_send(f"🚨 {title}\n\n{message}")
            except Exception as exc:
                self.db.notify("WARNING", "Telegram delivery failed", str(exc))
