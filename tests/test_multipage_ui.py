from starlette.testclient import TestClient
import pytest

from app.main import app, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_multipage.db")
    monkeypatch.setattr(db, "path", test_db_path)
    db.initialize(seed_doge_position=True)
    with TestClient(app) as c:
        yield c


def test_index_html_contains_multipage_tabs(client):
    """
    Verify GET / serves the refactored multi-page UI with all 6 required tabs and pages.
    """
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Verify all 6 Thai navigation items
    assert "ภาพรวม" in html
    assert "สถานะที่ถือ" in html
    assert "สแกนตลาด" in html
    assert "เทรด / เงินสด" in html
    assert "แจ้งเตือน" in html
    assert "ประวัติ / ตั้งค่า" in html

    # Verify all 6 page section containers
    assert 'id="page-dashboard"' in html
    assert 'id="page-position"' in html
    assert 'id="page-scanner"' in html
    assert 'id="page-trading"' in html
    assert 'id="page-notifications"' in html
    assert 'id="page-journal"' in html

    # Verify global critical alert banner
    assert 'id="global-critical-banner"' in html

    # Verify Open Position page elements:
    # DeepSeek second opinion and shortcut real SELL form
    assert 'id="btn-ask-ai"' in html
    assert 'id="position-sell-form"' in html
    assert 'id="pos-sell-pair"' in html
    assert 'id="pos-sell-qty"' in html
    assert 'id="pos-sell-price"' in html

    # Verify Market Scanner elements
    assert 'id="best-candidate-banner"' in html
    assert 'id="scan-now-btn"' in html
    assert 'id="candidates-body"' in html

    # Verify Trading & Cash elements
    assert 'id="trade-form"' in html
    assert 'id="cash-form"' in html

    # Verify Notifications elements
    assert 'id="notifications-list"' in html
    assert 'id="note-type-filter"' in html

    # Verify Settings and Snapshots elements
    assert 'id="settings-form"' in html
    assert 'id="snapshots-body"' in html


def test_update_settings_with_new_fields(client):
    """
    Verify PUT /api/settings accepts updated settings payload including optional fields.
    """
    payload = {
        "price_refresh_seconds": 90,
        "timeframes": ["15m", "1h"],
        "risk_percent": 1.5,
        "estimated_exit_fee_percent": 0.25,
        "scanner_enabled": True,
        "websocket_enabled": True,
        "telegram_enabled": False,
        "ai_opinion_enabled": True,
        "ai_opinion_threshold": 80,
        "realtime_alert_cooldown_seconds": 300,
    }
    res = client.put("/api/settings", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["price_refresh_seconds"] == 90
    assert data["timeframes"] == ["15m", "1h"]
    assert data["risk_percent"] == 1.5


def test_position_sell_shortcut_api_flow(client):
    """
    Verify trade endpoint handles manual real SELL execution properly.
    """
    # DOGE position seeded: 1676.44 DOGE @ 2.98
    res = client.post("/api/trades", json={
        "mode": "MANUAL_REAL",
        "side": "SELL",
        "pair": "DOGE/THB",
        "quantity": 1676.44,
        "price": 3.05,
        "fee": 12.5,
        "reason": "TAKE_PROFIT_1",
    })
    assert res.status_code == 200

    dash = client.get("/api/dashboard").json()
    real_pos = dash["manual_real"]["positions"]
    # Position should now be closed (quantity == 0)
    assert len(real_pos) == 0
    assert dash["manual_real"]["has_open_position"] is False
    assert dash["manual_real"]["realized_pnl"] > 0
