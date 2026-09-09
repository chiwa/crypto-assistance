import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.integrations import DeepSeekSecondOpinion
from app.realtime import RealtimeMonitor, format_buy_alert, format_sell_alert
from app.main import app, telegram, realtime


def _setup_test_db(tmp_path):
    db = Database(str(tmp_path / "test_alerts.db"))
    db.initialize()

    # Cash and existing position
    db.add_cash("MANUAL_REAL", "DEPOSIT", 10_000.0)
    db.trade("MANUAL_REAL", "BUY", "DOGE/THB", 1676.44, 2.98, fee=0.0)

    # Seed a signal
    db.save_signal("DOGE/THB", "15m", {"signal": "HOLD", "score": 50, "regime": "SIDEWAYS", "close": 2.98})

    # Seed a journal entry
    db.journal("MANUAL_REAL", "DOGE Trade Note", "Test journal entry content", "DOGE")

    return db


@pytest.mark.anyio
async def test_send_test_buy_alert_payload_and_content(tmp_path):
    """BUY test alert sends simulated payload with required content and DeepSeek Thai opinion."""
    db = _setup_test_db(tmp_path)
    sent_messages = []

    async def fake_telegram_send(text: str):
        sent_messages.append(text)
        return True

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send)
    msg = await monitor.send_test_buy_alert()

    assert len(sent_messages) == 1
    assert sent_messages[0] == msg

    # Starts with mandatory prefix
    assert msg.startswith("🧪 TEST ALERT — ไม่ใช่สัญญาณจริง")

    # Core simulated parameters
    assert "ETH/THB" in msg
    assert "86/100" in msg
    assert "3/3" in msg
    assert "EMA Pullback" in msg
    assert "82,100 THB" in msg
    assert "81,900 - 82,150 THB" in msg
    assert "80,950 THB" in msg
    assert "84,200 THB" in msg
    assert "85,500 THB" in msg
    assert "1:2.1" in msg
    assert "5,000 THB" in msg
    assert "190 THB" in msg
    assert "ขาขึ้น" in msg or "TRENDING_UP" in msg
    assert "BTC bias: POSITIVE" in msg

    # DeepSeek second opinion section
    assert "🤖 ความเห็นที่ 2 จาก DeepSeek" in msg
    assert "82%" in msg
    assert "โครงสร้างโดยรวมเป็นบวกและทั้ง 3 Timeframes สนับสนุนทิศทางเดียวกัน แต่ราคากำลังเข้าใกล้แนวต้านระยะสั้น" in msg
    assert "EMA 9/20/50 เรียงตัวเชิงบวก" in msg
    assert "Volume เพิ่มขึ้นพร้อมราคา" in msg
    assert "15m, 1h และ 4h ยืนยันตรงกัน" in msg
    assert "ราคาใกล้แนวต้าน" in msg
    assert "หาก Volume ลดลง breakout อาจล้มเหลว" in msg
    assert "BTC อ่อนแรงกะทันหัน" in msg
    assert "Failed breakout" in msg
    assert "Slippage ตอนเข้าซื้อ" in msg
    assert "การยืนเหนือ entry zone" in msg
    assert "Relative Volume หลัง breakout" in msg
    assert "RSI divergence" in msg
    assert "สัญญาณมีคุณภาพสูงตามระบบ แต่ควรรอ execution ที่อยู่ในโซนและรักษา Stop Loss ตามแผน" in msg

    # Test safety footer
    assert "⚠️ ระบบไม่ได้ส่งคำสั่งซื้ออัตโนมัติ" in msg


@pytest.mark.anyio
async def test_send_test_sell_alert_payload_and_content(tmp_path):
    """SELL test alert sends simulated DOGE payload with required content and DeepSeek Thai opinion."""
    db = _setup_test_db(tmp_path)
    sent_messages = []

    async def fake_telegram_send(text: str):
        sent_messages.append(text)
        return True

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send)
    msg = await monitor.send_test_sell_alert()

    assert len(sent_messages) == 1
    assert sent_messages[0] == msg

    # Starts with mandatory prefix
    assert msg.startswith("🧪 TEST ALERT — ไม่ใช่สัญญาณจริง")

    # Core simulated parameters
    assert "DOGE/THB" in msg
    assert "MANUAL_REAL" in msg
    assert "2.9800 THB" in msg
    assert "2.9200 THB" in msg
    assert "1,676.44 DOGE" in msg
    assert "-100.59 THB (-2.01%)" in msg
    assert "TREND_INVALIDATION" in msg
    assert "Stop Loss: 2.9320 THB" in msg
    assert "Effective/Trailing Stop: 2.9450 THB" in msg
    assert "TP1: 3.0520 THB" in msg
    assert "TP2: 3.1000 THB" in msg

    # DeepSeek second opinion section
    assert "🤖 ความเห็นที่ 2 จาก DeepSeek" in msg
    assert "88%" in msg
    assert "โครงสร้างระยะสั้นอ่อนตัวลงและสัญญาณออกจากระบบถูกกระตุ้นแล้ว" in msg
    assert "แนวโน้ม 15m และ 1h เสียโครงสร้างพร้อมราคาหลุดระดับป้องกัน" in msg
    assert "มีความเสี่ยงที่การขาดทุนจะขยายตัวหากแรงขายต่อเนื่อง" in msg
    assert "ราคาอาจเกิด technical rebound หลังหลุดแนวรับ แต่ยังไม่มี confirmation ว่ากลับตัว" in msg
    assert "Momentum ยังอ่อน" in msg
    assert "Support เดิมกลายเป็น resistance" in msg
    assert "ตลาด Altcoin อ่อนตาม BTC" in msg
    assert "ระบบมีเหตุผลเพียงพอในการแนะนำให้ออกจากสถานะ แต่การขายจริงยังต้องดำเนินการด้วยตนเองบน Bitkub" in msg

    # Test safety footer
    assert "⚠️ ยังไม่มีการขายจริง" in msg


