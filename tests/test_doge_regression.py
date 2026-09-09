import pytest
import sqlite3
from app.db import Database
from app.translations import format_entry_score, to_thai_strategy


def test_manual_real_stays_manual_real(tmp_path):
    """Regression Test 1: The position must remain MANUAL_REAL and never revert to PAPER."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize(seed_doge_position=True)

    # Verify MANUAL_REAL has open position
    real_portfolio = db.portfolio("MANUAL_REAL")
    assert real_portfolio["mode"] == "MANUAL_REAL"
    assert real_portfolio["has_open_position"] is True
    assert len(real_portfolio["positions"]) == 1
    assert real_portfolio["positions"][0]["asset"] == "DOGE"

    # Verify PAPER has no position
    paper_portfolio = db.portfolio("PAPER")
    assert paper_portfolio["mode"] == "PAPER"
    assert paper_portfolio["has_open_position"] is False
    assert len(paper_portfolio["positions"]) == 0
    assert paper_portfolio["position_plan"] is None


def test_actual_entry_price_remains_exact(tmp_path):
    """Regression Test 2: Actual cost basis must be exactly 2.98 THB, never 2.9823 or signal-derived."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize(seed_doge_position=True)

    portfolio = db.portfolio("MANUAL_REAL")
    pos = portfolio["positions"][0]
    plan = portfolio["position_plan"]

    # Exactly 2.98 THB
    assert pos["average_cost"] == 2.98
    assert plan["entry_price"] == 2.98
    assert pos["quantity"] == pytest.approx(1676.44, rel=1e-5)

    # Gross purchase value check: 1676.44 * 2.98 = 4995.7912 THB
    assert pos["quantity"] * pos["average_cost"] == pytest.approx(4995.7912, rel=1e-4)


def test_unknown_fee_not_invented(tmp_path):
    """Regression Test 3: Do not invent a trading fee for MANUAL_REAL execution when unknown."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize(seed_doge_position=True)

    # Query ledger for BUY trade
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM ledger WHERE mode='MANUAL_REAL' AND type='BUY' AND asset='DOGE'"
        ).fetchone()
        assert row is not None
        assert row["fee_thb"] == 0.0
        assert row["price_thb"] == 2.98
        assert row["quantity"] == 1676.44
        assert row["amount_thb"] == -4995.7912
        assert row["reference"] == "UNKNOWN_FEE"


def test_gross_unrealized_pnl_positive(tmp_path):
    """Regression Test 4: For current price 2.984 and cost 2.98, gross unrealized P/L must be +6.7058 THB.
    Assumed fees must never be silently deducted from gross unrealized P/L.
    """
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize(seed_doge_position=True)

    db.update_price("DOGE/THB", 2.984)
    portfolio = db.portfolio("MANUAL_REAL")
    pos = portfolio["positions"][0]

    # Gross P/L = (2.984 - 2.98) * 1676.44 = 0.004 * 1676.44 = +6.7058 THB
    expected_gross_pnl = round((2.984 - 2.98) * 1676.44, 4)
    assert expected_gross_pnl > 0
    assert pos["unrealized_pnl"] == expected_gross_pnl
    assert pos["gross_unrealized_pnl"] == expected_gross_pnl
    assert portfolio["unrealized_pnl"] == expected_gross_pnl
    assert portfolio["gross_unrealized_pnl"] == expected_gross_pnl

    # Estimated exit fee is separated and clearly reported
    expected_exit_fee = round(1676.44 * 2.984 * 0.0025, 4)
    assert pos["estimated_exit_fee"] == expected_exit_fee
    assert portfolio["estimated_exit_fee"] == expected_exit_fee

    # Net unrealized P/L is separate
    assert pos["net_unrealized_pnl"] == pytest.approx(expected_gross_pnl - expected_exit_fee, abs=1e-3)


def test_signal_prices_never_replace_execution_price(tmp_path):
    """Regression Test 5: Market prices / signals never replace execution price 2.98."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize(seed_doge_position=True)

    # Simulate price fluctuations and multiple signal updates
    for p in [2.95, 2.97, 2.984, 3.05, 3.12]:
        db.update_price("DOGE/THB", p)
        db.save_signal("DOGE/THB", "15m", {
            "signal": "BUY", "score": 80, "regime": "UPTREND",
            "close": p, "atr": 0.03, "stop_loss": p - 0.045, "take_profit": p + 0.045
        })

    portfolio = db.portfolio("MANUAL_REAL")
    pos = portfolio["positions"][0]
    plan = portfolio["position_plan"]

    # Entry price must remain strictly 2.98
    assert pos["average_cost"] == 2.98
    assert plan["entry_price"] == 2.98

    # Strategy and score formatting
    assert plan["strategy"] == "Manual Real Execution"
    assert plan["entry_score"] is None
    assert format_entry_score(plan["entry_score"]) == "N/A / ไม่มีข้อมูล"
    assert to_thai_strategy(plan["strategy"]) == "การซื้อจริงด้วยตนเอง (Manual Real)"


