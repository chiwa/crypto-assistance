import pytest
from app.db import Database


def test_doge_manual_real_position_initialization(tmp_path):
    db = Database(str(tmp_path / "crypto_assistance.db"))
    db.initialize(seed_doge_position=True)

    portfolio = db.portfolio("MANUAL_REAL")
    assert portfolio["mode"] == "MANUAL_REAL"
    assert portfolio["deposits"] == 10_000.0
    assert portfolio["cash"] == pytest.approx(5004.2088, rel=1e-4)
    assert portfolio["net_capital_inflow"] == 10_000.0
    assert len(portfolio["positions"]) == 1

    pos = portfolio["positions"][0]
    assert pos["asset"] == "DOGE"
    assert pos["quantity"] == pytest.approx(1676.44, rel=1e-5)
    assert pos["average_cost"] == pytest.approx(2.98, rel=1e-4)
    assert pos["realized_pnl"] == 0.0

    # Gross purchase value check: 1676.44 * 2.98 = 4995.7912
    gross_val = pos["quantity"] * pos["average_cost"]
    assert gross_val == pytest.approx(4995.7912, rel=1e-4)

    # Position plan verification
    plan = portfolio["position_plan"]
    assert plan is not None
    assert plan["asset"] == "DOGE"
    assert plan["entry_price"] == pytest.approx(2.98, rel=1e-4)
    assert plan["current_action"] == "HOLD"
    assert "2026-09-09" in plan["entry_time"]


def test_doge_unrealized_pnl_updates_from_market_price(tmp_path):
    db = Database(str(tmp_path / "crypto_assistance.db"))
    db.initialize(seed_doge_position=True)

    # Simulate market price moving to 3.10 THB
    db.update_price("DOGE/THB", 3.10)
    p = db.portfolio("MANUAL_REAL")
    pos = p["positions"][0]

    assert pos["market_price"] == 3.10
    expected_mval = 1676.44 * 3.10
    assert pos["market_value"] == pytest.approx(expected_mval, rel=1e-4)

    # Gross Unrealized P/L = quantity * (3.10 - 2.98), estimated exit fee (0.25%) is separated
    gross_gain = 1676.44 * (3.10 - 2.98)
    exit_fee = expected_mval * 0.0025
    assert pos["unrealized_pnl"] == pytest.approx(gross_gain, rel=1e-3)
    assert pos["gross_unrealized_pnl"] == pytest.approx(gross_gain, rel=1e-3)
    assert pos["estimated_exit_fee"] == pytest.approx(exit_fee, rel=1e-3)
    assert pos["net_unrealized_pnl"] == pytest.approx(gross_gain - exit_fee, rel=1e-3)


def test_doge_real_position_not_closed_by_signals(tmp_path):
    db = Database(str(tmp_path / "crypto_assistance.db"))
    db.initialize(seed_doge_position=True)

    # Signals simulate SELL_NOW or STOP_LOSS
    db.update_position_plan("MANUAL_REAL", "SELL_NOW", "TAKE_PROFIT_2 reached", 3.30, 2.98)
    p = db.portfolio("MANUAL_REAL")

    # Position MUST remain open with 1676.44 DOGE
    assert len(p["positions"]) == 1
    assert p["positions"][0]["quantity"] == pytest.approx(1676.44, rel=1e-5)
    assert p["position_plan"]["current_action"] == "SELL_NOW"


def test_user_recorded_sale_closes_doge_position(tmp_path):
    db = Database(str(tmp_path / "crypto_assistance.db"))
    db.initialize(seed_doge_position=True)

    # User manually sells all DOGE at 3.20 THB on Bitkub with 13.41 THB fee
    db.trade(
        mode="MANUAL_REAL",
        side="SELL",
        pair="DOGE/THB",
        quantity=1676.44,
        price=3.20,
        fee=13.41,
        note="Manual real execution on Bitkub",
        executed_at="2026-09-09T12:00:00+00:00",
        reason="TAKE_PROFIT_MANUAL",
    )

    p = db.portfolio("MANUAL_REAL")
    # Position must now be closed
    assert len(p["positions"]) == 0
    assert p["position_plan"] is None

    # Realized P/L = (3.20 - 2.98) * 1676.44 - 13.41 = 368.8168 - 13.41 = 355.4068 THB
    expected_realized = (3.20 - 2.98) * 1676.44 - 13.41
    assert p["realized_pnl"] == pytest.approx(expected_realized, rel=1e-3)
    # Cash = 5004.2088 + (1676.44 * 3.20 - 13.41) = 5004.2088 + 5351.198 = 10355.4068
    assert p["cash"] == pytest.approx(10355.4068, rel=1e-3)
