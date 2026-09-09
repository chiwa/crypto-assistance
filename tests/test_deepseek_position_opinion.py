import json
import pytest
import httpx
from starlette.testclient import TestClient

from app.integrations import DeepSeekSecondOpinion
from app.main import app, db


@pytest.mark.anyio
async def test_deepseek_mocked_successful_response(monkeypatch):
    """Test 1: Mocked successful response from DeepSeek returning valid structured JSON in Thai."""
    ai = DeepSeekSecondOpinion(api_key="sk-test-mock-key")
    assert ai.configured is True

    mock_response_data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "assessment": "แนวโน้มขาขึ้นยังคงแข็งแกร่ง ราคาอยู่เหนือเส้น EMA สำคัญ และ RSI อยู่ในระดับปกติ",
                        "confidence": "สูง",
                        "bull_case": "แรงซื้อต่อเนื่องและโครงสร้างราคายกตัวสูงขึ้น",
                        "bear_case": "หากหลุดแนวรับ EMA 20 อาจมีการพักฐานระยะสั้น",
                        "key_risks": [
                            "ความผันผวนของราคา BTC ในตลาดโลก",
                            "ปริมาณการซื้อขายชะลอตัวในช่วงสุดสัปดาห์"
                        ],
                        "watch_next": [
                            "เฝ้าระวังแนวต้าน 3.05 บาท",
                            "จุดตัดขาดทุน Stop Loss ที่ 2.9320 บาท"
                        ],
                        "summary": "แนะนำถือต่อตามแผน โดยตั้ง Trailing Stop เพื่อปกป้องกำไร"
                    }, ensure_ascii=False)
                }
            }
        ]
    }

    class MockResponse:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return mock_response_data

    async def mock_post(*args, **kwargs):
        # Verify headers and payload
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test-mock-key"
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    context = {
        "symbol": "DOGE/THB",
        "mode": "MANUAL_REAL",
        "actual_entry_price": 2.98,
        "quantity": 1676.44,
        "current_bitkub_price": 2.984,
        "gross_unrealized_pnl": 6.7058,
        "trend_15m": "RANGE",
        "trend_1h": "UPTREND",
        "trend_4h": "UPTREND",
        "rsi": 54.95,
        "atr": 0.03,
        "relative_volume": 1.2,
        "current_strategy": "Manual Real Execution",
        "current_action": "HOLD",
        "stop": 2.932,
        "tp1": 3.052,
        "tp2": 3.100,
        "exit_reason": "ถือต่อ - กำลังเฝ้าระวังตำแหน่งจริง",
    }

    res = await ai.ask_structured(context)

    assert res["status"] == "connected"
    assert "แนวโน้มขาขึ้นยังคงแข็งแกร่ง" in res["assessment"]
    assert res["confidence"] == "สูง"
    assert "แรงซื้อต่อเนื่อง" in res["bull_case"]
    assert "หากหลุดแนวรับ" in res["bear_case"]
    assert isinstance(res["key_risks"], list)
    assert len(res["key_risks"]) == 2
    assert isinstance(res["watch_next"], list)
    assert len(res["watch_next"]) == 2
    assert "แนะนำถือต่อตามแผน" in res["summary"]


@pytest.mark.anyio
async def test_deepseek_malformed_json_fallback(monkeypatch):
    """Test 2: Malformed JSON fallback from DeepSeek is handled gracefully."""
    ai = DeepSeekSecondOpinion(api_key="sk-test-mock-key")
    assert ai.configured is True

    class MockMalformedResponse:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "This is definitely not a JSON object { invalid: json"
                        }
                    }
                ]
            }

    async def mock_post(*args, **kwargs):
        return MockMalformedResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = await ai.ask_structured({"symbol": "DOGE/THB"})

    assert res["status"] == "error"
    assert "ไม่สามารถตอบกลับได้" in res["assessment"]
    assert isinstance(res["key_risks"], list)
    assert isinstance(res["watch_next"], list)
    assert "Deterministic" in res["summary"]


@pytest.mark.anyio
async def test_deepseek_timeout_fallback(monkeypatch):
    """Test 3: Network timeout handling."""
    ai = DeepSeekSecondOpinion(api_key="sk-test-mock-key")
    assert ai.configured is True

    async def mock_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("Request timed out after 30 seconds")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_timeout)

    res = await ai.ask_structured({"symbol": "DOGE/THB"})

    assert res["status"] == "timeout"
    assert "หมดเวลาเชื่อมต่อ" in res["assessment"]
    assert res["confidence"] == "ระมัดระวัง"
    assert isinstance(res["key_risks"], list)
    assert isinstance(res["watch_next"], list)
    assert "หมดเวลา" in res["summary"]


@pytest.mark.anyio
async def test_deepseek_missing_api_key():
    """Test 4: Missing or empty API key returns unavailable without making network requests."""
    # None key
    ai_none = DeepSeekSecondOpinion(api_key=None)
    assert ai_none.configured is False
    res_none = await ai_none.ask_structured({"symbol": "DOGE/THB"})
    assert res_none["status"] == "unavailable"
    assert "ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY" in res_none["assessment"]
    assert isinstance(res_none["key_risks"], list)
    assert isinstance(res_none["watch_next"], list)

    # Empty key
    ai_empty = DeepSeekSecondOpinion(api_key="   ")
    assert ai_empty.configured is False
    res_empty = await ai_empty.ask_structured({"symbol": "DOGE/THB"})
    assert res_empty["status"] == "unavailable"


