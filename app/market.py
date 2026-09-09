import time

import httpx


PAIR_TO_SYMBOL = {
    "BTC/THB": "BTC_THB",
    "ETH/THB": "ETH_THB",
    "SOL/THB": "SOL_THB",
    "XRP/THB": "XRP_THB",
    "DOGE/THB": "DOGE_THB",
}
RESOLUTIONS = {"15m": "15", "1h": "60", "4h": "240"}


class BitkubMarketData:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def candles(self, pair: str, timeframe: str, limit: int = 120) -> dict[str, list[float]]:
        resolution = RESOLUTIONS[timeframe]
        seconds = int(resolution) * 60
        now = int(time.time())
        params = {"symbol": PAIR_TO_SYMBOL[pair], "resolution": resolution, "from": now - seconds * limit, "to": now}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/tradingview/history", params=params)
            response.raise_for_status()
            data = response.json()
        if data.get("s") != "ok" or not data.get("c"):
            raise RuntimeError(f"No candle data for {pair} {timeframe}")
        return {"highs": data["h"], "lows": data["l"], "closes": data["c"], "volumes": data["v"], "times": data["t"]}
