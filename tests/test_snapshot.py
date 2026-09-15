from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.akumulasi import BarisBroker
from app.config import Settings
from app.repository import Repository
from app.snapshot import nilai_rekomendasi_matang, tarik_snapshot


def tabel(**periode: float) -> dict[str, list[BarisBroker]]:
    return {
        nama: [
            BarisBroker("AMMN", nilai, 0.0, 0.0, 0.0, 0.0),
            BarisBroker("BBCA", -nilai, 0.0, 0.0, 0.0, 0.0),
        ]
        for nama, nilai in periode.items()
    }


class RepositorySnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = Repository(Path(self.tempdir.name) / "uji.sqlite3")
        self.repo.initialize()

    def test_simpan_lalu_baca_kembali(self) -> None:
        jumlah = self.repo.simpan_snapshot(
            "2026-09-14", "foreign", tabel(**{"5d": 100.0, "20d": 50.0}), {"AMMN": 5100.0}
        )
        self.assertEqual(jumlah, 4)
        hasil = self.repo.snapshot("foreign")
        assert hasil is not None
        tanggal, dibaca = hasil
        self.assertEqual(tanggal, "2026-09-14")
        self.assertEqual({k: len(v) for k, v in dibaca.items()}, {"5d": 2, "20d": 2})
        self.assertEqual(dibaca["5d"][0].netval, 100.0)
        self.assertEqual(self.repo.harga_snapshot("2026-09-14", "ammn"), 5100.0)

    def test_menyimpan_ulang_tidak_menggandakan(self) -> None:
        """Job boleh diulang: dua kali sehari, atau setelah gagal separuh jalan."""
        for _ in range(2):
            self.repo.simpan_snapshot("2026-09-14", "foreign", tabel(**{"5d": 100.0}))
        hasil = self.repo.snapshot("foreign")
        assert hasil is not None
        self.assertEqual(len(hasil[1]["5d"]), 2)

    def test_kategori_terpisah(self) -> None:
        """Foreign dan institution bisa berlawanan; tidak boleh saling menimpa."""
        self.repo.simpan_snapshot("2026-09-14", "foreign", tabel(**{"5d": 100.0}))
        self.repo.simpan_snapshot("2026-09-14", "institution", tabel(**{"5d": -70.0}))
        asing = self.repo.snapshot("foreign")
        institusi = self.repo.snapshot("institution")
        assert asing is not None and institusi is not None
        self.assertEqual(asing[1]["5d"][0].netval, 100.0)
        self.assertEqual(institusi[1]["5d"][0].netval, -70.0)

    def test_tanggal_terbaru_lebih_dulu(self) -> None:
        for tanggal in ("2026-09-12", "2026-09-14", "2026-09-11"):
            self.repo.simpan_snapshot(tanggal, "foreign", tabel(**{"5d": 1.0}))
        self.assertEqual(
            self.repo.tanggal_snapshot("foreign"), ["2026-09-14", "2026-09-12"]
        )
        hasil = self.repo.snapshot("foreign")
        assert hasil is not None
        self.assertEqual(hasil[0], "2026-09-14")

    def test_belum_ada_data_mengembalikan_none(self) -> None:
        """Pemanggil harus lanjut tanpa blok akumulasi, bukan gagal."""
        self.assertIsNone(self.repo.snapshot("foreign"))
        self.assertIsNone(self.repo.harga_snapshot("2026-09-14", "AMMN"))

    def test_rasio_none_karena_rincian_tak_disimpan(self) -> None:
        self.repo.simpan_snapshot("2026-09-14", "foreign", tabel(**{"5d": 100.0}))
        hasil = self.repo.snapshot("foreign")
        assert hasil is not None
        self.assertIsNone(hasil[1]["5d"][0].rasio)

    def test_snapshot_pada_atau_sebelum_tidak_melihat_masa_depan(self) -> None:
        self.repo.simpan_snapshot("2026-09-12", "foreign", tabel(**{"5d": 10.0}))
        self.repo.simpan_snapshot("2026-09-16", "foreign", tabel(**{"5d": -10.0}))
        hasil = self.repo.snapshot_pada_atau_sebelum("foreign", "2026-09-15")
        assert hasil is not None
        self.assertEqual(hasil[0], "2026-09-12")
        self.assertEqual(hasil[1]["5d"][0].netval, 10.0)

    def test_rekomendasi_dinilai_setelah_tiga_snapshot_bursa(self) -> None:
        for tanggal, nilai in (
            ("2026-09-14", 100.0),
            ("2026-09-15", 80.0),
            ("2026-09-16", 50.0),
            ("2026-09-17", 20.0),
        ):
            self.repo.simpan_snapshot(tanggal, "foreign", tabel(**{"5d": nilai}))
        self.repo.simpan_rekomendasi(
            "d-1", "AMMN", "2026-09-14", "foreign",
            "akumulasi_berkelanjutan", 100.0, "pantau", "netval +100.0 M",
            "net sell", "sedang", 3,
        )

        settings = Settings(neobdm_kategori="foreign")
        self.assertEqual(nilai_rekomendasi_matang(settings, self.repo), 1)
        self.assertEqual(self.repo.rapor_lintasan(), {"akumulasi_berkelanjutan": (1, 1)})
        self.assertEqual(nilai_rekomendasi_matang(settings, self.repo), 0)  # idempoten


