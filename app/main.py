from contextlib import asynccontextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.backtest import replay
from app.config import settings
from app.db import Database, normalize_mode
from app.integrations import DeepSeekSecondOpinion, TelegramNotifier
from app.market import BitkubMarketData
from app.realtime import RealtimeMonitor
from app.scanner import Scanner
from app.schemas import (
    AskDeepSeekRequest,
    CashRequest,
    JournalRequest,
    SecondOpinionRequest,
    SettingsRequest,
    TradeRequest,
)

db = Database(settings.database_path)
market = BitkubMarketData(settings.bitkub_base_url)
scanner = Scanner(db, market)
deepseek = DeepSeekSecondOpinion(settings.deep_seek_api_key)
telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
realtime = RealtimeMonitor(db, telegram.send if telegram.configured else None)
scheduler = AsyncIOScheduler()


async def lifecycle_notification(kind: str, message: str):
    db.notify("INFO", kind, message)
    if telegram.configured:
        try:
            await telegram.send(f"ℹ️ {kind}\n{message}")
        except Exception as exc:
            db.notify("WARNING", "Telegram delivery failed", str(exc))


def schedule_scanner():
    interval = db.settings()["price_refresh_seconds"]
    scheduler.add_job(
        scanner.run,
        "interval",
        seconds=interval,
        id="market-scanner",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def take_daily_snapshots():
    db.create_daily_snapshot("MANUAL_REAL")
    db.create_daily_snapshot("PAPER")


async def market_data_watchdog():
    import time
    if not realtime.connected or (realtime.last_event_at and time.time() - realtime.last_event_at > 120):
        db.notify("WARNING", "SYSTEM_WARNING", "การเชื่อมต่อ Bitkub WebSocket ขัดข้องหรือข้อมูลไม่อัปเดต; ระบบใช้ REST Scanner สำรองข้อมูลต่อเนื่อง")


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio
    global scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    scheduler = AsyncIOScheduler()
    db.initialize(seed_doge_position=True)
    # Take initial daily snapshot if needed
    take_daily_snapshots()
    schedule_scanner()
    scheduler.start()
    scheduler.add_job(
        market_data_watchdog,
        "interval",
        seconds=30,
        id="market-watchdog",
        replace_existing=True,
    )
    scheduler.add_job(
        take_daily_snapshots,
        "cron",
        hour=0,
        minute=0,
        timezone="Asia/Bangkok",
        id="daily-snapshots-job",
        replace_existing=True,
    )
    realtime_task = asyncio.create_task(realtime.run_forever(), name="bitkub-realtime-monitor")
    await lifecycle_notification(
        "APP_STARTED",
        "Crypto Assistance เริ่มต้นระบบสมบูรณ์: เปิดใช้งาน REST Scanner และการตรวจจับ WebSocket ตามการตั้งค่าปัจจุบัน",
    )
    yield
    await lifecycle_notification("APP_STOPPING", "Crypto Assistance กำลังปิดระบบอย่างปลอดภัย")
    realtime.stop()
    realtime_task.cancel()
    try:
        await realtime_task
    except asyncio.CancelledError:
        pass
    scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="1.5.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def dashboard_view():
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "live_trading": False,
        "bitkub_websocket": {
            "connected": realtime.connected,
            "last_event_at": realtime.last_event_at,
        },
    }


@app.get("/api/dashboard")
def dashboard_data():
    candidates, best = db.scanner_snapshot()
    real_port = db.portfolio("MANUAL_REAL")
    paper_port = db.portfolio("PAPER")
    return {
        "paper": paper_port,
        "real": real_port,
        "manual_real": real_port,
        "settings": db.settings(),
        "candidates": candidates,
        "best_candidate": best,
        "signals": db.recent("signals", 30),
        "ledger": db.recent("ledger", 30),
        "journal": db.recent("journal", 30),
        "notifications": db.recent("notifications", 30),
        "unread_notifications_count": db.unread_notifications_count(),
        "snapshots": db.recent("daily_snapshots", 30),
        "integrations": {
            "deepseek": deepseek.configured,
            "telegram": telegram.configured,
        },
    }


@app.get("/api/notifications")
def get_notifications(unread_only: bool = False, limit: int = 50):
    notes = db.recent("notifications", limit)
    if unread_only:
        notes = [n for n in notes if not n.get("is_read")]
    return {"notifications": notes, "unread_count": db.unread_notifications_count()}


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int):
    db.mark_notification_read(notification_id)
    return {"status": "ok", "unread_count": db.unread_notifications_count()}


@app.post("/api/notifications/read-all")
def mark_all_notifications_read():
    count = db.mark_all_notifications_read()
    return {"status": "ok", "marked_count": count, "unread_count": 0}


@app.post("/api/snapshots/today")
def trigger_snapshot():
    real_snap = db.create_daily_snapshot("MANUAL_REAL")
    paper_snap = db.create_daily_snapshot("PAPER")
    return {"status": "ok", "manual_real": real_snap, "paper": paper_snap}


