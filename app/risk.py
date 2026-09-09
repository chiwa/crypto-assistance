from dataclasses import dataclass


@dataclass(frozen=True)
class RiskGuidance:
    allowed: bool
    reason: str
    reason_th: str
    quantity: float
    allocation_thb: float
    risk_budget_thb: float
    effective_risk_thb: float
    risk_percent: float
    risk_reward: float


class RiskManager:
    DEFAULT_MAX_ALLOCATION: float = 8_000.0   # THB
    BITKUB_FEE_PERCENT: float = 0.25          # 0.25% taker/maker
    DEFAULT_MIN_RR: float = 1.5               # Minimum R:R 1.5
    MAX_RISK_PERCENT: float = 2.0             # 2% max risk of equity

    @classmethod
    def calculate_sizing(
        cls,
        current_equity: float,
        available_cash: float,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        max_risk_percent: float = 2.0,
        max_allocation_thb: float = 8_000.0,
        min_risk_reward: float = 1.5,
        consecutive_losses_today: int = 0,
        has_open_position: bool = False,
    ) -> RiskGuidance:
        if has_open_position:
            return RiskGuidance(
                allowed=False,
                reason="Position already open (one-position limit)",
                reason_th="มีสถานะที่ถือครองอยู่แล้ว (จำกัด 1 สถานะพร้อมกัน)",
                quantity=0.0,
                allocation_thb=0.0,
                risk_budget_thb=0.0,
                effective_risk_thb=0.0,
                risk_percent=0.0,
                risk_reward=0.0,
            )

        if consecutive_losses_today >= 2:
            return RiskGuidance(
                allowed=False,
                reason="Trading halted: 2 consecutive losses today",
                reason_th="หยุดเทรดชั่วคราว: ขาดทุนติดต่อกัน 2 ครั้งในวันนี้ (Circuit breaker)",
                quantity=0.0,
                allocation_thb=0.0,
                risk_budget_thb=0.0,
                effective_risk_thb=0.0,
                risk_percent=0.0,
                risk_reward=0.0,
            )

        if entry_price <= 0 or stop_loss <= 0 or entry_price <= stop_loss:
            return RiskGuidance(
                allowed=False,
                reason="Invalid entry price or stop loss",
                reason_th="ราคาเข้าหรือ Stop Loss ไม่ถูกต้อง",
                quantity=0.0,
                allocation_thb=0.0,
                risk_budget_thb=0.0,
                effective_risk_thb=0.0,
                risk_percent=0.0,
                risk_reward=0.0,
            )

        risk_dist = entry_price - stop_loss
        reward_dist = take_profit_1 - entry_price
        rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 0.0

        if rr_ratio < min_risk_reward:
            return RiskGuidance(
                allowed=False,
                reason=f"Risk/Reward ({rr_ratio:.2f}) is below minimum ({min_risk_reward:.2f})",
                reason_th=f"Risk/Reward ({rr_ratio:.2f}) ต่ำกว่าเกณฑ์ขั้นต่ำ ({min_risk_reward:.2f})",
                quantity=0.0,
                allocation_thb=0.0,
                risk_budget_thb=0.0,
                effective_risk_thb=0.0,
                risk_percent=0.0,
                risk_reward=round(rr_ratio, 2),
            )

        effective_max_risk = max(0.1, min(max_risk_percent, 5.0))
        risk_budget = current_equity * (effective_max_risk / 100.0)
        fee_rate = cls.BITKUB_FEE_PERCENT / 100.0
        unit_risk = risk_dist + (entry_price * fee_rate * 2.0)

        target_quantity = risk_budget / unit_risk if unit_risk > 0 else 0.0
        target_value = target_quantity * entry_price

        cash_cap = min(available_cash, max_allocation_thb)
        max_affordable_value = cash_cap / (1.0 + fee_rate)

        actual_value = min(target_value, max_affordable_value)
        actual_quantity = actual_value / entry_price if entry_price > 0 else 0.0
        effective_risk = actual_quantity * unit_risk

        if actual_value < 10.0:
            return RiskGuidance(
                allowed=False,
                reason="Order value below Bitkub minimum (10 THB) or insufficient cash",
                reason_th="มูลค่าคำสั่งซื้อต่ำกว่าขั้นต่ำ 10 บาท หรือเงินสดไม่เพียงพอ",
                quantity=0.0,
                allocation_thb=0.0,
                risk_budget_thb=round(risk_budget, 2),
                effective_risk_thb=0.0,
                risk_percent=0.0,
                risk_reward=round(rr_ratio, 2),
            )

        return RiskGuidance(
            allowed=True,
            reason="Risk and sizing approved",
            reason_th="ผ่านเกณฑ์การบริหารความเสี่ยงและขนาดการลงทุน",
            quantity=round(actual_quantity, 6),
            allocation_thb=round(actual_value, 2),
            risk_budget_thb=round(risk_budget, 2),
            effective_risk_thb=round(effective_risk, 2),
            risk_percent=round((effective_risk / current_equity) * 100.0, 2) if current_equity > 0 else 0.0,
            risk_reward=round(rr_ratio, 2),
        )
