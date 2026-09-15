from __future__ import annotations

import unittest

from app.akumulasi import MIN_NETVAL, BarisBroker, analisis


def baris(symbol: str, netval: float, *, bval: float | None = None, bavg: float = 1000.0) -> BarisBroker:
    """Baris bikinan: bval/sval diturunkan dari netval agar rasio jauh dari churn."""
    if bval is None:
        bval = abs(netval) * 2 if netval >= 0 else abs(netval) * 0.5
    sval = bval - netval
    return BarisBroker(symbol=symbol, netval=netval, bval=bval, sval=sval, bavg=bavg, savg=bavg)


# Latar belakang: emiten lain agar peringkat punya arti.
LATAR = [baris("AAAA", 300.0), baris("BBBB", 200.0), baris("CCCC", -250.0), baris("DDDD", -150.0)]


def tabel(**periode: float) -> dict[str, list[BarisBroker]]:
    return {nama: [baris("TARGET", nilai), *LATAR] for nama, nilai in periode.items()}


class LintasanTest(unittest.TestCase):
    def test_akumulasi_berkelanjutan(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": 40.0, "10d": 70.0, "20d": 120.0, "60d": 300.0}))
        self.assertEqual(hasil.lintasan, "akumulasi_berkelanjutan")
        self.assertEqual(len(hasil.jejak), 4)

    def test_akumulasi_selesai(self) -> None:
        # Beli di jendela panjang, jendela pendek sudah di bawah ambang kebisingan.
        hasil = analisis("TARGET", tabel(**{"20d": 120.0, "60d": 300.0}))
        self.assertEqual(hasil.lintasan, "akumulasi_selesai")

    def test_akumulasi_baru(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": 40.0, "10d": 45.0}))
        self.assertEqual(hasil.lintasan, "akumulasi_baru")

    def test_distribusi_setelah_beli_panjang(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": -60.0, "20d": 120.0, "60d": 300.0}))
        self.assertEqual(hasil.lintasan, "distribusi")

    def test_jual_pendek_tanpa_beli_panjang_tetap_distribusi(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": -60.0, "10d": -80.0}))
        self.assertEqual(hasil.lintasan, "distribusi")

    def test_tidak_terpantau_saat_absen(self) -> None:
        hasil = analisis("TARGET", {"5d": LATAR, "60d": LATAR})
        self.assertEqual(hasil.lintasan, "tidak_terpantau")
        self.assertEqual(hasil.jejak, [])

    def test_tidak_terpantau_saat_tabel_kosong(self) -> None:
        self.assertEqual(analisis("TARGET", {}).lintasan, "tidak_terpantau")


class AmbangTest(unittest.TestCase):
    def test_di_bawah_min_netval_diabaikan(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": MIN_NETVAL - 0.1, "60d": 300.0}))
        self.assertEqual([j.periode for j in hasil.jejak], ["60d"])

    def test_tepat_di_min_netval_dihitung(self) -> None:
        hasil = analisis("TARGET", tabel(**{"5d": MIN_NETVAL}))
        self.assertEqual([j.periode for j in hasil.jejak], ["5d"])

    def test_periode_hilang_bukan_nol(self) -> None:
        # Hanya 60d yang ada: tak boleh disimpulkan distribusi hanya karena 5d absen.
        hasil = analisis("TARGET", tabel(**{"60d": 300.0}))
        self.assertEqual(hasil.lintasan, "akumulasi_selesai")


class PeringkatTest(unittest.TestCase):
    def test_peringkat_dihitung_di_antara_net_buyer(self) -> None:
        jejak = analisis("TARGET", tabel(**{"5d": 250.0})).jejak[0]
        # Net buyer: AAAA 300, TARGET 250, BBBB 200 -> tiga baris, TARGET kedua.
        self.assertEqual((jejak.peringkat, jejak.dari), (2, 3))

    def test_net_seller_diperingkat_di_antara_net_seller(self) -> None:
        jejak = analisis("TARGET", tabel(**{"5d": -200.0})).jejak[0]
        # Net seller: CCCC -250, TARGET -200, DDDD -150.
        self.assertEqual((jejak.peringkat, jejak.dari), (2, 3))

    def test_urutan_sumber_diabaikan(self) -> None:
        acak = {"5d": [LATAR[1], baris("TARGET", 250.0), LATAR[0], LATAR[2], LATAR[3]]}
        self.assertEqual(analisis("TARGET", acak).jejak[0].peringkat, 2)

    def test_kode_case_insensitive(self) -> None:
        self.assertEqual(analisis("target", tabel(**{"5d": 40.0})).kode, "TARGET")


class CatatanTest(unittest.TestCase):
    def test_churn_terdeteksi(self) -> None:
        # bval 500 / sval 490 = 1.02x: dua arah besar, netval 10 tak bermakna.
        data = {"5d": [BarisBroker("TARGET", 10.0, 500.0, 490.0, 1000.0, 1000.0), *LATAR]}
        catatan = " ".join(analisis("TARGET", data).catatan)
        self.assertIn("rotasi", catatan)

    def test_tanpa_churn_saat_rasio_jauh(self) -> None:
        catatan = " ".join(analisis("TARGET", tabel(**{"5d": 40.0})).catatan)
        self.assertNotIn("rotasi", catatan)

    def test_lonjakan_tunggal(self) -> None:
        # 5d menjelaskan 95% dari 60d -- satu order, bukan penyerapan bertahap.
        catatan = " ".join(analisis("TARGET", tabel(**{"5d": 95.0, "60d": 100.0})).catatan)
        self.assertIn("satu order", catatan)

    def test_akumulasi_bertahap_bukan_lonjakan(self) -> None:
        catatan = " ".join(analisis("TARGET", tabel(**{"5d": 40.0, "60d": 300.0})).catatan)
        self.assertNotIn("satu order", catatan)

    def test_lonjakan_tak_berlaku_pada_penjualan(self) -> None:
        catatan = " ".join(analisis("TARGET", tabel(**{"5d": -95.0, "60d": -100.0})).catatan)
        self.assertNotIn("satu order", catatan)

    def test_harga_di_atas_bavg(self) -> None:
        data = {"5d": [baris("TARGET", 40.0, bavg=1000.0), *LATAR]}
        catatan = " ".join(analisis("TARGET", data, harga_terakhir=1200.0).catatan)
        self.assertIn("sudah untung", catatan)

    def test_harga_di_bawah_bavg(self) -> None:
        data = {"5d": [baris("TARGET", 40.0, bavg=1000.0), *LATAR]}
        catatan = " ".join(analisis("TARGET", data, harga_terakhir=800.0).catatan)
        self.assertIn("bawah air", catatan)

    def test_tanpa_harga_tak_ada_catatan_harga(self) -> None:
        catatan = " ".join(analisis("TARGET", tabel(**{"5d": 40.0})).catatan)
        self.assertNotIn("bavg", catatan)


class RingkasTest(unittest.TestCase):
    def test_angka_mentah_ikut(self) -> None:
        teks = analisis("TARGET", tabel(**{"5d": 40.0, "60d": 300.0})).ringkas()
        self.assertIn("akumulasi_berkelanjutan", teks)
        self.assertIn("+40.0 M", teks)
        self.assertIn("+300.0 M", teks)

    def test_rasio_tak_terdefinisi_dihilangkan(self) -> None:
        data = {"5d": [BarisBroker("TARGET", 40.0, 40.0, 0.0, 1000.0, 0.0)]}
        self.assertNotIn("bval/sval", analisis("TARGET", data).ringkas())

    def test_sumber_tanpa_rincian_tidak_menulis_nol(self) -> None:
        """NeoBDM hanya memberi aliran bersih; 'bavg 0' akan terbaca nol sungguhan."""
        data = {"5d": [BarisBroker("TARGET", 40.0, 0.0, 0.0, 0.0, 0.0)]}
        teks = analisis("TARGET", data).ringkas()
        self.assertIn("+40.0 M", teks)
        self.assertNotIn("bavg", teks)


if __name__ == "__main__":
    unittest.main()
