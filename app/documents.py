from __future__ import annotations

import io

import requests
from pypdf import PdfReader

from .idx_client import HEADERS
from .models import Disclosure


MAX_DOCUMENT_CHARS = 12_000


def extract_primary_pdf(disclosure: Disclosure) -> str:
    """Download and extract the first IDX PDF attachment, if available."""
    attachment = next(
        (item for item in disclosure.attachments if item.get("FullSavePath")),
        None,
    )
    if not attachment:
        return ""

    response = requests.get(attachment["FullSavePath"], headers=HEADERS, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type and not response.content.startswith(b"%PDF-"):
        raise ValueError("Lampiran IDX bukan PDF")

    reader = PdfReader(io.BytesIO(response.content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:MAX_DOCUMENT_CHARS]
