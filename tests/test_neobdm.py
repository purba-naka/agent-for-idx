from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from app import neobdm
from app.neobdm import NAMA_SCREENER, NeoBDMClient, NeoBDMError

SEMESTA = "019ec712-5031-77cf-99e9-9b43469fa344"
MILIK_AGENT = "id-agent"


def envelope(data: Any, **meta: Any) -> dict[str, Any]:
    return {"success": True, "message": "ok", "data": data, "meta": meta}


def emiten(symbol: str, netval: float) -> dict[str, Any]:
    """Satu baris market summary untuk kategori foreign (f_c_N)."""
    return {
        "symbol": symbol,
        "f_c_5": netval,
        "f_c_10": netval,
        "f_c_20": netval,
        "f_c_50": netval,
        "close": 1000,
    }


class Balasan:
    def __init__(self, isi: Any, status: int = 200, teks: str = "") -> None:
        self._isi = isi
        self.status_code = status
        self.text = teks

    def json(self) -> Any:
        if self._isi is None:
            raise ValueError("bukan JSON")
        return self._isi


class SesiPalsu:
    """Pengganti AsyncSession. Merekam setiap permintaan agar bisa diperiksa."""

    def __init__(self, *, login_sukses: bool = True, **_: Any) -> None:
        self.cookies: dict[str, str] = {"csrftoken": "tok"}
        self._login_sukses = login_sukses
        self.jejak: list[tuple[str, str, Any]] = []
        self.screeners: list[dict[str, Any]] = []
        self.halaman: list[list[dict[str, Any]]] = [[emiten("AMMN", 100.0)]]
        self.tertutup = False

    async def close(self) -> None:
        self.tertutup = True

    async def get(self, url: str, **_: Any) -> Balasan:
        self.jejak.append(("GET", url, None))
        if url.endswith("/accounts/login/"):
            return Balasan(None, teks='<input name="csrfmiddlewaretoken" value="tok">')
        if url.endswith("/api/screeners"):
            return Balasan(envelope(self.screeners))
        if url.endswith("/api/screeners/preset"):
            return Balasan(envelope([{"id": "preset-1", "name": "Top Akum Asing"}]))
        if url.endswith("/api/stock-universe"):
            return Balasan(envelope([{"id": SEMESTA, "name": "COMPOSITE"}]))
        raise AssertionError(f"GET tak terduga: {url}")

    async def post(self, url: str, **argumen: Any) -> Balasan:
        self.jejak.append(("POST", url, argumen.get("json") or argumen.get("data")))
        if url.endswith("/accounts/login/"):
            if self._login_sukses:
                self.cookies["sessionid"] = "sid"
            return Balasan(None, teks="")
        if "/api/screeners/preset/" in url:
            baru = {"id": MILIK_AGENT, "name": "klon", "stock_universe_id": SEMESTA}
            self.screeners.append(baru)
            return Balasan(envelope(baru))
        if "/api/market-summary/summary/" in url:
            n = (argumen.get("json") or {}).get("page", 1)
            data = self.halaman[n - 1] if n <= len(self.halaman) else []
            return Balasan(envelope(data, last_page=len(self.halaman)))
        raise AssertionError(f"POST tak terduga: {url}")

    async def patch(self, url: str, **argumen: Any) -> Balasan:
        self.jejak.append(("PATCH", url, argumen.get("json")))
        return Balasan(envelope({}))

    def permintaan(self, metode: str, potongan: str) -> list[Any]:
        return [b for m, u, b in self.jejak if m == metode and potongan in u]


