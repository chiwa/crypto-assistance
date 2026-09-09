import pytest

from app.strategy import analyze, position_size, rsi


def test_uptrend_analysis_is_deterministic():
    closes = [100 + i for i in range(60)]
    result = analyze([x + 2 for x in closes], [x - 2 for x in closes], closes)
    assert result["regime"] in {"UPTREND", "TRENDING_UP"}
    assert result["signal"] in {"BUY", "HOLD"}
    assert result == analyze([x + 2 for x in closes], [x - 2 for x in closes], closes)


def test_position_size_is_limited_by_cash():
    assert position_size(1_000, 100, 90, 1) == pytest.approx(1)
    assert position_size(1_000, 100, 99.9, 5) == pytest.approx(10)


def test_flat_rsi_is_neutral():
    assert rsi([100] * 20) == 50
