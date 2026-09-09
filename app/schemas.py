from typing import Literal

from datetime import datetime
from pydantic import BaseModel, Field, field_validator


Mode = Literal["PAPER", "REAL", "MANUAL_REAL"]


class AskDeepSeekRequest(BaseModel):
    symbol: Literal["BTC/THB", "ETH/THB", "SOL/THB", "XRP/THB", "DOGE/THB"] = "DOGE/THB"
    mode: Mode = "MANUAL_REAL"



class CashRequest(BaseModel):
    mode: Mode
    type: Literal["DEPOSIT", "WITHDRAWAL"]
    amount: float = Field(gt=0)
    note: str = ""


class TradeRequest(BaseModel):
    mode: Mode
    side: Literal["BUY", "SELL"]
    pair: Literal["BTC/THB", "ETH/THB", "SOL/THB", "XRP/THB", "DOGE/THB"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    note: str = ""
    execution_timestamp: datetime | None = None
    reason: str = "MANUAL_EXECUTION"


class SettingsRequest(BaseModel):
    price_refresh_seconds: int = Field(ge=30, le=3600)
    timeframes: list[Literal["15m", "1h", "4h"]]
    risk_percent: float = Field(ge=0.1, le=5)
    relative_volume_min: float = Field(default=1.2, ge=0.5, le=5)
    estimated_exit_fee_percent: float = Field(default=.25, ge=0, le=5)
    scanner_enabled: bool
    websocket_enabled: bool = True
    telegram_enabled: bool = True
    ai_opinion_enabled: bool = True
    ai_opinion_threshold: int = Field(default=75, ge=50, le=100)
    realtime_alert_cooldown_seconds: int = Field(default=300, ge=30, le=86400)

    @field_validator("timeframes")
    @classmethod
    def timeframes_not_empty(cls, value):
        if not value:
            raise ValueError("Select at least one timeframe")
        return list(dict.fromkeys(value))


class JournalRequest(BaseModel):
    mode: Mode
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    tags: str = ""


class SecondOpinionRequest(BaseModel):
    signal_id: int
    question: str = Field(min_length=1, max_length=1000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