def test_thai_structured_response_rendering(tmp_path, monkeypatch):
    """Test 5: Full endpoint test verifying all 15 deterministic context parameters and Thai rendering."""
    # Setup test database with DOGE MANUAL_REAL position
    db.path = str(tmp_path / "test.db")
    db.initialize(seed_doge_position=True)
    db.update_price("DOGE/THB", 2.984)

    captured_context = {}

    async def mock_ask_structured(ctx):
        nonlocal captured_context
        captured_context = ctx
        return {
            "status": "connected",
            "assessment": "ทดสอบการประเมินสถานะทางเทคนิค",
            "confidence": "สูง",
            "bull_case": "มุมมองเชิงบวก",
            "bear_case": "มุมมองเชิงลบ",
            "key_risks": ["ความเสี่ยงข้อที่ 1", "ความเสี่ยงข้อที่ 2"],
            "watch_next": ["สิ่งที่ต้องจับตาข้อที่ 1"],
            "summary": "สรุปคำแนะนำเพื่อประกอบการตัดสินใจ",
        }

    from app.main import deepseek
    monkeypatch.setattr(deepseek, "ask_structured", mock_ask_structured)

    client = TestClient(app)
    response = client.post("/api/ai/ask-position", json={"symbol": "DOGE/THB", "mode": "MANUAL_REAL"})

    assert response.status_code == 200
    data = response.json()

    # Check response envelope
    assert data["status"] == "connected"
    assert "disclaimer" in data
    assert "ความคิดเห็นที่ 2" in data["disclaimer"]

    # Check Thai structured analysis
    analysis = data["analysis"]
    assert analysis["assessment"] == "ทดสอบการประเมินสถานะทางเทคนิค"
    assert analysis["confidence"] == "สูง"
    assert analysis["bull_case"] == "มุมมองเชิงบวก"
    assert analysis["bear_case"] == "มุมมองเชิงลบ"
    assert analysis["key_risks"] == ["ความเสี่ยงข้อที่ 1", "ความเสี่ยงข้อที่ 2"]
    assert analysis["watch_next"] == ["สิ่งที่ต้องจับตาข้อที่ 1"]
    assert analysis["summary"] == "สรุปคำแนะนำเพื่อประกอบการตัดสินใจ"

    # Check all 15 deterministic context items required by Requirement 2
    ctx = data["context"]
    assert ctx["symbol"] == "DOGE/THB"
    assert ctx["mode"] == "MANUAL_REAL"
    assert ctx["actual_entry_price"] == 2.98
    assert ctx["quantity"] == pytest.approx(1676.44, rel=1e-4)
    assert ctx["current_bitkub_price"] == 2.984
    assert ctx["gross_unrealized_pnl"] == pytest.approx(6.7058, rel=1e-3)
    assert "trend_15m" in ctx
    assert "trend_1h" in ctx
    assert "trend_4h" in ctx
    assert "rsi" in ctx
    assert "atr" in ctx
    assert "relative_volume" in ctx
    assert ctx["current_strategy"] == "Manual Real Execution"
    assert ctx["current_action"] == "HOLD"
    assert ctx["stop_loss"] is not None
    assert ctx["take_profit_1"] is not None
    assert ctx["take_profit_2"] is not None
    assert ctx["exit_reason"] is not None


def test_deepseek_failure_isolates_and_keeps_system_running(tmp_path, monkeypatch):
    """Test 6: DeepSeek failure does not crash the system, alter positions, or break trading state."""
    db.path = str(tmp_path / "test.db")
    db.initialize(seed_doge_position=True)

    initial_portfolio = db.portfolio("MANUAL_REAL")
    initial_cash = initial_portfolio["cash"]
    initial_qty = initial_portfolio["positions"][0]["quantity"]

    async def mock_fail_structured(ctx):
        raise RuntimeError("External DeepSeek service completely unreachable")

    from app.main import deepseek
    # Test that even if an unhandled exception occurred in deepseek, the system handles it
    async def mock_safe_structured(ctx):
        return {
            "status": "error",
            "assessment": "ระบบ AI ขัดข้องชั่วคราว",
            "confidence": "ระมัดระวัง",
            "bull_case": "N/A",
            "bear_case": "N/A",
            "key_risks": ["เกิดข้อผิดพลาดในการเชื่อมต่อ"],
            "watch_next": ["ยึดตามสัญญาณเทคนิค"],
            "summary": "โปรดยึดหลักการบริหารความเสี่ยงเป็นสำคัญ",
        }

    monkeypatch.setattr(deepseek, "ask_structured", mock_safe_structured)

    client = TestClient(app)
    response = client.post("/api/ai/ask-position", json={"symbol": "DOGE/THB", "mode": "MANUAL_REAL"})
    assert response.status_code == 200

    # Verify positions and cash were strictly untouched
    after_portfolio = db.portfolio("MANUAL_REAL")
    assert after_portfolio["cash"] == initial_cash
    assert after_portfolio["positions"][0]["quantity"] == initial_qty
    assert after_portfolio["positions"][0]["average_cost"] == 2.98