def test_manually_entered_positions_survive_restart_and_migration(tmp_path):
    """Regression Test 6: Manually entered positions do not become PAPER or get overwritten on restart."""
    db_path = str(tmp_path / "test.db")
    db1 = Database(db_path)
    db1.initialize(seed_doge_position=True)

    # Verify initial position
    p1 = db1.portfolio("MANUAL_REAL")
    assert len(p1["positions"]) == 1
    assert p1["positions"][0]["average_cost"] == 2.98

    # Simulate restart by instantiating new Database object and initializing again
    db2 = Database(db_path)
    db2.initialize()

    p2 = db2.portfolio("MANUAL_REAL")
    assert len(p2["positions"]) == 1
    assert p2["positions"][0]["average_cost"] == 2.98
    assert p2["position_plan"]["strategy"] == "Manual Real Execution"
    assert p2["position_plan"]["entry_score"] is None

    # Verify PAPER remains empty
    paper = db2.portfolio("PAPER")
    assert len(paper["positions"]) == 0


def test_legacy_database_migration_from_paper_to_manual_real(tmp_path):
    """Regression Test 7: Migrate an existing SQLite database with legacy PAPER DOGE data."""
    db_path = str(tmp_path / "legacy.db")

    # Construct the exact legacy schema and row found in the old container
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("""
            CREATE TABLE ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                type TEXT NOT NULL,
                pair TEXT,
                asset TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                amount_thb REAL NOT NULL,
                fee_thb REAL NOT NULL DEFAULT 0,
                price_thb REAL,
                reference TEXT,
                note TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE positions (
                mode TEXT NOT NULL,
                asset TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_cost REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(mode, asset)
            );
        """)
        conn.execute("""
            CREATE TABLE position_plans (
                mode TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                strategy TEXT NOT NULL,
                entry_score INTEGER NOT NULL,
                entry_reason TEXT NOT NULL,
                stop_loss REAL NOT NULL,
                effective_stop REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                highest_price REAL NOT NULL,
                trailing_enabled INTEGER NOT NULL DEFAULT 1,
                trailing_activation_percent REAL NOT NULL DEFAULT 2.0,
                trailing_distance_percent REAL NOT NULL DEFAULT 1.0,
                current_action TEXT NOT NULL DEFAULT 'IN_POSITION',
                action_reason TEXT NOT NULL DEFAULT 'Position recorded',
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE prices (pair TEXT PRIMARY KEY, price REAL NOT NULL, updated_at TEXT NOT NULL);
        """)
        conn.execute("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal TEXT NOT NULL,
                score INTEGER NOT NULL,
                regime TEXT NOT NULL,
                details TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, mode TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '');
        """)
        conn.execute("""
            CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, level TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0);
        """)

        # Insert legacy rows exactly as were present in Phase 1
        conn.execute("INSERT INTO positions VALUES ('PAPER', 'DOGE', 1676.44, 2.9822580253394095, 0.0, '2026-09-09T01:57:50.900703+00:00')")
        conn.execute("INSERT INTO position_plans VALUES ('PAPER', 'DOGE', '2026-09-09T01:57:50.900703+00:00', 2.9822580253394095, 'Imported Phase 1 position', 0, 'Existing position migrated', 2.9226, 2.9226, 3.0717, 3.1314, 2.982258, 1, 2.0, 1.0, 'HOLD', 'setup remains valid', '2026-09-09T02:58:39.825171+00:00')")
        conn.execute("INSERT INTO ledger VALUES (1, '2026-09-09T01:54:41.263115+00:00', 'PAPER', 'DEPOSIT', NULL, NULL, 0.0, 5000.0, 0.0, NULL, NULL, '')")
        conn.execute("INSERT INTO ledger VALUES (2, '2026-09-09T01:57:50.900754+00:00', 'PAPER', 'BUY', 'DOGE/THB', 'DOGE', 1676.44, -4999.576644, 12.0, 2.9751, NULL, '')")

    # Run Database.initialize() which must trigger schema upgrade and legacy data migration
    db = Database(db_path)
    db.initialize()

    # Verify PAPER is cleaned up
    paper = db.portfolio("PAPER")
    assert len(paper["positions"]) == 0
    assert paper["position_plan"] is None

    # Verify MANUAL_REAL has the exact corrected position
    real = db.portfolio("MANUAL_REAL")
    assert len(real["positions"]) == 1
    pos = real["positions"][0]
    assert pos["asset"] == "DOGE"
    assert pos["quantity"] == 1676.44
    assert pos["average_cost"] == 2.98

    plan = real["position_plan"]
    assert plan is not None
    assert plan["entry_price"] == 2.98
    assert plan["strategy"] == "Manual Real Execution"
    assert plan["entry_score"] is None
    assert plan["plan_type"] in {"MARKET_DERIVED", "FALLBACK_MANUAL"}
