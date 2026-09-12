from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.documents import MAX_DOCUMENT_CHARS, extract_primary_pdf
from app.models import Disclosure


class DocumentExtractionTest(unittest.TestCase):
    @staticmethod
    def disclosure(urls: list[str]) -> Disclosure:
        return Disclosure(
            id="test",
            published_at=datetime.now(timezone.utc),
            issuer="TEST",
            title="Pengumuman uji",
            attachments=[{"FullSavePath": url} for url in urls],
        )

    def test_combines_up_to_three_attachments_in_order(self) -> None:
        item = self.disclosure(["one.pdf", "two.pdf", "three.pdf", "four.pdf"])
        with patch(
            "app.documents._download_and_parse", side_effect=["satu", "dua", "tiga"]
        ) as parse:
            text = extract_primary_pdf(item)

        self.assertEqual(text, "[Lampiran 1]\nsatu\n\n[Lampiran 2]\ndua\n\n[Lampiran 3]\ntiga")
        self.assertEqual(
            [call.args[0] for call in parse.call_args_list],
            ["one.pdf", "two.pdf", "three.pdf"],
        )

    def test_continues_when_an_attachment_fails(self) -> None:
        item = self.disclosure(["broken.pdf", "good.pdf"])
        with patch("app.documents._download_and_parse", side_effect=[ValueError("rusak"), "isi"]):
            text = extract_primary_pdf(item)

        self.assertEqual(text, "[Lampiran 2]\nisi")

    def test_skips_non_pdf_attachments_before_download(self) -> None:
        item = self.disclosure(["utama.pdf", "data.xlsx", "lampiran.pdf"])
        with patch("app.documents._download_and_parse", side_effect=["utama", "lampiran"]) as parse:
            text = extract_primary_pdf(item)

        self.assertEqual(text, "[Lampiran 1]\nutama\n\n[Lampiran 2]\nlampiran")
        self.assertEqual([call.args[0] for call in parse.call_args_list], ["utama.pdf", "lampiran.pdf"])

    def test_respects_total_character_budget(self) -> None:
        item = self.disclosure(["one.pdf", "two.pdf"])
        first = "a" * 11_900
        with patch("app.documents._download_and_parse", side_effect=[first, "b" * 100]) as parse:
            text = extract_primary_pdf(item)

        self.assertEqual(len(text), MAX_DOCUMENT_CHARS)
        self.assertEqual(parse.call_args_list[1].args, ("two.pdf", 72))


if __name__ == "__main__":
    unittest.main()
