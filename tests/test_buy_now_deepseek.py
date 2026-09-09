import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.db import Database
from app.integrations import DeepSeekSecondOpinion
from app.realtime import RealtimeMonitor


def _setup_test_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Cash only, NO open positions
    db.add_cash("MANUAL_REAL", "DEPOSIT", 100_000.0)

    # Setup BTC signals satisfying 3/3 timeframes with score 85
    sig_15m = {
        "signal": "BUY",
        "score": 85,
        "regime": "UPTREND",
        "close": 2_350_000.0,
        "ema_9": 2_340_000.0,
        "ema_20": 2_320_000.0,
        "ema_50": 2_300_000.0,
        "rsi": 62.5,
        "atr": 15_000.0,
        "relative_volume": 1.45,
        "stop_loss": 2_300_000.0,
        "take_profit_1": 2_420_000.0,
        "take_profit_2": 2_480_000.0,
        "strategy": "Breakout",
    }
    sig_1h = {
        "signal": "BUY",
        "score": 85,
        "regime": "UPTREND",
        "close": 2_350_000.0,
        "ema_9": 2_335_000.0,
        "ema_20": 2_310_000.0,
        "ema_50": 2_280_000.0,
        "rsi": 64.0,
        "atr": 20_000.0,
        "relative_volume": 1.60,
        "stop_loss": 2_300_000.0,
        "take_profit_1": 2_420_000.0,
        "take_profit_2": 2_480_000.0,
        "strategy": "Breakout",
    }
    sig_4h = {
        "signal": "BUY",
        "score": 85,
        "regime": "UPTREND",
        "close": 2_350_000.0,
        "ema_9": 2_320_000.0,
        "ema_20": 2_290_000.0,
        "ema_50": 2_250_000.0,
        "rsi": 66.0,
        "atr": 25_000.0,
        "relative_volume": 1.80,
        "stop_loss": 2_300_000.0,
        "take_profit_1": 2_420_000.0,
        "take_profit_2": 2_480_000.0,
        "strategy": "Breakout",
    }
    db.save_signal("BTC/THB", "15m", sig_15m)
    db.save_signal("BTC/THB", "1h", sig_1h)
    db.save_signal("BTC/THB", "4h", sig_4h)

    return db


@pytest.mark.anyio
async def test_buy_now_high_conviction_calls_deepseek_and_sends_telegram(tmp_path):
    """
    BUY_NOW AND score >= 80 AND confirmed by 3/3 configured timeframes AND no open position:
    => automatically calls DeepSeek once
    => attaches Thai AI second-opinion analysis
    => sends combined deterministic BUY signal + DeepSeek analysis to Telegram
    """
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock(return_value={
        "status": "connected",
        "assessment": "สัญญาณซื้อมีความแข็งแกร่ง โครงสร้างราคายกไฮยกโลว์ต่อเนื่อง",
        "confidence": 0.88,
        "bull_case": "แรงซื้อหนาแน่นสอดคล้องทั้ง 3 กรอบเวลา และยืนเหนือแนวรับสำคัญ",
        "bear_case": "ระวังความผันผวนบริเวณแนวต้านจิตวิทยา 2,400,000 บาท",
        "key_risks": ["แรงขายทำกำไรระยะสั้น", "ความผันผวนของตลาดโลก"],
        "watch_next": ["เฝ้าระวัง Stop Loss ที่ 2,300,000 บาท", "ติดตามปริมาณการซื้อขายต่อเนื่อง"],
        "summary": "เป็นจังหวะเข้าสะสมตามระบบทางเทคนิค โดยตั้งจุดตัดขาดทุนเคร่งครัด",
    })

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)

    # Reference price = 2,350,000. Entry zone = [2,338,250 - 2,373,500]
    # Ticker price = 2,355,000 is inside entry zone
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    # Assert DeepSeek was called once
    assert mock_deepseek.ask_buy_opinion.call_count == 1
    ctx = mock_deepseek.ask_buy_opinion.call_args[0][0]

    # Verify context passed to DeepSeek
    assert ctx["symbol"] == "BTC/THB"
    assert ctx["current_bitkub_price"] == 2_355_000.0
    assert ctx["score"] == 85
    assert ctx["confirmed_timeframes"] == "3/3"
    assert ctx["stop_loss"] == 2_300_000.0
    assert ctx["take_profit_1"] == 2_420_000.0
    assert ctx["take_profit_2"] == 2_480_000.0
    assert ctx["trend_15m"] == "UPTREND"
    assert ctx["trend_1h"] == "UPTREND"
    assert ctx["trend_4h"] == "UPTREND"
    assert "ema_structure" in ctx

    # Verify Telegram message received combined content
    assert len(telegram_messages) == 1
    msg = telegram_messages[0]

    # 1. Deterministic BUY signal elements
    assert "BUY_NOW" in msg
    assert "2,355,000.0000" in msg
    assert "2,300,000.0000" in msg  # Stop Loss
    assert "2,420,000.0000" in msg  # TP1
    assert "2,480,000.0000" in msg  # TP2
    assert "คะแนน: 85" in msg
    assert "3/3 Timeframes" in msg

    # 2. DeepSeek AI Second-opinion elements
    assert "🤖 ความเห็นที่ 2 จาก DeepSeek" in msg
    assert "สัญญาณซื้อมีความแข็งแกร่ง" in msg
    assert "0.88 (88%)" in msg
    assert "แรงซื้อหนาแน่นสอดคล้องทั้ง 3 กรอบเวลา" in msg
    assert "ระวังความผันผวนบริเวณแนวต้านจิตวิทยา" in msg
    assert "เป็นจังหวะเข้าสะสมตามระบบทางเทคนิค" in msg

    # Also check Database Notification
    notes = db.recent("notifications")
    assert any("BUY_NOW · BTC/THB" in n["title"] and "ความเห็นที่ 2 จาก DeepSeek" in n["message"] for n in notes)


