from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db import Database
from app.integrations import DeepSeekSecondOpinion, TelegramNotifier
from app.market import BitkubMarketData
from app.realtime import RealtimeMonitor
from app.scanner import Scanner
from app.schemas import CashRequest, JournalRequest, SecondOpinionRequest, SettingsRequest, TradeRequest
from app.backtest import replay

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
    scheduler.add_job(scanner.run, "interval", seconds=interval, id="market-scanner", replace_existing=True, max_instances=1, coalesce=True)


async def market_data_watchdog():
    import time
    if not realtime.connected or (realtime.last_event_at and time.time()-realtime.last_event_at>120):
        db.notify("WARNING","SYSTEM_WARNING","Bitkub WebSocket is disconnected or stale; REST scanner remains active as fallback.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio
    db.initialize()
    schedule_scanner()
    scheduler.start()
    scheduler.add_job(market_data_watchdog,"interval",seconds=30,id="market-watchdog",replace_existing=True)
    realtime_task = asyncio.create_task(realtime.run_forever(), name="bitkub-realtime-monitor")
    await lifecycle_notification("APP_STARTED", "Crypto Assistance started. REST scanner and real-time monitoring are enabled according to current settings.")
    yield
    await lifecycle_notification("APP_STOPPING", "Crypto Assistance is shutting down gracefully.")
    realtime.stop()
    realtime_task.cancel()
    try:
        await realtime_task
    except asyncio.CancelledError:
        pass
    scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": settings.app_name, "live_trading": False, "bitkub_websocket": {"connected": realtime.connected, "last_event_at": realtime.last_event_at}}


@app.get("/api/dashboard")
def dashboard_data():
    candidates,best=db.scanner_snapshot()
    return {"paper": db.portfolio("PAPER"), "real": db.portfolio("REAL"), "settings": db.settings(), "candidates":candidates,"best_candidate":best, "signals": db.recent("signals", 30), "ledger": db.recent("ledger", 30), "journal": db.recent("journal", 30), "notifications": db.recent("notifications", 30), "integrations": {"deepseek": bool(settings.deep_seek_api_key), "telegram": telegram.configured}}


@app.get("/api/backtest/{pair}/{timeframe}")
async def backtest(pair: str, timeframe: str, fee_percent: float=.25, slippage_percent: float=.1):
    pair=pair.upper().replace("-","/")
    try:
        candles=await market.candles(pair,timeframe,500)
        return replay(pair,candles["highs"],candles["lows"],candles["closes"],fee_percent,slippage_percent)
    except (KeyError,ValueError) as exc:
        raise HTTPException(400,str(exc)) from exc


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
                yield f"event: notification\ndata: {json.dumps(row)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
        executed_at=None
        if request.execution_timestamp:
            from datetime import UTC
            from zoneinfo import ZoneInfo
            timestamp=request.execution_timestamp
            if timestamp.tzinfo is None:
                timestamp=timestamp.replace(tzinfo=ZoneInfo("Asia/Bangkok"))
            executed_at=timestamp.astimezone(UTC).isoformat()
        entry_id = db.trade(request.mode, request.side, request.pair, request.quantity, request.price, request.fee, f"{request.reason}: {request.note}".strip(), executed_at)
        db.journal(request.mode, f"{request.side} {request.pair}", request.note or f"{request.quantity} @ {request.price:,.2f} THB · {request.reason}", "trade")
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
        return {"answer": answer, "disclaimer": "Second opinion only; no trade was or can be executed."}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
