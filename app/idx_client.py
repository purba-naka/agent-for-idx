from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from curl_cffi.curl import CurlError
from curl_cffi.requests import AsyncSession

from .config import Settings
from .models import Disclosure

logger = logging.getLogger(__name__)

API_URL = "https://www.idx.co.id/primary/ListedCompany/GetAnnouncement"
# Tanpa User-Agent: fingerprint lengkap (UA + TLS) disediakan impersonate="chrome".
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi",
}
# Timestamp pengumuman IDX (TglPengumuman) memakai waktu WIB tanpa offset.
WIB = timezone(timedelta(hours=7))
# PENTING: API GetAnnouncement mengembalikan Replies KOSONG untuk indexFrom>0
# ketika dateFrom/dateTo berupa rentang pendek (pola teramati langsung), walau
# ResultCount melaporkan total penuh. Sebaliknya, pageSize besar didukung
# (teruji sampai 500). Karena itu klien ini mengambil SATU halaman besar dan
# tidak memakai paging indexFrom; pastikan lookback cukup kecil agar muat.
PAGE_SIZE = 500
MAX_ATTEMPTS = 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class IDXClient:
    """Klien API Keterbukaan Informasi IDX.

    www.idx.co.id dilindungi Cloudflare bot-management: client Python biasa
    (httpx/requests) ditolak 403 dengan halaman "Just a moment...".
    curl_cffi dengan impersonate="chrome" meniru TLS fingerprint browser
    sehingga request JSON lolos tanpa perlu cookie manual.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_recent(self) -> list[Disclosure]:
        """Ambil pengumuman pada jendela lookback (default: 10 menit terakhir)."""
        now_wib = datetime.now(WIB)
        start_wib = now_wib - timedelta(minutes=self.settings.idx_lookback_minutes)
        async with AsyncSession(impersonate="chrome", headers=HEADERS, timeout=30) as session:
            return await self._fetch_range(
                session,
                start_wib.strftime("%Y%m%d"),
                now_wib.strftime("%Y%m%d"),
            )

    async def _fetch_range(
        self, session: AsyncSession, date_from: str, date_to: str
    ) -> list[Disclosure]:
        """Satu request dengan pageSize besar (lihat catatan PAGE_SIZE di atas).

        Hasil diurutkan IDX dari yang terbaru, jadi item baru selalu ada di
        halaman pertama. Bila ResultCount melebihi PAGE_SIZE, sebagian item
        lama tidak ikut -- cukupkan lookback atau jalankan ulang.
        """
        extra_headers = {"Cookie": self.settings.idx_cookie} if self.settings.idx_cookie else None
        params = {
            "kodeEmiten": "",
            "emitenType": self.settings.idx_emiten_type,
            "indexFrom": 0,
            "pageSize": PAGE_SIZE,
            "dateFrom": date_from,
            "dateTo": date_to,
            "lang": "id",
            "keyword": "",
        }
        response = await self._get_with_retry(session, params, extra_headers)
        if response.status_code == 403:
            raise RuntimeError(
                "IDX menolak request (HTTP 403 / proteksi Cloudflare). "
                "Coba perbarui curl_cffi ke versi terbaru (impersonate browser lebih baru) "
                "atau isi IDX_COOKIE dari sesi browser yang valid."
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        replies = payload.get("Replies", [])
        total = int(payload.get("ResultCount", 0))
        if total > len(replies):
            logger.warning(
                "Window %s-%s berisi %s pengumuman, hanya %s yang diambil (PAGE_SIZE=%s). "
                "Kecilkan IDX_LOOKBACK_MINUTES bila butuh kelengkapan.",
                date_from,
                date_to,
                total,
                len(replies),
                PAGE_SIZE,
            )
        return [self._to_disclosure(reply) for reply in replies]

    async def _get_with_retry(
        self, session: AsyncSession, params: dict[str, Any], headers: dict[str, str] | None
    ) -> Any:
        """Ambil pengumuman, ulangi hanya kegagalan jaringan/HTTP transien."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await session.get(API_URL, params=params, headers=headers)
                if response.status_code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                    return response
                wait = self._retry_wait(response, attempt)
                logger.warning(
                    "IDX HTTP %s; ulang %d/%d dalam %.1f dtk",
                    response.status_code,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    wait,
                )
            except CurlError as error:
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = 2.0 ** (attempt - 1)
                logger.warning(
                    "IDX jaringan gagal (%s); ulang %d/%d dalam %.1f dtk",
                    type(error).__name__,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    wait,
                )
            await asyncio.sleep(wait)
        raise AssertionError("retry IDX berakhir tanpa response atau exception")

    @staticmethod
    def _retry_wait(response: Any, attempt: int) -> float:
        try:
            return max(float(response.headers.get("Retry-After")), 0.5)
        except (AttributeError, TypeError, ValueError):
            return 2.0 ** (attempt - 1)

    @staticmethod
    def _to_disclosure(reply: dict[str, Any]) -> Disclosure:
        announcement = reply.get("pengumuman", {})
        disclosure_id = str(announcement.get("Id2") or announcement.get("NoPengumuman") or "")
        if not disclosure_id:
            raise ValueError("IDX announcement tanpa Id2/NoPengumuman")
        return Disclosure(
            id=disclosure_id,
            published_at=announcement["TglPengumuman"],
            issuer=(announcement.get("Kode_Emiten") or "UNKNOWN").strip(),
            title=announcement.get("JudulPengumuman") or "Tanpa judul",
            announcement_number=announcement.get("NoPengumuman") or "",
            category=announcement.get("JenisPengumuman") or "",
            attachments=reply.get("attachments") or [],
            raw=reply,
        )
