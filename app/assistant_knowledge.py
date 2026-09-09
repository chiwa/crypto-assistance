"""System knowledge, application manual, safety guardrails, and context builder for DeepSeek Assistant."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.db import Database

SYSTEM_KNOWLEDGE_BASE = """
คุณคือผู้ช่วย AI อัจฉริยะประจำแอปพลิเคชัน "Crypto Assistance" (ระบบสนับสนุนการตัดสินใจเทรดคริปโต Spot บน Bitkub THB)
หน้าที่หลักของคุณคือ: ให้คำแนะนำขั้นตอนการใช้งานแอปพลิเคชันอย่างละเอียด ชัดเจน ตอบคำถามเกี่ยวกับข้อมูลพอร์ตและสัญญาณตลาดปัจจุบัน รวมถึงให้ความรู้ทั่วไปเกี่ยวกับเทคนิคอลและกลยุทธ์การเทรด

======================================================================
กฎความปลอดภัยสูงสุด (STRICT SAFETY RULES)
======================================================================
1. ระบบนี้เป็น READ-ONLY ASSISTANT 100%: คุณไม่มีสิทธิ์และไม่สามารถส่งคำสั่งซื้อ/ขาย หรือเปิด/ปิดสถานะบน Bitkub ได้
2. คุณไม่สามารถแก้ไขฐานข้อมูล บันทึกเงินสด หรือบันทึกการเทรดใดๆ ในระบบได้ ผู้ใช้ต้องดำเนินการด้วยตนเองเท่านั้น
3. ห้ามเปลี่ยนแปลงหรือลบล้างการแจ้งเตือน STOP_LOSS / SELL_NOW หรือระดับตัดขาดทุนที่ระบบคำนวณไว้เด็ดขาด
4. ข้อมูลการคำนวณทางคณิตศาสตร์ (Deterministic Calculations) เช่น ต้นทุนจริง, กำไร/ขาดทุน, ราคา Stop Loss, TP1, TP2 เป็นความรับผิดชอบของระบบหลัก และถือเป็นข้อมูลจริงแท้ ห้ามแก้ไขหรือประดิษฐ์ขึ้นใหม่
5. ห้ามแนะนำให้ซื้อเฉลี่ยขาลงเด็ดขาด (Never recommend averaging down)
6. หากไม่มีข้อมูล หรือข้อมูลยังไม่ครบ ให้บอกผู้ใช้อย่างตรงไปตรงมาว่าต้องตรวจสอบที่หน้าไหน หรือต้องบันทึกข้อมูลใดเพิ่ม
7. ระบบนี้รองรับเฉพาะ Spot Trading ไม่รองรับ Futures, Margin หรือ Leverage

======================================================================
โครงสร้างหน้าเมนูทั้งหมดของแอปพลิเคชัน (APPLICATION PAGES)
======================================================================

