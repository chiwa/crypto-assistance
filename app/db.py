import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL, type TEXT NOT NULL,
 pair TEXT, asset TEXT, quantity REAL NOT NULL DEFAULT 0, amount_thb REAL NOT NULL,
 fee_thb REAL NOT NULL DEFAULT 0, price_thb REAL, reference TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS positions (
 mode TEXT NOT NULL, asset TEXT NOT NULL, quantity REAL NOT NULL, average_cost REAL NOT NULL,
 realized_pnl REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY(mode, asset)
);
CREATE TABLE IF NOT EXISTS prices (pair TEXT PRIMARY KEY, price REAL NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, pair TEXT NOT NULL, timeframe TEXT NOT NULL,
 signal TEXT NOT NULL, score INTEGER NOT NULL, regime TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, tags TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, level TEXT NOT NULL, title TEXT NOT NULL,
 message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS position_plans (
 mode TEXT PRIMARY KEY, asset TEXT NOT NULL, entry_time TEXT NOT NULL, entry_price REAL NOT NULL,
 strategy TEXT NOT NULL, entry_score INTEGER NOT NULL, entry_reason TEXT NOT NULL,
 stop_loss REAL NOT NULL, effective_stop REAL NOT NULL, take_profit_1 REAL NOT NULL, take_profit_2 REAL NOT NULL,
 highest_price REAL NOT NULL, trailing_enabled INTEGER NOT NULL DEFAULT 1,
 trailing_activation_percent REAL NOT NULL DEFAULT 2, trailing_distance_percent REAL NOT NULL DEFAULT 1,
 current_action TEXT NOT NULL DEFAULT 'IN_POSITION', action_reason TEXT NOT NULL DEFAULT 'Position recorded', updated_at TEXT NOT NULL
);
"""
DEFAULT_SETTINGS = {"price_refresh_seconds": 60, "timeframes": ["15m", "1h", "4h"], "risk_percent": 1.0, "estimated_exit_fee_percent": 0.25, "scanner_enabled": True, "websocket_enabled": True, "realtime_alert_cooldown_seconds": 300}


def now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        self.lock = RLock()

    @contextmanager
    def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.lock, sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()

    def initialize(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, json.dumps(value)))
            conn.execute("""INSERT OR IGNORE INTO position_plans(mode,asset,entry_time,entry_price,strategy,entry_score,entry_reason,stop_loss,effective_stop,take_profit_1,take_profit_2,highest_price,current_action,action_reason,updated_at)
                SELECT mode,asset,updated_at,average_cost,'Imported Phase 1 position',0,'Existing position migrated',average_cost*.98,average_cost*.98,average_cost*1.03,average_cost*1.05,average_cost,'IN_POSITION','Monitoring migrated position',? FROM positions WHERE quantity>0""",(now(),))

    def settings(self) -> dict:
        with self.connect() as conn:
            return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT * FROM settings")}

    def update_settings(self, values: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        with self.connect() as conn:
            for key, value in values.items():
                if key in allowed:
                    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, json.dumps(value)))
        return self.settings()

    def cash(self, mode: str) -> float:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(amount_thb),0) total FROM ledger WHERE mode=?", (mode,)).fetchone()
            return float(row["total"])

    def cash_flow(self, mode: str) -> dict:
        with self.connect() as conn:
            deposits = conn.execute("SELECT COALESCE(SUM(amount_thb),0) v FROM ledger WHERE mode=? AND type='DEPOSIT'", (mode,)).fetchone()["v"]
            withdrawals = -conn.execute("SELECT COALESCE(SUM(amount_thb),0) v FROM ledger WHERE mode=? AND type='WITHDRAWAL'", (mode,)).fetchone()["v"]
        return {"deposits": deposits, "withdrawals": withdrawals, "net_capital_inflow": deposits - withdrawals}

    def add_cash(self, mode: str, kind: str, amount: float, note: str = "") -> int:
        if kind not in {"DEPOSIT", "WITHDRAWAL"} or amount <= 0:
            raise ValueError("A positive deposit or withdrawal is required")
        signed = amount if kind == "DEPOSIT" else -amount
        if kind == "WITHDRAWAL" and amount > self.cash(mode):
            raise ValueError("Withdrawal exceeds available cash")
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO ledger(created_at,mode,type,amount_thb,note) VALUES (?,?,?,?,?)", (now(), mode, kind, signed, note))
            return cur.lastrowid

    def trade(self, mode: str, side: str, pair: str, quantity: float, price: float, fee: float = 0, note: str = "", executed_at: str | None = None) -> int:
        if side not in {"BUY", "SELL"} or quantity <= 0 or price <= 0 or fee < 0:
            raise ValueError("Invalid trade")
        asset = pair.split("/")[0]
        execution_time = executed_at or now()
        with self.connect() as conn:
            pos = conn.execute("SELECT * FROM positions WHERE mode=? AND asset=?", (mode, asset)).fetchone()
            other = conn.execute("SELECT asset FROM positions WHERE mode=? AND quantity>0 AND asset<>?", (mode, asset)).fetchone()
            if side == "BUY" and other:
                raise ValueError(f"One-position-at-a-time: close {other['asset']}/THB first")
            old_qty, old_avg, realized = (pos["quantity"], pos["average_cost"], pos["realized_pnl"]) if pos else (0.0, 0.0, 0.0)
            gross = quantity * price
            if side == "BUY":
                required = gross + fee
                if required > self.cash(mode):
                    raise ValueError("Trade exceeds available cash")
                new_qty = old_qty + quantity
                new_avg = (old_qty * old_avg + gross + fee) / new_qty
                cash_delta = -required
            else:
                if quantity > old_qty:
                    raise ValueError("Sell quantity exceeds open position")
                new_qty, new_avg = old_qty - quantity, old_avg
                realized += (price - old_avg) * quantity - fee
                cash_delta = gross - fee
                if new_qty == 0:
                    new_avg = 0.0
            conn.execute("INSERT OR REPLACE INTO positions(mode,asset,quantity,average_cost,realized_pnl,updated_at) VALUES (?,?,?,?,?,?)", (mode, asset, new_qty, new_avg, realized, now()))
            cur = conn.execute("INSERT INTO ledger(created_at,mode,type,pair,asset,quantity,amount_thb,fee_thb,price_thb,note) VALUES (?,?,?,?,?,?,?,?,?,?)", (execution_time, mode, side, pair, asset, quantity, cash_delta, fee, price, note))
            if side == "BUY" and old_qty == 0:
                signals = self._signals_for_pair(conn, pair)
                from app.engines import EntryEngine
                candidate = EntryEngine().rank(pair, signals, price)
                conn.execute("INSERT OR REPLACE INTO position_plans(mode,asset,entry_time,entry_price,strategy,entry_score,entry_reason,stop_loss,effective_stop,take_profit_1,take_profit_2,highest_price,current_action,action_reason,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (mode,asset,execution_time,price,candidate.strategy,candidate.score,candidate.reason,candidate.stop_loss,candidate.stop_loss,candidate.take_profit_1,candidate.take_profit_2,price,"IN_POSITION","Actual execution recorded",now()))
            elif side == "SELL" and new_qty == 0:
                conn.execute("DELETE FROM position_plans WHERE mode=?", (mode,))
            return cur.lastrowid

    def _signals_for_pair(self, conn, pair: str) -> list[dict]:
        rows=conn.execute("SELECT s.* FROM signals s WHERE s.pair=? AND s.id=(SELECT MAX(s2.id) FROM signals s2 WHERE s2.pair=s.pair AND s2.timeframe=s.timeframe)",(pair,)).fetchall()
        return [dict(r) | {"details":json.loads(r["details"])} for r in rows]

    def portfolio(self, mode: str) -> dict:
        cash = self.cash(mode)
        flow = self.cash_flow(mode)
        with self.connect() as conn:
            rows = conn.execute("SELECT p.*, COALESCE(px.price,p.average_cost) market_price, COALESCE(px.updated_at,p.updated_at) market_updated_at FROM positions p LEFT JOIN prices px ON px.pair=p.asset||'/THB' WHERE p.mode=? AND p.quantity>0", (mode,)).fetchall()
            realized = float(conn.execute("SELECT COALESCE(SUM(realized_pnl),0) v FROM positions WHERE mode=?", (mode,)).fetchone()["v"])
        exit_fee_percent=float(self.settings().get("estimated_exit_fee_percent",0))
        positions = [dict(r) | {"market_value": r["quantity"] * r["market_price"], "estimated_exit_fee":r["quantity"]*r["market_price"]*exit_fee_percent/100, "unrealized_pnl": r["quantity"] * (r["market_price"] - r["average_cost"])-r["quantity"]*r["market_price"]*exit_fee_percent/100} for r in rows]
        market_value = sum(p["market_value"] for p in positions)
        unrealized = sum(p["unrealized_pnl"] for p in positions)
        equity = cash + market_value
        with self.connect() as conn:
            plan=conn.execute("SELECT * FROM position_plans WHERE mode=?",(mode,)).fetchone()
        return {"mode": mode, "cash": cash, "positions": positions, "position_plan":dict(plan) if plan else None, "market_value": market_value, "equity": equity, "realized_pnl": realized, "unrealized_pnl": unrealized, "trading_pnl": equity - flow["net_capital_inflow"], **flow}

    def save_signal(self, pair: str, timeframe: str, result: dict):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO prices(pair,price,updated_at) VALUES (?,?,?)", (pair, result["close"], now()))
            conn.execute("INSERT INTO signals(created_at,pair,timeframe,signal,score,regime,details) VALUES (?,?,?,?,?,?,?)", (now(), pair, timeframe, result["signal"], result["score"], result["regime"], json.dumps(result)))

    def update_price(self, pair: str, price: float):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO prices(pair,price,updated_at) VALUES (?,?,?)", (pair, price, now()))

    def realtime_context(self, pair: str) -> dict:
        asset = pair.split("/")[0]
        with self.connect() as conn:
            signals = conn.execute(
                "SELECT s.* FROM signals s WHERE s.pair=? AND s.id=(SELECT MAX(s2.id) FROM signals s2 WHERE s2.pair=s.pair AND s2.timeframe=s.timeframe) ORDER BY CASE s.timeframe WHEN '15m' THEN 1 WHEN '1h' THEN 2 ELSE 3 END",
                (pair,),
            ).fetchall()
            positions = conn.execute("SELECT * FROM positions WHERE asset=? AND quantity>0", (asset,)).fetchall()
            plans = conn.execute("SELECT * FROM position_plans WHERE asset=?", (asset,)).fetchall()
        parsed_signals = []
        for signal in signals:
            parsed = dict(signal)
            parsed["details"] = json.loads(parsed["details"])
            parsed_signals.append(parsed)
        return {"signals": parsed_signals, "positions": [dict(row) for row in positions], "plans": [dict(row) for row in plans]}

    def update_position_plan(self, mode: str, action: str, reason: str, price: float, effective_stop: float):
        with self.connect() as conn:
            conn.execute("UPDATE position_plans SET current_action=?,action_reason=?,highest_price=MAX(highest_price,?),effective_stop=MAX(effective_stop,?),updated_at=? WHERE mode=?",(action,reason,price,effective_stop,now(),mode))

    def scanner_snapshot(self) -> list[dict]:
        from app.engines import EntryEngine, candidate_dict
        with self.connect() as conn:
            pairs=[r["pair"] for r in conn.execute("SELECT DISTINCT pair FROM signals")]
            open_count=conn.execute("SELECT COUNT(*) v FROM positions WHERE quantity>0").fetchone()["v"]
            result=[]
            for pair in pairs:
                signals=self._signals_for_pair(conn,pair)
                row=conn.execute("SELECT price,updated_at FROM prices WHERE pair=?",(pair,)).fetchone()
                if row:
                    item=candidate_dict(EntryEngine().rank(pair,signals,row["price"])); item["timestamp"]=row["updated_at"]
                    if open_count: item["status"]="WAIT"; item["reason"]="one-position-at-a-time rule"
                    result.append(item)
        result.sort(key=lambda x:x["score"],reverse=True)
        best=next((x for x in result if x["status"]=="BUY_NOW"),None) if not open_count else None
        return result, best

    def recent(self, table: str, limit: int = 50) -> list[dict]:
        if table not in {"ledger", "signals", "journal", "notifications"}:
            raise ValueError("Unsupported table")
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,))]

    def journal(self, mode: str, title: str, body: str, tags: str = ""):
        with self.connect() as conn:
            conn.execute("INSERT INTO journal(created_at,mode,title,body,tags) VALUES (?,?,?,?,?)", (now(), mode, title, body, tags))

    def notify(self, level: str, title: str, message: str):
        with self.connect() as conn:
            recent=conn.execute("SELECT created_at FROM notifications WHERE title=? ORDER BY id DESC LIMIT 1",(title,)).fetchone()
            if recent and (datetime.now(UTC)-datetime.fromisoformat(recent["created_at"])).total_seconds()<300:
                return None
            cur = conn.execute("INSERT INTO notifications(created_at,level,title,message) VALUES (?,?,?,?)", (now(), level, title, message))
            return cur.lastrowid

    def notifications_after(self, last_id: int) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM notifications WHERE id>? ORDER BY id", (last_id,))]
