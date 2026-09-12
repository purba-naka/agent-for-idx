from __future__ import annotations

import io
import logging

from curl_cffi.requests import Session
from pypdf import PdfReader

from .idx_client import HEADERS
from .models import Disclosure

logger = logging.getLogger("idx")

MAX_DOCUMENT_CHARS = 12_000


def extract_primary_pdf(disclosure: Disclosure) -> str:
    """Teks lampiran PDF pertama; string kosong bila tidak tersedia.

    Kontrak: TIDAK PERNAH melempar exception. Dokumen hanyalah bahan tambahan
    bagi peringkas -- kegagalan membacanya tidak boleh menjatuhkan notifikasi,
    dan caller tidak perlu tahu library apa yang dipakai di dalam (pypdf hari
    ini, mungkin docling nanti) apalagi menebak kelas exception-nya.
    """
    url = next(
        (item["FullSavePath"] for item in disclosure.attachments if item.get("FullSavePath")),
        "",
    )
    if not url:
        return ""

    try:
        return _download_and_parse(url)
    except Exception as error:
        logger.warning(
            "lampiran gagal dibaca | %s | %s: %s",
            disclosure.issuer,
            type(error).__name__,
            str(error).splitlines()[0][:120] if str(error).strip() else "-",
        )
        return ""


def _download_and_parse(url: str) -> str:
    """Unduh lampiran lalu ambil teksnya.

    curl_cffi, bukan requests: StaticData IDX berada di balik Cloudflare yang
    sama dengan API-nya, dan menolak klien Python biasa dengan 403 + halaman
    HTML. impersonate="chrome" meniru TLS fingerprint browser.
    """
    with Session(impersonate="chrome", headers=HEADERS, timeout=60) as session:
        response = session.get(url)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type and not response.content.startswith(b"%PDF-"):
        raise ValueError("Lampiran IDX bukan PDF")

    reader = PdfReader(io.BytesIO(response.content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:MAX_DOCUMENT_CHARS]