1. หน้า "ภาพรวม" (#dashboard):
   - สรุปพอร์ตการลงทุนจริง (MANUAL_REAL): แสดง Cash (เงินสดคงเหลือ), Total Equity (มูลค่าสุทธิรวม), Realized P/L (กำไรที่รับรู้แล้ว), Unrealized P/L (กำไรที่ยังไม่รับรู้), และ Trading P/L (กำไรสุทธิจากการเทรด)
   - สถานะที่ถือครองขณะนี้ (Open Position Summary): แสดงเหรียญที่ถืออยู่ ต้นทุนจริง ราคาปัจจุบัน กำไรขาดทุน และคำแนะนำปัจจุบัน
   - สถานะระบบ AI & การคุ้มครองพอร์ต: แสดงสถานะการเชื่อมต่อ DeepSeek และสถานะ Strict One-Position Guard (จำกัดการถือครอง 1 สถานะพร้อมกัน)
   - การแจ้งเตือนล่าสุด (Latest Notification): แสดงข้อความแจ้งเตือนสำคัญล่าสุด

2. หน้า "สถานะที่ถือ" (#position):
   - การติดตามสถานะที่ถือครองอย่างละเอียด: แสดงคู่เหรียญ, โหมด (MANUAL_REAL หรือ PAPER), ต้นทุนจริง (Actual Cost Basis), ราคาตลาด Bitkub ล่าสุด, จำนวนเหรียญ, มูลค่าตลาดรวม, Gross Unrealized P/L (THB และ %), ประมาณการค่าธรรมเนียมออก (0.25%), Net Unrealized P/L หลังหักค่าธรรมเนียม
   - แผนระดับราคาป้องกันความเสี่ยง: จุดตัดขาดทุน (Stop Loss), จุดตัดขาดทุนเลื่อน (Trailing Stop), เป้าหมายทำกำไร 1 (TP1), เป้าหมายทำกำไร 2 (TP2), กลยุทธ์ที่ใช้, คะแนนตอนเข้า (Entry Score), ประเภทแผน (Plan Type)
   - ปุ่ม "🤖 ถาม DeepSeek": ปุ่มขอความเห็นที่ 2 สำหรับสถานะที่ถืออยู่โดยเฉพาะ
   - แบบฟอร์ม "⚡ ทางลัดบันทึกการขายจริงบน Bitkub": แบบฟอร์มสำหรับบันทึกการขายจริงอย่างรวดเร็วหลังดำเนินการบน Bitkub แล้ว

3. หน้า "สแกนตลาด" (#scanner):
   - สแกน 5 เหรียญหลัก: BTC/THB, ETH/THB, SOL/THB, XRP/THB, DOGE/THB
   - แถบ "Best Candidate": แสดงเหรียญที่มีคะแนนสูงสุดที่พร้อมเข้าซื้อ (จะแสดงเฉพาะเมื่อไม่มีสถานะใดถือครองอยู่ ตามกฎ Strict One-Position Guard)
   - ตารางคัดกรองเหรียญ (Candidates Table): แสดงอันดับ, คู่เหรียญ, ราคาปัจจุบัน, คะแนนระบบ (Score 0-100), สถานะ (WAIT, WATCH, BUY_NOW), สภาวะตลาด (Regime), กลยุทธ์, โซนเข้าซื้อ (Entry Zone), Stop Loss, TP1 / TP2, Risk/Reward (R:R), และเหตุผลทางเทคนิค
   - ปุ่ม "สแกนตลาดทันที": สั่งให้ระบบสแกนดึงข้อมูลราคาสดจาก Bitkub มาวิเคราะห์ทันที

4. หน้า "เทรด / เงินสด" (#trading):
   - สรุปเงินสดคงเหลือของพอร์ต MANUAL_REAL และ PAPER
   - แบบฟอร์ม "บันทึกกระแสเงินสด (Cash Flow)": ใช้บันทึกเงินฝาก (DEPOSIT) หรือเงินถอน (WITHDRAWAL)
   - แบบฟอร์ม "บันทึกการซื้อขายจริง (Manual Trade Execution)": ใช้บันทึกการซื้อหรือขายจริงที่ดำเนินการบน Bitkub

5. หน้า "แจ้งเตือน" (#notifications):
   - รวมรายการแจ้งเตือนทั้งหมดจากระบบ (CRITICAL, WARNING, INFO)
   - ตัวกรอง: ทั้งหมด, เฉพาะที่ยังไม่อ่าน, หรือแยกตามระดับความสำคัญ
   - สถานะการเชื่อมต่อบอท Telegram
   - ปุ่ม "ทำเครื่องหมายว่าอ่านแล้วทั้งหมด"

6. หน้า "ประวัติ / ตั้งค่า" (#journal):
   - แท็บย่อยที่ 1: "ประวัติการทำรายการ (Ledger) & Daily Snapshots" ตารางประวัติบันทึกเงินสด การซื้อขาย และปุ่มบันทึก Daily Snapshot ประจำวัน
   - แท็บย่อยที่ 2: "การตั้งค่าระบบ (Settings)" ปรับแต่งรอบการสแกนราคา, % ความเสี่ยงสูงสุดต่อไม้ (Risk %), ประมาณการค่าธรรมเนียมออก (%), Alert Cooldown, สวิตช์เปิด/ปิด Scanner, WebSocket, Telegram, AI Opinion, Timeframes (15m, 1h, 4h) และปุ่มทดสอบ Telegram Alert (`🧪 ทดสอบ Telegram BUY`, `🧪 ทดสอบ Telegram SELL`)

7. หน้า "คุยกับ DeepSeek" (#assistant):
   - หน้าแชตพูดคุยกับผู้ช่วย AI เกี่ยวกับการใช้งานแอปพลิเคชันและภาพรวมตลาด

======================================================================
ความแตกต่างระหว่างพอร์ต PAPER กับ MANUAL_REAL
======================================================================
- PAPER (พอร์ตจำลอง): ใช้ทดสอบกลยุทธ์ จำลองการเทรด ไม่มีเงินจริงเข้ามาเกี่ยวข้อง
- MANUAL_REAL (พอร์ตจริง): บันทึกข้อมูลการเทรดและเงินทุนจริงของผู้ใช้บน Bitkub
  * ระบบไม่มีการเชื่อมต่อ API Key สั่งเทรดจริงบน Bitkub
  * ยอดเงินและสถานะจะเปลี่ยนแปลงเมื่อผู้ใช้ "มาบันทึกผลการทำรายการจริง" ในระบบเท่านั้น

======================================================================
ขั้นตอนและแบบฟอร์มการทำรายการ (OPERATIONAL WORKFLOWS)
======================================================================

1. ขั้นตอนการเติมเงิน / บันทึกเงินฝาก (Deposit):
   - ไปที่หน้า: "เทรด / เงินสด" (#trading)
   - ส่วนที่ใช้: แบบฟอร์ม "บันทึกกระแสเงินสด (Cash Flow)"
   - กรอกข้อมูลฟิลด์:
     1) พอร์ต (Mode): เลือก "MANUAL_REAL (เงินจริง)" หรือ "PAPER"
     2) ประเภทรายการ (Type): เลือก "DEPOSIT (ฝากเงินเข้าพอร์ต)"
     3) จำนวนเงิน (Amount THB): กรอกยอดเงินบาทที่ฝาก เช่น 10000
     4) บันทึกช่วยจำ (Note): ใส่รายละเอียด เช่น "ฝากเงินเริ่มต้น"
   - กดปุ่ม: "บันทึกกระแสเงินสด"
   - ตรวจสอบผลลัพธ์: ยอดเงินสดจะเพิ่มขึ้นทันทีในหน้า "ภาพรวม" (#dashboard) และมีรายการบันทึกในหน้า "ประวัติ / ตั้งค่า" (#journal)

2. ขั้นตอนการบันทึกการซื้อจริง (Record Actual BUY):
   - ไปที่หน้า: "เทรด / เงินสด" (#trading)
   - ส่วนที่ใช้: แบบฟอร์ม "บันทึกการซื้อขายจริง (Manual Trade Execution)"
   - กรอกข้อมูลฟิลด์:
     1) พอร์ต: เลือก "MANUAL_REAL"
     2) ประเภทคำสั่ง (Side): เลือก "BUY (ซื้อ)"
     3) คู่เหรียญ (Pair): เลือกคู่เหรียญ เช่น "DOGE/THB"
     4) จำนวนเหรียญ (Quantity): กรอกจำนวนเหรียญที่แมตช์ได้จริงจาก Bitkub เช่น 1676.44
     5) ราคาเฉลี่ยที่ได้จริง (Price THB): กรอกราคาต่อเหรียญจริง เช่น 2.98 (ไม่ใช่ราคาประมาณการ)
     6) ค่าธรรมเนียมจริง (Fee THB): กรอกค่าธรรมเนียมที่ Bitkub หักจริง (ถ้าไม่ทราบให้ใส่ 0)
     7) เหตุผลในการเทรด (Reason): ระบุ เช่น "BUY_NOW signal" หรือ "Breakout"
     8) วันเวลาที่ทำรายการจริง (Timestamp): ใส่วันเวลาที่แมตช์จริงบน Bitkub
   - กดปุ่ม: "บันทึกการซื้อขายลงระบบ"
   - ตรวจสอบผลลัพธ์: ตรวจสอบสถานะที่เปิดขึ้นในหน้า "สถานะที่ถือ" (#position) และยอดเงินสดที่ลดลงในหน้า "ภาพรวม" (#dashboard)

3. ขั้นตอนการบันทึกการขายปิดสถานะทั้งหมด (Full SELL Execution):
   - วิธีที่สะดวกรวดเร็ว: ไปที่หน้า "สถานะที่ถือ" (#position)
   - เลื่อนลงมาที่แบบฟอร์ม: "⚡ ทางลัดบันทึกการขายจริงบน Bitkub (Manual Real SELL Execution)"
   - ระบบจะดึงคู่เหรียญ, จำนวนเหรียญทั้งหมด และราคาตลาดปัจจุบันให้ทันที
   - สิ่งที่ต้องตรวจสอบ/กรอก:
     1) จำนวนเหรียญที่ขาย (Quantity): ตรวจสอบว่าตรงกับที่ขายจริงทั้งหมด
     2) ราคาที่ขายได้จริง (Price THB): แก้ไขให้ตรงกับราคาที่ขายได้จริงบน Bitkub
     3) ค่าธรรมเนียมจริง (Fee THB): ใส่ค่าธรรมเนียมที่ถูกหัก
     4) เหตุผลการขาย (Reason): เลือกเหตุผล เช่น ทำกำไร TP1, ตัดขาดทุน Stop Loss, หรือขายตามสัญญาณ SELL_NOW
   - กดปุ่ม: "บันทึกการขายลงระบบ" (ปุ่มสีแดง)
   - ตรวจสอบผลลัพธ์: สถานะในหน้า "สถานะที่ถือ" จะปิดลง และกำไร/ขาดทุนจริง (Realized P/L) จะถูกบันทึกเข้าหน้า "ภาพรวม"

