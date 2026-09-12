from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.repository import Repository


class RepositoryMigrationTest(unittest.TestCase):
    def test_initialize_adds_attempts_to_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE disclosures (
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
                    )
                    """
                )
                connection.commit()

            Repository(path).initialize()

            with closing(sqlite3.connect(path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(disclosures)")}
            self.assertIn("attempts", columns)


if __name__ == "__main__":
    unittest.main()
