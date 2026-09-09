import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.db import Database
from app.integrations import DeepSeekSecondOpinion
from app.realtime import RealtimeMonitor


def _setup_test_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Signals
    sig_details = {
        "signal": "HOLD",
        "score": 10,
        "regime": "TRENDING_UP",
        "close": 2.984,
        "ema_9": 2.975,
        "ema_20": 2.960,
        "ema_50": 2.940,
        "rsi": 58.5,
        "atr": 0.045,
        "relative_volume": 1.25,
        "stop_loss": 2.92,
        "take_profit_1": 3.05,
        "take_profit_2": 3.12,
        "strategy": "Breakout",
    }
    db.save_signal("DOGE/THB", "15m", sig_details)
    db.save_signal("DOGE/THB", "1h", sig_details)
    db.save_signal("DOGE/THB", "4h", sig_details)

    # BTC signals for btc_market_bias
    btc_details = {
        "signal": "BUY",
        "score": 65,
        "regime": "TRENDING_UP",
        "close": 2_350_000.0,
        "stop_loss": 2_300_000.0,
        "take_profit": 2_450_000.0,
    }
    db.save_signal("BTC/THB", "4h", btc_details)

    # Cash and MANUAL_REAL DOGE position
    db.add_cash("MANUAL_REAL", "DEPOSIT", 10_000.0)
    db.trade("MANUAL_REAL", "BUY", "DOGE/THB", 1676.44, 2.98, fee=0.0)

    # Update position plan to deterministic levels
    with db.connect() as conn:
        conn.execute(
            """UPDATE position_plans
               SET stop_loss=2.92, effective_stop=2.92, take_profit_1=3.05, take_profit_2=3.12,
                   highest_price=2.984, current_action='HOLD', action_reason='In position',
                   strategy='Manual Real Execution', entry_score=0
               WHERE mode='MANUAL_REAL'"""
        )

    return db


@pytest.mark.anyio
async def test_sell_now_deepseek_called(tmp_path):
    """SELL_NOW -> DeepSeek called with all deterministic context parameters."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock(return_value={
        "status": "connected",
        "assessment": "ราคาบรรลุเป้าหมายกำไร TP2 แล้ว แนะนำขายปิดสถานะ",
        "confidence": 0.90,
        "why_exit_now": "แตะระดับ Take Profit 2 ของกลยุทธ์",
        "risk_of_holding": "มีความเสี่ยงสูงที่จะเผชิญแรงขายทำกำไรระยะสั้น",
        "counter_case": "หากมีแรงซื้อหนุนอย่างหนาแน่นอาจไปต่อได้",
        "key_risks": ["แรงขายทำกำไรกดดันราคา", "แนวต้านทางจิตวิทยา"],
        "watch_next": ["จับตาการพักตัวของราคา", "สัญญาณแท่งเทียนกลับตัว"],
        "summary": "แนะนำให้พิจารณาขายทำกำไรบน Bitkub ตามสัญญาณ Deterministic",
    })

    telegram_sent_messages = []
    async def fake_telegram_send(text: str):
        telegram_sent_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)

    # Trigger event with price=3.15 (exceeds TP2=3.12, triggering SELL_NOW)
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 3.15})

    # Assert DeepSeek was called
    assert mock_deepseek.ask_sell_opinion.call_count == 1
    ctx = mock_deepseek.ask_sell_opinion.call_args[0][0]

    # Verify context parameters sent to DeepSeek
    assert ctx["symbol"] == "DOGE/THB"
    assert ctx["mode"] == "MANUAL_REAL"
    assert ctx["actual_entry_price"] == 2.98
    assert ctx["current_bitkub_price"] == 3.15
    assert ctx["quantity"] == 1676.44
    assert ctx["gross_unrealized_pnl"] > 0
    assert ctx["gross_unrealized_pnl_percent"] > 0
    assert "TAKE_PROFIT_2" in ctx["current_exit_reason"]
    assert ctx["stop_loss"] == 2.92
    assert ctx["effective_stop"] >= 2.92
    assert ctx["take_profit_1"] == 3.05
    assert ctx["take_profit_2"] == 3.12
    assert "ema_structure" in ctx
    assert "ema_9" in ctx["ema_structure"]
    assert "ema_20" in ctx["ema_structure"]
    assert "ema_50" in ctx["ema_structure"]
    assert ctx["rsi"] is not None
    assert ctx["atr"] is not None
    assert ctx["relative_volume"] is not None
    assert ctx["market_regime"] is not None
    assert ctx["btc_market_bias"] == "TRENDING_UP"
    assert ctx["original_entry_strategy"] == "Manual Real Execution"
    assert ctx["original_entry_score"] == 0

    # Verify Telegram message contains all 5 required items
    assert len(telegram_sent_messages) == 1
    msg = telegram_sent_messages[0]
    assert "SELL_NOW" in msg  # 1. deterministic SELL_NOW signal
    assert "2.9800" in msg and "1,676.4400" in msg  # 2. actual position info
    assert "กำไร/ขาดทุนโดยประมาณ" in msg  # 3. estimated current P/L
    assert "TAKE_PROFIT_2" in msg  # 4. deterministic exit reason
    assert "🤖 ความเห็นที่ 2 จาก DeepSeek" in msg  # 5. DeepSeek second opinion
    assert "ราคาบรรลุเป้าหมายกำไร TP2 แล้ว" in msg
    assert "0.90 (90%)" in msg


@pytest.mark.anyio
async def test_hold_deepseek_not_called(tmp_path):
    """HOLD -> not called."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock()

    telegram_sent_messages = []
    async def fake_telegram_send(text: str):
        telegram_sent_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)

    # Price = 2.984 is within normal range (no stop, no TP reached, HOLD)
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 2.984})

    # DeepSeek must NOT be called for HOLD
    assert mock_deepseek.ask_sell_opinion.call_count == 0
    assert len(telegram_sent_messages) == 0


