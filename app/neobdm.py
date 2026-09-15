"""Klien NeoBDM: sumber tabel aliran institusi untuk node akumulasi.

NeoBDM tidak punya API publik. Endpoint di bawah hasil pembacaan JS halamannya,
jadi anggap rapuh: bentuk payload bisa berubah tanpa pemberitahuan. Semua
kegagalan dibungkus `NeoBDMError` supaya pemanggil bisa melanjutkan tanpa data
akumulasi alih-alih menjatuhkan pipeline.

Kenapa market-summary, bukan broker-summary: `akumulasi.analisis()` memberi
peringkat satu emiten di antara semua emiten, jadi yang dibutuhkan tabel
per-emiten. `/api/broker-summary` hanya memberi per-broker untuk satu ticker.
Kolom `i_c_N` (Institution Flow kumulatif N candle, miliar rupiah) adalah
padanan terdekat dari netval di tabel stalking.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from curl_cffi.curl import CurlError
from curl_cffi.requests import AsyncSession

from .akumulasi import PERIODE, BarisBroker

logger = logging.getLogger(__name__)

# Nama screener milik agent. Dipakai agar tidak pernah menimpa screener buatan
# pengguna: klien mencari nama ini, dan hanya membuat baru bila belum ada.
NAMA_SCREENER = "agent-idx-akumulasi"

# NeoBDM memisahkan aliran jadi enam kategori, dan kategori berbeda bisa
# memberi kesimpulan berlawanan untuk emiten yang sama (AMMN 14 Sep 2026:
# Foreign +274 M di 5d, Institution -71 M di 5d). Karena itu kategori adalah
# keputusan eksplisit, bukan default tersembunyi.
KATEGORI = {
    "bandar": "m",
    "nonretail": "nr",
    "foreign": "f",
    "sultan": "s",
    "institution": "i",
    "zombie": "z",
}

# PERIODE akumulasi.py memakai hari bursa; NeoBDM memakai "candle". Untuk data
# harian keduanya setara, jadi 5d -> _c_5. 60d tidak tersedia: pilihan NeoBDM
# melompat 20 -> 50, dan 50 candle lebih dekat ke 60d daripada 20d.
CANDLE = {"5d": 5, "10d": 10, "20d": 20, "60d": 50}

# Batas keras server: size > 20 ditolak ERROR_MARKET_SUMMARY_001.
UKURAN_HALAMAN = 20
# Pagar pengaman agar satu emiten hilang tidak berubah jadi ratusan permintaan.
MAX_HALAMAN = 60


class NeoBDMError(RuntimeError):
    """Gagal mengambil data NeoBDM; pemanggil sebaiknya lanjut tanpa akumulasi."""


class NeoBDMClient:
    """Sesi login Django + pembaca market summary.

    Sesi disimpan antar panggilan karena login butuh dua permintaan penuh.
    Tidak thread-safe; satu instance untuk satu pipeline.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        kategori: str = "foreign",
    ) -> None:
        if kategori not in KATEGORI:
            raise ValueError(f"kategori tidak dikenal: {kategori}")
        self.base = base_url.rstrip("/")
        self.kategori = kategori
        self._username = username
        self._password = password
        self._sesi: AsyncSession | None = None
        self._csrf = ""
        self._screener_id = ""
        self._cache: list[dict[str, Any]] | None = None
        self._kunci = asyncio.Lock()

    @property
    def kolom_periode(self) -> dict[str, str]:
        """Petakan periode akumulasi.py ke nama kolom kategori terpilih."""
        awalan = KATEGORI[self.kategori]
        return {p: f"{awalan}_c_{n}" for p, n in CANDLE.items()}

    @property
    def kolom(self) -> list[str]:
        return ["symbol", *self.kolom_periode.values(), "close", "is_liquid"]

    async def aclose(self) -> None:
        if self._sesi is not None:
            await self._sesi.close()
            self._sesi = None

    async def tabel_akumulasi(self) -> dict[str, list[BarisBroker]]:
        """Tabel per periode berisi seluruh emiten dengan aliran institusi.

        Bentuk hasilnya cocok langsung untuk `akumulasi.analisis()`. Emiten
        tanpa aliran sama sekali tetap disertakan bila lolos filter server;
        `analisis()` yang menyaring lewat MIN_NETVAL.
        """
        baris = await self._semua_baris()
        kolom_periode = self.kolom_periode
        tabel: dict[str, list[BarisBroker]] = {}
        for periode in PERIODE:
            kolom = kolom_periode[periode]
            isi = [
                _ke_baris(item, kolom)
                for item in baris
                if item.get("symbol") and item.get(kolom) is not None
            ]
            if isi:
                tabel[periode] = isi
        if not tabel:
            raise NeoBDMError("market summary kosong untuk semua periode")
        return tabel

    async def harga_terakhir(self, kode: str) -> float | None:
        """Harga penutupan terakhir dari tabel yang sama; None bila tak ada."""
        for item in await self._semua_baris():
            if str(item.get("symbol", "")).upper() == kode.upper():
                tutup = item.get("close")
                return float(tutup) if tutup else None
        return None

    async def _semua_baris(self) -> list[dict[str, Any]]:
        """Ambil seluruh halaman market summary sekali, lalu simpan di memori.

        Satu disclosure hanya menyentuh satu emiten, tetapi peringkat menuntut
        seluruh papan. Menarik ulang per emiten akan mengalikan ~50 permintaan.
        """
        if self._cache is None:
            async with self._kunci:
                if self._cache is None:
                    self._cache = await self._tarik_halaman()
        return self._cache

    async def _tarik_halaman(self) -> list[dict[str, Any]]:
        sesi = await self._sesi_siap()
        sid = await self._screener(sesi)
        kumpulan: list[dict[str, Any]] = []
        halaman = 1
        while halaman <= MAX_HALAMAN:
            isi = await self._post_json(
                sesi,
                f"{self.base}/api/market-summary/summary/{sid}",
                {"page": halaman, "size": UKURAN_HALAMAN},
            )
            data = isi.get("data") or []
            kumpulan.extend(data)
            terakhir = (isi.get("meta") or {}).get("last_page") or halaman
            if halaman >= terakhir or not data:
                break
            halaman += 1
        else:
            logger.warning("NeoBDM: berhenti di MAX_HALAMAN=%s", MAX_HALAMAN)
        logger.info("NeoBDM: %s emiten terbaca", len(kumpulan))
        return kumpulan

    async def _sesi_siap(self) -> AsyncSession:
        if self._sesi is not None:
            return self._sesi
        # impersonate: situs di belakang Cloudflare, sama seperti idx.co.id.
        sesi = AsyncSession(impersonate="chrome", timeout=60)
        url = f"{self.base}/accounts/login/"
        try:
            halaman = await sesi.get(url)
            cocok = re.search(
                r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)',
                halaman.text,
            )
            csrf = cocok.group(1) if cocok else (sesi.cookies.get("csrftoken") or "")
            await sesi.post(
                url,
                data={
                    "csrfmiddlewaretoken": csrf,
                    "login": self._username,
                    "password": self._password,
                },
                headers={"Referer": url, "Origin": self.base},
            )
        except CurlError as error:
            await sesi.close()
            raise NeoBDMError(f"jaringan gagal saat login: {error}") from error
        if "sessionid" not in sesi.cookies:
            await sesi.close()
            raise NeoBDMError("login ditolak; periksa NEOBDM_USERNAME/PASSWORD")
        self._sesi = sesi
        self._csrf = sesi.cookies.get("csrftoken") or ""
        return sesi

    async def _screener(self, sesi: AsyncSession) -> str:
        """Pakai screener milik agent; buat sekali bila belum ada.

        Filter dikosongkan dengan sengaja: peringkat "#3 dari 180" hanya sah
        bila pembandingnya seluruh papan, bukan sisa hasil saringan.
        """
        if self._screener_id:
            return self._screener_id

        daftar = await self._get_json(sesi, f"{self.base}/api/screeners")
        milik = next(
            (s for s in (daftar.get("data") or []) if s.get("name") == NAMA_SCREENER),
            None,
        )
        if milik is None:
            semesta = await self._get_json(sesi, f"{self.base}/api/stock-universe")
            komposit = next(
                (
                    u
                    for u in (semesta.get("data") or [])
                    if u.get("name") == "COMPOSITE"
                ),
                None,
            )
            if komposit is None:
                raise NeoBDMError("universe COMPOSITE tidak ditemukan")
            preset = await self._get_json(sesi, f"{self.base}/api/screeners/preset")
            asal = (preset.get("data") or [{}])[0].get("id")
            if not asal:
                raise NeoBDMError("tidak ada preset screener untuk diklon")
            dibuat = await self._post_json(
                sesi, f"{self.base}/api/screeners/preset/{asal}", {}
            )
            milik = dibuat.get("data") or {}
            milik["stock_universe_id"] = komposit["id"]

        sid = milik.get("id")
        if not sid:
            raise NeoBDMError("screener tanpa id")
        await self._patch_json(
            sesi,
            f"{self.base}/api/screeners/{sid}",
            {
                "name": NAMA_SCREENER,
                "columns": self.kolom,
                "filters": [],
                "stock_universe_id": milik.get("stock_universe_id"),
                "sort_field": self.kolom_periode["20d"],
                "sort_direction": "desc",
            },
        )
        self._screener_id = sid
        return sid

    def _kepala(self) -> dict[str, str]:
        return {
            "X-CSRFToken": self._csrf,
            "Referer": f"{self.base}/market_summary/",
            "Origin": self.base,
            "Content-Type": "application/json",
        }

    async def _get_json(self, sesi: AsyncSession, url: str) -> dict[str, Any]:
        return _bongkar(await self._minta(sesi.get, url), url)

    async def _post_json(
        self, sesi: AsyncSession, url: str, tubuh: dict[str, Any]
    ) -> dict[str, Any]:
        return _bongkar(
            await self._minta(sesi.post, url, json=tubuh, headers=self._kepala()), url
        )

    async def _patch_json(
        self, sesi: AsyncSession, url: str, tubuh: dict[str, Any]
    ) -> dict[str, Any]:
        return _bongkar(
            await self._minta(sesi.patch, url, json=tubuh, headers=self._kepala()), url
        )

    @staticmethod
    async def _minta(metode, url: str, **argumen) -> Any:
        try:
            return await metode(url, **argumen)
        except CurlError as error:
            raise NeoBDMError(f"jaringan gagal ke {url}: {error}") from error


def _bongkar(balasan: Any, url: str) -> dict[str, Any]:
    """Buka envelope NeoBDM. `success: false` datang dengan HTTP 200."""
    if balasan.status_code >= 400:
        raise NeoBDMError(f"HTTP {balasan.status_code} dari {url}")
    try:
        isi = balasan.json()
    except ValueError as error:  # HTML error page, bukan JSON
        raise NeoBDMError(f"balasan bukan JSON dari {url}") from error
    if not isi.get("success"):
        raise NeoBDMError(f"{url}: {isi.get('message') or 'gagal tanpa pesan'}")
    return isi


def _ke_baris(item: dict[str, Any], kolom: str) -> BarisBroker:
    """Petakan satu baris market summary ke BarisBroker.

    NeoBDM memberi aliran bersih saja, tanpa rincian beli/jual. bval/sval dan
    bavg/savg diisi nol supaya `rasio` bernilai None dan `_catatan_harga`
    melewatkannya: lebih baik kehilangan catatan daripada mengarang angka.
    """
    return BarisBroker(
        symbol=str(item["symbol"]).upper(),
        netval=float(item.get(kolom) or 0.0),
        bval=0.0,
        sval=0.0,
        bavg=0.0,
        savg=0.0,
    )
