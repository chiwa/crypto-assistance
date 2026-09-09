from dataclasses import asdict, dataclass
from statistics import fmean


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    changes = [b - a for a, b in zip(values[-period - 1 : -1], values[-period:])]
    gains = fmean(max(v, 0) for v in changes)
    losses = fmean(max(-v, 0) for v in changes)
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if not closes:
        return 0.0
    start = max(1, len(closes) - period)
    ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(start, len(closes))]
    return fmean(ranges) if ranges else highs[-1] - lows[-1]


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


def analyze(highs: list[float], lows: list[float], closes: list[float], volumes: list[float] | None = None) -> dict:
    if len(closes) < 50:
        raise ValueError("At least 50 candles are required")
    close = closes[-1]
    fast, slow, ema_50, strength, volatility = ema(closes, 9), ema(closes, 20), ema(closes, 50), rsi(closes), atr(highs, lows, closes)
    slope = ema(closes[-12:], 6) - ema(closes[-24:-12], 6)
    if fast > slow > ema_50 and slope > 0:
        regime = "UPTREND"
    elif fast < slow < ema_50 and slope < 0:
        regime = "DOWNTREND"
    else:
        regime = "RANGE"
    score = 30 if regime == "UPTREND" else -30 if regime == "DOWNTREND" else 0
    score += 20 if close >= fast and slope > 0 else (10 if close >= slow else -20)
    score += 15 if 45 <= strength <= 68 else (-15 if strength >= 75 else (5 if strength < 35 else 0))
    relative_volume = 1.0
    if volumes and len(volumes) >= 21 and fmean(volumes[-21:-1]) > 0:
        relative_volume = volumes[-1] / fmean(volumes[-21:-1])
        score += 15 if relative_volume >= 1.2 else 0
    score += 10 if volatility / close < 0.04 else -10
    score = max(-100, min(100, score))
    signal = "BUY" if score >= 45 else "SELL" if score <= -45 else "HOLD"
    risk = max(volatility * 2, close * 0.01)
    result=asdict(Analysis(signal, score, regime, close, fast, slow, strength, volatility, max(0, close - risk), close + risk * 2, risk))
    result.update({"ema_9":fast,"ema_20":slow,"ema_50":ema_50,"relative_volume":relative_volume})
    return result


def position_size(available_cash: float, price: float, stop_loss: float, risk_percent: float = 1.0) -> float:
    risk_per_unit = max(price - stop_loss, 0)
    if available_cash <= 0 or price <= 0 or risk_per_unit <= 0:
        return 0.0
    risk_budget = available_cash * max(0, min(risk_percent, 5)) / 100
    return min(risk_budget / risk_per_unit, available_cash / price)