@pytest.mark.anyio
async def test_exit_watch_not_called_by_default(tmp_path):
    """EXIT_WATCH -> not called by default."""
    db = _setup_test_db(tmp_path)

    # Save signals with negative score to trigger MOMENTUM_REVERSAL -> EXIT_WATCH
    rev_signal = {
        "signal": "HOLD",
        "score": -25,
        "regime": "RANGING",
        "close": 2.984,
        "stop_loss": 2.92,
        "take_profit": 3.05,
        "details": {"rsi": 42.0, "atr": 0.05, "relative_volume": 0.9},
    }
    db.save_signal("DOGE/THB", "15m", rev_signal)
    db.save_signal("DOGE/THB", "1h", rev_signal)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock()

    monitor = RealtimeMonitor(db, deepseek=mock_deepseek)
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 2.984})

    # Exit engine produces EXIT_WATCH; DeepSeek must NOT be called
    assert mock_deepseek.ask_sell_opinion.call_count == 0


@pytest.mark.anyio
async def test_stop_loss_telegram_sent_immediately(tmp_path):
    """STOP_LOSS -> deterministic Telegram sent immediately without waiting for AI."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock()

    telegram_sent_messages = []
    async def fake_telegram_send(text: str):
        telegram_sent_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)

    # Price = 2.85 is below stop_loss (2.92)
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 2.85})

    # Telegram alert sent immediately with deterministic info
    assert len(telegram_sent_messages) >= 1
    stop_msg = next((m for m in telegram_sent_messages if "STOP_LOSS" in m), None)
    assert stop_msg is not None
    assert "2.9800" in stop_msg  # actual entry cost
    assert "1,676.4400" in stop_msg  # actual quantity
    assert "2.8500" in stop_msg  # current price

    # DeepSeek must NOT delay or be called for urgent STOP_LOSS
    assert mock_deepseek.ask_sell_opinion.call_count == 0


@pytest.mark.anyio
async def test_deepseek_timeout_urgent_alert_still_sent(tmp_path):
    """DeepSeek timeout -> urgent alert still sent with 'AI analysis unavailable'."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    # Simulate timeout
    mock_deepseek.ask_sell_opinion = AsyncMock(side_effect=asyncio.TimeoutError())

    telegram_sent_messages = []
    async def fake_telegram_send(text: str):
        telegram_sent_messages.append(text)

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send, deepseek=mock_deepseek)

    # Price = 3.15 triggers SELL_NOW
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 3.15})

    # Alert must STILL be sent immediately!
    assert len(telegram_sent_messages) == 1
    msg = telegram_sent_messages[0]
    assert "SELL_NOW" in msg
    assert "TAKE_PROFIT_2" in msg
    assert "2.9800" in msg
    # Must contain "AI analysis unavailable"
    assert "AI analysis unavailable" in msg


