import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets

from app.db import Database
from app.engines import PositionExitEngine
from app.integrations import DeepSeekSecondOpinion
from app.market import PAIR_TO_SYMBOL
from app.translations import to_thai_status

logger = logging.getLogger(__name__)

PAIR_TO_WS = {pair: f"thb_{pair.split('/')[0].lower()}" for pair in PAIR_TO_SYMBOL}
AI_SELL_SIGNAL_COOLDOWN_SECONDS = 15 * 60  # 15 minutes
AI_BUY_SIGNAL_COOLDOWN_SECONDS = 15 * 60  # 15 minutes


def format_bullets(val) -> str:
    if isinstance(val, list):
        items = [f"• {item}" for item in val if item]
        return "\n".join(items) if items else "• N/A"
    if isinstance(val, str):
        lines = [line.strip("- *• \t\r") for line in val.split("\n") if line.strip()]
        if len(lines) > 1:
            return "\n".join(f"• {line}" for line in lines)
        return f"• {val.strip()}" if val.strip() else "• N/A"
    return "• N/A"


def format_buy_alert(
    pair: str,
    price: float,
    entry_low: float,
    entry_high: float,
    reference: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    score: int,
    confirmed_timeframes: str,
    strategy: str,
    regime: str,
    btc_bias: str | None = None,
    ai_opinion: dict | None = None,
    ai_section: str | None = None,
    risk_reward: str | None = None,
    suggested_allocation: float | None = None,
    risk_amount: float | None = None,
    is_test: bool = False,
    now_str: str | None = None,
) -> str:
    prefix = "🧪 TEST ALERT — ไม่ใช่สัญญาณจริง\n\n" if is_test else ""
    title = f"{prefix}🚨 ซื้อได้ตอนนี้ · {pair}" + ("" if is_test else " (BUY_NOW)")

    regime_map = {
        "TRENDING_UP": "แนวโน้มขาขึ้น",
        "UPTREND": "แนวโน้มขาขึ้น",
        "TRENDING_DOWN": "แนวโน้มขาลง",
        "DOWNTREND": "แนวโน้มขาลง",
        "SIDEWAYS": "ตลาดไซด์เวย์",
        "RANGE": "ตลาดไซด์เวย์",
    }
    regime_th = regime_map.get(str(regime).upper(), str(regime))

    if is_test:
        score_line = f"คะแนนระบบ: {score}/100"
    else:
        score_line = f"คะแนนระบบ: {score}/100 (คะแนน: {score})"

    def _fmt_price_val(val: float) -> str:
        if is_test:
            if val >= 100:
                if val == int(val):
                    return f"{int(val):,} THB"
                return f"{val:,.2f} THB"
            return f"{val:,.4f} THB"
        return f"{val:,.4f} THB"

    def _fmt_zone_val(low: float, high: float) -> str:
        if is_test and low >= 100 and high >= 100:
            l_str = f"{int(low):,}" if low == int(low) else f"{low:,.2f}"
            h_str = f"{int(high):,}" if high == int(high) else f"{high:,.2f}"
            return f"{l_str} - {h_str} THB"
        return f"{low:,.4f} - {high:,.4f} THB"

    btc_bias_line = f"BTC bias: {btc_bias}\n" if btc_bias else ""

    header_block = (
        f"{title}\n\n"
        f"{score_line}\n"
        f"ยืนยัน: {confirmed_timeframes}\n"
        f"กลยุทธ์: {strategy}\n"
        f"สภาวะตลาด: {regime_th}\n"
        f"{btc_bias_line}\n"
        f"ราคาปัจจุบัน: {_fmt_price_val(price)}\n"
        f"โซนเข้าซื้อ: {_fmt_zone_val(entry_low, entry_high)}\n"
        f"Stop Loss: {_fmt_price_val(stop_loss)}\n"
        f"TP1: {_fmt_price_val(tp1)}\n"
        f"TP2: {_fmt_price_val(tp2)}"
    )

    sizing_lines = []
    if risk_reward:
        sizing_lines.append(f"Risk/Reward: {risk_reward}")
    if suggested_allocation is not None:
        sizing_lines.append(f"เงินที่แนะนำ: {suggested_allocation:,.0f} THB")
    if risk_amount is not None:
        sizing_lines.append(f"ความเสี่ยงโดยประมาณ: {risk_amount:,.0f} THB")

    sizing_block = ("\n\n" + "\n".join(sizing_lines)) if sizing_lines else ""

    if ai_section:
        ai_block = ai_section
    elif ai_opinion:
        status = ai_opinion.get("status", "connected")
        is_unavail = (
            status in {"timeout", "error", "unavailable"}
            or "AI analysis unavailable" in str(ai_opinion.get("assessment", ""))
        )
        if is_unavail:
            ai_block = "🤖 ความเห็นที่ 2 จาก DeepSeek:\nAI analysis unavailable"
        else:
            conf = ai_opinion.get("confidence", 0.0)
            if isinstance(conf, (int, float)):
                conf_str = f"{int(round(conf * 100))}%" if is_test else f"{conf:.2f} ({int(round(conf * 100))}%)"
            elif isinstance(conf, str):
                conf_str = conf if "%" in conf else f"{conf}%"
            else:
                conf_str = "80%"

            ai_block = (
                f"🤖 ความเห็นที่ 2 จาก DeepSeek\n"
                f"ความมั่นใจ: {conf_str}\n\n"
                f"ภาพรวม:\n{ai_opinion.get('assessment', 'N/A')}\n\n"
                f"มุมบวก:\n{format_bullets(ai_opinion.get('bull_case', 'N/A'))}\n\n"
                f"มุมลบ:\n{format_bullets(ai_opinion.get('bear_case', 'N/A'))}\n\n"
                f"ความเสี่ยงสำคัญ:\n{format_bullets(ai_opinion.get('key_risks', 'N/A'))}\n\n"
                f"สิ่งที่ควรจับตา:\n{format_bullets(ai_opinion.get('watch_next', 'N/A'))}\n\n"
                f"สรุป:\n{ai_opinion.get('summary', 'N/A')}"
            )
    else:
        ai_block = "🤖 ความเห็นที่ 2 จาก DeepSeek:\nAI analysis unavailable"

    if is_test:
        footer = (
            "⚠️ ระบบไม่ได้ส่งคำสั่งซื้ออัตโนมัติ\n"
            "กรุณาตรวจสอบราคาและดำเนินการบน Bitkub ด้วยตนเอง"
        )
    else:
        footer = (
            "⚠️ ระบบไม่ได้ส่งคำสั่งซื้ออัตโนมัติ — ผู้ใช้เป็นผู้ตัดสินใจและดำเนินการบน Bitkub ด้วยตนเอง"
        )

    return f"{header_block}{sizing_block}\n\n{ai_block}\n\n{footer}"


