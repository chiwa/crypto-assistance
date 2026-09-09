"""Tests for DeepSeek Assistant Chat feature."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import httpx

from app.assistant_knowledge import SYSTEM_KNOWLEDGE_BASE, build_assistant_context, build_system_prompt
from app.db import Database
from app.integrations import DeepSeekSecondOpinion
from app.main import app, deepseek
from app.schemas import ChatMessage, ChatRequest


def test_chat_request_schema_validation():
    # Valid minimal
    req = ChatRequest(message="สวัสดีครับ")
    assert req.message == "สวัสดีครับ"
    assert req.history == []

    # Valid with history
    req2 = ChatRequest(
        message="ขอดูสถานะพอร์ต",
        history=[
            ChatMessage(role="user", content="สวัสดี"),
            ChatMessage(role="assistant", content="สวัสดีครับ มีอะไรให้ช่วยครับ"),
        ],
    )
    assert len(req2.history) == 2
    assert req2.history[0].role == "user"

    # Empty message rejection
    with pytest.raises(Exception):
        ChatRequest(message="")

    # Message exceeding 1000 chars rejection
    with pytest.raises(Exception):
        ChatRequest(message="a" * 1001)

    # History exceeding 10 messages rejection
    with pytest.raises(Exception):
        ChatRequest(
            message="ทดสอบ",
            history=[ChatMessage(role="user", content=f"msg {i}") for i in range(11)],
        )

    # Invalid role rejection
    with pytest.raises(Exception):
        ChatMessage(role="system", content="not allowed")


import asyncio


def test_assistant_context_and_safety(tmp_path):
    db_file = tmp_path / "test_assistant.db"
    db = Database(str(db_file))
    db.initialize()

    ctx = build_assistant_context(db)
    assert "timestamp_bkk" in ctx
    assert "has_open_position" in ctx
    assert "manual_real_portfolio" in ctx
    assert "paper_portfolio" in ctx
    assert "safe_settings" in ctx
    assert "recent_notifications" in ctx

    # Verify no secret tokens or keys are present
    ctx_str = str(ctx).lower()
    assert "secret" not in ctx_str
    assert "telegram_bot_token" not in ctx_str
    assert "deep_seek_api_key" not in ctx_str
    assert "api_key" not in ctx_str
    assert "bot_token" not in ctx_str


def test_system_prompt_content(tmp_path):
    db_file = tmp_path / "test_prompt.db"
    db = Database(str(db_file))
    db.initialize()

    prompt = build_system_prompt(db)
    # Check strict safety rules
    assert "READ-ONLY ASSISTANT 100%" in prompt
    assert "ไม่มีสิทธิ์และไม่สามารถส่งคำสั่งซื้อ/ขาย" in prompt
    assert "ห้ามเปลี่ยนแปลงหรือลบล้างการแจ้งเตือน STOP_LOSS" in prompt
    assert "ห้ามแนะนำให้ซื้อเฉลี่ยขาลง" in prompt

    # Check that application pages and workflows are documented
    assert "#dashboard" in prompt
    assert "#position" in prompt
    assert "#scanner" in prompt
    assert "#trading" in prompt
    assert "#notifications" in prompt
    assert "#journal" in prompt
    assert "#assistant" in prompt

    # Check key operational workflows
    assert "ขั้นตอนการเติมเงิน" in prompt
    assert "ขั้นตอนการบันทึกการซื้อจริง" in prompt
    assert "ขั้นตอนการบันทึกการขายบางส่วน" in prompt
    assert "การดูจุด Stop Loss และ Take Profit" in prompt
    assert "ความหมายของสัญญาณ" in prompt
    assert "BUY_NOW" in prompt


def test_deepseek_chat_integration_unconfigured():
    async def _run():
        ds = DeepSeekSecondOpinion(api_key=None)
        assert not ds.configured
        with pytest.raises(RuntimeError, match="ไม่ได้ถูกตั้งค่า"):
            await ds.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="sys")

    asyncio.run(_run())


def test_deepseek_chat_integration_success():
    async def _run():
        ds = DeepSeekSecondOpinion(api_key="sk-test-12345")
        mock_resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "สวัสดีครับ ต้องการให้ผมช่วยเหลือเรื่องใดครับ",
                    }
                }
            ]
        }

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(200, json=mock_resp, request=httpx.Request("POST", "https://api.deepseek.com"))
            reply = await ds.chat(
                messages=[{"role": "user", "content": "สวัสดี"}],
                system_prompt="ระบบช่วยเหลือ",
            )
            assert "สวัสดีครับ ต้องการให้ผมช่วยเหลือเรื่องใดครับ" in reply
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert kwargs["json"]["model"] == "deepseek-chat"
            assert kwargs["json"]["temperature"] == 0.3
            assert len(kwargs["json"]["messages"]) == 2  # system + user

    asyncio.run(_run())


def test_deepseek_chat_integration_timeout():
    async def _run():
        ds = DeepSeekSecondOpinion(api_key="sk-test-12345")
        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timeout")):
            with pytest.raises(TimeoutError, match="หมดเวลา"):
                await ds.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="sys")

    asyncio.run(_run())


def test_api_chat_endpoint_unconfigured():
    client = TestClient(app)
    orig_key = deepseek.api_key
    try:
        deepseek.api_key = None
        resp = client.post("/api/chat", json={"message": "สวัสดี"})
        assert resp.status_code == 503
        assert "DeepSeek API Key ไม่ได้ถูกกำหนดค่า" in resp.json()["detail"]
    finally:
        deepseek.api_key = orig_key


def test_api_chat_endpoint_success():
    client = TestClient(app)
    orig_key = deepseek.api_key
    try:
        deepseek.api_key = "sk-mock-key-abc"
        with patch.object(deepseek, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "ยินดีต้อนรับครับ นี่คือคำแนะนำการใช้งานแอปพลิเคชัน"
            resp = client.post(
                "/api/chat",
                json={
                    "message": "ต้องการเติมเงินทำอย่างไร",
                    "history": [
                        {"role": "user", "content": "สวัสดี"},
                        {"role": "assistant", "content": "สวัสดีครับ"},
                    ],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "ยินดีต้อนรับครับ" in data["reply"]
            assert "disclaimer" in data
            assert "timestamp" in data
            mock_chat.assert_called_once()
    finally:
        deepseek.api_key = orig_key


def test_api_chat_endpoint_timeout_504():
    client = TestClient(app)
    orig_key = deepseek.api_key
    try:
        deepseek.api_key = "sk-mock-key-abc"
        with patch.object(deepseek, "chat", side_effect=TimeoutError("Timeout")):
            resp = client.post("/api/chat", json={"message": "ทดสอบ timeout"})
            assert resp.status_code == 504
            assert "หมดเวลา" in resp.json()["detail"]
    finally:
        deepseek.api_key = orig_key


def test_api_chat_endpoint_error_redaction():
    client = TestClient(app)
    orig_key = deepseek.api_key
    secret = "sk-sensitive-secret-token"
    try:
        deepseek.api_key = secret
        with patch.object(deepseek, "chat", side_effect=Exception(f"Failed with key {secret}")):
            resp = client.post("/api/chat", json={"message": "ทดสอบ error"})
            assert resp.status_code == 500
            assert secret not in resp.text
            assert "[REDACTED]" in resp.text
    finally:
        deepseek.api_key = orig_key


def test_index_html_ui_elements():
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Verify navigation link
    assert 'href="#assistant"' in html
    assert 'data-page="assistant"' in html
    assert "คุยกับ DeepSeek" in html

    # Verify section
    assert 'id="page-assistant"' in html
    assert 'id="assistant-chat-messages"' in html
    assert 'id="assistant-chat-input"' in html
    assert 'id="assistant-chat-send-btn"' in html
    assert 'id="assistant-typing-indicator"' in html

    # Verify all 6 required suggested questions
    assert "ต้องการเติมเงินต้องทำอย่างไร" in html
    assert "บันทึกการซื้อจริงตรงไหน" in html
    assert "บันทึกขายบางส่วนอย่างไร" in html
    assert "ดู Stop Loss และ Take Profit ตรงไหน" in html
    assert "BUY_NOW หมายความว่าอะไร" in html
    assert "อธิบายสถานะพอร์ตตอนนี้" in html

    # Verify XSS prevention and safe rendering logic
    assert "escapeHtml" in html
    assert ".replace(/&/g, '&amp;')" in html
    assert ".replace(/</g, '&lt;')" in html
    assert ".replace(/>/g, '&gt;')" in html
    assert ".replace(/\"/g, '&quot;')" in html
    assert ".replace(/'/g, '&#039;')" in html

    # Verify router includes assistant
    assert "'assistant'" in html
