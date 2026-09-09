import pytest
from starlette.testclient import TestClient
from app.main import app, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Set test database path
    test_db_path = str(tmp_path / "test_api.db")
    monkeypatch.setattr(db, "path", test_db_path)
    db.initialize(seed_doge_position=True)
    with TestClient(app) as c:
        yield c


def test_api_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["live_trading"] is False


def test_api_dashboard(client):
    res = client.get("/api/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "paper" in data
    assert "real" in data
    assert "manual_real" in data
    assert "candidates" in data
    assert "notifications" in data
    assert "unread_notifications_count" in data

    # Verify seeded DOGE position in dashboard
    real_pos = data["manual_real"]["positions"]
    assert len(real_pos) == 1
    assert real_pos[0]["asset"] == "DOGE"
    assert real_pos[0]["quantity"] == 1676.44
    assert real_pos[0]["average_cost"] == 2.98


def test_api_cash_deposit_and_withdrawal(client):
    res = client.post("/api/cash", json={
        "mode": "MANUAL_REAL",
        "type": "DEPOSIT",
        "amount": 500.0,
        "note": "Top up"
    })
    assert res.status_code == 200
    assert res.json()["portfolio"]["cash"] > 5000.0

    # Overdraft withdrawal must fail
    res_err = client.post("/api/cash", json={
        "mode": "MANUAL_REAL",
        "type": "WITHDRAWAL",
        "amount": 100_000.0,
        "note": "Too large"
    })
    assert res_err.status_code == 400


def test_api_ask_deepseek_position(client):
    res = client.post("/api/ai/ask-position", json={
        "symbol": "DOGE/THB",
        "mode": "MANUAL_REAL"
    })
    assert res.status_code == 200
    data = res.json()
    assert "analysis" in data
    assert "disclaimer" in data
    assert data["context"]["symbol"] == "DOGE/THB"


def test_api_notifications_read(client):
    db.notify("CRITICAL", "TEST_ALERT", "Test alert message")
    res = client.get("/api/notifications?unread_only=true")
    assert res.status_code == 200
    notes = res.json()["notifications"]
    assert len(notes) >= 1
    note_id = notes[0]["id"]

    res_read = client.post(f"/api/notifications/{note_id}/read")
    assert res_read.status_code == 200

    res_read_all = client.post("/api/notifications/read-all")
    assert res_read_all.status_code == 200
    assert res_read_all.json()["unread_count"] == 0