def format_sell_alert(
    pair: str,
    mode: str,
    actual_entry_price: float,
    current_price: float,
    quantity: float,
    asset: str,
    gross_pnl_thb: float,
    gross_pnl_pct: float,
    reason: str,
    stop_loss: float,
    trailing_stop: float,
    tp1: float,
    tp2: float,
    ai_opinion: dict | None = None,
    ai_section: str | None = None,
    is_test: bool = False,
    now_str: str | None = None,
) -> str:
    prefix = "🧪 TEST ALERT — ไม่ใช่สัญญาณจริง\n\n" if is_test else ""
    title = f"{prefix}🚨 ขายตอนนี้ · {pair}" + ("" if is_test else " (SELL_NOW)")

    def _fmt_dec(val: float, decimals: int = 4) -> str:
        return f"{val:,.{decimals}f}"

    qty_str = f"{quantity:,.2f}" if (is_test and quantity == round(quantity, 2)) else f"{quantity:,.4f}"
    pnl_label = "กำไร/ขาดทุนตามราคาตลาด" if is_test else "กำไร/ขาดทุนโดยประมาณ (ตามราคาตลาด)"

    if ai_section:
        ai_block = ai_section
    elif ai_opinion:
        status = ai_opinion.get("status", "connected")
        is_unavail = (
            status in {"timeout", "error", "unavailable"}
            or "AI analysis unavailable" in str(ai_opinion.get("assessment", ""))
            or "AI analysis unavailable" in str(ai_opinion.get("why_exit_now", ""))
        )
        if is_unavail:
            ai_block = "🤖 ความเห็นที่ 2 จาก DeepSeek:\nAI analysis unavailable"
        else:
            conf = ai_opinion.get("confidence", 0.0)
            if isinstance(conf, (int, float)):
                conf_str = f"{int(round(conf * 100))}%" if is_test else f"{conf:.2f} ({int(round(conf * 100))}%)"
            elif isinstance(conf, str):
                conf_str = conf if "%" in conf else f"{conf}%"
            else:
                conf_str = "88%"

            assessment_val = ai_opinion.get("assessment")
            assessment_line = f"ภาพรวม:\n{assessment_val}\n\n" if assessment_val else ""

            ai_block = (
                f"🤖 ความเห็นที่ 2 จาก DeepSeek\n"
                f"ความมั่นใจ: {conf_str}\n\n"
                f"{assessment_line}"
                f"ทำไมระบบแนะนำให้ออก:\n{ai_opinion.get('why_exit_now', 'N/A')}\n\n"
                f"ความเสี่ยงหากยังถือ:\n{ai_opinion.get('risk_of_holding', 'N/A')}\n\n"
                f"มุมมองโต้แย้ง:\n{ai_opinion.get('counter_case', 'N/A')}\n\n"
                f"ความเสี่ยงสำคัญ:\n{format_bullets(ai_opinion.get('key_risks', 'N/A'))}\n\n"
                f"สรุป:\n{ai_opinion.get('summary', 'N/A')}"
            )
    else:
        ai_block = "🤖 ความเห็นที่ 2 จาก DeepSeek:\nAI analysis unavailable"

    if is_test:
        footer = (
            "⚠️ ยังไม่มีการขายจริง\n"
            "กรุณาดำเนินการขายบน Bitkub แล้วกลับมาบันทึกราคาขายจริงในระบบ"
        )
    else:
        footer = (
            "⚠️ ระบบเพื่อการตัดสินใจเท่านั้น — ไม่มีการส่งคำสั่งเทรดอัตโนมัติ ผู้ใช้เป็นผู้ตัดสินใจและดำเนินการบน Bitkub ด้วยตนเอง"
        )

    thb_suffix = " THB" if is_test else ""
    trailing_label = "Effective/Trailing Stop" if is_test else "Trailing Stop"

    msg = (
        f"{title}\n\n"
        f"พอร์ต: {mode}\n"
        f"ต้นทุนจริง: {_fmt_dec(actual_entry_price, 4)} THB\n"
        f"ราคาปัจจุบัน: {_fmt_dec(current_price, 4)} THB\n"
        f"จำนวน: {qty_str} {asset}\n\n"
        f"{pnl_label}: {gross_pnl_thb:+,.2f} THB ({gross_pnl_pct:+.2f}%)\n"
        f"เหตุผลจากระบบ: {reason}\n\n"
        f"Stop Loss: {_fmt_dec(stop_loss, 4)}{thb_suffix}\n"
        f"{trailing_label}: {_fmt_dec(trailing_stop, 4)}{thb_suffix}\n"
        f"TP1: {_fmt_dec(tp1, 4)}{thb_suffix}\n"
        f"TP2: {_fmt_dec(tp2, 4)}{thb_suffix}\n\n"
        f"{ai_block}\n\n"
        f"{footer}"
    )
    return msg