@app.get("/api/backtest/{pair}/{timeframe}")
async def backtest_endpoint(pair: str, timeframe: str, fee_percent: float = 0.25, slippage_percent: float = 0.1):
    pair = pair.upper().replace("-", "/")
    try:
        candles = await market.candles(pair, timeframe, 500)
        return replay(pair, candles["highs"], candles["lows"], candles["closes"], fee_percent, slippage_percent)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/notifications/stream")
async def notification_stream():
    import asyncio
    import json

    async def events():
        last_id = max((row["id"] for row in db.recent("notifications", 1)), default=0)
        yield ": connected\n\n"
        while True:
            rows = db.notifications_after(last_id)
            for row in rows:
                last_id = row["id"]
                yield f"event: notification\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cash")
def add_cash(request: CashRequest):
    try:
        entry_id = db.add_cash(request.mode, request.type, request.amount, request.note)
        return {"id": entry_id, "portfolio": db.portfolio(request.mode)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/trades")
def add_trade(request: TradeRequest):
    try:
        executed_at = None
        if request.execution_timestamp:
            timestamp = request.execution_timestamp
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Bangkok"))
            executed_at = timestamp.astimezone(UTC).isoformat()

        entry_id = db.trade(
            mode=request.mode,
            side=request.side,
            pair=request.pair,
            quantity=request.quantity,
            price=request.price,
            fee=request.fee,
            note=f"{request.reason}: {request.note}".strip(),
            executed_at=executed_at,
            reason=request.reason,
        )
        db.journal(
            request.mode,
            f"{request.side} {request.pair}",
            request.note or f"{request.quantity} @ {request.price:,.2f} THB · {request.reason}",
            "trade",
        )
        return {"id": entry_id, "portfolio": db.portfolio(request.mode)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/journal")
def add_journal(request: JournalRequest):
    db.journal(request.mode, request.title, request.body, request.tags)
    return {"status": "created"}


@app.put("/api/settings")
def update_settings(request: SettingsRequest):
    values = db.update_settings(request.model_dump())
    schedule_scanner()
    return values


@app.post("/api/scan")
async def run_scan():
    return await scanner.run()


@app.post("/api/second-opinion")
async def second_opinion(request: SecondOpinionRequest):
    signal = next((s for s in db.recent("signals", 500) if s["id"] == request.signal_id), None)
    if not signal:
        raise HTTPException(404, "Signal not found")
    try:
        answer = await deepseek.ask(signal, request.question)
        return {"answer": answer, "disclaimer": "ความคิดเห็นที่ 2 สำหรับประกอบการตัดสินใจเท่านั้น ไม่มีการส่งคำสั่งเทรด"}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ai/ask-position")
async def ask_position_opinion(request: AskDeepSeekRequest):
    norm_mode = normalize_mode(request.mode)
    portfolio = db.portfolio(norm_mode)
    positions = portfolio.get("positions", [])
    target_asset = request.symbol.split("/")[0].upper()
    pos = next((p for p in positions if p["asset"] == target_asset), None)
    context = db.realtime_context(request.symbol)
    plan = portfolio.get("position_plan")

    signals = context.get("signals", [])
    trend_15m = next((s["regime"] for s in signals if s["timeframe"] == "15m"), "N/A")
    trend_1h = next((s["regime"] for s in signals if s["timeframe"] == "1h"), "N/A")
    trend_4h = next((s["regime"] for s in signals if s["timeframe"] == "4h"), "N/A")

    structured_ctx = {
        "symbol": request.symbol,
        "mode": norm_mode,
        "has_open_position": pos is not None,
        "entry_price": pos["average_cost"] if pos else (plan["entry_price"] if plan else None),
        "current_price": pos["market_price"] if pos else None,
        "quantity": pos["quantity"] if pos else 0.0,
        "unrealized_pnl_thb": pos["unrealized_pnl"] if pos else 0.0,
        "unrealized_pnl_percent": pos["unrealized_pnl_percent"] if pos else 0.0,
        "current_action": plan["current_action"] if plan else "WAIT",
        "action_reason": plan["action_reason"] if plan else "None",
        "stop_loss": plan["stop_loss"] if plan else None,
        "effective_stop": plan["effective_stop"] if plan else None,
        "take_profit_1": plan["take_profit_1"] if plan else None,
        "take_profit_2": plan["take_profit_2"] if plan else None,
        "trend_15m": trend_15m,
        "trend_1h": trend_1h,
        "trend_4h": trend_4h,
        "signals": [
            {
                "timeframe": s["timeframe"],
                "signal": s["signal"],
                "score": s["score"],
                "rsi": s["details"].get("rsi"),
                "relative_volume": s["details"].get("relative_volume"),
                "strategy": s["details"].get("strategy"),
            }
            for s in signals
        ],
    }

    result = await deepseek.ask_structured(structured_ctx)
    return {
        "analysis": result,
        "context": structured_ctx,
        "disclaimer": "ความคิดเห็นที่ 2 สำหรับประกอบการตัดสินใจเท่านั้น ไม่มีการส่งคำสั่งเทรดอัตโนมัติ",
    }
