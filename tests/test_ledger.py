import pytest

from app.db import Database


def test_deposit_trade_sell_withdrawal_and_pnl(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("PAPER", "DEPOSIT", 10_000)
    db.trade("PAPER", "BUY", "ETH/THB", 1, 8_000, 20)
    assert db.cash("PAPER") == pytest.approx(1_980)
    db.trade("PAPER", "SELL", "ETH/THB", 1, 8_500, 20)
    db.add_cash("PAPER", "WITHDRAWAL", 2_000)
    p = db.portfolio("PAPER")
    assert p["cash"] == pytest.approx(8_460)
    assert p["trading_pnl"] == pytest.approx(460)


def test_withdrawal_cannot_exceed_available_cash(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("REAL", "DEPOSIT", 100)
    with pytest.raises(ValueError, match="available cash"):
        db.add_cash("REAL", "WITHDRAWAL", 101)


def test_modes_are_isolated(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("PAPER", "DEPOSIT", 500)
    assert db.cash("REAL") == 0


def test_one_position_at_a_time_and_deposit_during_position(tmp_path):
    db=Database(str(tmp_path/"test.db")); db.initialize(); db.add_cash("PAPER","DEPOSIT",1000)
    db.trade("PAPER","BUY","ETH/THB",1,100)
    db.add_cash("PAPER","DEPOSIT",50)
    with pytest.raises(ValueError,match="One-position"):
        db.trade("PAPER","BUY","BTC/THB",1,100)
    assert db.cash("PAPER")==950
