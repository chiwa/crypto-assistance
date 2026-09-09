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

    async def ask_structured(self, context: dict) -> dict:
        if not self.configured:
            return {
                "assessment": "ไม่ได้กำหนดค่า DEEP_SEEK_API_KEY",
                "confidence": "ไม่สามารถระบุได้",
                "bull_case": "N/A",
                "bear_case": "N/A",
                "key_risks": "ไม่ได้เชื่อมต่อระบบ AI วิเคราะห์",
                "summary": "ระบบยังคงใช้การคำนวณแบบ Deterministic 100% ตามปกติ",
            }

        prompt = (
            "กรุณาวิเคราะห์บริบทการเทรดคริปโตต่อไปนี้เป็นภาษาไทย โดยให้ความเห็นที่ 2 (Second Opinion) "
            "ตอบกลับเป็น JSON ภาษาไทยที่มีคีย์ดังนี้เท่านั้น: "
            "assessment (การประเมินภาพรวม), confidence (ระดับความมั่นใจ: สูง/ปานกลาง/ระมัดระวัง), "
            "bull_case (มุมมองเชิงบวก), bear_case (มุมมองเชิงลบ), key_risks (ความเสี่ยงสำคัญ), "
            "summary (สรุปคำแนะนำเพื่อการตัดสินใจ)\n\n"
            f"บริบท: {json.dumps(context, ensure_ascii=False)}"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as exc:
            logger.warning(f"DeepSeek structured call failed: {exc}")
            return {
                "assessment": "ระบบ AI ไม่สามารถตอบกลับได้ในขณะนี้",
                "confidence": "ระมัดระวัง",
                "bull_case": "ดูตามสัญญาณระบบทางเทคนิคเดิม",
                "bear_case": "ความผันผวนของตลาดหรือการเชื่อมต่อขัดข้อง",
                "key_risks": f"เกิดข้อผิดพลาดในการเชื่อมต่อ: {exc}",
                "summary": "โปรดยึดหลักการบริหารความเสี่ยงและ Stop Loss ของระบบ Deterministic เป็นหลัก",
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
