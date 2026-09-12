from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .models import Disclosure

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTACHMENTS = 15


def _attachment_list(
    disclosure: Disclosure, max_items: int = MAX_ATTACHMENTS
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for attachment in disclosure.attachments[:max_items]:
        url = attachment.get("FullSavePath", "")
        items.append(
            {
                "name": attachment.get("OriginalFilename") or url.rsplit("/", 1)[-1] or "dokumen",
                "url": url,
                "is_attachment": bool(attachment.get("IsAttachment", False)),
            }
        )
    return items


def build_generic(disclosure: Disclosure) -> dict[str, Any]:
    """Payload JSON generik untuk endpoint milik Anda sendiri (POST JSON)."""
    return {
        "event": "idx.disclosure.new",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "id": disclosure.id,
            "published_at": disclosure.published_at.isoformat(),
            "issuer": disclosure.issuer,
            "title": disclosure.title,
            "announcement_number": disclosure.announcement_number,
            "category": disclosure.category,
            "attachments": _attachment_list(disclosure),
        },
    }


def build_discord(disclosures: list[Disclosure]) -> dict[str, Any]:
    """Payload Discord webhook (embed, maksimal 10 per pesan)."""
    embeds = []
    for disclosure in disclosures[:10]:
        attachments = _attachment_list(disclosure, max_items=8)
        documents = "\n".join(f"[{a['name'][:80]}]({a['url']})" for a in attachments) or "-"
        embeds.append(
            {
                "title": f"[{disclosure.issuer}] {disclosure.title}"[:256],
                "url": attachments[0]["url"] if attachments else "",
                "description": (
                    f"No: {disclosure.announcement_number or '-'} • "
                    f"Kategori: {disclosure.category or '-'} • "
                    f"{disclosure.published_at:%Y-%m-%d %H:%M} WIB"
                )[:4096],
                "color": 0x1ABC9C,
                "fields": [{"name": "Dokumen", "value": documents[:1024]}],
                "timestamp": disclosure.published_at.replace(tzinfo=WIB).isoformat(),
            }
        )
    return {"username": "IDX Keterbukaan Informasi", "embeds": embeds}


def build_slack(disclosure: Disclosure) -> dict[str, Any]:
    """Payload Slack Incoming Webhook."""
    attachments = _attachment_list(disclosure, max_items=8)
    documents = "\n".join(f"• <{a['url']}|{a['name'][:60]}>" for a in attachments) or "-"
    text = (
        f"*[{disclosure.issuer}] {disclosure.title}*\n"
        f"No: {disclosure.announcement_number or '-'} • Kategori: {disclosure.category or '-'} • "
        f"{disclosure.published_at:%Y-%m-%d %H:%M} WIB\n{documents}"
    )
    return {"text": text[:3900]}


class WebhookNotifier:
    """Pengirim webhook keluar (outbound) setiap ada disclosure baru."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.format = settings.webhook_format
        headers = {"Content-Type": "application/json"}
        if settings.webhook_secret:
            headers["X-Webhook-Secret"] = settings.webhook_secret
        self._headers = headers

    def _payload(self, disclosure: Disclosure) -> dict[str, Any]:
        if self.format == "discord":
            return build_discord([disclosure])
        if self.format == "slack":
            return build_slack(disclosure)
        return build_generic(disclosure)

    async def send(self, disclosure: Disclosure) -> bool:
        """Kirim satu disclosure; True bila diterima target (2xx)."""
        payload = self._payload(disclosure)
        async with httpx.AsyncClient(timeout=15) as client:
            return await self._post(client, payload)

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> bool:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    self.settings.webhook_url, json=payload, headers=self._headers
                )
                if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(self._retry_wait(response, attempt))
                    continue
                if 400 <= response.status_code < 500:
                    logger.error(
                        "Webhook ditolak target (HTTP %s): %.300s",
                        response.status_code,
                        response.text,
                    )
                    return False
                response.raise_for_status()
                return True
            except httpx.HTTPError as error:
                if attempt == MAX_ATTEMPTS:
                    logger.error("Webhook gagal setelah %d percobaan: %s", MAX_ATTEMPTS, error)
                    return False
                await asyncio.sleep(2.0 ** (attempt - 1))
        return False

    @staticmethod
    def _retry_wait(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        try:
            return max(float(retry_after), 0.5)
        except (TypeError, ValueError):
            return 2.0 ** (attempt - 1)
