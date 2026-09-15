"""Apakah NeoBDM memperbarui data intraday, atau sekali setelah bursa tutup?

Jawabannya menentukan frekuensi pemantau transaksi: kalau last-update hanya
berubah sekali sehari, polling tiap jam cuma membaca angka yang sama.

    .venv/Scripts/python.exe analisis/cek_kesegaran.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.neobdm import NeoBDMClient  # noqa: E402


async def main() -> int:
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
        sesi = await klien._sesi_siap()  # noqa: SLF001 - probe sengaja
        print("sekarang (lokal):", datetime.now().isoformat(timespec="seconds"))

        segar = await klien._get_json(  # noqa: SLF001
            sesi, f"{klien.base}/api/market-summary/last-update"
        )
        print("last-update:", segar.get("data"))

        # Adakah kolom aliran harian intraday (d_0 = hari ini)? Kalau nilainya
        # bergerak selama jam bursa, pemantau intraday masuk akal.
        kolom = await klien._get_json(  # noqa: SLF001
            sesi, f"{klien.base}/api/market-summary/columns"
        )
        semua = kolom.get("data") or []
        awalan = klien.kolom_periode["5d"].split("_")[0]
        harian = [
            k for k in semua if str(k.get("field", "")).startswith(f"{awalan}_d_")
        ]
        print(f"\nkolom aliran harian ({awalan}_d_N):")
        for k in harian[:6]:
            print(f"  {k['field']:<10} {k.get('title')}")

        # Kolom lain yang menyiratkan data transaksi berjalan.
        menarik = [
            k["field"]
            for k in semua
            if any(
                p in str(k.get("field", ""))
                for p in ("tval", "freq", "vol", "last", "time")
            )
        ]
        print("\nkolom bernuansa transaksi:", menarik[:20])
        return 0
    finally:
        await klien.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
