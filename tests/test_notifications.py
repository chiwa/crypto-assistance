import pytest
from app.db import Database


def test_notification_lifecycle_and_read_status(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Initial unread count should be 0
    assert db.unread_notifications_count() == 0

    id1 = db.notify("CRITICAL", "BUY_NOW · ETH/THB", "ราคาแตะโซนเข้าซื้อ")
    id2 = db.notify("INFO", "WATCH · SOL/THB", "กำลังเฝ้าระวัง")

    assert id1 is not None and id2 is not None
    assert db.unread_notifications_count() == 2

    # Mark first notification as read
    assert db.mark_notification_read(id1) is True
    assert db.unread_notifications_count() == 1

    # Mark all remaining as read
    marked = db.mark_all_notifications_read()
    assert marked == 1
    assert db.unread_notifications_count() == 0


def test_notification_deduplication_cooldown(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    id1 = db.notify("CRITICAL", "BUY_NOW · BTC/THB", "Alert 1")
    assert id1 is not None

    # Immediate duplicate notification with same title should be rejected by 5-min cooldown
    id2 = db.notify("CRITICAL", "BUY_NOW · BTC/THB", "Alert 2")
    assert id2 is None

    # Different title should succeed immediately
    id3 = db.notify("CRITICAL", "STOP_LOSS · BTC/THB", "Alert 3")
    assert id3 is not None