@pytest.mark.anyio
async def test_buy_now_lower_score_does_not_call_deepseek(tmp_path):
    """When score < 80, DeepSeek is NOT called automatically, but deterministic alert is sent."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("MANUAL_REAL", "DEPOSIT", 100_000.0)

    # Score 75 (< 80) across all 3 timeframes
    for tf in ("15m", "1h", "4h"):
        db.save_signal("BTC/THB", tf, {
            "signal": "BUY",
            "score": 75,
            "regime": "UPTREND",
            "close": 2_350_000.0,
            "stop_loss": 2_300_000.0,
            "take_profit_1": 2_420_000.0,
            "take_profit_2": 2_480_000.0,
            "relative_volume": 1.3,
        })

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock()

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    # DeepSeek must NOT be called
    mock_deepseek.ask_buy_opinion.assert_not_called()

    # Deterministic alert still dispatched
    assert len(telegram_messages) == 1
    assert "คะแนน: 75" in telegram_messages[0]
    assert "ความเห็นที่ 2 จาก DeepSeek" not in telegram_messages[0]


@pytest.mark.anyio
async def test_buy_now_2_of_3_timeframes_does_not_call_deepseek(tmp_path):
    """When only 2/3 timeframes confirmed (not 3/3), DeepSeek is NOT called."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("MANUAL_REAL", "DEPOSIT", 100_000.0)

    db.save_signal("BTC/THB", "15m", {
        "signal": "BUY", "score": 90, "regime": "UPTREND", "close": 2_350_000.0,
        "stop_loss": 2_300_000.0, "take_profit_1": 2_420_000.0,
        "relative_volume": 1.3,
    })
    db.save_signal("BTC/THB", "1h", {
        "signal": "BUY", "score": 90, "regime": "UPTREND", "close": 2_350_000.0,
        "stop_loss": 2_300_000.0, "take_profit_1": 2_420_000.0,
        "relative_volume": 1.3,
    })
    # 4h is HOLD, not BUY
    db.save_signal("BTC/THB", "4h", {
        "signal": "HOLD", "score": 80, "regime": "UPTREND", "close": 2_350_000.0,
        "stop_loss": 2_300_000.0, "take_profit_1": 2_420_000.0,
    })

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock()

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    # DeepSeek must NOT be called because only 2/3 timeframes BUY
    mock_deepseek.ask_buy_opinion.assert_not_called()
    assert len(telegram_messages) == 1
    assert "สัญญาณซื้อ 2/3 และ Volume 2/3 Timeframes" in telegram_messages[0]
    assert "ความเห็นที่ 2 จาก DeepSeek" not in telegram_messages[0]


@pytest.mark.anyio
async def test_buy_now_suppressed_when_position_open(tmp_path):
    """Strict One-Position Guard: When ANY position is open, BUY_NOW, DeepSeek, and Telegram are all suppressed."""
    db = _setup_test_db(tmp_path)

    # Open a DOGE position
    db.trade("MANUAL_REAL", "BUY", "DOGE/THB", 1676.44, 2.98, fee=0.0)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock()

    mock_telegram = AsyncMock()

    monitor = RealtimeMonitor(db, telegram_send=mock_telegram, deepseek=mock_deepseek)
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    # DeepSeek NOT called
    mock_deepseek.ask_buy_opinion.assert_not_called()

    # Telegram NOT called
    mock_telegram.assert_not_called()

    # No BUY_NOW notification
    notes = db.recent("notifications")
    assert not any("BUY_NOW" in n["title"] for n in notes)


