from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Settings

MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def send_telegram(settings: Settings, text: str) -> None:
    """Kirim pesan Telegram, ulangi hanya kegagalan transien."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        await _post_with_retry(client, url, payload)


async def _post_with_retry(client: Any, url: str, payload: dict[str, Any]) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.post(url, json=payload)
            body = response.json()
            if response.is_success and body.get("ok"):
                return
            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                await asyncio.sleep(_retry_wait(response, body, attempt))
                continue
            raise RuntimeError(f"Telegram API gagal ({response.status_code}): {body}")
        except httpx.HTTPError:
            if attempt == MAX_ATTEMPTS:
                raise
            await asyncio.sleep(2.0 ** (attempt - 1))


def _retry_wait(response: Any, body: dict[str, Any], attempt: int) -> float:
    """Telegram memakai JSON parameters.retry_after; header sebagai fallback."""
    retry_after = body.get("parameters", {}).get("retry_after")
    if retry_after is None:
        retry_after = response.headers.get("Retry-After")
    try:
        return max(float(retry_after), 0.5)
    except (TypeError, ValueError):
        return 2.0 ** (attempt - 1)
