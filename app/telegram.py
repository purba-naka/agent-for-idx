from __future__ import annotations

import httpx

from .config import Settings


async def send_telegram(settings: Settings, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": True},
        )
    payload = response.json()
    if not response.is_success or not payload.get("ok"):
        raise RuntimeError(f"Telegram API gagal ({response.status_code}): {payload}")