@pytest.mark.anyio
async def test_duplicate_sell_now_cooldown(tmp_path):
    """duplicate SELL_NOW -> no repeated AI calls during cooldown."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock(return_value={
        "status": "connected",
        "assessment": "สัญญาณขายรอบแรก",
        "confidence": 0.85,
        "why_exit_now": "TP2 reached",
        "risk_of_holding": "ย่อตัว",
        "counter_case": "None",
        "key_risks": ["ความผันผวน"],
        "watch_next": ["Stop Loss"],
        "summary": "ขายตามสัญญาณ",
    })

    monitor = RealtimeMonitor(db, deepseek=mock_deepseek, ai_sell_cooldown_seconds=900)

    # First SELL_NOW event
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 3.15})
    assert mock_deepseek.ask_sell_opinion.call_count == 1

    # Second SELL_NOW event immediately afterwards (within 15 min cooldown)
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 3.16})
    # Must NOT call DeepSeek again!
    assert mock_deepseek.ask_sell_opinion.call_count == 1


@pytest.mark.anyio
async def test_manual_real_remains_in_position_after_sell_now(tmp_path):
    """MANUAL_REAL remains IN_POSITION until actual sell execution is recorded."""
    db = _setup_test_db(tmp_path)

    mock_deepseek = MagicMock(spec=DeepSeekSecondOpinion)
    mock_deepseek.configured = True
    mock_deepseek.ask_sell_opinion = AsyncMock(return_value={
        "status": "connected",
        "assessment": "ขายด่วน",
        "confidence": 1.0,
        "why_exit_now": "TP2 Reached",
        "risk_of_holding": "High Risk",
        "counter_case": "None",
        "key_risks": ["Drawdown"],
        "watch_next": ["Exit"],
        "summary": "ขายทันที",
    })

    monitor = RealtimeMonitor(db, deepseek=mock_deepseek)

    # Trigger SELL_NOW
    await monitor.handle_event({"stream": "market.ticker.thb_doge", "last": 3.15})

    # Verify that MANUAL_REAL position is NOT modified or closed
    port = db.portfolio("MANUAL_REAL")
    positions = port["positions"]
    assert len(positions) == 1
    assert positions[0]["asset"] == "DOGE"
    assert positions[0]["quantity"] == 1676.44
    assert positions[0]["average_cost"] == 2.98
    # Current action in plan reflects SELL_NOW
    assert port["position_plan"]["current_action"] == "SELL_NOW"

    # Only when user executes and records actual sell is the position closed
    db.trade("MANUAL_REAL", "SELL", "DOGE/THB", 1676.44, 3.15, fee=0.0)
    port_after_sell = db.portfolio("MANUAL_REAL")
    assert port_after_sell["positions"] == []
    assert port_after_sell["position_plan"] is None


@pytest.mark.anyio
async def test_ask_sell_opinion_structured_parsing():
    """Unit test for DeepSeekSecondOpinion.ask_sell_opinion with mocked HTTP client."""
    service = DeepSeekSecondOpinion("test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{\n'
                        '  "assessment": "ภาพรวมเข้าใกล้แนวต้าน แนะนำทำกำไร",\n'
                        '  "confidence": 0.82,\n'
                        '  "why_exit_now": "สัญญาณ EMA และแนวต้านเทคนิค",\n'
                        '  "risk_of_holding": "ความเสี่ยงจากการย่อตัว",\n'
                        '  "counter_case": "หากทะลุ 3.20 ได้อาจไปต่อ",\n'
                        '  "key_risks": ["แรงเทขายทำกำไร", "ตลาดชะลอตัว"],\n'
                        '  "watch_next": ["แนวรับ 2.95", "ปริมาณซื้อขาย"],\n'
                        '  "summary": "ควรแบ่งขายทำกำไรตามระบบ"\n'
                        '}'
                    )
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await service.ask_sell_opinion({"symbol": "DOGE/THB", "price": 3.15})

        assert result["status"] == "connected"
        assert result["assessment"] == "ภาพรวมเข้าใกล้แนวต้าน แนะนำทำกำไร"
        assert result["confidence"] == 0.82
        assert result["why_exit_now"] == "สัญญาณ EMA และแนวต้านเทคนิค"
        assert result["risk_of_holding"] == "ความเสี่ยงจากการย่อตัว"
        assert result["counter_case"] == "หากทะลุ 3.20 ได้อาจไปต่อ"
        assert len(result["key_risks"]) == 2
        assert len(result["watch_next"]) == 2
        assert "ควรแบ่งขายทำกำไร" in result["summary"]


@pytest.mark.anyio
async def test_ask_sell_opinion_timeout_fallback():
    """Unit test for ask_sell_opinion when request times out."""
    service = DeepSeekSecondOpinion("test-key")

    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Timeout")):
        result = await service.ask_sell_opinion({"symbol": "DOGE/THB"})
        assert result["status"] == "timeout"
        assert "AI analysis unavailable" in result["assessment"]
        assert result["confidence"] == 0.0
        assert "AI analysis unavailable" in result["why_exit_now"]