class AturanPalsu:
    neobdm_enabled = True
    neobdm_base_url = "https://contoh.test"
    neobdm_username = "u"
    neobdm_password = "p"
    neobdm_kategori = "foreign"


class KlienPalsu:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.tarikan = 0

    async def tanggal_data(self) -> str:
        return "2026-09-14"

    async def tabel_akumulasi(self) -> dict[str, list[BarisBroker]]:
        self.tarikan += 1
        return tabel(**{"5d": 100.0, "20d": 50.0})

    async def harga_semua(self) -> dict[str, float]:
        return {"AMMN": 5100.0}

    async def aclose(self) -> None:
        return None


class JobTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = Repository(Path(self.tempdir.name) / "uji.sqlite3")
        self.repo.initialize()
        self.klien = KlienPalsu()
        from app import snapshot

        asli = snapshot.NeoBDMClient
        snapshot.NeoBDMClient = lambda *a, **k: self.klien  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(snapshot, "NeoBDMClient", asli))

    async def test_menarik_lalu_menyimpan(self) -> None:
        tanggal = await tarik_snapshot(AturanPalsu(), self.repo)  # type: ignore[arg-type]
        self.assertEqual(tanggal, "2026-09-14")
        self.assertIsNotNone(self.repo.snapshot("foreign"))

    async def test_tanggal_sama_dilewati_tanpa_menarik(self) -> None:
        """NeoBDM end-of-day: menarik ulang tanggal yang sama buang ~47 permintaan."""
        await tarik_snapshot(AturanPalsu(), self.repo)  # type: ignore[arg-type]
        kedua = await tarik_snapshot(AturanPalsu(), self.repo)  # type: ignore[arg-type]
        self.assertIsNone(kedua)
        self.assertEqual(self.klien.tarikan, 1)

    async def test_paksa_menarik_ulang(self) -> None:
        await tarik_snapshot(AturanPalsu(), self.repo)  # type: ignore[arg-type]
        await tarik_snapshot(AturanPalsu(), self.repo, paksa=True)  # type: ignore[arg-type]
        self.assertEqual(self.klien.tarikan, 2)

    async def test_gagal_tidak_melempar(self) -> None:
        """Sumber sekunder; scheduler yang sama memegang polling IDX."""
        from app.neobdm import NeoBDMError

        async def meledak() -> str:
            raise NeoBDMError("login ditolak")

        self.klien.tanggal_data = meledak  # type: ignore[method-assign]
        self.assertIsNone(await tarik_snapshot(AturanPalsu(), self.repo))  # type: ignore[arg-type]

    async def test_nonaktif_dilewati(self) -> None:
        class Mati(AturanPalsu):
            neobdm_enabled = False

        self.assertIsNone(await tarik_snapshot(Mati(), self.repo))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
