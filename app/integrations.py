import json
import logging
import httpx

logger = logging.getLogger(__name__)


class DeepSeekSecondOpinion:
    def __init__(self, api_key: str | None):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def ask(self, context: dict, question: str) -> str:
        if not self.configured:
            raise RuntimeError("DEEP_SEEK_API_KEY ไม่ได้ถูกตั้งค่า (AI Second Opinion is disabled)")
            
        system_prompt = (
            "คุณคือผู้ช่วยวิเคราะห์ทางเทคนิคสำหรับคริปโต (Second-opinion technical analyst) "
            "หน้าที่ของคุณคือให้ความเห็นประกอบการตัดสินใจของมนุษย์เท่านั้น ห้ามทำการเทรดจริง "
            "จงตอบเป็นภาษาไทยอย่างกระชับ ชัดเจน อ้างอิงข้อมูล Indicators เชิงสถิติที่ได้รับ "
            "และเน้นการบริหารความเสี่ยงเป็นสำคัญ"
        )
        user_prompt = f"บริบทข้อมูลตลาดและกลยุทธ์: {json.dumps(context, ensure_ascii=False)}\nคำถาม: {question}"
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning(f"DeepSeek call failed: {exc}")
            raise RuntimeError(f"DeepSeek ขัดข้องชั่วคราว: {exc}") from exc

    async def chat(self, messages: list[dict], system_prompt: str) -> str:
        """General chat with DeepSeek using system prompt knowledge and bounded history."""
        if not self.configured:
            raise RuntimeError("DEEP_SEEK_API_KEY ไม่ได้ถูกตั้งค่า (AI Chat is disabled)")

        payload_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            payload_messages.append({"role": m["role"], "content": m["content"]})

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": payload_messages,
                        "temperature": 0.3,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            logger.warning(f"DeepSeek chat call timed out: {exc}")
            raise TimeoutError("การเชื่อมต่อ DeepSeek หมดเวลา (Timeout)") from exc
        except Exception as exc:
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            logger.warning(f"DeepSeek chat call failed: {err_msg}")
            raise RuntimeError(f"DeepSeek ขัดข้องชั่วคราว: {err_msg}") from exc

    async def ask_structured(self, context: dict) -> dict:
        if not self.configured:
            return {
                "status": "unavailable",
                "assessment": "ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY ในระบบ (AI Second Opinion ปิดการทำงาน)",
                "confidence": "ไม่สามารถระบุได้",
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": ["ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY", "ไม่มีการเชื่อมต่อกับระบบ AI วิเคราะห์"],
                "watch_next": ["โปรดยึดตามสัญญาณทางเทคนิคและระดับ Stop Loss / TP ของระบบ Deterministic"],
                "summary": "ระบบยังคงใช้การคำนวณแบบ Deterministic 100% ตามปกติ",
            }

        system_prompt = (
            "คุณคือผู้ช่วยวิเคราะห์ทางเทคนิคสำหรับคริปโต (Second-opinion technical analyst)\n"
            "หน้าที่ของคุณคือให้ความเห็นประกอบการตัดสินใจของมนุษย์เท่านั้น\n\n"
            "ข้อกำหนดและข้อห้ามอย่างเด็ดขาด:\n"
            "1. ห้ามดึงราคาจากภายนอกหรือสร้างข้อมูลที่ไม่มีอยู่ขึ้นมาเอง (Do not fetch market prices or invent missing data) ให้ใช้เฉพาะตัวเลขเชิงสถิติและบริบททางเทคนิคที่ได้รับเท่านั้น\n"
            "2. ห้ามสั่งเปิด/ปิดสถานะ หรือเปลี่ยนแปลงสถานะการเทรด (Do not change trading state or open/close positions)\n"
            "3. ห้ามเปลี่ยนแปลงหรือลบล้างการแจ้งเตือน STOP_LOSS / SELL_NOW หรือระบบบริหารความเสี่ยง (Do not override SELL_NOW / STOP_LOSS / RiskManager)\n"
            "4. ห้ามสร้างหรือแนะนำระดับ Stop Loss หรือ Take Profit ขึ้นมาใหม่ ให้ประเมินตามระดับที่ระบบคำนวณไว้แล้วเท่านั้น\n"
            "5. ห้ามแนะนำให้ซื้อเฉลี่ยขาลงเด็ดขาด (Never recommend averaging down)\n"
            "6. ห้ามคำนวณบัญชีหรือดัดแปลงผลกำไรขาดทุนของพอร์ตโฟลิโอ\n\n"
            "รูปแบบการตอบ:\n"
            "ต้องตอบเป็น JSON ภาษาไทยที่มีโครงสร้างตามคีย์ดังนี้เท่านั้น:\n"
            "{\n"
            '  "assessment": "การประเมินสถานะปัจจุบันในภาพรวม",\n'
            '  "confidence": "ระดับความมั่นใจ (เช่น สูง / ปานกลาง / ระมัดระวัง)",\n'
            '  "bull_case": "มุมมองเชิงบวกและปัจจัยสนับสนุน",\n'
            '  "bear_case": "มุมมองเชิงลบและปัจจัยกดดัน",\n'
            '  "key_risks": ["ความเสี่ยงข้อที่ 1", "ความเสี่ยงข้อที่ 2"],\n'
            '  "watch_next": ["สิ่งที่ต้องติดตามข้อที่ 1", "สิ่งที่ต้องติดตามข้อที่ 2"],\n'
            '  "summary": "บทสรุปคำแนะนำเพื่อประกอบการตัดสินใจ"\n'
            "}"
        )

        user_prompt = (
            f"กรุณาวิเคราะห์บริบททางเทคนิคของสถานะที่ถือครองต่อไปนี้:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "จงตอบเป็น JSON ภาษาไทยตามโครงสร้างที่กำหนดเท่านั้น"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                def _ensure_list(val) -> list[str]:
                    if isinstance(val, list):
                        return [str(x) for x in val if x]
                    if isinstance(val, str) and val.strip():
                        lines = [line.strip("- *• \t\r") for line in val.split("\n") if line.strip()]
                        return lines if lines else [val.strip()]
                    return []

                return {
                    "status": "connected",
                    "assessment": str(parsed.get("assessment", "ประเมินสถานะตามสัญญาณทางเทคนิค")),
                    "confidence": str(parsed.get("confidence", "ปานกลาง")),
                    "bull_case": str(parsed.get("bull_case", "สัญญาณและแนวโน้มยังคงประคองตัว")),
                    "bear_case": str(parsed.get("bear_case", "ระมัดระวังแรงขายหากหลุดแนวรับ")),
                    "key_risks": _ensure_list(parsed.get("key_risks")) or ["ความผันผวนของตลาดคริปโต"],
                    "watch_next": _ensure_list(parsed.get("watch_next")) or ["เฝ้าระวังจุดตัดขาดทุน Stop Loss"],
                    "summary": str(parsed.get("summary", "โปรดยึดหลักการบริหารความเสี่ยงเป็นสำคัญ")),
                }

        except httpx.TimeoutException:
            logger.warning("DeepSeek structured call timed out")
            return {
                "status": "timeout",
                "assessment": "ระบบ AI ไม่สามารถตอบกลับได้ในขณะนี้ (หมดเวลาเชื่อมต่อ)",
                "confidence": "ระมัดระวัง",
                "bull_case": "ดูตามสัญญาณเทคนิคในระบบ",
                "bear_case": "เซิร์ฟเวอร์ AI ตอบสนองช้าหรือไม่สามารถเข้าถึงได้",
                "key_risks": ["ไม่ได้รับข้อมูลความเห็นที่ 2 จาก AI ในรอบนี้", "การเชื่อมต่อเครือข่ายหมดเวลา"],
                "watch_next": [
                    "เฝ้าระวังราคาตามจุด Stop Loss และ Take Profit ของระบบ",
                    "สามารถกดขอความเห็นใหม่ได้ในภายหลัง",
                ],
                "summary": "การเชื่อมต่อ AI หมดเวลา โปรดตัดสินใจตามระดับความเสี่ยงของระบบ Deterministic หลัก",
            }
        except (json.JSONDecodeError, KeyError, TypeError) as parse_exc:
            logger.warning(f"DeepSeek returned malformed JSON: {parse_exc}")
            return {
                "status": "error",
                "assessment": "ระบบ AI ไม่สามารถตอบกลับได้ในขณะนี้ (รูปแบบข้อมูลไม่ถูกต้อง)",
                "confidence": "ระมัดระวัง",
                "bull_case": "ดูตามสัญญาณเทคนิคเดิมของระบบ",
                "bear_case": "ข้อมูลจาก AI ไม่สมบูรณ์",
                "key_risks": ["AI ส่งผลการวิเคราะห์กลับมาไม่ถูกต้องตามรูปแบบ JSON"],
                "watch_next": [
                    "ตรวจสอบสัญญาณและระดับราคาบนกระดาน Bitkub",
                    "ยึดจุดตัดขาดทุน Stop Loss ของระบบเป็นสำคัญ",
                ],
                "summary": "AI ส่งคำตอบในรูปแบบที่ไม่สมบูรณ์ ระบบยังคงใช้การคำนวณแบบ Deterministic ตามปกติ",
            }
        except Exception as exc:
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            logger.warning(f"DeepSeek structured call failed: {err_msg}")
            return {
                "status": "error",
                "assessment": "ระบบ AI ไม่สามารถตอบกลับได้ในขณะนี้",
                "confidence": "ระมัดระวัง",
                "bull_case": "ดูตามสัญญาณระบบทางเทคนิคเดิม",
                "bear_case": "ความผันผวนของตลาดหรือการเชื่อมต่อขัดข้อง",
                "key_risks": [f"เกิดข้อผิดพลาดในการเชื่อมต่อ: {err_msg}"],
                "watch_next": [
                    "ตรวจสอบการเชื่อมต่ออินเทอร์เน็ตหรือสถานะบริการ DeepSeek",
                    "ยึดตามวินัยการเทรดและจุดตัดขาดทุนของระบบ",
                ],
                "summary": "โปรดยึดหลักการบริหารความเสี่ยงและ Stop Loss ของระบบ Deterministic เป็นหลัก",
            }

    async def ask_sell_opinion(self, context: dict) -> dict:
        """DeepSeek second opinion for critical SELL_NOW signals.
        DeepSeek must NOT create the SELL_NOW signal. It only analyzes an already-existing deterministic exit decision.
        """
        if not self.configured:
            return {
                "status": "unavailable",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "why_exit_now": "AI analysis unavailable (ไม่ได้ตั้งค่า DEEP_SEEK_API_KEY)",
                "risk_of_holding": "N/A",
                "counter_case": "N/A",
                "key_risks": ["AI analysis unavailable", "ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY ในระบบ"],
                "watch_next": ["ยึดตามสัญญาณเทคนิคและระดับ Stop Loss / TP ของระบบ Deterministic"],
                "summary": "AI analysis unavailable - ระบบใช้การคำนวณแบบ Deterministic 100%",
            }

        system_prompt = (
            "คุณคือผู้ช่วยวิเคราะห์ทางเทคนิคสำหรับคริปโต (Second-opinion technical analyst)\n"
            "หน้าที่ของคุณคือให้ความเห็นประกอบการตัดสินใจขาย (SELL_NOW second opinion) "
            "โดยวิเคราะห์การตัดสินใจขายที่เกิดขึ้นจากระบบคำนวณทางเทคนิค (Deterministic PositionExitEngine) ของระบบหลัก\n\n"
            "ข้อกำหนดและข้อห้ามอย่างเด็ดขาด:\n"
            "1. คุณไม่ได้เป็นผู้สร้างสัญญาณ SELL_NOW ระบบหลักเป็นผู้ตัดสินใจทางเทคนิคไว้แล้ว คุณมีหน้าที่ให้ความเห็นที่สองเท่านั้น (DeepSeek must NOT create the SELL_NOW signal. It only analyzes an already-existing deterministic exit decision.)\n"
            "2. ห้ามดึงราคาจากภายนอกหรือสร้างข้อมูลที่ไม่มีอยู่ขึ้นมาเอง (Do not fetch market prices or invent missing data) ให้ใช้เฉพาะตัวเลขเชิงสถิติและบริบททางเทคนิคที่ได้รับเท่านั้น\n"
            "3. ห้ามสั่งเปิด/ปิดสถานะ หรือเปลี่ยนแปลงสถานะการเทรดในพอร์ตโฟลิโอ (Do not change trading state or open/close positions)\n"
            "4. ห้ามเปลี่ยนแปลงหรือยกเลิกการตัดสินใจ STOP_LOSS / SELL_NOW ของระบบหลัก (Do not override SELL_NOW / STOP_LOSS / RiskManager)\n"
            "5. ห้ามแนะนำให้ซื้อเฉลี่ยขาลงเด็ดขาด (Never recommend averaging down)\n"
            "6. ห้ามคำนวณบัญชีหรือดัดแปลงผลกำไรขาดทุนของพอร์ตโฟลิโอ\n\n"
            "รูปแบบการตอบ:\n"
            "ต้องตอบเป็น JSON ภาษาไทยที่มีโครงสร้างตามคีย์ดังนี้เท่านั้น โดย confidence ต้องเป็นตัวเลข float ระหว่าง 0.0 ถึง 1.0:\n"
            "{\n"
            '  "assessment": "การประเมินสัญญาณขายในภาพรวม",\n'
            '  "confidence": 0.85,\n'
            '  "why_exit_now": "เหตุผลทางเทคนิคที่สนับสนุนการขายหรือปิดสถานะทันที",\n'
            '  "risk_of_holding": "ความเสี่ยงหากยังฝืนถือสถานะต่อไป",\n'
            '  "counter_case": "มุมมองโต้แย้งหรือโอกาสที่ราคาอาจฟื้นตัว",\n'
            '  "key_risks": ["ความเสี่ยงสำคัญข้อที่ 1", "ความเสี่ยงสำคัญข้อที่ 2"],\n'
            '  "watch_next": ["สิ่งที่ต้องจับตาข้อที่ 1", "สิ่งที่ต้องจับตาข้อที่ 2"],\n'
            '  "summary": "บทสรุปความเห็นที่สองเพื่อประกอบการตัดสินใจขายของผู้ใช้"\n'
            "}"
        )

        user_prompt = (
            f"กรุณาวิเคราะห์สัญญาณขาย SELL_NOW สำหรับสถานะเปิดต่อไปนี้:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            f"จงตอบเป็น JSON ภาษาไทยตามโครงสร้างที่กำหนดเท่านั้น โดย confidence ต้องเป็นตัวเลข float ระหว่าง 0.0 ถึง 1.0"
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                def _parse_confidence(val) -> float:
                    if isinstance(val, (int, float)):
                        return round(max(0.0, min(1.0, float(val))), 2)
                    if isinstance(val, str):
                        cleaned = val.replace("%", "").strip()
                        try:
                            num = float(cleaned)
                            if num > 1.0:
                                num = num / 100.0
                            return round(max(0.0, min(1.0, num)), 2)
                        except ValueError:
                            return 0.5
                    return 0.5

                def _ensure_list(val) -> list[str]:
                    if isinstance(val, list):
                        return [str(x) for x in val if x]
                    if isinstance(val, str) and val.strip():
                        lines = [line.strip("- *• \t\r") for line in val.split("\n") if line.strip()]
                        return lines if lines else [val.strip()]
                    return []

                return {
                    "status": "connected",
                    "assessment": str(parsed.get("assessment", "ประเมินสัญญาณขายตามระบบเทคนิค")),
                    "confidence": _parse_confidence(parsed.get("confidence", 0.7)),
                    "why_exit_now": str(parsed.get("why_exit_now", "สัญญาณเทคนิคถึงเงื่อนไขการขาย")),
                    "risk_of_holding": str(parsed.get("risk_of_holding", "อาจมีความเสี่ยงจากการปรับฐาน")),
                    "counter_case": str(parsed.get("counter_case", "ราคาอาจแกว่งตัวผันผวนระยะสั้น")),
                    "key_risks": _ensure_list(parsed.get("key_risks")) or ["ความผันผวนของราคา"],
                    "watch_next": _ensure_list(parsed.get("watch_next")) or ["เฝ้าระวังระดับราคาและ Stop Loss"],
                    "summary": str(parsed.get("summary", "โปรดยึดหลักวินัยการเทรดเป็นสำคัญ")),
                }
        except httpx.TimeoutException:
            logger.warning("DeepSeek sell opinion call timed out")
            return {
                "status": "timeout",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "why_exit_now": "AI analysis unavailable",
                "risk_of_holding": "N/A",
                "counter_case": "N/A",
                "key_risks": ["AI analysis unavailable", "การเชื่อมต่อหมดเวลา"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - หมดเวลาเชื่อมต่อ โปรดยึดตามสัญญาณเทคนิคของระบบหลัก",
            }
        except (json.JSONDecodeError, KeyError, TypeError) as parse_exc:
            logger.warning(f"DeepSeek sell opinion returned malformed JSON: {parse_exc}")
            return {
                "status": "error",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "why_exit_now": "AI analysis unavailable",
                "risk_of_holding": "N/A",
                "counter_case": "N/A",
                "key_risks": ["AI analysis unavailable", "รูปแบบข้อมูลไม่ถูกต้อง"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - รูปแบบข้อมูลจาก AI ไม่ถูกต้อง ยึดตามสัญญาณเทคนิคของระบบหลัก",
            }
        except Exception as exc:
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            logger.warning(f"DeepSeek sell opinion call failed: {err_msg}")
            return {
                "status": "error",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "why_exit_now": "AI analysis unavailable",
                "risk_of_holding": "N/A",
                "counter_case": "N/A",
                "key_risks": ["AI analysis unavailable", f"เกิดข้อผิดพลาด: {err_msg}"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - โปรดยึดตามสัญญาณเทคนิคของระบบหลัก",
            }

    async def ask_buy_opinion(self, context: dict) -> dict:
        """DeepSeek second opinion for high-conviction BUY_NOW signals.
        DeepSeek must NOT create the BUY_NOW signal. It only analyzes an already-existing deterministic buy decision.
        """
        if not self.configured:
            return {
                "status": "unavailable",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": ["AI analysis unavailable", "ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY ในระบบ"],
                "watch_next": ["ยึดตามสัญญาณเทคนิคและระดับ Stop Loss / TP ของระบบ Deterministic"],
                "summary": "AI analysis unavailable - ระบบใช้การคำนวณแบบ Deterministic 100%",
            }

        system_prompt = (
            "คุณคือผู้ช่วยวิเคราะห์ทางเทคนิคสำหรับคริปโต (Second-opinion technical analyst)\n"
            "หน้าที่ของคุณคือให้ความเห็นประกอบการพิจารณาซื้อ (BUY_NOW second opinion) "
            "โดยวิเคราะห์สัญญาณซื้อที่มีความเชื่อมั่นสูง (High-conviction BUY) ที่คำนวณได้จากระบบเทคนิค (Deterministic Signal Engine) ของระบบหลัก\n\n"
            "ข้อกำหนดและข้อห้ามอย่างเด็ดขาด:\n"
            "1. คุณไม่ได้เป็นผู้สร้างสัญญาณ BUY_NOW ระบบหลักเป็นผู้ตัดสินใจทางเทคนิคไว้แล้ว คุณมีหน้าที่ให้ความเห็นที่สองเท่านั้น (DeepSeek must NOT create the BUY_NOW signal. It only analyzes an already-existing deterministic buy decision.)\n"
            "2. ห้ามดึงราคาจากภายนอกหรือสร้างข้อมูลที่ไม่มีอยู่ขึ้นมาเอง (Do not fetch market prices or invent missing data) ให้ใช้เฉพาะตัวเลขเชิงสถิติและบริบททางเทคนิคที่ได้รับเท่านั้น\n"
            "3. ห้ามสั่งเปิด/ปิดสถานะ หรือเปลี่ยนแปลงสถานะการเทรดในพอร์ตโฟลิโอ (Do not change trading state or open/close positions)\n"
            "4. ห้ามเปลี่ยนแปลงหรือกำหนดจุด Stop Loss / Take Profit ขึ้นมาใหม่แทนระบบหลัก (Do not invent or override stop/TP levels)\n"
            "5. ห้ามแนะนำให้ซื้อเฉลี่ยขาลงเด็ดขาด (Never recommend averaging down)\n"
            "6. ห้ามคำนวณบัญชีหรือดัดแปลงพอร์ตโฟลิโอ\n\n"
            "รูปแบบการตอบ:\n"
            "ต้องตอบเป็น JSON ภาษาไทยที่มีโครงสร้างตามคีย์ดังนี้เท่านั้น โดย confidence ต้องเป็นตัวเลข float ระหว่าง 0.0 ถึง 1.0:\n"
            "{\n"
            '  "assessment": "การประเมินสัญญาณซื้อในภาพรวม",\n'
            '  "confidence": 0.85,\n'
            '  "bull_case": "มุมมองเชิงบวกและปัจจัยทางเทคนิคที่สนับสนุนการปรับตัวขึ้น",\n'
            '  "bear_case": "มุมมองเชิงระมัดระวังหรือความเสี่ยงหากราคาไม่เป็นไปตามคาด",\n'
            '  "key_risks": ["ความเสี่ยงสำคัญข้อที่ 1", "ความเสี่ยงสำคัญข้อที่ 2"],\n'
            '  "watch_next": ["สิ่งที่ต้องจับตาข้อที่ 1", "สิ่งที่ต้องจับตาข้อที่ 2"],\n'
            '  "summary": "บทสรุปความเห็นที่สองเพื่อประกอบการพิจารณาของผู้ใช้"\n'
            "}"
        )

        user_prompt = (
            f"กรุณาวิเคราะห์สัญญาณซื้อ BUY_NOW สำหรับเหรียญต่อไปนี้:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            f"จงตอบเป็น JSON ภาษาไทยตามโครงสร้างที่กำหนดเท่านั้น โดย confidence ต้องเป็นตัวเลข float ระหว่าง 0.0 ถึง 1.0"
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                def _parse_confidence(val) -> float:
                    if isinstance(val, (int, float)):
                        return round(max(0.0, min(1.0, float(val))), 2)
                    if isinstance(val, str):
                        cleaned = val.replace("%", "").strip()
                        try:
                            num = float(cleaned)
                            if num > 1.0:
                                num = num / 100.0
                            return round(max(0.0, min(1.0, num)), 2)
                        except ValueError:
                            return 0.5
                    return 0.5

                def _ensure_list(val) -> list[str]:
                    if isinstance(val, list):
                        return [str(x) for x in val if x]
                    if isinstance(val, str) and val.strip():
                        lines = [line.strip("- *• \t\r") for line in val.split("\n") if line.strip()]
                        return lines if lines else [val.strip()]
                    return []

                return {
                    "status": "connected",
                    "assessment": str(parsed.get("assessment", "ประเมินสัญญาณซื้อตามระบบเทคนิค")),
                    "confidence": _parse_confidence(parsed.get("confidence", 0.7)),
                    "bull_case": str(parsed.get("bull_case", "สัญญาณและแนวโน้มขาขึ้นสอดคล้องทุกกรอบเวลา")),
                    "bear_case": str(parsed.get("bear_case", "ระมัดระวังความผันผวนหากไม่ผ่านแนวต้าน")),
                    "key_risks": _ensure_list(parsed.get("key_risks")) or ["ความผันผวนของราคา"],
                    "watch_next": _ensure_list(parsed.get("watch_next")) or ["เฝ้าระวังระดับราคาและ Stop Loss"],
                    "summary": str(parsed.get("summary", "โปรดยึดหลักวินัยการเทรดและจุด Stop Loss เป็นสำคัญ")),
                }
        except httpx.TimeoutException:
            logger.warning("DeepSeek buy opinion call timed out")
            return {
                "status": "timeout",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": ["AI analysis unavailable", "การเชื่อมต่อหมดเวลา"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - หมดเวลาเชื่อมต่อ โปรดยึดตามสัญญาณเทคนิคของระบบหลัก",
            }
        except (json.JSONDecodeError, KeyError, TypeError) as parse_exc:
            logger.warning(f"DeepSeek buy opinion returned malformed JSON: {parse_exc}")
            return {
                "status": "error",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": ["AI analysis unavailable", "รูปแบบข้อมูลไม่ถูกต้อง"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - รูปแบบข้อมูลจาก AI ไม่ถูกต้อง ยึดตามสัญญาณเทคนิคของระบบหลัก",
            }
        except Exception as exc:
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            logger.warning(f"DeepSeek buy opinion call failed: {err_msg}")
            return {
                "status": "error",
                "assessment": "AI analysis unavailable",
                "confidence": 0.0,
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": ["AI analysis unavailable", f"เกิดข้อผิดพลาด: {err_msg}"],
                "watch_next": ["ยึดตามระดับ Stop Loss และสัญญาณของระบบ Deterministic"],
                "summary": "AI analysis unavailable - โปรดยึดตามสัญญาณเทคนิคของระบบหลัก",
            }



class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token = token.strip() if token else None
        self.chat_id = chat_id.strip() if chat_id else None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, message: str) -> bool:
        if not self.configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                )
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning(f"Telegram notification delivery failed: {exc}")
            return False