@pytest.mark.anyio
async def test_test_alerts_never_mutate_state(tmp_path):
    """Test alerts must NEVER modify signal state, portfolio, positions, ledger, or trading journal."""
    db = _setup_test_db(tmp_path)

    async def fake_telegram_send(text: str):
        return True

    monitor = RealtimeMonitor(db, telegram_send=fake_telegram_send)

    # Pre-execution snapshots
    cash_before = db.portfolio("MANUAL_REAL")["cash"]
    with db.connect() as conn:
        positions_before = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
        plans_before = [dict(r) for r in conn.execute("SELECT * FROM position_plans").fetchall()]
        signals_before = [dict(r) for r in conn.execute("SELECT * FROM signals").fetchall()]
        ledger_before = [dict(r) for r in conn.execute("SELECT * FROM ledger").fetchall()]
        journal_before = [dict(r) for r in conn.execute("SELECT * FROM journal").fetchall()]
        notifications_before = [dict(r) for r in conn.execute("SELECT * FROM notifications").fetchall()]

    # Execute both test alerts
    await monitor.send_test_buy_alert()
    await monitor.send_test_sell_alert()

    # Post-execution comparisons
    cash_after = db.portfolio("MANUAL_REAL")["cash"]
    with db.connect() as conn:
        positions_after = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
        plans_after = [dict(r) for r in conn.execute("SELECT * FROM position_plans").fetchall()]
        signals_after = [dict(r) for r in conn.execute("SELECT * FROM signals").fetchall()]
        ledger_after = [dict(r) for r in conn.execute("SELECT * FROM ledger").fetchall()]
        journal_after = [dict(r) for r in conn.execute("SELECT * FROM journal").fetchall()]
        notifications_after = [dict(r) for r in conn.execute("SELECT * FROM notifications").fetchall()]

    assert cash_after == cash_before
    assert positions_after == positions_before
    assert plans_after == plans_before
    assert signals_after == signals_before
    assert ledger_after == ledger_before
    assert journal_after == journal_before
    assert notifications_after == notifications_before


@pytest.mark.anyio
async def test_unconfigured_telegram_raises_error(tmp_path):
    """Missing Telegram configuration raises ValueError with exact Thai message."""
    db = _setup_test_db(tmp_path)
    monitor = RealtimeMonitor(db, telegram_send=None)

    with pytest.raises(ValueError, match="ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ"):
        await monitor.send_test_buy_alert()

    with pytest.raises(ValueError, match="ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ"):
        await monitor.send_test_sell_alert()


def test_api_test_alert_endpoints_validation_and_delivery():
    """FastAPI endpoints validate configuration, handle exceptions and redact tokens."""
    client = TestClient(app)

    # 1. When Telegram is unconfigured
    with patch.object(telegram, "token", None), patch.object(telegram, "chat_id", None):
        res_buy = client.post("/api/test-alert/buy")
        assert res_buy.status_code == 400
        assert "ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ" in res_buy.json()["detail"]

        res_sell = client.post("/api/test-alert/sell")
        assert res_sell.status_code == 400
        assert "ยังไม่ได้กำหนดค่า Telegram Bot Token หรือ Chat ID ในระบบ" in res_sell.json()["detail"]

    # 2. When Telegram is configured and delivery succeeds
    mock_send = AsyncMock(return_value=True)
    with patch.object(telegram, "token", "valid_token_123"), \
         patch.object(telegram, "chat_id", "valid_chat_456"), \
         patch.object(realtime, "telegram_send", mock_send):
        res_buy = client.post("/api/test-alert/buy")
        assert res_buy.status_code == 200
        data_buy = res_buy.json()
        assert data_buy["status"] == "ok"
        assert data_buy["message"] == "ส่งข้อความทดสอบสำเร็จ"
        assert "🧪 TEST ALERT — ไม่ใช่สัญญาณจริง" in data_buy["preview"]
        assert "ETH/THB" in data_buy["preview"]

        res_sell = client.post("/api/test-alert/sell")
        assert res_sell.status_code == 200
        data_sell = res_sell.json()
        assert data_sell["status"] == "ok"
        assert data_sell["message"] == "ส่งข้อความทดสอบสำเร็จ"
        assert "🧪 TEST ALERT — ไม่ใช่สัญญาณจริง" in data_sell["preview"]
        assert "DOGE/THB" in data_sell["preview"]

    # 3. When delivery fails and contains token, token is redacted
    secret_token = "secret_bot_token_xyz987"
    failing_send = AsyncMock(side_effect=Exception(f"Failed connecting with token {secret_token} to Telegram"))
    with patch.object(telegram, "token", secret_token), \
         patch.object(telegram, "chat_id", "valid_chat_456"), \
         patch.object(realtime, "telegram_send", failing_send):
        res_fail = client.post("/api/test-alert/buy")
        assert res_fail.status_code == 500
        detail = res_fail.json()["detail"]
        assert secret_token not in detail
        assert "[REDACTED]" in detail
        assert "ส่งไม่สำเร็จ:" in detail


def test_index_html_has_test_buttons_and_handlers():
    """Verify app/static/index.html includes required buttons and handlers."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "🧪 ทดสอบ Telegram BUY" in html
    assert "🧪 ทดสอบ Telegram SELL" in html
    assert "sendTestAlert('buy')" in html
    assert "sendTestAlert('sell')" in html
    assert "async function sendTestAlert(type)" in html
    assert "ส่งข้อความทดสอบสำเร็จ" in html
    assert "ส่งไม่สำเร็จ:" in html
