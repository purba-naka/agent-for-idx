"""Pemeriksaan mandiri app/neobdm.py terhadap server sungguhan.

Membuktikan tiga hal yang tidak bisa dibuktikan unit test: login berhasil,
screener agent terbentuk tanpa menyentuh milik pengguna, dan tabel hasilnya
bisa dikonsumsi langsung oleh akumulasi.analisis().

    .venv/Scripts/python.exe analisis/cek_neobdm.py [KODE]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.akumulasi import PERIODE, analisis  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.neobdm import NAMA_SCREENER, NeoBDMClient  # noqa: E402


async def main() -> int:
    kode = (sys.argv[1] if len(sys.argv) > 1 else "AMMN").upper()
    atur = get_settings()
    if not atur.neobdm_enabled:
        print("NEOBDM_USERNAME/PASSWORD belum diisi di .env")
        return 1

    klien = NeoBDMClient(
        atur.neobdm_base_url,
        atur.neobdm_username,
        atur.neobdm_password,
        atur.neobdm_kategori,
    )
    try:
        print(f"kategori: {klien.kategori} -> {klien.kolom_periode}\n")
        tabel = await klien.tabel_akumulasi()
        for periode in PERIODE:
            isi = tabel.get(periode)
            print(f"{periode:>4}: {len(isi) if isi else 0} emiten")
        assert tabel, "tabel kosong"
        assert all(len(v) > 100 for v in tabel.values()), "papan terlalu sedikit"

        harga = await klien.harga_terakhir(kode)
        hasil = analisis(kode, tabel, harga_terakhir=harga)
        print(f"\nharga {kode}: {harga}")
        print(hasil.ringkas())

        # Screener milik pengguna tidak boleh tersentuh.
        sesi = await klien._sesi_siap()  # noqa: SLF001 - pemeriksaan sengaja
        daftar = (await sesi.get(f"{klien.base}/api/screeners")).json()["data"]
        nama = [s["name"] for s in daftar]
        print("\nscreener di akun:", nama)
        assert NAMA_SCREENER in nama, "screener agent tidak terbentuk"

        print("\nOK")
        return 0
    finally:
        await klien.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
