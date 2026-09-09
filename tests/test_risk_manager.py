import pytest
from app.risk import RiskManager


def test_risk_budget_sizing_formula():
    equity = 10_000.0
    cash = 10_000.0
    entry_price = 100.0
    stop_loss = 95.0
    tp1 = 110.0

    # Risk budget = 10,000 * 2% = 200 THB
    # Unit risk = (100 - 95) + (100 * 0.0025 * 2) = 5.0 + 0.5 = 5.5 THB
    # Target quantity = 200 / 5.5 = 36.363636 units
    # Target value = 36.363636 * 100 = 3636.36 THB (well under 8,000 THB max allocation)
    res = RiskManager.calculate_sizing(
        current_equity=equity,
        available_cash=cash,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        max_risk_percent=2.0,
        max_allocation_thb=8_000.0,
    )
    assert res.allowed is True
    assert res.risk_budget_thb == 200.0
    assert res.allocation_thb == pytest.approx(3636.36, rel=1e-2)
    assert res.quantity == pytest.approx(36.3636, rel=1e-3)
    assert res.risk_reward == pytest.approx(2.0, rel=1e-2)


def test_sizing_capped_by_max_allocation():
    equity = 50_000.0
    cash = 50_000.0
    entry_price = 100.0
    stop_loss = 99.0   # Very tight stop -> huge risk budget (1,000 THB)
    tp1 = 105.0

    res = RiskManager.calculate_sizing(
        current_equity=equity,
        available_cash=cash,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        max_risk_percent=2.0,
        max_allocation_thb=8_000.0,
    )
    assert res.allowed is True
    # Must be capped by max allocation 8,000 THB minus fee buffer
    assert res.allocation_thb <= 8_000.0


def test_reject_when_risk_reward_too_low():
    res = RiskManager.calculate_sizing(
        current_equity=10_000.0,
        available_cash=10_000.0,
        entry_price=100.0,
        stop_loss=90.0,   # Risk = 10
        take_profit_1=105.0, # Reward = 5 -> R:R = 0.5 < 1.5
        min_risk_reward=1.5,
    )
    assert res.allowed is False
    assert "Risk/Reward" in res.reason


def test_circuit_breaker_halts_after_two_consecutive_losses():
    res = RiskManager.calculate_sizing(
        current_equity=10_000.0,
        available_cash=10_000.0,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit_1=110.0,
        consecutive_losses_today=2,
    )
    assert res.allowed is False
    assert "2 consecutive losses" in res.reason


def test_reject_when_open_position_exists():
    res = RiskManager.calculate_sizing(
        current_equity=10_000.0,
        available_cash=5_000.0,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit_1=110.0,
        has_open_position=True,
    )
    assert res.allowed is False
    assert "already open" in res.reason
