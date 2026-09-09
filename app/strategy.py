from dataclasses import asdict, dataclass
from statistics import fmean


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return values[-1]
    k = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * k + result * (1.0 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if not closes or len(closes) < 2:
        return (highs[-1] - lows[-1]) if (highs and lows) else 0.0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)
    if len(tr_list) <= period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.0
    res = sum(tr_list[:period]) / period
    for val in tr_list[period:]:
        res = (res * (period - 1) + val) / period
    return res


def relative_volume(volumes: list[float] | None, period: int = 20) -> float:
    if not volumes or len(volumes) < 2:
        return 1.0
    past = volumes[-period - 1 : -1] if len(volumes) > period else volumes[:-1]
    if not past:
        return 1.0
    avg_v = fmean(past)
    return (volumes[-1] / avg_v) if avg_v > 0 else 1.0


@dataclass(frozen=True)
class Analysis:
    signal: str
    score: int
    regime: str
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    stop_loss: float
    take_profit: float
    risk_per_unit: float


def analyze(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float] | None = None,
    btc_trend_up: bool = True,
) -> dict:
    if len(closes) < 30:
        raise ValueError("At least 30 candles are required")
        
    close = closes[-1]
    ema_9 = ema(closes, 9)
    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50) if len(closes) >= 50 else ema(closes, len(closes))
    rsi_val = rsi(closes, 14)
    atr_val = atr(highs, lows, closes, 14)
    rv_val = relative_volume(volumes, 20)
    
    slope_20 = ema(closes[-10:], 5) - ema(closes[-20:-10], 5) if len(closes) >= 20 else 0.0

    # Market Regime Detection
    # Regimes: TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY, LOW_LIQUIDITY
    volatility_ratio = (atr_val / close) if close > 0 else 0.0
    if volatility_ratio > 0.05:
        regime = "HIGH_VOLATILITY"
    elif volumes and rv_val < 0.3:
        regime = "LOW_LIQUIDITY"
    elif ema_9 > ema_20 >= ema_50 and slope_20 > 0:
        regime = "TRENDING_UP"
    elif ema_9 < ema_20 <= ema_50 and slope_20 < 0:
        regime = "TRENDING_DOWN"
    else:
        regime = "RANGING"

    # Score-based framework (0 to 100 target scale)
    # 1. Trend quality: +30
    trend_score = 0
    if ema_9 > ema_20 > ema_50:
        trend_score = 30
    elif ema_9 > ema_20:
        trend_score = 20
    elif ema_9 < ema_20 < ema_50:
        trend_score = -30
    elif ema_9 < ema_20:
        trend_score = -20

    # 2. EMA pullback / Breakout: +20
    strategy_score = 0
    recent_high_20 = max(highs[-21:-1]) if len(highs) >= 21 else highs[-1]
    if close >= recent_high_20 and close > ema_9:
        # Breakout
        strategy_score = 20
        strategy_name = "Breakout"
    elif close >= ema_20 and lows[-1] <= ema_20 * 1.008 and slope_20 > 0:
        # EMA Pullback
        strategy_score = 20
        strategy_name = "EMA Pullback"
    elif close > ema_20:
        strategy_score = 10
        strategy_name = "EMA Pullback"
    else:
        strategy_score = -10
        strategy_name = "Breakout" if close > ema_50 else "None"

    # 3. RSI / Momentum: +15
    rsi_score = 0
    if 45 <= rsi_val <= 65:
        rsi_score = 15
    elif 40 <= rsi_val < 45 or 65 < rsi_val <= 70:
        rsi_score = 10
    elif rsi_val > 75:
        rsi_score = -15
    elif rsi_val < 35:
        rsi_score = 5

    # 4. Volume confirmation: +15
    vol_score = 0
    if rv_val >= 1.5:
        vol_score = 15
    elif rv_val >= 1.1:
        vol_score = 10
    elif rv_val >= 0.8:
        vol_score = 5
    else:
        vol_score = -5

    # 5. Market / BTC bias: +10
    btc_score = 10 if btc_trend_up else 0

    # 6. Risk / Reward: +10
    risk = max(atr_val * 1.5, close * 0.015)
    stop_loss = max(0.0, close - risk)
    tp1 = close + risk * 1.5
    tp2 = close + risk * 2.5
    rr_score = 10 if risk > 0 and (tp1 - close) / risk >= 1.5 else 0

    raw_score = trend_score + strategy_score + rsi_score + vol_score + btc_score + rr_score
    # Clamp to -100 .. 100
    score = max(-100, min(100, raw_score))

    signal = "BUY" if score >= 45 else ("SELL" if score <= -45 else "HOLD")

    result = asdict(Analysis(
        signal=signal,
        score=score,
        regime=regime,
        close=close,
        ema_fast=ema_9,
        ema_slow=ema_20,
        rsi=round(rsi_val, 2),
        atr=round(atr_val, 4),
        stop_loss=round(stop_loss, 4),
        take_profit=round(tp1, 4),
        risk_per_unit=round(risk, 4),
    ))
    result.update({
        "strategy": strategy_name,
        "ema_9": round(ema_9, 4),
        "ema_20": round(ema_20, 4),
        "ema_50": round(ema_50, 4),
        "take_profit_1": round(tp1, 4),
        "take_profit_2": round(tp2, 4),
        "relative_volume": round(rv_val, 2),
    })
    return result


def position_size(available_cash: float, price: float, stop_loss: float, risk_percent: float = 1.0) -> float:
    risk_per_unit = max(price - stop_loss, 0)
    if available_cash <= 0 or price <= 0 or risk_per_unit <= 0:
        return 0.0
    risk_budget = available_cash * max(0, min(risk_percent, 5)) / 100
    return min(risk_budget / risk_per_unit, available_cash / price)
