import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.testclient import TestClient

from app.db import Database
from app.engines import PositionExitEngine, EntryEngine
from app.realtime import RealtimeMonitor
from app.scanner import Scanner


def test_portfolio_guard_suppresses_buy_now_when_position_open(tmp_path):
    """
    Regression Test:
    Open DOGE position + BTC realtime trigger that would otherwise be BUY_NOW:
    => BTC must NOT create BUY_NOW notification
    => DeepSeek must NOT be called
    => Telegram must NOT be called
    => Other coins continue to be scored informationally
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # 1. Setup open MANUAL_REAL DOGE position
    db.add_cash("MANUAL_REAL", "DEPOSIT", 10_000.0)
    db.trade("MANUAL_REAL", "BUY", "DOGE/THB", 1676.44, 2.98, fee=0.0)

    # 2. Setup BTC signals that satisfy BUY_NOW criteria (aligned in 15m, 1h, 4h)
    btc_buy_sig = {
        "signal": "BUY",
        "score": 85,
        "regime": "UPTREND",
        "close": 2_350_000.0,
        "stop_loss": 2_300_000.0,
        "take_profit_1": 2_420_000.0,
        "take_profit_2": 2_480_000.0,
        "strategy": "Breakout",
        "relative_volume": 1.3,
    }
    db.save_signal("BTC/THB", "15m", btc_buy_sig)
    db.save_signal("BTC/THB", "1h", btc_buy_sig)
    db.save_signal("BTC/THB", "4h", btc_buy_sig)

    # 3. Setup Mocks for Telegram and DeepSeek
    mock_telegram = AsyncMock()
    mock_deepseek = MagicMock()
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock()
    mock_deepseek.ask_sell_opinion = AsyncMock()
    mock_deepseek.ask_structured = AsyncMock()

    monitor = RealtimeMonitor(db, telegram_send=mock_telegram, deepseek=mock_deepseek)

    # 4. Trigger BTC ticker event right in the entry zone: 2,350,000 <= 2,355,000 <= 2,350,000 * 1.01 (2,373,500)
    asyncio.run(monitor.handle_event({
        "stream": "market.ticker.thb_btc",
        "last": 2_355_000.0,
    }))

    # Verify notifications: NO BUY_NOW notification
    notes = db.recent("notifications")
    buy_now_notes = [n for n in notes if "BUY_NOW" in n["title"]]
    assert len(buy_now_notes) == 0, f"Expected 0 BUY_NOW notifications, got: {buy_now_notes}"

    # Verify Telegram: NOT called
    mock_telegram.assert_not_called()

    # Verify DeepSeek: NOT called
    mock_deepseek.ask_buy_opinion.assert_not_called()
    mock_deepseek.ask_sell_opinion.assert_not_called()
    mock_deepseek.ask_structured.assert_not_called()

    # Verify other coins are scored and displayed informationally
    candidates, best = db.scanner_snapshot()
    btc_cand = next(c for c in candidates if c["pair"] == "BTC/THB")
    assert btc_cand["score"] == 85
    assert btc_cand["status"] == "WAIT"  # Suppressed to WAIT by portfolio guard
    assert "จำกัดการถือครอง" in btc_cand["reason_th"]
    assert best is None  # No best candidate allowed while position is open


def test_buy_now_allowed_when_no_position_open(tmp_path):
    """
    When no position is open, BUY_NOW trigger works normally.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    btc_buy_sig = {
        "signal": "BUY",
        "score": 85,
        "regime": "UPTREND",
        "close": 2_350_000.0,
        "stop_loss": 2_300_000.0,
        "take_profit_1": 2_420_000.0,
        "take_profit_2": 2_480_000.0,
        "strategy": "Breakout",
        "relative_volume": 1.3,
    }
    db.save_signal("BTC/THB", "15m", btc_buy_sig)
    db.save_signal("BTC/THB", "1h", btc_buy_sig)
    db.save_signal("BTC/THB", "4h", btc_buy_sig)

    mock_telegram = AsyncMock()
    monitor = RealtimeMonitor(db, telegram_send=mock_telegram)

    asyncio.run(monitor.handle_event({
        "stream": "market.ticker.thb_btc",
        "last": 2_355_000.0,
    }))

    notes = db.recent("notifications")
    buy_now_notes = [n for n in notes if "BUY_NOW" in n["title"]]
    assert len(buy_now_notes) == 1
    assert buy_now_notes[0]["level"] == "CRITICAL"
    mock_telegram.assert_called_once()


