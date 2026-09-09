import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from app.translations import to_thai_regime, to_thai_status, to_thai_strategy


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
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

CREATE TABLE IF NOT EXISTS positions (
    mode TEXT NOT NULL,
    asset TEXT NOT NULL,
    quantity REAL NOT NULL,
    average_cost REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(mode, asset)
);

CREATE TABLE IF NOT EXISTS position_plans (
    mode TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    strategy TEXT NOT NULL,
    entry_score INTEGER,
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
    plan_type TEXT NOT NULL DEFAULT 'MARKET_DERIVED',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    pair TEXT PRIMARY KEY,
    price REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal TEXT NOT NULL,
    score INTEGER NOT NULL,
    regime TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    mode TEXT NOT NULL,
    cash REAL NOT NULL,
    position_value REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    net_capital_inflow REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(date, mode)
);
"""

DEFAULT_SETTINGS = {
    "price_refresh_seconds": 60,
    "timeframes": ["15m", "1h", "4h"],
    "risk_percent": 2.0,
    "estimated_exit_fee_percent": 0.25,
    "scanner_enabled": True,
    "websocket_enabled": True,
    "realtime_alert_cooldown_seconds": 300,
    "telegram_enabled": True,
    "ai_opinion_enabled": True,
    "ai_opinion_threshold": 75,
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def today_bkk() -> str:
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d")


def normalize_mode(mode: str) -> str:
    m = mode.upper()
    return "MANUAL_REAL" if m in {"REAL", "MANUAL_REAL"} else "PAPER"


class Database:
    def __init__(self, path: str):
        self.path = path
        self.lock = RLock()

    @contextmanager
    def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.lock, sqlite3.connect(self.path, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn
            conn.commit()

    def initialize(self, seed_doge_position: bool = False):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_position_plans_schema(conn)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            self.migrate_legacy_data(conn, force_seed=seed_doge_position)

    def _ensure_position_plans_schema(self, conn: sqlite3.Connection):
        pragma = conn.execute("PRAGMA table_info(position_plans)").fetchall()
        cols = {r["name"]: dict(r) for r in pragma}
        needs_migration = False
        if "plan_type" not in cols:
            needs_migration = True
        elif cols.get("entry_score", {}).get("notnull", 0) == 1:
            needs_migration = True

        if needs_migration:
            conn.execute("ALTER TABLE position_plans RENAME TO position_plans_old")
            conn.execute("""
                CREATE TABLE position_plans (
                    mode TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    strategy TEXT NOT NULL,
                    entry_score INTEGER,
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
                    plan_type TEXT NOT NULL DEFAULT 'MARKET_DERIVED',
                    updated_at TEXT NOT NULL
                );
            """)
            has_old_plan_type = "plan_type" in cols
            plan_expr = "COALESCE(plan_type, 'MARKET_DERIVED')" if has_old_plan_type else "'MARKET_DERIVED'"
            conn.execute(f"""
                INSERT INTO position_plans (
                    mode, asset, entry_time, entry_price, strategy, entry_score, entry_reason,
                    stop_loss, effective_stop, take_profit_1, take_profit_2, highest_price,
                    trailing_enabled, trailing_activation_percent, trailing_distance_percent,
                    current_action, action_reason, plan_type, updated_at
                )
                SELECT
                    mode, asset, entry_time, entry_price, strategy,
                    CASE WHEN entry_score = 0 THEN NULL ELSE entry_score END,
                    entry_reason, stop_loss, effective_stop, take_profit_1, take_profit_2, highest_price,
                    trailing_enabled, trailing_activation_percent, trailing_distance_percent,
                    current_action, action_reason, {plan_expr}, updated_at
                FROM position_plans_old
            """)
            conn.execute("DROP TABLE position_plans_old")

    def migrate_legacy_data(self, conn: sqlite3.Connection, force_seed: bool = False):
        row = conn.execute(
            "SELECT value FROM settings WHERE key='legacy_migration_v2'"
        ).fetchone()
        if row and not force_seed:
            return

        from app.engines import build_exit_plan_from_market

        # Check if legacy PAPER DOGE exists or force_seed is True
        legacy_paper_pos = conn.execute(
            "SELECT * FROM positions WHERE mode='PAPER' AND asset='DOGE'"
        ).fetchone()
        legacy_paper_plan = conn.execute(
            "SELECT * FROM position_plans WHERE mode='PAPER' AND asset='DOGE'"
        ).fetchone()
        has_legacy_doge = bool(legacy_paper_pos or legacy_paper_plan or force_seed)

        # 1. Clean up legacy PAPER data that erroneously held DOGE position
        conn.execute("DELETE FROM positions WHERE mode='PAPER' AND asset='DOGE'")
        conn.execute("DELETE FROM position_plans WHERE mode='PAPER' AND asset='DOGE'")
        conn.execute("DELETE FROM ledger WHERE mode='PAPER' AND (pair='DOGE/THB' OR asset='DOGE' OR amount_thb=5000.0)")

        if has_legacy_doge:
            # 2. Check if MANUAL_REAL has an open DOGE position already
            pos = conn.execute(
                "SELECT * FROM positions WHERE mode='MANUAL_REAL' AND asset='DOGE'"
            ).fetchone()

            if not pos:
                deposit = conn.execute(
                    "SELECT id FROM ledger WHERE mode='MANUAL_REAL' AND type='DEPOSIT'"
                ).fetchone()
                if not deposit:
                    conn.execute(
                        """INSERT INTO ledger(created_at, mode, type, amount_thb, fee_thb, note)
                           VALUES ('2026-09-09T00:00:00+00:00', 'MANUAL_REAL', 'DEPOSIT', 10000.0, 0.0, 'เงินทุนเริ่มต้น (Initial capital)')"""
                    )

                buy = conn.execute(
                    "SELECT id FROM ledger WHERE mode='MANUAL_REAL' AND type='BUY' AND asset='DOGE'"
                ).fetchone()
                if not buy:
                    conn.execute(
                        """INSERT INTO ledger(created_at, mode, type, pair, asset, quantity, amount_thb, fee_thb, price_thb, reference, note)
                           VALUES ('2026-09-09T00:00:00+00:00', 'MANUAL_REAL', 'BUY', 'DOGE/THB', 'DOGE', 1676.44, -4995.7912, 0.0, 2.98, 'UNKNOWN_FEE', 'ซื้อจริงบน Bitkub (Manual Real Execution) - ค่าธรรมเนียมไม่ระบุ')"""
                    )

                conn.execute(
                    """INSERT OR REPLACE INTO positions(mode, asset, quantity, average_cost, realized_pnl, updated_at)
                       VALUES ('MANUAL_REAL', 'DOGE', 1676.44, 2.98, 0.0, '2026-09-09T00:00:00+00:00')"""
                )
            else:
                if abs(float(pos["average_cost"]) - 2.98) > 0.0001 and float(pos["quantity"]) == 1676.44:
                    conn.execute(
                        "UPDATE positions SET average_cost=2.98 WHERE mode='MANUAL_REAL' AND asset='DOGE'"
                    )

            # 3. Dynamic exit plan derived from market context
            signals = self._signals_for_pair(conn, "DOGE/THB")
            price_row = conn.execute("SELECT price FROM prices WHERE pair='DOGE/THB'").fetchone()
            m_price = float(price_row["price"]) if price_row else 2.984

            plan_data = build_exit_plan_from_market(
                pair="DOGE/THB",
                entry_price=2.98,
                current_price=m_price,
                signals=signals,
                strategy="Manual Real Execution",
                entry_score=None,
                entry_reason="การซื้อจริงบน Bitkub (ต้นทุน 2.98 บาท)",
            )

            conn.execute(
                """INSERT OR REPLACE INTO position_plans(
                       mode, asset, entry_time, entry_price, strategy, entry_score, entry_reason,
                       stop_loss, effective_stop, take_profit_1, take_profit_2, highest_price,
                       trailing_enabled, trailing_activation_percent, trailing_distance_percent,
                       current_action, action_reason, plan_type, updated_at
                   ) VALUES (
                       'MANUAL_REAL', 'DOGE', '2026-09-09T00:00:00+00:00', 2.98, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, 'HOLD', ?, ?, '2026-09-09T00:00:00+00:00'
                   )""",
                (
                    plan_data["strategy"],
                    plan_data["entry_score"],
                    plan_data["entry_reason"],
                    plan_data["stop_loss"],
                    plan_data["effective_stop"],
                    plan_data["take_profit_1"],
                    plan_data["take_profit_2"],
                    plan_data["highest_price"],
                    plan_data["trailing_enabled"],
                    plan_data["trailing_activation_percent"],
                    plan_data["trailing_distance_percent"],
                    plan_data["action_reason"],
                    plan_data["plan_type"],
                ),
            )

            conn.execute(
                """INSERT OR IGNORE INTO prices(pair, price, updated_at)
                   VALUES ('DOGE/THB', 2.984, '2026-09-09T00:00:00+00:00')"""
            )

        # Mark migration done
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('legacy_migration_v2', ?)",
            (json.dumps("done"),),
        )

    def seed_doge_position(self):
        with self.connect() as conn:
            self.migrate_legacy_data(conn, force_seed=True)

    def settings(self) -> dict:
        with self.connect() as conn:
            return {
                row["key"]: json.loads(row["value"])
                for row in conn.execute("SELECT * FROM settings")
            }

    def update_settings(self, values: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        with self.connect() as conn:
            for key, value in values.items():
                if key in allowed:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )
        return self.settings()

    def cash(self, mode: str) -> float:
        norm_mode = normalize_mode(mode)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_thb), 0) total FROM ledger WHERE mode=?",
                (norm_mode,),
            ).fetchone()
            return float(row["total"])

    def cash_flow(self, mode: str) -> dict:
        norm_mode = normalize_mode(mode)
        with self.connect() as conn:
            deposits = conn.execute(
                "SELECT COALESCE(SUM(amount_thb), 0) v FROM ledger WHERE mode=? AND type='DEPOSIT'",
                (norm_mode,),
            ).fetchone()["v"]
            withdrawals = -conn.execute(
                "SELECT COALESCE(SUM(amount_thb), 0) v FROM ledger WHERE mode=? AND type='WITHDRAWAL'",
                (norm_mode,),
            ).fetchone()["v"]
            fees = conn.execute(
                "SELECT COALESCE(SUM(fee_thb), 0) v FROM ledger WHERE mode=?",
                (norm_mode,),
            ).fetchone()["v"]
        return {
            "deposits": float(deposits),
            "withdrawals": float(withdrawals),
            "fees": float(fees),
            "net_capital_inflow": float(deposits - withdrawals),
        }

    def add_cash(self, mode: str, kind: str, amount: float, note: str = "") -> int:
        norm_mode = normalize_mode(mode)
        kind = kind.upper()
        if kind not in {"DEPOSIT", "WITHDRAWAL", "ADJUSTMENT", "FEE"} or amount <= 0:
            raise ValueError("A positive cash amount is required")

        if kind == "WITHDRAWAL":
            current_cash = self.cash(norm_mode)
            if amount > current_cash:
                raise ValueError(
                    f"Withdrawal ({amount:,.2f} THB) exceeds available cash ({current_cash:,.2f} THB)"
                )
            signed = -amount
        elif kind in {"DEPOSIT", "ADJUSTMENT"}:
            signed = amount
        else:  # FEE
            signed = -amount

        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO ledger(created_at, mode, type, amount_thb, fee_thb, note) VALUES (?, ?, ?, ?, ?, ?)",
                (now(), norm_mode, kind, signed, amount if kind == "FEE" else 0.0, note),
            )
            return cur.lastrowid

    def trade(
        self,
        mode: str,
        side: str,
        pair: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        note: str = "",
        executed_at: str | None = None,
        reason: str = "MANUAL_EXECUTION",
    ) -> int:
        norm_mode = normalize_mode(mode)
        side = side.upper()
        if side not in {"BUY", "SELL"} or quantity <= 0 or price <= 0 or fee < 0:
            raise ValueError("Invalid trade parameters")

        asset = pair.split("/")[0].upper()
        execution_time = executed_at or now()
        gross = quantity * price

        with self.connect() as conn:
            pos = conn.execute(
                "SELECT * FROM positions WHERE mode=? AND asset=?",
                (norm_mode, asset),
            ).fetchone()
            old_qty = float(pos["quantity"]) if pos else 0.0
            old_avg = float(pos["average_cost"]) if pos else 0.0
            realized = float(pos["realized_pnl"]) if pos else 0.0

            if side == "BUY":
                # Rule 1: One open position at a time
                other = conn.execute(
                    "SELECT asset FROM positions WHERE mode=? AND quantity>0 AND asset<>?",
                    (norm_mode, asset),
                ).fetchone()
                if other:
                    raise ValueError(
                        f"One-position-at-a-time rule: close {other['asset']}/THB first"
                    )

                # Rule 2: No averaging down
                if old_qty > 0:
                    raise ValueError(
                        f"No averaging down: position already exists for {asset}"
                    )

                required = gross + fee
                current_cash = self.cash(norm_mode)
                if required > current_cash:
                    raise ValueError(
                        f"Trade exceeds available cash (need {required:,.2f} THB, have {current_cash:,.2f} THB)"
                    )

                new_qty = quantity
                new_avg = price if fee == 0.0 else (gross + fee) / new_qty
                cash_delta = -required

            else:  # SELL
                if quantity > old_qty:
                    raise ValueError(
                        f"Sell quantity ({quantity}) exceeds open position ({old_qty})"
                    )

                new_qty = old_qty - quantity
                new_avg = old_avg if new_qty > 0 else 0.0
                trade_realized = (price - old_avg) * quantity - fee
                realized += trade_realized
                cash_delta = gross - fee

            # Update positions
            conn.execute(
                """INSERT OR REPLACE INTO positions(mode, asset, quantity, average_cost, realized_pnl, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (norm_mode, asset, new_qty, new_avg, realized, now()),
            )

            # Insert ledger entry
            cur = conn.execute(
                """INSERT INTO ledger(created_at, mode, type, pair, asset, quantity, amount_thb, fee_thb, price_thb, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (execution_time, norm_mode, side, pair, asset, quantity, cash_delta, fee, price, note),
            )

            # Manage Position Plan
            if side == "BUY":
                signals = self._signals_for_pair(conn, pair)
                from app.engines import EntryEngine, build_exit_plan_from_market
                if norm_mode == "MANUAL_REAL":
                    plan_data = build_exit_plan_from_market(
                        pair=pair,
                        entry_price=price,
                        current_price=price,
                        signals=signals,
                        strategy="Manual Real Execution",
                        entry_score=None,
                        entry_reason=note or f"บันทึกการเข้าซื้อจริง @ {price:,.2f} THB",
                    )
                else:
                    candidate = EntryEngine().rank(pair, signals, price)
                    plan_data = {
                        "strategy": candidate.strategy,
                        "entry_score": candidate.score,
                        "entry_reason": candidate.reason,
                        "stop_loss": candidate.stop_loss,
                        "effective_stop": candidate.stop_loss,
                        "take_profit_1": candidate.take_profit_1,
                        "take_profit_2": candidate.take_profit_2,
                        "highest_price": price,
                        "trailing_enabled": 1,
                        "trailing_activation_percent": 2.0,
                        "trailing_distance_percent": 1.0,
                        "current_action": "HOLD",
                        "action_reason": f"บันทึกการเข้าซื้อจำลอง @ {price:,.2f} THB",
                        "plan_type": "MARKET_DERIVED",
                    }

                conn.execute(
                    """INSERT OR REPLACE INTO position_plans(
                           mode, asset, entry_time, entry_price, strategy, entry_score, entry_reason,
                           stop_loss, effective_stop, take_profit_1, take_profit_2, highest_price,
                           trailing_enabled, trailing_activation_percent, trailing_distance_percent,
                           current_action, action_reason, plan_type, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        norm_mode, asset, execution_time, price,
                        plan_data["strategy"], plan_data["entry_score"], plan_data["entry_reason"],
                        plan_data["stop_loss"], plan_data["effective_stop"],
                        plan_data["take_profit_1"], plan_data["take_profit_2"],
                        plan_data["highest_price"], plan_data["trailing_enabled"],
                        plan_data["trailing_activation_percent"], plan_data["trailing_distance_percent"],
                        plan_data["current_action"], plan_data["action_reason"],
                        plan_data["plan_type"], now()
                    ),
                )
            elif side == "SELL" and new_qty == 0:
                conn.execute(
                    "DELETE FROM position_plans WHERE mode=?",
                    (norm_mode,),
                )

            return cur.lastrowid

    def _signals_for_pair(self, conn, pair: str) -> list[dict]:
        rows = conn.execute(
            """SELECT s.* FROM signals s
               WHERE s.pair=? AND s.id=(
                   SELECT MAX(s2.id) FROM signals s2
                   WHERE s2.pair=s.pair AND s2.timeframe=s.timeframe
               ) ORDER BY CASE s.timeframe WHEN '15m' THEN 1 WHEN '1h' THEN 2 ELSE 3 END""",
            (pair,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"])
            result.append(d)
        return result

    def portfolio(self, mode: str) -> dict:
        norm_mode = normalize_mode(mode)
        cash = self.cash(norm_mode)
        flow = self.cash_flow(norm_mode)
        exit_fee_percent = float(self.settings().get("estimated_exit_fee_percent", 0.25))

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT p.*,
                          COALESCE(px.price, p.average_cost) market_price,
                          COALESCE(px.updated_at, p.updated_at) market_updated_at
                   FROM positions p
                   LEFT JOIN prices px ON px.pair = p.asset || '/THB'
                   WHERE p.mode=? AND p.quantity > 0""",
                (norm_mode,),
            ).fetchall()
            realized = float(
                conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl), 0) v FROM positions WHERE mode=?",
                    (norm_mode,),
                ).fetchone()["v"]
            )
            plan_row = conn.execute(
                "SELECT * FROM position_plans WHERE mode=?",
                (norm_mode,),
            ).fetchone()

        positions = []
        for r in rows:
            pos_dict = dict(r)
            m_price = float(pos_dict["market_price"])
            qty = float(pos_dict["quantity"])
            cost = float(pos_dict["average_cost"])
            m_val = qty * m_price
            est_fee = m_val * (exit_fee_percent / 100.0)
            gross_unreal = qty * (m_price - cost)
            gross_unreal_pct = ((m_price - cost) / cost * 100.0) if cost > 0 else 0.0

            pos_dict.update({
                "market_value": round(m_val, 4),
                "gross_unrealized_pnl": round(gross_unreal, 4),
                "gross_unrealized_pnl_percent": round(gross_unreal_pct, 2),
                "estimated_exit_fee": round(est_fee, 4),
                "unrealized_pnl": round(gross_unreal, 4),
                "unrealized_pnl_percent": round(gross_unreal_pct, 2),
                "net_unrealized_pnl": round(gross_unreal - est_fee, 4),
            })
            positions.append(pos_dict)

        market_value = sum(p["market_value"] for p in positions)
        gross_unrealized = sum(p["gross_unrealized_pnl"] for p in positions)
        total_estimated_exit_fee = sum(p["estimated_exit_fee"] for p in positions)
        equity = cash + market_value
        trading_pnl = equity - flow["net_capital_inflow"]

        return {
            "mode": norm_mode,
            "cash": round(cash, 4),
            "positions": positions,
            "position_plan": dict(plan_row) if plan_row else None,
            "market_value": round(market_value, 4),
            "equity": round(equity, 4),
            "realized_pnl": round(realized, 4),
            "gross_unrealized_pnl": round(gross_unrealized, 4),
            "unrealized_pnl": round(gross_unrealized, 4),
            "estimated_exit_fee": round(total_estimated_exit_fee, 4),
            "net_unrealized_pnl": round(gross_unrealized - total_estimated_exit_fee, 4),
            "total_pnl": round(realized + gross_unrealized, 4),
            "trading_pnl": round(trading_pnl, 4),
            "has_open_position": len(positions) > 0,
            **flow,
        }

    def daily_consecutive_losses(self, mode: str, date_str: str | None = None) -> int:
        norm_mode = normalize_mode(mode)
        target_date = date_str or today_bkk()
        with self.connect() as conn:
            # Query recent SELL transactions today
            rows = conn.execute(
                """SELECT l.*, p.average_cost
                   FROM ledger l
                   LEFT JOIN positions p ON p.mode = l.mode AND p.asset = l.asset
                   WHERE l.mode=? AND l.type='SELL' AND l.created_at LIKE ?
                   ORDER BY l.id DESC LIMIT 10""",
                (norm_mode, f"{target_date}%"),
            ).fetchall()

        consecutive = 0
        for r in rows:
            # Trade amount_thb for SELL is positive (gross - fee)
            # Compare price_thb with position or check profit
            price = float(r["price_thb"] or 0)
            avg_cost = float(r["average_cost"] or 0)
            if price > 0 and avg_cost > 0 and price < avg_cost:
                consecutive += 1
            elif price > 0 and avg_cost > 0 and price >= avg_cost:
                break
        return consecutive

    def save_signal(self, pair: str, timeframe: str, result: dict):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO prices(pair, price, updated_at) VALUES (?, ?, ?)",
                (pair, result["close"], now()),
            )
            conn.execute(
                """INSERT INTO signals(created_at, pair, timeframe, signal, score, regime, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now(), pair, timeframe, result["signal"], result["score"], result["regime"], json.dumps(result)),
            )

    def update_price(self, pair: str, price: float):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO prices(pair, price, updated_at) VALUES (?, ?, ?)",
                (pair, price, now()),
            )

    def realtime_context(self, pair: str) -> dict:
        asset = pair.split("/")[0].upper()
        with self.connect() as conn:
            signals = self._signals_for_pair(conn, pair)
            btc_signals = self._signals_for_pair(conn, "BTC/THB") if pair != "BTC/THB" else signals
            positions = conn.execute(
                "SELECT * FROM positions WHERE asset=? AND quantity>0",
                (asset,),
            ).fetchall()
            plans = conn.execute(
                "SELECT * FROM position_plans WHERE asset=?",
                (asset,),
            ).fetchall()
        return {
            "signals": signals,
            "btc_signals": btc_signals,
            "positions": [dict(row) for row in positions],
            "plans": [dict(row) for row in plans],
        }

    def update_position_plan(self, mode: str, action: str, reason: str, price: float, effective_stop: float):
        norm_mode = normalize_mode(mode)
        with self.connect() as conn:
            conn.execute(
                """UPDATE position_plans
                   SET current_action=?, action_reason=?,
                       highest_price=MAX(highest_price, ?),
                       effective_stop=MAX(effective_stop, ?),
                       updated_at=?
                   WHERE mode=?""",
                (action, reason, price, effective_stop, now(), norm_mode),
            )

    def scanner_snapshot(self) -> tuple[list[dict], dict | None]:
        from app.engines import EntryEngine, candidate_dict
        entry_engine = EntryEngine()
        all_pairs = ["BTC/THB", "ETH/THB", "SOL/THB", "XRP/THB", "DOGE/THB"]
        with self.connect() as conn:
            open_count = conn.execute(
                "SELECT COUNT(*) v FROM positions WHERE quantity>0"
            ).fetchone()["v"]
            real_losses = self.daily_consecutive_losses("MANUAL_REAL")

            result = []
            for pair in all_pairs:
                signals = self._signals_for_pair(conn, pair)
                row = conn.execute("SELECT price, updated_at FROM prices WHERE pair=?", (pair,)).fetchone()
                current_price = float(row["price"]) if row else 0.0
                updated_at = row["updated_at"] if row else now()
                
                candidate = entry_engine.rank(
                    pair=pair,
                    signals=signals,
                    current_price=current_price,
                    consecutive_losses=real_losses,
                    has_open_position=bool(open_count > 0),
                )
                item = candidate_dict(candidate)
                item["timestamp"] = updated_at
                result.append(item)

        result.sort(key=lambda x: x["score"], reverse=True)
        best = next((x for x in result if x["status"] == "BUY_NOW"), None) if open_count == 0 else None
        return result, best

    def recent(self, table: str, limit: int = 50) -> list[dict]:
        if table not in {"ledger", "signals", "journal", "notifications", "daily_snapshots"}:
            raise ValueError(f"Unsupported table: {table}")
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def journal(self, mode: str, title: str, body: str, tags: str = ""):
        norm_mode = normalize_mode(mode)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO journal(created_at, mode, title, body, tags) VALUES (?, ?, ?, ?, ?)",
                (now(), norm_mode, title, body, tags),
            )

    def notify(self, level: str, title: str, message: str) -> int | None:
        with self.connect() as conn:
            # 5-minute cooldown for identical notification title
            recent_alert = conn.execute(
                "SELECT created_at FROM notifications WHERE title=? ORDER BY id DESC LIMIT 1",
                (title,),
            ).fetchone()
            if recent_alert:
                delta = (datetime.now(UTC) - datetime.fromisoformat(recent_alert["created_at"])).total_seconds()
                if delta < 300:
                    return None

            cur = conn.execute(
                "INSERT INTO notifications(created_at, level, title, message, is_read) VALUES (?, ?, ?, ?, 0)",
                (now(), level, title, message),
            )
            return cur.lastrowid

    def mark_notification_read(self, notification_id: int) -> bool:
        with self.connect() as conn:
            conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
            return True

    def mark_all_notifications_read(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
            return cur.rowcount

    def unread_notifications_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) v FROM notifications WHERE is_read=0").fetchone()
            return int(row["v"])

    def notifications_after(self, last_id: int) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM notifications WHERE id>? ORDER BY id",
                    (last_id,),
                )
            ]

    def create_daily_snapshot(self, mode: str, snapshot_date: str | None = None) -> dict:
        norm_mode = normalize_mode(mode)
        date_key = snapshot_date or today_bkk()
        p = self.portfolio(norm_mode)
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_snapshots(
                       date, mode, cash, position_value, equity, realized_pnl, unrealized_pnl, net_capital_inflow, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    date_key, norm_mode, p["cash"], p["market_value"],
                    p["equity"], p["realized_pnl"], p["unrealized_pnl"],
                    p["net_capital_inflow"], now()
                ),
            )
        return {
            "date": date_key,
            "mode": norm_mode,
            "cash": p["cash"],
            "position_value": p["market_value"],
            "equity": p["equity"],
            "realized_pnl": p["realized_pnl"],
            "unrealized_pnl": p["unrealized_pnl"],
            "net_capital_inflow": p["net_capital_inflow"],
        }
