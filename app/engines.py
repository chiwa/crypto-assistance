from dataclasses import asdict, dataclass
from statistics import fmean

from app.translations import to_thai_regime, to_thai_status, to_thai_strategy


@dataclass(frozen=True)
class Candidate:
    pair: str
    score: int
    status: str
    status_th: str
    strategy: str
    strategy_th: str
    regime: str
    regime_th: str
    current_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    reason: str
    reason_th: str


class EntryEngine:
    def rank(
        self,
        pair: str,
        signals: list[dict],
        current_price: float,
        consecutive_losses: int = 0,
        has_open_position: bool = False,
    ) -> Candidate:
        buys = [s for s in signals if s["signal"] == "BUY"]
        four = next((s for s in signals if s["timeframe"] == "4h"), None)
        avg_score = round(fmean(s["score"] for s in signals)) if signals else 0
        closes = [s["details"]["close"] for s in buys]
        ref = fmean(closes) if closes else current_price
        atr_val = fmean(s["details"]["atr"] for s in signals) if signals else current_price * 0.01
        risk = max(atr_val * 1.5, ref * 0.015)
        
        regime = four["regime"] if four else "UNKNOWN"
        strategy = "Breakout" if current_price >= ref else "EMA Pullback"
        
        stop_loss = round(ref - risk, 4)
        tp1 = round(ref + risk * 1.5, 4)
        tp2 = round(ref + risk * 2.5, 4)
        rr_ratio = round((tp1 - ref) / risk, 2) if risk > 0 else 1.5

        entry_low = round(ref * 0.995, 4)
        entry_high = round(ref * 1.01, 4)
        in_zone = entry_low <= current_price <= entry_high
        aligned = len(buys) >= 2 and (not four or four["signal"] != "SELL")

        # Check circuit breaker and position locks
        if has_open_position:
            status = "WAIT"
            reason = "one-position-at-a-time rule: existing position open"
            reason_th = "จำกัดการถือครอง 1 สถานะพร้อมกัน (มีสถานะเปิดอยู่แล้ว)"
        elif consecutive_losses >= 2:
            status = "WAIT"
            reason = "Circuit breaker triggered: 2 consecutive losses today"
            reason_th = "หยุดเทรดประจำวัน: ขาดทุนติดต่อกัน 2 ครั้งในวันนี้"
        elif aligned and in_zone and avg_score >= 60:
            status = "BUY_NOW" if avg_score >= 75 or (len(buys) == 3 and in_zone) else "WATCH"
            reason = f"{len(buys)}/3 timeframes aligned; price in entry zone [{entry_low:,.2f} - {entry_high:,.2f}]"
            reason_th = f"สัญญาณซื้อสอดคล้อง {len(buys)}/3 Timeframes, ราคาอยู่ในโซนเข้าซื้อ [{entry_low:,.2f} - {entry_high:,.2f}]"
        elif avg_score >= 45 or aligned:
            status = "WATCH"
            reason = f"Setup developing (score {avg_score}); waiting for entry zone touch"
            reason_th = f"กำลังก่อตัว (คะแนน {avg_score}); รอย่อเข้าโซนซื้อหรือยืนยันราคา"
        else:
            status = "WAIT"
            reason = f"No valid setup (score {avg_score}); insufficient timeframe confirmation"
            reason_th = f"ยังไม่มีจังหวะเข้าเทรด (คะแนน {avg_score}); สัญญาณยังไม่ชัดเจน"

        return Candidate(
            pair=pair,
            score=avg_score,
            status=status,
            status_th=to_thai_status(status),
            strategy=strategy,
            strategy_th=to_thai_strategy(strategy),
            regime=regime,
            regime_th=to_thai_regime(regime),
            current_price=round(current_price, 4),
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward=rr_ratio,
            reason=reason,
            reason_th=reason_th,
        )


