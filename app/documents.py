from __future__ import annotations

import io
import logging

from curl_cffi.requests import Session
from pypdf import PdfReader

from .idx_client import HEADERS
from .models import Disclosure

logger = logging.getLogger("idx")

MAX_DOCUMENT_CHARS = 12_000
MAX_ATTACHMENTS = 3


def extract_primary_pdf(disclosure: Disclosure) -> str:
    """Gabungkan teks sampai tiga PDF IDX, maksimal 12.000 karakter.

    Kontrak: TIDAK PERNAH melempar exception. Lampiran adalah bahan tambahan
    bagi peringkas; kegagalan satu file tidak boleh membatalkan lampiran
    berikutnya atau menjatuhkan notifikasi.
    """
    urls = [
        item["FullSavePath"]
        for item in disclosure.attachments
        if item.get("FullSavePath")
    ][:MAX_ATTACHMENTS]
    if not urls:
        return ""

    chunks: list[str] = []
    remaining = MAX_DOCUMENT_CHARS
    for number, url in enumerate(urls, start=1):
        separator = "\n\n" if chunks else ""
        header = f"{separator}[Lampiran {number}]\n"
        available = remaining - len(header)
        if available <= 0:
            break
        try:
            text = _download_and_parse(url, available)[:available]
        except Exception as error:
            logger.warning(
                "lampiran %d gagal dibaca | %s | %s: %s",
                number,
                disclosure.issuer,
                type(error).__name__,
                str(error).splitlines()[0][:120] if str(error).strip() else "-",
            )
            continue
        if text:
            chunks.append(f"{header}{text}")
            remaining -= len(header) + len(text)
        if remaining <= 0:
            break
    return "".join(chunks)


def _download_and_parse(url: str, max_chars: int = MAX_DOCUMENT_CHARS) -> str:
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
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:max_chars]
