from __future__ import annotations

import logging
from typing import Any, Protocol

from .config import Settings
from .models import Disclosure
from .repository import Repository
from .webhook import WebhookNotifier

logger = logging.getLogger("idx")
MAX_RETRY_ATTEMPTS = 3
RETRY_BATCH_SIZE = 10


class DisclosureGraph(Protocol):
    """Seam LangGraph: pipeline hanya perlu menjalankan satu disclosure."""

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


def short_error(error: Exception) -> str:
    """Satu baris ringkas untuk log error jaringan/LLM."""
    message = str(error).strip().splitlines()[0] if str(error).strip() else "-"
    return f"{type(error).__name__}: {message[:160]}"


class Pipeline:
    """Distribusikan satu disclosure baru ke channel aktif.

    Interface: ``process(disclosure, notify=True) -> status``. Dedupe, status
    SQLite, webhook, dan graph berada di sini supaya HTTP/scheduler maupun tes
    memakai perilaku yang sama.
    """

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        graph: DisclosureGraph | None = None,
        webhook_notifier: WebhookNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.graph = graph
        self.webhook_notifier = webhook_notifier

    async def process(self, disclosure: Disclosure, notify: bool = True) -> str:
        """Simpan disclosure baru, lalu distribusikan ke channel yang aktif."""
        if not self.repository.insert_if_new(disclosure):
            return "duplicate"
        if not notify:
            self.repository.mark_skipped(disclosure.id, "Baseline awal (di luar jendela lookback)")
            return "baseline"

        statuses: list[str] = []
        await self._send_webhook(disclosure, statuses)
        await self._run_agent(disclosure, statuses)
        self._finish_without_agent(disclosure, statuses)

        status = "+".join(statuses)
        logger.info("%s | %s | %s", status, disclosure.issuer, disclosure.title[:70])
        return status

    async def retry_failed(self) -> list[str]:
        """Coba ulang agent untuk kegagalan lama, tanpa mengirim ulang webhook."""
        if self.graph is None:
            return []

        statuses: list[str] = []
        for disclosure in self.repository.failed_for_retry(MAX_RETRY_ATTEMPTS, RETRY_BATCH_SIZE):
            result: list[str] = []
            await self._run_agent(disclosure, result)
            status = result[-1]
            statuses.append(status)
            logger.info("retry %s | %s | %s", status, disclosure.issuer, disclosure.title[:70])
        return statuses

    async def _send_webhook(self, disclosure: Disclosure, statuses: list[str]) -> None:
        notifier = self.webhook_notifier
        if notifier is None:
            statuses.append("no_webhook")
        elif self.settings.webhook_respect_filter and not disclosure.is_relevant(
            self.settings.issuers, self.settings.keywords
        ):
            statuses.append("webhook_filtered")
        elif await notifier.send(disclosure):
            statuses.append("webhook_sent")
        else:
            statuses.append("webhook_failed")

    async def _run_agent(self, disclosure: Disclosure, statuses: list[str]) -> None:
        if self.graph is None:
            statuses.append("no_agent")
            return

        try:
            result = await self.graph.ainvoke({"disclosure": disclosure})
            penilaian = result.get("penilaian")
            if not result.get("relevant"):
                self.repository.mark_skipped(disclosure.id, "Tidak sesuai filter kata kunci")
                statuses.append("agent_skipped")
            elif not result.get("telegram_message"):
                alasan = (
                    f"Triage skor {penilaian.skor}/5 ({penilaian.kategori}): {penilaian.alasan}"
                    if penilaian
                    else "Dihentikan sebelum peringkasan"
                )
                self.repository.mark_skipped(disclosure.id, alasan)
                statuses.append("agent_skipped")
            else:
                self.repository.mark_processed(
                    disclosure.id,
                    result["summary"],
                    result["telegram_message"],
                )
                statuses.append("agent_sent")
        except Exception as error:
            self.repository.mark_failed(disclosure.id, str(error))
            logger.error(
                "agen gagal | %s | %s | %s",
                disclosure.issuer,
                disclosure.title[:60],
                short_error(error),
            )
            statuses.append("agent_failed")

    def _finish_without_agent(self, disclosure: Disclosure, statuses: list[str]) -> None:
        if self.graph is not None:
            return
        if "webhook_failed" in statuses:
            self.repository.mark_failed(disclosure.id, "Webhook keluar gagal")
        elif "webhook_filtered" in statuses:
            self.repository.mark_skipped(disclosure.id, "Tidak sesuai filter (webhook)")
        elif "webhook_sent" in statuses:
            self.repository.mark_processed(disclosure.id, "", "")
        else:
            self.repository.mark_skipped(disclosure.id, "Tidak ada channel notifikasi aktif")