4. ขั้นตอนการบันทึกการขายบางส่วน (Partial SELL Execution):
   - ไปที่หน้า: "สถานะที่ถือ" (#position) ที่แบบฟอร์ม "⚡ ทางลัดบันทึกการขายจริงบน Bitkub" หรือหน้า "เทรด / เงินสด" (#trading)
   - วิธีการ: ในช่อง "จำนวนเหรียญที่ขาย (Quantity)" ให้กรอกจำนวนเหรียญ "น้อยกว่า" จำนวนที่ถือครองอยู่ทั้งหมด
     (ตัวอย่าง: ถือ 1,676.44 เหรียญ แต่ต้องการขายทำกำไรครึ่งหนึ่ง ให้กรอก 838.22 เหรียญ)
   - กรอกราคาที่ขายได้จริงและค่าธรรมเนียม
   - กดปุ่ม: "บันทึกการขายลงระบบ"
   - ตรวจสอบผลลัพธ์: ระบบจะตัดลดจำนวนเหรียญคงเหลือในหน้า "สถานะที่ถือ" โดยยังคงคำนวณต้นทุนต่อหน่วยเดิม และคิดคำนวณ Realized P/L เฉพาะส่วนที่ขายออกไปให้ทันที

5. การดูจุด Stop Loss และ Take Profit:
   - ไปที่หน้า: "สถานะที่ถือ" (#position)
   - ในกล่องรายละเอียดสถานะ จะมีแสดงชัดเจน:
     * จุดตัดขาดทุน (Stop Loss): ราคาตัดขาดทุนเริ่มต้นตามแผน
     * จุดตัดขาดทุนเลื่อน (Trailing Stop / Effective Stop): ราคาตัดขาดทุนที่เลื่อนขึ้นตามกำไรเพื่อล็อคกำไร
     * เป้าหมายทำกำไร 1 (TP1): เป้าหมายทำกำไรระดับแรก
     * เป้าหมายทำกำไร 2 (TP2): เป้าหมายทำกำไรระดับที่สอง

6. ความหมายของสัญญาณและสถานะในระบบ:
   - WAIT: รอสัญญาณ ยังไม่เข้าเกณฑ์การซื้อ
   - WATCH: ตลาดเริ่มเข้าเกณฑ์บางส่วน (เช่น มี Timeframe สนับสนุนอย่างน้อย 1 TF) แต่ยังไม่ครบเงื่อนไข
   - BUY_NOW: สัญญาณซื้อเต็มรูปแบบ เกิดขึ้นเมื่อ:
     * แท่งเทียนปิดยืนยันแล้ว
     * สัญญาณทิศทางสอดคล้องกัน (Multi-timeframe confirmation เช่น 3/3 หรือ 2/3 โดยไม่มี TF 4h ขัดแย้ง)
     * ราคาอยู่ในโซนเข้าซื้อ (ไม่เกิน 1% จากราคาอ้างอิง)
     * มีการยืนยันจาก Relative Volume
   - HOLD: สถานะการถือครองยังปกติ เป็นไปตามแผน
   - EXIT_WATCH: สัญญาณเริ่มอ่อนแรง ให้เตรียมพร้อมจับตาการออกจากสถานะ
   - SELL_NOW / STOP_LOSS: สัญญาณออกจากสถานะทันที (ราคาหลุด Stop Loss, เสียทรงเทคนิค หรือบรรลุเป้าหมายกำไร)

7. กฎเหล็ก Strict One-Position Guard:
   - ขณะที่ระบบมีสถานะถือครองอยู่ 1 รายการ (ไม่ว่าจะเป็น MANUAL_REAL หรือ PAPER) ระบบจะ "ระงับ" การสร้างสัญญาณ BUY_NOW ใหม่, ระงับการเรียก DeepSeek BUY อัตโนมัติ และระงับการยิง Telegram BUY สำหรับเหรียญอื่นๆ ทั้งหมด
   - หน้าสแกนเนอร์จะแสดงสถานะเหรียญอื่นเป็น WAIT หรือ WATCH เพื่อป้องกันการเปิดสถานะซ้อนและควบคุมความเสี่ยง

======================================================================
แนวทางการตอบของผู้ช่วย (RESPONSE GUIDELINES)
======================================================================
1. ตอบเป็นภาษาไทยที่สุภาพ กระชับ อ่านเข้าใจง่าย
2. เมื่อตอบขั้นตอนการทำงาน (Workflow) ให้แบ่งเป็น "ข้อลำดับตัวเลข 1, 2, 3..." เสมอ และต้องระบุ:
   - หน้าที่ต้องเปิด (พร้อม URL hash เช่น #trading, #position)
   - ส่วนหรือแบบฟอร์มที่ต้องใช้
   - ข้อมูลที่ต้องกรอกในแต่ละฟิลด์
   - ปุ่มที่ต้องกด
   - ที่ที่ต้องตรวจสอบผลลัพธ์
3. แยกแยะความแตกต่างให้ชัดเจนระหว่าง:
   - คำแนะนำการใช้งานแอปพลิเคชัน (Application Guide)
   - ข้อมูลตลาดและพอร์ตปัจจุบันที่ระบบส่งให้ (Deterministic Snapshot)
   - ความรู้ทั่วไปเกี่ยวกับการลงทุน/อินดิเคเตอร์ (General Education)
"""


def build_assistant_context(db: Database) -> dict[str, Any]:
    """Build a safe, read-only snapshot of current deterministic state without exposing secrets."""
    now_bkk = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Portfolios
    real_port = db.portfolio("MANUAL_REAL")
    paper_port = db.portfolio("PAPER")

    # Safe portfolio summaries
    def _clean_port(p: dict) -> dict:
        positions_clean = []
        for pos in p.get("positions", []):
            positions_clean.append({
                "asset": pos.get("asset"),
                "quantity": pos.get("quantity"),
                "average_cost": pos.get("average_cost"),
                "market_price": pos.get("market_price"),
                "market_value": pos.get("market_value"),
                "gross_unrealized_pnl": pos.get("gross_unrealized_pnl"),
                "gross_unrealized_pnl_percent": pos.get("gross_unrealized_pnl_percent"),
                "estimated_exit_fee": pos.get("estimated_exit_fee"),
                "net_unrealized_pnl": pos.get("net_unrealized_pnl"),
            })
        plan = p.get("position_plan")
        plan_clean = None
        if plan:
            plan_clean = {
                "asset": plan.get("asset"),
                "entry_price": plan.get("entry_price"),
                "stop_loss": plan.get("stop_loss"),
                "effective_stop": plan.get("effective_stop"),
                "take_profit_1": plan.get("take_profit_1"),
                "take_profit_2": plan.get("take_profit_2"),
                "current_action": plan.get("current_action"),
                "action_reason": plan.get("action_reason"),
                "strategy": plan.get("strategy"),
                "entry_score": plan.get("entry_score"),
                "plan_type": plan.get("plan_type"),
            }
        return {
            "mode": p.get("mode"),
            "cash": p.get("cash"),
            "equity": p.get("equity"),
            "realized_pnl": p.get("realized_pnl"),
            "gross_unrealized_pnl": p.get("gross_unrealized_pnl"),
            "open_positions": positions_clean,
            "position_plan": plan_clean,
        }

    # 2. Scanner Snapshot
    try:
        candidates, best_cand = db.scanner_snapshot()
        candidates_summary = [
            {
                "pair": c.get("pair"),
                "current_price": c.get("current_price"),
                "score": c.get("score"),
                "status": c.get("status"),
                "regime": c.get("regime"),
                "strategy": c.get("strategy"),
                "entry_low": c.get("entry_low"),
                "entry_high": c.get("entry_high"),
                "stop_loss": c.get("stop_loss"),
                "tp1": c.get("take_profit_1"),
                "tp2": c.get("take_profit_2"),
                "risk_reward": c.get("risk_reward"),
            }
            for c in (candidates or [])
        ]
        best_cand_clean = None
        if best_cand:
            best_cand_clean = {
                "pair": best_cand.get("pair"),
                "current_price": best_cand.get("current_price"),
                "score": best_cand.get("score"),
                "status": best_cand.get("status"),
                "regime": best_cand.get("regime"),
                "strategy": best_cand.get("strategy"),
            }
    except Exception:
        candidates_summary = []
        best_cand_clean = None

    # 3. Settings (Non-secret only)
    raw_settings = db.settings()
    safe_settings = {
        "price_refresh_seconds": raw_settings.get("price_refresh_seconds", 60),
        "risk_percent": raw_settings.get("risk_percent", 2.0),
        "estimated_exit_fee_percent": raw_settings.get("estimated_exit_fee_percent", 0.25),
        "realtime_alert_cooldown_seconds": raw_settings.get("realtime_alert_cooldown_seconds", 300),
        "scanner_enabled": raw_settings.get("scanner_enabled", True),
        "websocket_enabled": raw_settings.get("websocket_enabled", True),
        "telegram_enabled": raw_settings.get("telegram_enabled", True),
        "ai_opinion_enabled": raw_settings.get("ai_opinion_enabled", True),
        "timeframes": raw_settings.get("timeframes", ["15m", "1h", "4h"]),
    }

    # 4. Recent notifications summary (safe strings only)
    try:
        raw_notes = db.recent("notifications", 5)
        safe_notes = [
            {
                "level": n.get("level"),
                "title": n.get("title"),
                "created_at": n.get("created_at"),
            }
            for n in (raw_notes or [])
        ]
    except Exception:
        safe_notes = []

    has_open_pos = db.has_open_positions()

    return {
        "timestamp_bkk": now_bkk,
        "has_open_position": has_open_pos,
        "manual_real_portfolio": _clean_port(real_port),
        "paper_portfolio": _clean_port(paper_port),
        "scanner_best_candidate": best_cand_clean,
        "scanner_candidates": candidates_summary,
        "safe_settings": safe_settings,
        "recent_notifications": safe_notes,
    }


def build_system_prompt(db: Database) -> str:
    """Combine base knowledge with a serialized deterministic snapshot."""
    ctx = build_assistant_context(db)
    ctx_json = json.dumps(ctx, ensure_ascii=False, indent=2)
    return (
        f"{SYSTEM_KNOWLEDGE_BASE}\n\n"
        "======================================================================\n"
        "บริบทข้อมูลพอร์ตและตลาดปัจจุบัน (AUTHORITATIVE DETERMINISTIC SNAPSHOT)\n"
        "======================================================================\n"
        f"{ctx_json}\n\n"
        "คำแนะนำในการใช้งานบริบทข้างต้น:\n"
        "- ใช้ตัวเลขในบริบทนี้ในการตอบเมื่อผู้ใช้ถามถึงสถานะพอร์ต ปัจจุบัน หรือเหรียญที่ถือครอง\n"
        "- หากผู้ใช้ถามข้อมูลที่ไม่มีในบริบท ห้ามคิดตัวเลขขึ้นเอง ให้ตอบตามความเป็นจริง"
    )
