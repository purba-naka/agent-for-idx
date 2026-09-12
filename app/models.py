from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Disclosure(BaseModel):
    id: str
    published_at: datetime
    issuer: str = "UNKNOWN"
    title: str
    announcement_number: str = ""
    category: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    def is_relevant(self, issuers: set[str], keywords: tuple[str, ...]) -> bool:
        """Cocok dengan filter emiten/kata kunci (filter kosong = lolos semua)."""
        issuer_ok = not issuers or self.issuer.upper() in issuers
        corpus = f"{self.issuer} {self.title} {self.category}".lower()
        keyword_ok = not keywords or any(keyword in corpus for keyword in keywords)
        return issuer_ok and keyword_ok


class IngestPayload(BaseModel):
    disclosures: list[Disclosure]
