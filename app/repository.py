from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import Disclosure


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Buka transaksi SQLite dan selalu lepaskan file handle di Windows."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS disclosures (
                    id TEXT PRIMARY KEY,
                    published_at TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'received',
                    summary TEXT,
                    telegram_message TEXT,
                    error TEXT,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_disclosures_published_at
                    ON disclosures(published_at);
                """
            )

    def insert_if_new(self, disclosure: Disclosure) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO disclosures
                    (id, published_at, issuer, title, category, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    disclosure.id,
                    disclosure.published_at.isoformat(),
                    disclosure.issuer,
                    disclosure.title,
                    disclosure.category,
                    json.dumps(disclosure.raw, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def mark_processed(self, disclosure_id: str, summary: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE disclosures
                SET status = 'sent', summary = ?, telegram_message = ?,
                    error = NULL, processed_at = datetime('now')
                WHERE id = ?
                """,
                (summary, message, disclosure_id),
            )

    def mark_skipped(self, disclosure_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE disclosures SET status = 'skipped', error = ? WHERE id = ?",
                (reason, disclosure_id),
            )

    def mark_failed(self, disclosure_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE disclosures SET status = 'failed', error = ? WHERE id = ?",
                (error, disclosure_id),
            )

    def stats(self) -> dict[str, int]:
        """Hitungan disclosure per status (untuk endpoint /stats)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM disclosures GROUP BY status"
            ).fetchall()
        return {row["status"]: row["total"] for row in rows}