class PositionExitEngine:
    def evaluate(self, price: float, plan: dict, signals: list[dict]) -> tuple[str, str, float]:
        high = max(float(plan.get("highest_price", price)), price)
        initial_stop = float(plan["stop_loss"])
        effective = float(plan.get("effective_stop", initial_stop))
        entry_price = float(plan["entry_price"])

        # Trailing stop update (never moves downward)
        trailing_enabled = bool(plan.get("trailing_enabled", True))
        activation_pct = float(plan.get("trailing_activation_percent", 2.0))
        distance_pct = float(plan.get("trailing_distance_percent", 1.0))

        if trailing_enabled and entry_price > 0 and ((high / entry_price) - 1.0) * 100.0 >= activation_pct:
            trailed = high * (1.0 - (distance_pct / 100.0))
            effective = max(effective, trailed, initial_stop)

        # 1. Hard stop or Trailing stop hit
        if price <= effective:
            is_trailing = effective > initial_stop
            action = "STOP_LOSS"
            reason = "Trailing stop touched" if is_trailing else "Hard stop loss touched"
            return action, reason, round(effective, 4)

        # 2. Take Profit 2
        tp2 = float(plan["take_profit_2"])
        if price >= tp2:
            return "SELL_NOW", "TAKE_PROFIT_2 reached", round(effective, 4)

        # 3. Take Profit 1
        tp1 = float(plan["take_profit_1"])
        if price >= tp1:
            return "TAKE_PROFIT", "TAKE_PROFIT_1 reached; consider partial exit", round(effective, 4)

        # 4. Strategy Exit / Trend Invalidation
        sells = sum(1 for s in signals if s.get("signal") == "SELL")
        four = next((s for s in signals if s.get("timeframe") == "4h"), None)
        if sells >= 2 or (four and four.get("signal") == "SELL"):
            return "SELL_NOW", "TREND_INVALIDATION across strategy timeframes", round(effective, 4)

        # 5. Momentum Reversal
        negatives = sum(1 for s in signals if s.get("score", 0) < 0)
        if negatives >= 2:
            return "EXIT_WATCH", "MOMENTUM_REVERSAL developing", round(effective, 4)

        return "HOLD", "setup remains valid", round(effective, 4)


def build_exit_plan_from_market(
    pair: str,
    entry_price: float,
    current_price: float,
    signals: list[dict],
    strategy: str = "Manual Real Execution",
    entry_score: int | None = None,
    entry_reason: str = "การซื้อจริงบน Bitkub",
    trailing_activation_percent: float = 2.0,
    trailing_distance_percent: float = 1.0,
) -> dict:
    """Builds an exit plan dynamically from deterministic market context (ATR & structure).
    The actual entry_price is strictly preserved.
    """
    atr_vals = [
        s["details"]["atr"]
        for s in signals
        if "details" in s and isinstance(s["details"], dict) and s["details"].get("atr", 0) > 0
    ]
    if atr_vals:
        atr_val = fmean(atr_vals)
        risk = max(atr_val * 1.5, entry_price * 0.015)
        stop_loss = round(entry_price - risk, 4)
        tp1 = round(entry_price + risk * 1.5, 4)
        tp2 = round(entry_price + risk * 2.5, 4)
        plan_type = "MARKET_DERIVED"
    else:
        # Percentage fallback plan
        stop_loss = round(entry_price * 0.98, 4)  # -2%
        tp1 = round(entry_price * 1.03, 4)        # +3%
        tp2 = round(entry_price * 1.05, 4)        # +5%
        plan_type = "FALLBACK_MANUAL"

    return {
        "asset": pair.split("/")[0].upper(),
        "entry_price": float(entry_price),
        "strategy": strategy,
        "entry_score": entry_score,
        "entry_reason": entry_reason or f"บันทึกการเข้าซื้อ @ {entry_price:,.2f} THB",
        "stop_loss": stop_loss,
        "effective_stop": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "highest_price": max(float(entry_price), float(current_price)),
        "trailing_enabled": 1,
        "trailing_activation_percent": trailing_activation_percent,
        "trailing_distance_percent": trailing_distance_percent,
        "current_action": "HOLD",
        "action_reason": "ถือต่อ - กำลังเฝ้าระวังตำแหน่งจริง",
        "plan_type": plan_type,
    }


def candidate_dict(candidate: Candidate) -> dict:
    return asdict(candidate)