def test_scanner_state_change_deduplication(tmp_path):
    """
    Test scanner deduplication:
    1. First scan notifies BUY setup.
    2. Identical scan produces NO new notifications.
    3. Score change >= 10 points produces update notification.
    4. Transition to HOLD produces 'Setup no longer valid' notification.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    mock_market = MagicMock()
    # Mock candle data returning consistent closes
    mock_market.candles = AsyncMock(return_value={
        "highs": [100.0] * 60,
        "lows": [95.0] * 60,
        "closes": [98.0 + (i * 0.05) for i in range(60)],
        "volumes": [1000.0] * 60,
    })

    scanner = Scanner(db, mock_market)

    # Custom settings with only 1 timeframe for precise testing
    db.update_settings({"timeframes": ["1h"]})

    # Manually test scanner notification logic
    # Mock analyze function outputs
    with pytest.MonkeyPatch.context() as mp:
        # 1. Initial scan: BUY setup, score 70
        mp.setattr("app.scanner.analyze", lambda h, l, c, v: {
            "signal": "BUY",
            "score": 70,
            "regime": "UPTREND",
            "strategy": "Breakout",
            "close": 100.0,
            "stop_loss": 95.0,
            "take_profit_1": 105.0,
            "take_profit_2": 110.0,
        })
        asyncio.run(scanner.run())
        initial_notes = db.recent("notifications")
        assert len(initial_notes) == 5  # 5 pairs, 1 tf each = 5 notifications

        # 2. Identical scan: score 70, same signal and strategy -> NO new notifications
        asyncio.run(scanner.run())
        second_notes = db.recent("notifications")
        assert len(second_notes) == len(initial_notes)  # No change

        # 3. Small score change (e.g. 70 -> 74, delta = 4 < 10) -> NO new notifications
        mp.setattr("app.scanner.analyze", lambda h, l, c, v: {
            "signal": "BUY",
            "score": 74,
            "regime": "UPTREND",
            "strategy": "Breakout",
            "close": 100.0,
            "stop_loss": 95.0,
            "take_profit_1": 105.0,
            "take_profit_2": 110.0,
        })
        asyncio.run(scanner.run())
        third_notes = db.recent("notifications")
        assert len(third_notes) == len(initial_notes)

        # 4. Material score change (74 -> 85, delta = 11 >= 10) -> Notification emitted!
        mp.setattr("app.scanner.analyze", lambda h, l, c, v: {
            "signal": "BUY",
            "score": 85,
            "regime": "UPTREND",
            "strategy": "Breakout",
            "close": 100.0,
            "stop_loss": 95.0,
            "take_profit_1": 105.0,
            "take_profit_2": 110.0,
        })
        asyncio.run(scanner.run())
        fourth_notes = db.recent("notifications")
        assert len(fourth_notes) == len(initial_notes) + 5
        assert "score updated" in fourth_notes[0]["title"]

        # 5. Transition BUY -> HOLD -> 'Setup no longer valid' notification emitted
        mp.setattr("app.scanner.analyze", lambda h, l, c, v: {
            "signal": "HOLD",
            "score": 40,
            "regime": "SIDEWAYS",
            "strategy": "Breakout",
            "close": 100.0,
            "stop_loss": 95.0,
            "take_profit_1": 105.0,
            "take_profit_2": 110.0,
        })
        asyncio.run(scanner.run())
        fifth_notes = db.recent("notifications")
        assert len(fifth_notes) == len(fourth_notes) + 5
        assert "Setup no longer valid" in fifth_notes[0]["title"]


def test_critical_exit_bypasses_cooldown_on_state_change(tmp_path):
    """
    Test that critical exit signals bypass cooldown when state changes (e.g. HOLD -> STOP_LOSS).
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    db.add_cash("MANUAL_REAL", "DEPOSIT", 200_000.0)
    db.trade("MANUAL_REAL", "BUY", "ETH/THB", 1.0, 100_000.0, fee=0.0)

    # Setup ETH position plan
    with db.connect() as conn:
        conn.execute(
            """UPDATE position_plans
               SET stop_loss=95_000.0, effective_stop=95_000.0, take_profit_1=110_000.0, take_profit_2=115_000.0,
                   highest_price=100_000.0, current_action='HOLD', action_reason='In position'
               WHERE mode='MANUAL_REAL'"""
        )

    sig = {
        "signal": "HOLD",
        "score": 10,
        "regime": "UPTREND",
        "close": 100_000.0,
        "stop_loss": 95_000.0,
        "take_profit": 110_000.0,
    }
    db.save_signal("ETH/THB", "15m", sig)
    db.save_signal("ETH/THB", "1h", sig)
    db.save_signal("ETH/THB", "4h", sig)

    mock_telegram = AsyncMock()
    monitor = RealtimeMonitor(db, telegram_send=mock_telegram)

    # Set artificial previous alert on STOP_LOSS within cooldown window (e.g. 50 seconds ago)
    monitor._last_alert["STOP_LOSS:ETH/THB"] = 10_000_000.0  # arbitrary monotonic time

    # Price drops below stop loss 95,000 -> 94,000
    # Because plan was 'HOLD' and is transitioning to 'STOP_LOSS', force_state_change=True bypasses cooldown!
    asyncio.run(monitor.handle_event({
        "stream": "market.ticker.thb_eth",
        "last": 94_000.0,
    }))

    notes = db.recent("notifications")
    sl_notes = [n for n in notes if "STOP_LOSS" in n["title"]]
    assert len(sl_notes) == 1
    assert sl_notes[0]["level"] == "CRITICAL"
    mock_telegram.assert_called_once()


