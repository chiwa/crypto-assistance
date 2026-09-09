import httpx


class DeepSeekSecondOpinion:
    def __init__(self, api_key: str | None):
        self.api_key = api_key

    async def ask(self, context: dict, question: str) -> str:
        if not self.api_key:
            raise RuntimeError("DEEP_SEEK_API_KEY is not configured")
        prompt = f"Review this deterministic crypto analysis as a second opinion only. Do not execute trades. Context: {context}. Question: {question}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token, self.chat_id = token, chat_id

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, message: str) -> None:
        if not self.configured:
            raise RuntimeError("Telegram is not fully configured")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={"chat_id": self.chat_id, "text": message})
            response.raise_for_status()
