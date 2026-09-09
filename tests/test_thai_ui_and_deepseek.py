import pytest
from app.translations import to_thai_regime, to_thai_status, to_thai_strategy
from app.integrations import DeepSeekSecondOpinion


def test_thai_status_translations():
    mapping = {
        "WAIT": "รอดู",
        "WATCH": "จับตา",
        "BUY_NOW": "ซื้อได้ตอนนี้",
        "IN_POSITION": "กำลังถือ",
        "HOLD": "ถือต่อ",
        "EXIT_WATCH": "เตรียมออก",
        "TAKE_PROFIT": "ทำกำไร",
        "STOP_LOSS": "ตัดขาดทุน",
        "SELL_NOW": "ขายตอนนี้",
        "CLOSED": "ปิดสถานะแล้ว",
    }
    for eng, expected_th in mapping.items():
        assert to_thai_status(eng) == expected_th


def test_thai_regime_translations():
    assert to_thai_regime("TRENDING_UP") == "แนวโน้มขาขึ้น"
    assert to_thai_regime("TRENDING_DOWN") == "แนวโน้มขาลง"
    assert to_thai_regime("RANGING") == "ไซด์เวย์ (ไร้ทิศทาง)"
    assert to_thai_regime("HIGH_VOLATILITY") == "ความผันผวนสูง"
    assert to_thai_regime("LOW_LIQUIDITY") == "สภาพคล่องต่ำ"


@pytest.mark.anyio
async def test_deepseek_fallback_when_unconfigured():
    # When api_key is None, structured opinion returns safe fallback in Thai
    ai = DeepSeekSecondOpinion(api_key=None)
    assert ai.configured is False

    res = await ai.ask_structured({"symbol": "DOGE/THB", "price": 2.98})
    assert "ไม่ได้กำหนดค่า" in res["assessment"]
    assert "Deterministic" in res["summary"]

from app.integrations import TelegramNotifier
from app.db import Database


@pytest.mark.anyio
async def test_telegram_disabled_without_chat_id():
    tg = TelegramNotifier(token="mock_token", chat_id=None)
    assert tg.configured is False
    # send should return False safely without raising
    sent = await tg.send("test message")
    assert sent is False


@pytest.mark.anyio
async def test_deepseek_handles_exception_gracefully(monkeypatch):
    import httpx
    ai = DeepSeekSecondOpinion(api_key="mock_key")
    assert ai.configured is True

    async def mock_post(*args, **kwargs):
        raise httpx.ConnectTimeout("Connection timed out")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    # ask_structured should catch the exception and return a fallback dict
    res = await ai.ask_structured({"pair": "DOGE/THB"})
    assert "ไม่สามารถตอบกลับได้" in res["assessment"]
    assert "ระมัดระวัง" in res["confidence"]


def test_daily_snapshots_persistence(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_cash("MANUAL_REAL", "DEPOSIT", 10_000)

    snap = db.create_daily_snapshot("MANUAL_REAL", "2026-09-09")
    assert snap["date"] == "2026-09-09"
    assert snap["cash"] == 10_000.0
    assert snap["equity"] == 10_000.0

    recent = db.recent("daily_snapshots")
    assert len(recent) == 1
    assert recent[0]["date"] == "2026-09-09"
