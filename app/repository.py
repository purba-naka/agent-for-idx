from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .akumulasi import BarisBroker
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
                    processed_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_disclosures_published_at
                    ON disclosures(published_at);

                -- Aliran dana harian per emiten. `tanggal` adalah last-update
                -- NeoBDM (tanggal data), bukan tanggal job berjalan: keduanya
                -- berbeda bila job telat atau bursa libur.
                -- `kategori` ikut kunci karena foreign dan institution bisa
                -- memberi kesimpulan berlawanan untuk emiten yang sama.
                CREATE TABLE IF NOT EXISTS snapshot_aliran (
                    tanggal TEXT NOT NULL,
                    kategori TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    periode TEXT NOT NULL,
                    netval REAL NOT NULL,
                    PRIMARY KEY (tanggal, kategori, symbol, periode)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS snapshot_harga (
                    tanggal TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    close REAL NOT NULL,
                    PRIMARY KEY (tanggal, symbol)
                ) WITHOUT ROWID;

                -- Rekomendasi disimpan supaya bisa dibuktikan salah di
                -- kemudian hari. Tanpa tabel ini agent hanya bercerita:
                -- tidak ada catatan berapa kali tesisnya meleset.
                -- `hasil` NULL berarti belum jatuh tempo penilaian.
                CREATE TABLE IF NOT EXISTS rekomendasi (
                    disclosure_id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    tanggal_snapshot TEXT NOT NULL,
                    kategori TEXT NOT NULL,
                    lintasan TEXT NOT NULL,
                    netval_5d REAL NOT NULL,
                    tesis TEXT NOT NULL,
                    dasar TEXT NOT NULL,
                    pembantah TEXT NOT NULL,
                    keyakinan TEXT NOT NULL,
                    horizon_hari INTEGER NOT NULL,
                    dibuat_at TEXT NOT NULL DEFAULT (datetime('now')),
                    hasil TEXT,
                    catatan_hasil TEXT
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(disclosures)").fetchall()
            }
            if "attempts" not in columns:
                connection.execute(
                    "ALTER TABLE disclosures ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
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
                "UPDATE disclosures SET status = 'failed', error = ?, attempts = attempts + 1 WHERE id = ?",
                (error, disclosure_id),
            )

    def failed_for_retry(self, max_attempts: int, limit: int) -> list[Disclosure]:
        """Item gagal yang masih boleh diulang, diurutkan dari kegagalan terlama."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, published_at, issuer, title, category, raw_json
                FROM disclosures
                WHERE status = 'failed' AND attempts < ?
                ORDER BY rowid
                LIMIT ?
                """,
                (max_attempts, limit),
            ).fetchall()
        return [self._disclosure_from_row(row) for row in rows]

    @staticmethod
    def _disclosure_from_row(row: sqlite3.Row) -> Disclosure:
        raw = json.loads(row["raw_json"])
        announcement = raw.get("pengumuman", {})
        return Disclosure(
            id=row["id"],
            published_at=row["published_at"],
            issuer=row["issuer"],
            title=row["title"],
            category=row["category"],
            announcement_number=announcement.get("NoPengumuman") or "",
            attachments=raw.get("attachments") or [],
            raw=raw,
        )

    # --- Snapshot aliran dana harian ---

    def simpan_snapshot(
        self,
        tanggal: str,
        kategori: str,
        tabel: dict[str, list[BarisBroker]],
        harga: dict[str, float] | None = None,
    ) -> int:
        """Simpan satu hari aliran; idempoten agar job boleh diulang.

        Harga ikut disimpan meski `analisis()` belum memakainya: data pasar
        kemarin tidak bisa ditarik ulang besok, dan penilaian rekomendasi
        nantinya perlu tahu harga bergerak ke mana.
        """
        baris = [
            (tanggal, kategori, b.symbol, periode, b.netval)
            for periode, isi in tabel.items()
            for b in isi
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO snapshot_aliran
                    (tanggal, kategori, symbol, periode, netval)
                VALUES (?, ?, ?, ?, ?)
                """,
                baris,
            )
            if harga:
                connection.executemany(
                    "INSERT OR REPLACE INTO snapshot_harga (tanggal, symbol, close) VALUES (?, ?, ?)",
                    [(tanggal, simbol, nilai) for simbol, nilai in harga.items()],
                )
        return len(baris)

    def tanggal_snapshot(self, kategori: str, limit: int = 2) -> list[str]:
        """Tanggal snapshot terbaru lebih dulu; pemindai lompatan butuh dua."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT tanggal FROM snapshot_aliran
                WHERE kategori = ? ORDER BY tanggal DESC LIMIT ?
                """,
                (kategori, limit),
            ).fetchall()
        return [row["tanggal"] for row in rows]

    def snapshot_pada_atau_sebelum(
        self, kategori: str, tanggal_batas: str
    ) -> tuple[str, dict[str, list[BarisBroker]]] | None:
        """Snapshot terakhir yang sudah tersedia saat disclosure terbit.

        Retry disclosure lama tidak boleh memakai snapshot masa depan karena itu
        akan mengubah analisis pre-positioning menjadi hindsight.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(tanggal) AS tanggal FROM snapshot_aliran
                WHERE kategori = ? AND tanggal <= ?
                """,
                (kategori, tanggal_batas),
            ).fetchone()
        return self.snapshot(kategori, row["tanggal"]) if row and row["tanggal"] else None

    def snapshot(
        self, kategori: str, tanggal: str | None = None
    ) -> tuple[str, dict[str, list[BarisBroker]]] | None:
        """Satu hari aliran dalam bentuk yang diterima `akumulasi.analisis()`.

        Tanpa `tanggal` berarti yang terbaru. None bila belum ada data sama
        sekali -- pemanggil harus melanjutkan tanpa blok akumulasi, bukan gagal.
        """
        if tanggal is None:
            tersedia = self.tanggal_snapshot(kategori, limit=1)
            if not tersedia:
                return None
            tanggal = tersedia[0]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, periode, netval FROM snapshot_aliran
                WHERE kategori = ? AND tanggal = ?
                """,
                (kategori, tanggal),
            ).fetchall()
        if not rows:
            return None
        tabel: dict[str, list[BarisBroker]] = {}
        for row in rows:
            # Nol di sini berarti "tidak diketahui": NeoBDM hanya memberi aliran
            # bersih, jadi `rasio` sengaja bernilai None alih-alih dikarang.
            tabel.setdefault(row["periode"], []).append(
                BarisBroker(
                    symbol=row["symbol"],
                    netval=row["netval"],
                    bval=0.0,
                    sval=0.0,
                    bavg=0.0,
                    savg=0.0,
                )
            )
        return tanggal, tabel

    def harga_snapshot(self, tanggal: str, symbol: str) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT close FROM snapshot_harga WHERE tanggal = ? AND symbol = ?",
                (tanggal, symbol.upper()),
            ).fetchone()
        return row["close"] if row else None

    # --- Rekomendasi dan penilaiannya ---

    def simpan_rekomendasi(
        self,
        disclosure_id: str,
        issuer: str,
        tanggal_snapshot: str,
        kategori: str,
        lintasan: str,
        netval_5d: float,
        tesis: str,
        dasar: str,
        pembantah: str,
        keyakinan: str,
        horizon_hari: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rekomendasi
                    (disclosure_id, issuer, tanggal_snapshot, kategori, lintasan,
                     netval_5d, tesis, dasar, pembantah, keyakinan, horizon_hari)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(disclosure_id) DO UPDATE SET
                    issuer = excluded.issuer,
                    tanggal_snapshot = excluded.tanggal_snapshot,
                    kategori = excluded.kategori,
                    lintasan = excluded.lintasan,
                    netval_5d = excluded.netval_5d,
                    tesis = excluded.tesis,
                    dasar = excluded.dasar,
                    pembantah = excluded.pembantah,
                    keyakinan = excluded.keyakinan,
                    horizon_hari = excluded.horizon_hari
                """,
                (
                    disclosure_id,
                    issuer.upper(),
                    tanggal_snapshot,
                    kategori,
                    lintasan,
                    netval_5d,
                    tesis,
                    dasar,
                    pembantah,
                    keyakinan,
                    horizon_hari,
                ),
            )

    def rekomendasi_belum_dinilai(self, sebelum: str) -> list[sqlite3.Row]:
        """Rekomendasi yang snapshot-nya lebih tua dari `sebelum` dan belum dinilai."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM rekomendasi
                WHERE hasil IS NULL AND tanggal_snapshot < ?
                ORDER BY tanggal_snapshot
                """,
                (sebelum,),
            ).fetchall()

    def nilai_rekomendasi(self, disclosure_id: str, hasil: str, catatan: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE rekomendasi SET hasil = ?, catatan_hasil = ? WHERE disclosure_id = ?",
                (hasil, catatan, disclosure_id),
            )

    def rapor_lintasan(self) -> dict[str, tuple[int, int]]:
        """Per lintasan: (jumlah bertahan, jumlah dinilai).

        Inilah satu-satunya alat ukur apakah tesis agent lebih baik daripada
        tebakan; dipakai manusia, bukan diumpankan balik ke prompt.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT lintasan,
                       SUM(hasil = 'bertahan') AS benar,
                       COUNT(*) AS total
                FROM rekomendasi WHERE hasil IS NOT NULL
                GROUP BY lintasan
                """
            ).fetchall()
        return {row["lintasan"]: (row["benar"] or 0, row["total"]) for row in rows}

    def stats(self) -> dict[str, int]:
        """Hitungan disclosure per status (untuk endpoint /stats)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM disclosures GROUP BY status"
            ).fetchall()
        return {row["status"]: row["total"] for row in rows}
