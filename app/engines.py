from dataclasses import asdict, dataclass
from statistics import fmean


@dataclass(frozen=True)
class Candidate:
    pair: str; score: int; status: str; strategy: str; regime: str
    current_price: float; entry_low: float; entry_high: float; stop_loss: float
    take_profit_1: float; take_profit_2: float; risk_reward: float; reason: str


class EntryEngine:
    def rank(self, pair: str, signals: list[dict], current_price: float) -> Candidate:
        buys = [s for s in signals if s["signal"] == "BUY"]
        four = next((s for s in signals if s["timeframe"] == "4h"), None)
        avg_score = round(fmean(s["score"] for s in signals)) if signals else 0
        closes = [s["details"]["close"] for s in buys]
        ref = fmean(closes) if closes else current_price
        atr = fmean(s["details"]["atr"] for s in signals) if signals else current_price * .01
        risk = max(atr * 2, ref * .01)
        aligned = len(buys) >= 2 and (not four or four["signal"] != "SELL")
        in_zone = ref * .995 <= current_price <= ref * 1.01
        status = "BUY_NOW" if aligned and in_zone else "WATCH" if avg_score >= 45 else "WAIT"
        strategy = "Breakout" if current_price >= ref else "EMA Pullback"
        reason = f"{len(buys)}/3 timeframes aligned; 4h veto clear" if aligned else "insufficient timeframe confirmation"
        return Candidate(pair, avg_score, status, strategy, four["regime"] if four else "UNKNOWN", current_price, ref * .995, ref * 1.01, ref-risk, ref+risk*1.5, ref+risk*2.5, 2.5, reason)


class PositionExitEngine:
    def evaluate(self, price: float, plan: dict, signals: list[dict]) -> tuple[str, str, float]:
        high = max(float(plan.get("highest_price", price)), price)
        effective = float(plan["stop_loss"])
        if plan.get("trailing_enabled") and (high / plan["entry_price"] - 1) * 100 >= plan["trailing_activation_percent"]:
            effective = max(effective, high * (1 - plan["trailing_distance_percent"] / 100), float(plan.get("effective_stop", 0)))
        if price <= effective: return "STOP_LOSS", "effective stop touched", effective
        if price >= plan["take_profit_2"]: return "SELL_NOW", "TAKE_PROFIT_2 reached", effective
        if price >= plan["take_profit_1"]: return "TAKE_PROFIT", "TAKE_PROFIT_1 reached; consider partial exit", effective
        sells = sum(s["signal"] == "SELL" for s in signals)
        four = next((s for s in signals if s["timeframe"] == "4h"), None)
        if sells >= 2 or (four and four["signal"] == "SELL"): return "SELL_NOW", "TREND_INVALIDATION across strategy timeframes", effective
        if sum(s["score"] < 0 for s in signals) >= 2: return "EXIT_WATCH", "MOMENTUM_REVERSAL developing", effective
        return "HOLD", "setup remains valid", effective


def candidate_dict(candidate: Candidate) -> dict:
    return asdict(candidate)