@pytest.mark.anyio
async def test_buy_now_deepseek_timeout_fallback(tmp_path):
    """When DeepSeek times out (> 10s), deterministic alert is still sent with fallback message."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True

    async def timeout_ask(*args, **kwargs):
        raise asyncio.TimeoutError("Call timed out")

    mock_deepseek.ask_buy_opinion = AsyncMock(side_effect=timeout_ask)

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    # Alert still delivered!
    assert len(telegram_messages) == 1
    msg = telegram_messages[0]
    assert "BUY_NOW" in msg
    assert "2,355,000.0000" in msg
    assert "AI analysis unavailable" in msg


@pytest.mark.anyio
async def test_buy_now_deepseek_unconfigured_fallback(tmp_path):
    """When DeepSeek is not configured, deterministic alert is still sent with fallback message."""
    db = _setup_test_db(tmp_path)

    ai = DeepSeekSecondOpinion(api_key=None)
    assert not ai.configured

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=ai)
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})

    assert len(telegram_messages) == 1
    assert "BUY_NOW" in telegram_messages[0]
    assert "AI analysis unavailable" in telegram_messages[0]


@pytest.mark.anyio
async def test_buy_now_deduplication_and_cooldown(tmp_path):
    """
    Deduplication and Cooldown:
    1. First trigger sends combined alert and calls DeepSeek.
    2. Repeated trigger within alert cooldown is suppressed (no DeepSeek call, no Telegram).
    3. Trigger after alert cooldown but within AI cooldown sends alert with cooldown notice without re-calling DeepSeek.
    """
    db = _setup_test_db(tmp_path)
    # Shorten alert cooldown to 10s for precise testing, AI cooldown 60s
    db.update_settings({"realtime_alert_cooldown_seconds": 10})

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_buy_opinion = AsyncMock(return_value={
        "status": "connected",
        "assessment": "สัญญาณซื้อชัดเจน",
        "confidence": 0.85,
        "bull_case": "ขาขึ้นแข็งแกร่ง",
        "bear_case": "อาจพักฐานสั้นๆ",
        "key_risks": ["ความผันผวน"],
        "watch_next": ["Stop Loss"],
        "summary": "ซื้อสะสมตามแผน",
    })

    telegram_messages = []
    async def fake_telegram_send(text: str):
        telegram_messages.append(text)

    monitor = RealtimeMonitor(
        db,
        telegram_send=fake_telegram_send,
        deepseek=mock_deepseek,
        ai_buy_cooldown_seconds=60,
    )

    # 1. First trigger
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})
    assert mock_deepseek.ask_buy_opinion.call_count == 1
    assert len(telegram_messages) == 1
    assert "สัญญาณซื้อชัดเจน" in telegram_messages[0]

    # 2. Second trigger immediately (e.g. 1s later) -> suppressed by alert cooldown
    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})
    assert mock_deepseek.ask_buy_opinion.call_count == 1
    assert len(telegram_messages) == 1  # No duplicate Telegram

    # 3. Simulate alert cooldown elapsed (12 seconds later), but still within AI cooldown (60s)
    monitor._last_alert["BUY_NOW:BTC/THB"] = time.monotonic() - 15.0

    await monitor.handle_event({"stream": "market.ticker.thb_btc", "last": 2_355_000.0})
    # DeepSeek API was NOT called again!
    assert mock_deepseek.ask_buy_opinion.call_count == 1
    # But updated alert was dispatched with Cooldown notice
    assert len(telegram_messages) == 2
    assert "ข้ามการเรียก AI ซ้ำ — อยู่ในช่วง Cooldown" in telegram_messages[1]


@pytest.mark.anyio
async def test_ask_buy_opinion_direct_mock(monkeypatch):
    """Direct test of DeepSeekSecondOpinion.ask_buy_opinion parsing and error handling."""
    ai = DeepSeekSecondOpinion(api_key="sk-test-buy-key")
    assert ai.configured is True

    # 1. Successful response
    mock_data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "assessment": "สัญญาณซื้อขาขึ้นแข็งแกร่ง",
                        "confidence": "85%",
                        "bull_case": "EMA เรียงตัวสมบูรณ์",
                        "bear_case": "RSI เริ่มเข้าใกล้ Overbought",
                        "key_risks": ["แรงเทขายทำกำไร"],
                        "watch_next": ["แนวต้านสำคัญ"],
                        "summary": "น่าสนใจสำหรับการเปิดสถานะ",
                    }, ensure_ascii=False)
                }
            }
        ]
    }

    class MockResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return mock_data

    async def mock_post(*args, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test-buy-key"
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = await ai.ask_buy_opinion({"symbol": "BTC/THB", "score": 85})
    assert res["status"] == "connected"
    assert res["confidence"] == 0.85
    assert "สัญญาณซื้อขาขึ้นแข็งแกร่ง" in res["assessment"]
    assert "EMA เรียงตัวสมบูรณ์" in res["bull_case"]
    assert len(res["key_risks"]) == 1

    # 2. Malformed JSON fallback
    class MockMalformed:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(httpx.AsyncClient, "post", lambda *a, **k: MockMalformed())
    res_err = await ai.ask_buy_opinion({"symbol": "BTC/THB"})
    assert res_err["status"] == "error"
    assert "AI analysis unavailable" in res_err["assessment"]
