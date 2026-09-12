from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
        response = await session.get(
            API_URL,
            params={
                "kodeEmiten": "",
                "emitenType": self.settings.idx_emiten_type,
                "indexFrom": 0,
                "pageSize": PAGE_SIZE,
                "dateFrom": date_from,
                "dateTo": date_to,
                "lang": "id",
                "keyword": "",
            },
            headers=extra_headers,
        )
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