class NeoBDMTest(unittest.IsolatedAsyncioTestCase):
    def siapkan(self, **argumen: Any) -> tuple[NeoBDMClient, SesiPalsu]:
        sesi = SesiPalsu(**argumen)
        self.tambalan = patch.object(neobdm, "AsyncSession", lambda **_: sesi)
        self.tambalan.start()
        self.addCleanup(self.tambalan.stop)
        return NeoBDMClient("https://contoh.test", "u", "p"), sesi

    async def test_login_ditolak_tanpa_sessionid(self) -> None:
        klien, sesi = self.siapkan(login_sukses=False)
        with self.assertRaises(NeoBDMError):
            await klien.tabel_akumulasi()
        self.assertTrue(sesi.tertutup, "sesi gagal wajib ditutup")

    async def test_kategori_tak_dikenal_ditolak_saat_konstruksi(self) -> None:
        with self.assertRaises(ValueError):
            NeoBDMClient("https://contoh.test", "u", "p", kategori="tidak-ada")

    async def test_kolom_mengikuti_kategori(self) -> None:
        klien = NeoBDMClient("https://contoh.test", "u", "p", kategori="institution")
        self.assertEqual(klien.kolom_periode["60d"], "i_c_50")

    async def test_membuat_screener_sendiri_dan_mengosongkan_filter(self) -> None:
        klien, sesi = self.siapkan()
        await klien.tabel_akumulasi()
        tubuh = sesi.permintaan("PATCH", "/api/screeners/")[0]
        self.assertEqual(tubuh["name"], NAMA_SCREENER)
        # Peringkat hanya sah bila pembandingnya seluruh papan.
        self.assertEqual(tubuh["filters"], [])
        # None di sini memicu ValidationError UUID di server.
        self.assertEqual(tubuh["stock_universe_id"], SEMESTA)

    async def test_memakai_ulang_screener_agent_yang_sudah_ada(self) -> None:
        klien, sesi = self.siapkan()
        sesi.screeners = [
            {"id": "milik-user", "name": "P - Market Summary (old)"},
            {"id": MILIK_AGENT, "name": NAMA_SCREENER, "stock_universe_id": SEMESTA},
        ]
        await klien.tabel_akumulasi()
        self.assertEqual(sesi.permintaan("POST", "/preset/"), [], "tak boleh klon lagi")
        patches = [u for m, u, _ in sesi.jejak if m == "PATCH"]
        self.assertTrue(all("milik-user" not in u for u in patches))

    async def test_paginasi_menggabungkan_semua_halaman(self) -> None:
        klien, sesi = self.siapkan()
        sesi.halaman = [[emiten("AAAA", 10.0)], [emiten("BBBB", 20.0)]]
        tabel = await klien.tabel_akumulasi()
        self.assertEqual([b.symbol for b in tabel["5d"]], ["AAAA", "BBBB"])
        self.assertEqual(len(sesi.permintaan("POST", "/summary/")), 2)

    async def test_cache_menahan_tarikan_kedua(self) -> None:
        klien, sesi = self.siapkan()
        await klien.tabel_akumulasi()
        jumlah = len(sesi.permintaan("POST", "/summary/"))
        await klien.harga_terakhir("AMMN")
        self.assertEqual(len(sesi.permintaan("POST", "/summary/")), jumlah)

    async def test_harga_terakhir(self) -> None:
        klien, _ = self.siapkan()
        self.assertEqual(await klien.harga_terakhir("ammn"), 1000.0)
        self.assertIsNone(await klien.harga_terakhir("TIDAKADA"))

    async def test_baris_tanpa_rincian_beli_jual_tidak_dikarang(self) -> None:
        klien, _ = self.siapkan()
        baris = (await klien.tabel_akumulasi())["5d"][0]
        self.assertEqual(baris.netval, 100.0)
        # NeoBDM hanya memberi aliran bersih; rasio harus None, bukan angka palsu.
        self.assertIsNone(baris.rasio)

    async def test_emiten_tanpa_kolom_periode_dilewati(self) -> None:
        klien, sesi = self.siapkan()
        sepi = {"symbol": "KOSONG", "f_c_5": None, "close": 100}
        sesi.halaman = [[emiten("AAAA", 10.0), sepi]]
        tabel = await klien.tabel_akumulasi()
        self.assertEqual([b.symbol for b in tabel["5d"]], ["AAAA"])

    async def test_tabel_kosong_jadi_error(self) -> None:
        klien, sesi = self.siapkan()
        sesi.halaman = [[]]
        with self.assertRaises(NeoBDMError):
            await klien.tabel_akumulasi()


class BongkarTest(unittest.TestCase):
    def test_success_false_pada_http_200_tetap_error(self) -> None:
        balasan = Balasan({"success": False, "message": "Invalid Pagination size"})
        with self.assertRaises(NeoBDMError) as galat:
            neobdm._bongkar(balasan, "/url")
        self.assertIn("Invalid Pagination size", str(galat.exception))

    def test_http_error(self) -> None:
        with self.assertRaises(NeoBDMError):
            neobdm._bongkar(Balasan({"success": True}, status=500), "/url")

    def test_balasan_html_bukan_json(self) -> None:
        with self.assertRaises(NeoBDMError):
            neobdm._bongkar(Balasan(None, teks="<html>"), "/url")


if __name__ == "__main__":
    unittest.main()
