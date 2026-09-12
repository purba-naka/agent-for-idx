from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.graph import Penilaian
from app.models import Disclosure
from app.pipeline import Pipeline
from app.repository import Repository


class GraphPalsu:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result

    async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.tempdir.name) / "test.sqlite3")
        self.repository.initialize()
        self.settings = Settings(database_path=self.repository.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def disclosure(id: str = "test") -> Disclosure:
        return Disclosure(
            id=id,
            published_at=datetime.now(),
            issuer="TEST",
            title="Pengumuman uji",
            category="Uji",
        )

    async def test_baseline_and_duplicate_do_not_notify(self) -> None:
        pipeline = Pipeline(self.settings, self.repository)
        item = self.disclosure()

        self.assertEqual(await pipeline.process(item, notify=False), "baseline")
        self.assertEqual(await pipeline.process(item), "duplicate")
        self.assertEqual(self.repository.stats(), {"skipped": 1})

    async def test_filter_rejection_is_recorded(self) -> None:
        pipeline = Pipeline(
            self.settings,
            self.repository,
            GraphPalsu({"relevant": False}),
        )

        self.assertEqual(await pipeline.process(self.disclosure()), "no_webhook+agent_skipped")
        self.assertEqual(self.repository.stats(), {"skipped": 1})

    async def test_triage_rejection_is_recorded(self) -> None:
        penilaian = Penilaian(
            skor=2, kategori="administratif", dampak="netral", alasan="laporan rutin"
        )
        pipeline = Pipeline(
            self.settings,
            self.repository,
            GraphPalsu({"relevant": True, "penilaian": penilaian}),
        )

        self.assertEqual(await pipeline.process(self.disclosure()), "no_webhook+agent_skipped")
        with self.repository._connect() as connection:  # noqa: SLF001 - assertion state storage
            error = connection.execute("SELECT error FROM disclosures").fetchone()["error"]
        self.assertEqual(error, "Triage skor 2/5 (administratif): laporan rutin")

    async def test_success_is_saved(self) -> None:
        pipeline = Pipeline(
            self.settings,
            self.repository,
            GraphPalsu(
                {
                    "relevant": True,
                    "summary": "ringkasan",
                    "telegram_message": "pesan",
                }
            ),
        )

        self.assertEqual(await pipeline.process(self.disclosure()), "no_webhook+agent_sent")
        self.assertEqual(self.repository.stats(), {"sent": 1})

    async def test_agent_error_is_failed(self) -> None:
        pipeline = Pipeline(self.settings, self.repository, GraphPalsu(RuntimeError("model mati")))

        self.assertEqual(await pipeline.process(self.disclosure()), "no_webhook+agent_failed")
        self.assertEqual(self.repository.stats(), {"failed": 1})

    async def test_failed_agent_is_retried_without_webhook_or_dedupe(self) -> None:
        item = self.disclosure()
        pipeline = Pipeline(self.settings, self.repository, GraphPalsu(RuntimeError("model mati")))
        await pipeline.process(item)
        pipeline.graph = GraphPalsu(
            {"relevant": True, "summary": "ringkasan", "telegram_message": "pesan"}
        )

        self.assertEqual(await pipeline.retry_failed(), ["agent_sent"])
        self.assertEqual(self.repository.stats(), {"sent": 1})
        with self.repository._connect() as connection:  # noqa: SLF001 - assertion state storage
            attempts = connection.execute("SELECT attempts FROM disclosures").fetchone()["attempts"]
        self.assertEqual(attempts, 1)

    async def test_failed_agent_stops_after_three_total_attempts(self) -> None:
        pipeline = Pipeline(self.settings, self.repository, GraphPalsu(RuntimeError("model mati")))
        await pipeline.process(self.disclosure())

        self.assertEqual(await pipeline.retry_failed(), ["agent_failed"])
        self.assertEqual(await pipeline.retry_failed(), ["agent_failed"])
        self.assertEqual(await pipeline.retry_failed(), [])
        with self.repository._connect() as connection:  # noqa: SLF001 - assertion state storage
            attempts = connection.execute("SELECT attempts FROM disclosures").fetchone()["attempts"]
        self.assertEqual(attempts, 3)


if __name__ == "__main__":
    unittest.main()