class RealtimeMonitor:
    """Public market watcher only. It creates alerts and can never execute orders."""

    def __init__(
        self,
        db: Database,
        telegram_send: Callable[[str], Awaitable[None]] | None = None,
        deepseek: DeepSeekSecondOpinion | None = None,
        ai_sell_cooldown_seconds: int = AI_SELL_SIGNAL_COOLDOWN_SECONDS,
        ai_buy_cooldown_seconds: int = AI_BUY_SIGNAL_COOLDOWN_SECONDS,
    ):
        self.db = db
        self.telegram_send = telegram_send
        self.deepseek = deepseek
        self.ai_sell_cooldown_seconds = ai_sell_cooldown_seconds
        self.ai_buy_cooldown_seconds = ai_buy_cooldown_seconds
        self.connected = False
        self.last_event_at: float | None = None
        self._last_alert: dict[str, float] = {}
        self._last_ai_sell_opinion: dict[str, float] = {}
        self._last_ai_buy_opinion: dict[str, float] = {}
        self._last_candidate_status: dict[str, str] = {}
        self._ws_disconnected_notified = False
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
                    if self._ws_disconnected_notified:
                        self._ws_disconnected_notified = False
                        self.db.notify("INFO", "Bitkub WebSocket connected", "เชื่อมต่อ Bitkub WebSocket สำเร็จและรับข้อมูลเรียบร้อยแล้ว")
                    async for raw in socket:
                        for line in str(raw).splitlines():
                            if line.strip():
                                await self.handle_event(json.loads(line))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                if not self._ws_disconnected_notified:
                    self._ws_disconnected_notified = True
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

        # Portfolio Guard: Check if ANY position is open across portfolio
        has_any_open_pos = (
            context.get("has_open_position", False)
            or bool(context.get("all_open_positions"))
            or self.db.has_open_positions()
        )

        if signals:
            buys = [signal for signal in signals if signal["signal"] == "BUY"]
            sells = [signal for signal in signals if signal["signal"] == "SELL"]
            four_hour = next((signal for signal in signals if signal["timeframe"] == "4h"), None)

            # Check BUY_NOW condition: >= 2 timeframes aligned, 4h not selling, price in entry zone
            aligned = len(buys) >= 2 and (not four_hour or four_hour["signal"] != "SELL")
            in_zone = False
            if aligned:
                reference = sum(signal["details"]["close"] for signal in buys) / len(buys)
                entry_low = reference * 0.995
                entry_high = reference * 1.01
                in_zone = reference <= price <= entry_high

                if in_zone:
                    # STRICT ONE-POSITION SIGNAL GUARD:
                    # Suppress BUY_NOW event, DeepSeek, and Telegram if ANY position is open.
                    if has_any_open_pos:
                        logger.info(
                            "Portfolio Guard: Suppressed BUY_NOW for %s because another position is currently open.",
                            pair,
                        )
                    else:
                        details = buys[0]["details"]
                        score = round(sum(s["score"] for s in signals) / len(signals))
                        is_high_conviction = (
                            score >= 80
                            and len(buys) >= 3
                            and len(buys) == len(signals)
                        )
                        if is_high_conviction:
                            await self._handle_buy_now_with_ai(
                                pair=pair,
                                price=price,
                                entry_low=entry_low,
                                entry_high=entry_high,
                                reference=reference,
                                details=details,
                                score=score,
                                signals=signals,
                                buys=buys,
                                context=context,
                            )
                        else:
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

            # Candidate status tracking for state transitions:
            # WAIT -> WATCH, WATCH -> BUY_NOW, BUY_NOW -> no longer valid
            if has_any_open_pos:
                cand_status = "WAIT"
            elif aligned and in_zone:
                cand_status = "BUY_NOW"
            elif aligned or len(buys) >= 1:
                cand_status = "WATCH"
            else:
                cand_status = "WAIT"

            prev_cand_status = self._last_candidate_status.get(pair)
            self._last_candidate_status[pair] = cand_status
            if prev_cand_status is not None and prev_cand_status != cand_status:
                if prev_cand_status == "WAIT" and cand_status == "WATCH":
                    self.db.notify(
                        "INFO",
                        f"WATCH · {pair}",
                        f"เริ่มจับตา: สัญญาณซื้อเริ่มสอดคล้อง {len(buys)}/3 Timeframes (รอย่อเข้าโซนซื้อ)",
                    )
                elif prev_cand_status == "BUY_NOW" and cand_status != "BUY_NOW":
                    self.db.notify(
                        "INFO",
                        f"BUY_NOW no longer valid · {pair}",
                        f"สัญญาณซื้อ {pair} สิ้นสุดลง (เปลี่ยนสถานะเป็น {to_thai_status(cand_status)})",
                    )

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

            # Protective exits for open positions without a dynamic plan
            planned_modes = {p["mode"] for p in context["plans"]}
            protective = four_hour or signals[-1]
            details = protective["details"]
            for position in context["positions"]:
                if position["mode"] in planned_modes:
                    continue
                pos_mode = position["mode"]
                avg_cost = float(position["average_cost"])
                pnl_thb = float(position["quantity"]) * (price - avg_cost)
                pnl_pct = ((price - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0

                stop_loss_val = details.get("stop_loss")
                take_profit_val = details.get("take_profit", details.get("take_profit_1"))

                if stop_loss_val is not None and price <= stop_loss_val:
                    msg = (
                        f"🚨 ตัดขาดทุน (STOP_LOSS) · {pair}\n\n"
                        f"พอร์ต: {pos_mode}\n"
                        f"ต้นทุนจริง: {avg_cost:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท (หลุด Stop: {stop_loss_val:,.4f})\n"
                        f"กำไร/ขาดทุนโดยประมาณ: {pnl_thb:+,.2f} บาท ({pnl_pct:+.2f}%)\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"คำแนะนำ: หากเป็นพอร์ตจริง ให้ดำเนินการขายตัดขาดทุนบน Bitkub แล้วบันทึกในระบบ"
                    )
                    candidates.append(("STOP_LOSS", msg, {"price": price}))
                elif take_profit_val is not None and price >= take_profit_val:
                    msg = (
                        f"🎯 ทำกำไร (TAKE_PROFIT) · {pair}\n\n"
                        f"พอร์ต: {pos_mode}\n"
                        f"ต้นทุนจริง: {avg_cost:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท (แตะเป้าหมาย: {take_profit_val:,.4f})\n"
                        f"กำไร/ขาดทุนโดยประมาณ: {pnl_thb:+,.2f} บาท ({pnl_pct:+.2f}%)\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"คำแนะนำ: หากเป็นพอร์ตจริง ให้ดำเนินการแบ่งทำกำไรบน Bitkub แล้วบันทึกในระบบ"
                    )
                    candidates.append(("TAKE_PROFIT", msg, {"price": price}))

            for plan in context["plans"]:
                prev_action = plan.get("current_action") or plan.get("action")
                action, reason, effective_stop = self.exit_engine.evaluate(price, plan, signals)
                self.db.update_position_plan(plan["mode"], action, reason, price, effective_stop)

                # Check if an open position exists for this plan
                matching_pos = next(
                    (p for p in context["positions"] if p["mode"] == plan["mode"] and float(p.get("quantity", 0)) > 0),
                    None,
                )
                has_open_pos = matching_pos is not None
                is_state_change = bool(prev_action is not None and action != prev_action)

                if action == "SELL_NOW":
                    # Automatic DeepSeek second opinion for critical SELL_NOW:
                    # Trigger: status == SELL_NOW, open position exists, exit signal produced by PositionExitEngine
                    if has_open_pos:
                        await self._handle_sell_now_with_ai(
                            pair=pair,
                            price=price,
                            plan=plan,
                            matching_pos=matching_pos,
                            reason=reason,
                            effective_stop=effective_stop,
                            signals=signals,
                            context=context,
                            force_state_change=is_state_change,
                        )
                    else:
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
                        candidates.append((action, msg, {"price": price, "force_state_change": is_state_change}))

                elif action == "STOP_LOSS":
                    # Urgent exit! Send deterministic alert immediately without waiting for AI
                    th_action = to_thai_status(action)
                    actual_cost = float(matching_pos["average_cost"]) if matching_pos else float(plan["entry_price"])
                    qty = float(matching_pos["quantity"]) if matching_pos else 0.0
                    asset = plan.get("asset", pair.split("/")[0].upper())
                    pnl_thb = qty * (price - actual_cost)
                    pnl_pct = ((price - actual_cost) / actual_cost * 100.0) if actual_cost > 0 else 0.0

                    msg = (
                        f"🚨 {th_action} ({action}) · {pair}\n\n"
                        f"พอร์ต: {plan['mode']}\n"
                        f"ต้นทุนจริง: {actual_cost:,.4f} บาท | จำนวน: {qty:,.4f} {asset}\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท (หลุด Stop: {effective_stop:,.4f})\n"
                        f"กำไร/ขาดทุนโดยประมาณ: {pnl_thb:+,.2f} บาท ({pnl_pct:+.2f}%)\n"
                        f"เหตุผลทางเทคนิค: {reason}\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"⚠️ คำแนะนำ: การตัดสินใจดำเนินการขึ้นอยู่กับผู้ใช้ ให้ดำเนินการขายตัดขาดทุนบน Bitkub แล้วบันทึกในระบบ"
                    )
                    candidates.append((action, msg, {"price": price, "force_state_change": is_state_change}))

                elif action in {"EXIT_WATCH", "TAKE_PROFIT"}:
                    # DeepSeek is NOT called for EXIT_WATCH by default
                    th_action = to_thai_status(action)
                    msg = (
                        f"🚨 {th_action} ({action}) · {pair}\n\n"
                        f"พอร์ต: {plan['mode']}\n"
                        f"ต้นทุนจริง: {plan['entry_price']:,.4f} บาท\n"
                        f"ราคาปัจจุบัน: {price:,.4f} บาท\n"
                        f"เหตุผล: {reason}\n"
                        f"เวลา: {now_bkk}\n\n"
                        f"คำแนะนำ: การตัดสินใจดำเนินการขึ้นอยู่กับผู้ใช้ โดยบันทึกผลการเทรดในระบบ"
                    )
                    candidates.append((action, msg, {"price": price, "force_state_change": is_state_change}))

                elif action == "HOLD":
                    if prev_action == "EXIT_WATCH":
                        self.db.notify(
                            "INFO",
                            f"HOLD · {pair}",
                            f"สัญญาณกลับสู่ปกติ: โมเมนตัมฟื้นตัว สภาพตลาดยังอยู่ในเกณฑ์ปกติ ({reason})",
                        )

        for item in candidates:
            kind, message = item[0], item[1]
            meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
            force = meta.get("force_state_change", False)
            await self._alert(kind, pair, message, force_state_change=force)

    async def _handle_buy_now_with_ai(
        self,
        pair: str,
        price: float,
        entry_low: float,
        entry_high: float,
        reference: float,
        details: dict,
        score: int,
        signals: list[dict],
        buys: list[dict],
        context: dict,
    ):
        now_mono = time.monotonic()
        alert_key = f"BUY_NOW:{pair}"
        alert_cooldown = self.db.settings().get("realtime_alert_cooldown_seconds", 300)
        last_alert_time = self._last_alert.get(alert_key)
        if last_alert_time is not None and (now_mono - last_alert_time) < alert_cooldown:
            return

        now_bkk = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
        stop_loss_val = float(details.get("stop_loss", reference * 0.98))
        tp1_val = float(details.get("take_profit_1", reference * 1.03))
        tp2_val = float(details.get("take_profit_2", reference * 1.05))

        sig_15m = next((s for s in signals if s.get("timeframe") == "15m"), None)
        sig_1h = next((s for s in signals if s.get("timeframe") == "1h"), None)
        sig_4h = next((s for s in signals if s.get("timeframe") == "4h"), None)

        trend_15m = sig_15m.get("regime", "UNKNOWN") if sig_15m else "UNKNOWN"
        trend_1h = sig_1h.get("regime", "UNKNOWN") if sig_1h else "UNKNOWN"
        trend_4h = sig_4h.get("regime", "UNKNOWN") if sig_4h else "UNKNOWN"

        primary_sig = sig_1h or sig_15m or (signals[0] if signals else None)
        sig_details = primary_sig.get("details", {}) if primary_sig else details

        ema_9 = sig_details.get("ema_9", sig_details.get("ema_fast"))
        ema_20 = sig_details.get("ema_20", sig_details.get("ema_slow"))
        ema_50 = sig_details.get("ema_50")
        rsi_val = sig_details.get("rsi")
        atr_val = sig_details.get("atr")
        rel_vol = sig_details.get("relative_volume")
        market_regime = primary_sig.get("regime", "UNKNOWN") if primary_sig else "UNKNOWN"

        btc_signals = context.get("btc_signals", [])
        btc_primary = (
            next((s for s in btc_signals if s.get("timeframe") == "4h"), None)
            or next((s for s in btc_signals if s.get("timeframe") == "1h"), None)
            or (btc_signals[0] if btc_signals else None)
        )
        btc_market_bias = btc_primary.get("regime", "NEUTRAL") if btc_primary else "NEUTRAL"
        strategy = details.get("strategy") or "Deterministic High-Conviction Trend"

        ai_cooldown_key = f"{pair}:BUY_NOW"
        last_ai_time = self._last_ai_buy_opinion.get(ai_cooldown_key)
        in_ai_cooldown = (last_ai_time is not None) and ((now_mono - last_ai_time) < self.ai_buy_cooldown_seconds)

        if in_ai_cooldown:
            ai_section = (
                "🤖 ความเห็นที่ 2 จาก DeepSeek:\n"
                f"(ข้ามการเรียก AI ซ้ำ — อยู่ในช่วง Cooldown {self.ai_buy_cooldown_seconds // 60} นาที)"
            )
        else:
            self._last_ai_buy_opinion[ai_cooldown_key] = now_mono
            ai_ctx = {
                "symbol": pair,
                "current_bitkub_price": price,
                "entry_zone": f"{entry_low:,.4f} - {entry_high:,.4f}",
                "reference_price": round(reference, 4),
                "stop_loss": stop_loss_val,
                "take_profit_1": tp1_val,
                "take_profit_2": tp2_val,
                "score": score,
                "confirmed_timeframes": f"{len(buys)}/{len(signals)}",
                "trend_15m": trend_15m,
                "trend_1h": trend_1h,
                "trend_4h": trend_4h,
                "ema_structure": {
                    "ema_9": ema_9,
                    "ema_20": ema_20,
                    "ema_50": ema_50,
                },
                "rsi": rsi_val,
                "atr": atr_val,
                "relative_volume": rel_vol,
                "strategy": strategy,
                "market_regime": market_regime,
                "btc_market_bias": btc_market_bias,
            }

            if self.deepseek and self.deepseek.configured:
                try:
                    ai_opinion = await asyncio.wait_for(self.deepseek.ask_buy_opinion(ai_ctx), timeout=10.0)
                except Exception as exc:
                    logger.warning(f"DeepSeek buy opinion call failed or timed out: {exc}")
                    ai_opinion = {
                        "status": "timeout" if isinstance(exc, asyncio.TimeoutError) else "error",
                        "assessment": "AI analysis unavailable",
                        "confidence": 0.0,
                        "bull_case": "N/A",
                        "bear_case": "N/A",
                        "key_risks": ["AI analysis unavailable"],
                        "watch_next": ["ยึดตามสัญญาณเทคนิคและ Stop Loss ของระบบ"],
                        "summary": "AI analysis unavailable - ระบบยังคงใช้การคำนวณแบบ Deterministic",
                    }
            else:
                ai_opinion = {
                    "status": "unavailable",
                    "assessment": "AI analysis unavailable",
                    "confidence": 0.0,
                    "bull_case": "N/A",
                    "bear_case": "N/A",
                    "key_risks": ["AI analysis unavailable"],
                    "watch_next": ["ยึดตามสัญญาณเทคนิคและ Stop Loss ของระบบ"],
                    "summary": "AI analysis unavailable",
                }

            status = ai_opinion.get("status", "error")
            is_unavailable = (
                status in {"timeout", "error", "unavailable"}
                or "AI analysis unavailable" in str(ai_opinion.get("assessment", ""))
            )

            if is_unavailable:
                ai_section = (
                    "🤖 ความเห็นที่ 2 จาก DeepSeek:\n"
                    "AI analysis unavailable"
                )
            else:
                ai_section = None

        msg = format_buy_alert(
            pair=pair,
            price=price,
            entry_low=entry_low,
            entry_high=entry_high,
            reference=reference,
            stop_loss=stop_loss_val,
            tp1=tp1_val,
            tp2=tp2_val,
            score=score,
            confirmed_timeframes=f"{len(buys)}/3 Timeframes",
            strategy=strategy,
            regime=market_regime,
            ai_opinion=ai_opinion if (not in_ai_cooldown and not is_unavailable) else None,
            ai_section=ai_section,
            is_test=False,
            now_str=now_bkk,
        )
        await self._alert("BUY_NOW", pair, msg)

    async def _handle_sell_now_with_ai(
        self,
        pair: str,
        price: float,
        plan: dict,
        matching_pos: dict,
        reason: str,
        effective_stop: float,
        signals: list[dict],
        context: dict,
        force_state_change: bool = False,
    ):
        now_bkk = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
        asset = plan.get("asset", pair.split("/")[0].upper())
        actual_entry_price = float(matching_pos.get("average_cost", plan["entry_price"]))
        quantity = float(matching_pos.get("quantity", 0.0))
        gross_pnl_thb = quantity * (price - actual_entry_price)
        gross_pnl_pct = ((price - actual_entry_price) / actual_entry_price * 100.0) if actual_entry_price > 0 else 0.0

        # Signals analysis
        sig_15m = next((s for s in signals if s.get("timeframe") == "15m"), None)
        sig_1h = next((s for s in signals if s.get("timeframe") == "1h"), None)
        sig_4h = next((s for s in signals if s.get("timeframe") == "4h"), None)

        trend_15m = sig_15m.get("regime", "UNKNOWN") if sig_15m else "UNKNOWN"
        trend_1h = sig_1h.get("regime", "UNKNOWN") if sig_1h else "UNKNOWN"
        trend_4h = sig_4h.get("regime", "UNKNOWN") if sig_4h else "UNKNOWN"

        primary_sig = sig_1h or sig_15m or (signals[0] if signals else None)
        details = primary_sig.get("details", {}) if primary_sig else {}

        ema_9 = details.get("ema_9", details.get("ema_fast"))
        ema_20 = details.get("ema_20", details.get("ema_slow"))
        ema_50 = details.get("ema_50")
        rsi_val = details.get("rsi")
        atr_val = details.get("atr")
        rel_vol = details.get("relative_volume")
        market_regime = primary_sig.get("regime", "UNKNOWN") if primary_sig else "UNKNOWN"

        btc_signals = context.get("btc_signals", [])
        btc_primary = (
            next((s for s in btc_signals if s.get("timeframe") == "4h"), None)
            or next((s for s in btc_signals if s.get("timeframe") == "1h"), None)
            or (btc_signals[0] if btc_signals else None)
        )
        btc_market_bias = btc_primary.get("regime", "NEUTRAL") if btc_primary else "NEUTRAL"

        ai_cooldown_key = f"{plan['mode']}:{pair}:SELL_NOW"
        now_mono = time.monotonic()
        last_ai_time = self._last_ai_sell_opinion.get(ai_cooldown_key)
        in_cooldown = (not force_state_change) and (last_ai_time is not None) and ((now_mono - last_ai_time) < self.ai_sell_cooldown_seconds)

        if in_cooldown:
            ai_section = (
                "🤖 ความเห็นที่ 2 จาก DeepSeek:\n"
                f"(ข้ามการเรียก AI ซ้ำ — อยู่ในช่วง Cooldown {self.ai_sell_cooldown_seconds // 60} นาที)"
            )
        else:
            self._last_ai_sell_opinion[ai_cooldown_key] = now_mono
            ai_ctx = {
                "symbol": pair,
                "mode": plan["mode"],
                "actual_entry_price": actual_entry_price,
                "current_bitkub_price": price,
                "quantity": quantity,
                "gross_unrealized_pnl": round(gross_pnl_thb, 4),
                "gross_unrealized_pnl_percent": round(gross_pnl_pct, 2),
                "current_exit_reason": reason,
                "stop_loss": float(plan.get("stop_loss", 0.0)),
                "effective_stop": float(effective_stop),
                "trailing_stop": float(effective_stop),
                "take_profit_1": float(plan.get("take_profit_1", 0.0)),
                "take_profit_2": float(plan.get("take_profit_2", 0.0)),
                "tp1": float(plan.get("take_profit_1", 0.0)),
                "tp2": float(plan.get("take_profit_2", 0.0)),
                "trend_15m": trend_15m,
                "trend_1h": trend_1h,
                "trend_4h": trend_4h,
                "ema_structure": {
                    "ema_9": ema_9,
                    "ema_20": ema_20,
                    "ema_50": ema_50,
                },
                "rsi": rsi_val,
                "atr": atr_val,
                "relative_volume": rel_vol,
                "market_regime": market_regime,
                "btc_market_bias": btc_market_bias,
                "original_entry_strategy": plan.get("strategy") or "Manual Real Execution",
                "original_entry_score": plan.get("entry_score"),
                "current_deterministic_exit_score": primary_sig.get("score") if primary_sig else None,
                "current_deterministic_exit_reason": reason,
            }

            if self.deepseek and self.deepseek.configured:
                try:
                    ai_opinion = await asyncio.wait_for(self.deepseek.ask_sell_opinion(ai_ctx), timeout=10.0)
                except Exception as exc:
                    logger.warning(f"DeepSeek sell opinion call failed or timed out: {exc}")
                    ai_opinion = {
                        "status": "timeout" if isinstance(exc, asyncio.TimeoutError) else "error",
                        "assessment": "AI analysis unavailable",
                        "confidence": 0.0,
                        "why_exit_now": "AI analysis unavailable",
                        "risk_of_holding": "N/A",
                        "counter_case": "N/A",
                        "key_risks": ["AI analysis unavailable"],
                        "watch_next": ["ยึดตามสัญญาณเทคนิคและ Stop Loss ของระบบ"],
                        "summary": "AI analysis unavailable - ระบบยังคงใช้การคำนวณแบบ Deterministic",
                    }
            else:
                ai_opinion = {
                    "status": "unavailable",
                    "assessment": "AI analysis unavailable",
                    "confidence": 0.0,
                    "why_exit_now": "AI analysis unavailable",
                    "risk_of_holding": "N/A",
                    "counter_case": "N/A",
                    "key_risks": ["AI analysis unavailable"],
                    "watch_next": ["ยึดตามสัญญาณเทคนิคและ Stop Loss ของระบบ"],
                    "summary": "AI analysis unavailable",
                }

            status = ai_opinion.get("status", "error")
            is_unavailable = (
                status in {"timeout", "error", "unavailable"}
                or "AI analysis unavailable" in str(ai_opinion.get("assessment", ""))
                or "AI analysis unavailable" in str(ai_opinion.get("why_exit_now", ""))
            )

            if is_unavailable:
                ai_section = (
                    "🤖 ความเห็นที่ 2 จาก DeepSeek:\n"
                    "AI analysis unavailable"
                )
            else:
                ai_section = None

        msg = format_sell_alert(
            pair=pair,
            mode=plan["mode"],
            actual_entry_price=actual_entry_price,
            current_price=price,
            quantity=quantity,
            asset=asset,
            gross_pnl_thb=gross_pnl_thb,
            gross_pnl_pct=gross_pnl_pct,
            reason=reason,
            stop_loss=float(plan.get("stop_loss", 0.0)),
            trailing_stop=float(effective_stop),
            tp1=float(plan.get("take_profit_1", 0.0)),
            tp2=float(plan.get("take_profit_2", 0.0)),
            ai_opinion=ai_opinion if (not in_cooldown and not is_unavailable) else None,
            ai_section=ai_section,
            is_test=False,
            now_str=now_bkk,
        )
        await self._alert("SELL_NOW", pair, msg, force_state_change=force_state_change)

    async def send_test_buy_alert(self) -> str:
        """Send simulated high-conviction BUY alert to Telegram without mutating portfolio/trading state."""
        if not self.telegram_send:
            raise ValueError("ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ")

        sim_ai_opinion = {
            "status": "connected",
            "assessment": "โครงสร้างโดยรวมเป็นบวกและทั้ง 3 Timeframes สนับสนุนทิศทางเดียวกัน แต่ราคากำลังเข้าใกล้แนวต้านระยะสั้น",
            "confidence": "82%",
            "bull_case": [
                "EMA 9/20/50 เรียงตัวเชิงบวก",
                "Volume เพิ่มขึ้นพร้อมราคา",
                "15m, 1h และ 4h ยืนยันตรงกัน",
            ],
            "bear_case": [
                "ราคาใกล้แนวต้าน",
                "หาก Volume ลดลง breakout อาจล้มเหลว",
            ],
            "key_risks": [
                "BTC อ่อนแรงกะทันหัน",
                "Failed breakout",
                "Slippage ตอนเข้าซื้อ",
            ],
            "watch_next": [
                "การยืนเหนือ entry zone",
                "Relative Volume หลัง breakout",
                "RSI divergence",
            ],
            "summary": "สัญญาณมีคุณภาพสูงตามระบบ แต่ควรรอ execution ที่อยู่ในโซนและรักษา Stop Loss ตามแผน",
        }

        msg = format_buy_alert(
            pair="ETH/THB",
            price=82_100.0,
            entry_low=81_900.0,
            entry_high=82_150.0,
            reference=82_000.0,
            stop_loss=80_950.0,
            tp1=84_200.0,
            tp2=85_500.0,
            score=86,
            confirmed_timeframes="3/3",
            strategy="EMA Pullback",
            regime="TRENDING_UP",
            btc_bias="POSITIVE",
            ai_opinion=sim_ai_opinion,
            risk_reward="1:2.1",
            suggested_allocation=5_000.0,
            risk_amount=190.0,
            is_test=True,
        )

        ok = await self.telegram_send(msg)
        if ok is False:
            raise RuntimeError("Telegram API ปฏิเสธการส่งข้อความหรือเกิดข้อผิดพลาดในการเชื่อมต่อ")
        return msg

    async def send_test_sell_alert(self) -> str:
        """Send simulated DOGE SELL alert to Telegram without mutating portfolio/trading state."""
        if not self.telegram_send:
            raise ValueError("ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ")

        qty = 1676.44
        entry = 2.98
        current = 2.92
        gross_pnl_thb = qty * (current - entry)
        gross_pnl_pct = ((current - entry) / entry) * 100.0

        sim_ai_opinion = {
            "status": "connected",
            "assessment": "โครงสร้างระยะสั้นอ่อนตัวลงและสัญญาณออกจากระบบถูกกระตุ้นแล้ว",
            "confidence": "88%",
            "why_exit_now": "แนวโน้ม 15m และ 1h เสียโครงสร้างพร้อมราคาหลุดระดับป้องกัน",
            "risk_of_holding": "มีความเสี่ยงที่การขาดทุนจะขยายตัวหากแรงขายต่อเนื่อง",
            "counter_case": "ราคาอาจเกิด technical rebound หลังหลุดแนวรับ แต่ยังไม่มี confirmation ว่ากลับตัว",
            "key_risks": [
                "Momentum ยังอ่อน",
                "Support เดิมกลายเป็น resistance",
                "ตลาด Altcoin อ่อนตาม BTC",
            ],
            "summary": "ระบบมีเหตุผลเพียงพอในการแนะนำให้ออกจากสถานะ แต่การขายจริงยังต้องดำเนินการด้วยตนเองบน Bitkub",
        }

        msg = format_sell_alert(
            pair="DOGE/THB",
            mode="MANUAL_REAL",
            actual_entry_price=entry,
            current_price=current,
            quantity=qty,
            asset="DOGE",
            gross_pnl_thb=gross_pnl_thb,
            gross_pnl_pct=gross_pnl_pct,
            reason="TREND_INVALIDATION",
            stop_loss=2.9320,
            trailing_stop=2.9450,
            tp1=3.0520,
            tp2=3.1000,
            ai_opinion=sim_ai_opinion,
            is_test=True,
        )

        ok = await self.telegram_send(msg)
        if ok is False:
            raise RuntimeError("Telegram API ปฏิเสธการส่งข้อความหรือเกิดข้อผิดพลาดในการเชื่อมต่อ")
        return msg

    async def _alert(self, kind: str, pair: str, message: str, force_state_change: bool = False):
        key, current = f"{kind}:{pair}", time.monotonic()
        cooldown = self.db.settings().get("realtime_alert_cooldown_seconds", 300)
        previous = self._last_alert.get(key)
        if not force_state_change and previous is not None and current - previous < cooldown:
            return
        self._last_alert[key] = current
        title = f"{kind} · {pair}"
        level = "WARNING" if kind == "EXIT_WATCH" else "CRITICAL"
        self.db.notify(level, title, message)
        if self.telegram_send and kind in {"BUY_NOW", "SELL_NOW", "STOP_LOSS", "TAKE_PROFIT"}:
            try:
                text = message if message.startswith("🚨") else f"🚨 {title}\n\n{message}"
                await self.telegram_send(text)
            except Exception as exc:
                self.db.notify("WARNING", "Telegram delivery failed", str(exc))
