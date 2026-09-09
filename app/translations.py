"""Thai-first translations and status mappings for Crypto Assistance."""

STATUS_THAI: dict[str, str] = {
    "WAIT": "รอดู",
    "WATCH": "จับตา",
    "BUY_NOW": "ซื้อได้ตอนนี้",
    "IN_POSITION": "กำลังถือ",
    "HOLD": "ถือต่อ",
    "EXIT_WATCH": "เตรียมออก",
    "TAKE_PROFIT": "ทำกำไร",
    "STOP_LOSS": "ตัดขาดทุน",
    "SELL_NOW": "ขายตอนนี้",
    "CLOSED": "ปิดสถานะแล้ว",
    "TRAILING_EXIT": "ขายตามจุดตัดขาดทุนเลื่อน",
    "STRATEGY_EXIT": "ขายตามกลยุทธ์",
    "SYSTEM_WARNING": "เตือนระบบ",
    "APP_STARTED": "เริ่มระบบสำเร็จ",
    "APP_STOPPING": "กำลังปิดระบบ",
}

REGIME_THAI: dict[str, str] = {
    "TRENDING_UP": "แนวโน้มขาขึ้น",
    "UPTREND": "แนวโน้มขาขึ้น",
    "TRENDING_DOWN": "แนวโน้มขาลง",
    "DOWNTREND": "แนวโน้มขาลง",
    "RANGING": "ไซด์เวย์ (ไร้ทิศทาง)",
    "RANGE": "ไซด์เวย์ (ไร้ทิศทาง)",
    "HIGH_VOLATILITY": "ความผันผวนสูง",
    "LOW_LIQUIDITY": "สภาพคล่องต่ำ",
    "UNKNOWN": "ไม่ทราบสภาวะ",
}

STRATEGY_THAI: dict[str, str] = {
    "EMA Pullback": "EMA ย่อตัว (EMA Pullback)",
    "Breakout": "เบรกเอาต์ (Breakout)",
    "Manual Real Execution": "การซื้อจริงด้วยตนเอง (Manual Real)",
    "Manual Bitkub Execution": "บันทึกการเทรดจริงบน Bitkub",
}

PLAN_TYPE_THAI: dict[str, str] = {
    "MARKET_DERIVED": "คำนวณตามสภาวะตลาดจริง (Market ATR & Structure)",
    "FALLBACK_MANUAL": "แผนสำรองตามสัดส่วนร้อยละ (Fallback Plan: SL -2%, TP1 +3%, TP2 +5%)",
}


def to_thai_status(status: str) -> str:
    return STATUS_THAI.get(status.upper(), status)


def to_thai_plan_type(plan_type: str) -> str:
    return PLAN_TYPE_THAI.get(plan_type.upper(), plan_type)


def format_entry_score(score: int | None) -> str:
    if score is None or score == 0:
        return "N/A / ไม่มีข้อมูล"
    return str(score)



def to_thai_regime(regime: str) -> str:
    return REGIME_THAI.get(regime.upper(), regime)


def to_thai_strategy(strategy: str) -> str:
    return STRATEGY_THAI.get(strategy, strategy)