def test_exit_watch_to_hold_state_change_notifies_info(tmp_path):
    """
    Test transition EXIT_WATCH -> HOLD emits INFO notification.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    db.add_cash("MANUAL_REAL", "DEPOSIT", 100_000.0)
    db.trade("MANUAL_REAL", "BUY", "SOL/THB", 10.0, 5_000.0, fee=0.0)

    # Position plan previously in EXIT_WATCH
    with db.connect() as conn:
        conn.execute(
            """UPDATE position_plans
               SET stop_loss=4_800.0, effective_stop=4_800.0, take_profit_1=5_500.0, take_profit_2=6_000.0,
                   highest_price=5_000.0, current_action='EXIT_WATCH', action_reason='Momentum reversal'
               WHERE mode='MANUAL_REAL'"""
        )

    # Signals healthy (HOLD)
    sig = {
        "signal": "HOLD",
        "score": 30,
        "regime": "RANGE",
        "close": 5_050.0,
        "stop_loss": 4_800.0,
        "take_profit": 5_500.0,
    }
    db.save_signal("SOL/THB", "15m", sig)
    db.save_signal("SOL/THB", "1h", sig)
    db.save_signal("SOL/THB", "4h", sig)

    monitor = RealtimeMonitor(db)
    asyncio.run(monitor.handle_event({
        "stream": "market.ticker.thb_sol",
        "last": 5_050.0,
    }))

    notes = db.recent("notifications")
    hold_notes = [n for n in notes if n["title"] == "HOLD · SOL/THB"]
    assert len(hold_notes) == 1
    assert hold_notes[0]["level"] == "INFO"
    assert "สัญญาณกลับสู่ปกติ" in hold_notes[0]["message"]


def test_position_exit_engine_thai_reason():
    """
    Test PositionExitEngine returns Thai reason preserving English tokens for substring tests.
    """
    engine = PositionExitEngine()
    plan = {
        "mode": "MANUAL_REAL",
        "entry_price": 100.0,
        "highest_price": 100.0,
        "stop_loss": 95.0,
        "effective_stop": 95.0,
        "take_profit_1": 110.0,
        "take_profit_2": 120.0,
        "trailing_enabled": 0,
    }
    signals = [
        {"timeframe": "15m", "signal": "HOLD", "regime": "RANGE", "details": {}},
        {"timeframe": "1h", "signal": "HOLD", "regime": "RANGE", "details": {}},
    ]

    action, reason, stop = engine.evaluate(102.0, plan, signals)
    assert action == "HOLD"
    assert "setup remains valid" in reason
    assert "สัญญาณยังคงเป็นไปตามแผน" in reason

    # Stop loss
    action_sl, reason_sl, _ = engine.evaluate(94.0, plan, signals)
    assert action_sl == "STOP_LOSS"
    assert "Hard stop loss touched" in reason_sl
    assert "แตะจุดตัดขาดทุนหลัก" in reason_sl


def test_api_ask_position_immutable_snapshot_timestamps(tmp_path):
    """
    Test /api/ai/ask-position returns consistent context_timestamp and analyzed_at in Asia/Bangkok.
    """
    from app.main import app, db as main_db, deepseek as main_deepseek

    # Configure temporary DB in app
    test_db = Database(str(tmp_path / "test.db"))
    test_db.initialize()
    test_db.add_cash("MANUAL_REAL", "DEPOSIT", 10_000.0)
    test_db.trade("MANUAL_REAL", "BUY", "DOGE/THB", 1676.44, 2.98, fee=0.0)

    # Mock DeepSeek structured return
    mock_deepseek = MagicMock()
    mock_deepseek.configured = True
    captured_ctx = {}

    async def mock_ask_structured(ctx):
        nonlocal captured_ctx
        captured_ctx = dict(ctx)
        return {
            "status": "connected",
            "assessment": "ถือต่อตามกรอบแนวโน้ม",
            "confidence": 0.85,
            "bull_case": "แรงซื้อยังประคองเหนือแนวรับ",
            "bear_case": "BTC พักฐานอาจฉุดลง",
            "key_risks": ["หลุดแนวรับ 2.92"],
            "watch_next": ["เฝ้าระวัง EMA 20"],
            "summary": "ยังไม่หลุด Stop ให้ถือตามแผน",
        }

    mock_deepseek.ask_structured = AsyncMock(side_effect=mock_ask_structured)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.main.db", test_db)
        mp.setattr("app.main.deepseek", mock_deepseek)

        client = TestClient(app)
        response = client.post("/api/ai/ask-position", json={"symbol": "DOGE/THB", "mode": "MANUAL_REAL"})

        assert response.status_code == 200
        data = response.json()

        assert "context_timestamp" in data
        assert "analyzed_at" in data
        assert "context" in data

        ctx = data["context"]
        # Exact same snapshot passed to DeepSeek and returned in response
        assert ctx["actual_entry_price"] == 2.98
        assert ctx["quantity"] == 1676.44
        assert ctx["context_timestamp"] == data["context_timestamp"]
        assert captured_ctx == ctx
