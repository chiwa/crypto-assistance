import asyncio

from app.db import Database
from app.realtime import RealtimeMonitor


def test_websocket_buy_now_alert_is_deterministic(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    result = {"signal": "BUY", "score": 70, "regime": "UPTREND", "close": 100.0, "stop_loss": 90.0, "take_profit": 120.0, "relative_volume": 1.3}
    db.save_signal("BTC/THB", "15m", result)
    db.save_signal("BTC/THB", "1h", result)
    monitor = RealtimeMonitor(db)
    asyncio.run(monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 101}))
    notes = db.recent("notifications")
    assert notes[0]["title"] == "BUY_NOW · BTC/THB"
    assert notes[0]["level"] == "CRITICAL"


def test_one_timeframe_does_not_trigger_buy_now(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    result = {"signal": "BUY", "score": 70, "regime": "UPTREND", "close": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
    db.save_signal("BTC/THB", "15m", result)
    monitor = RealtimeMonitor(db)
    asyncio.run(monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 101}))
    assert db.recent("notifications") == []


def test_websocket_stop_loss_uses_open_position(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("PAPER", "DEPOSIT", 1_000)
    db.trade("PAPER", "BUY", "ETH/THB", 1, 100)
    result = {"signal": "HOLD", "score": 0, "regime": "RANGE", "close": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
    db.save_signal("ETH/THB", "1h", result)
    monitor = RealtimeMonitor(db)
    asyncio.run(monitor.handle_event({"stream": "market.trade.thb_eth", "rat": "89"}))
    assert db.recent("notifications")[0]["title"] == "STOP_LOSS · ETH/THB"


def test_real_signal_never_changes_position_quantity(tmp_path):
    db=Database(str(tmp_path/"test.db")); db.initialize(); db.add_cash("REAL","DEPOSIT",1000)
    db.trade("REAL","BUY","ETH/THB",2,100,1,executed_at="2026-01-01T00:00:00+00:00")
    result={"signal":"SELL","score":-80,"regime":"DOWNTREND","close":100.0,"atr":1.0,"stop_loss":98.0,"take_profit":104.0}
    for tf in ("15m","1h","4h"): db.save_signal("ETH/THB",tf,result)
    asyncio.run(RealtimeMonitor(db).handle_event({"stream":"market.ticker.thb_eth","last":90}))
    assert db.portfolio("REAL")["positions"][0]["quantity"]==2
    assert db.portfolio("REAL")["position_plan"]["current_action"] in {"STOP_LOSS","SELL_NOW"}
