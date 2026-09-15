"""Bandingkan enam kategori aliran NeoBDM untuk satu emiten.

NeoBDM memisahkan Bandar (m), NonRetail (nr), Foreign (f), Sultan (s),
Institution (i), dan Zombie (z). Kategori berbeda bisa memberi kesimpulan
berlawanan untuk emiten yang sama, jadi pilihan kategori adalah keputusan
desain -- bukan detail teknis.

    .venv/Scripts/python.exe analisis/banding_kategori.py AMMN
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.neobdm import NAMA_SCREENER, NeoBDMClient  # noqa: E402

KATEGORI = {
    "m": "Bandar",
    "nr": "NonRetail",
    "f": "Foreign",
    "s": "Sultan",
    "i": "Institution",
    "z": "Zombie",
}


async def main() -> int:
    kode = (sys.argv[1] if len(sys.argv) > 1 else "AMMN").upper()
    atur = get_settings()
    klien = NeoBDMClient(
        atur.neobdm_base_url, atur.neobdm_username, atur.neobdm_password
    )
    try:
        sesi = await klien._sesi_siap()  # noqa: SLF001
        sid = await klien._screener(sesi)  # noqa: SLF001
        semesta = await klien._get_json(sesi, f"{klien.base}/api/stock-universe")  # noqa: SLF001
        uid = next(
            u["id"] for u in semesta["data"] if u.get("name") == "COMPOSITE"
        )

        print(f"{kode}: aliran kumulatif per kategori (miliar rupiah)\n")
        print(f"{'kategori':<12} {'5d':>10} {'10d':>10} {'20d':>10} {'50d':>10}")
        for awalan, nama in KATEGORI.items():
            kolom = [f"{awalan}_c_{n}" for n in (5, 10, 20, 50)]
            await klien._patch_json(  # noqa: SLF001
                sesi,
                f"{klien.base}/api/screeners/{sid}",
                {
                    "name": NAMA_SCREENER,
                    "columns": ["symbol", *kolom, "close"],
                    "filters": [],
                    "stock_universe_id": uid,
                    "sort_field": kolom[2],
                    "sort_direction": "desc",
                },
            )
            nilai = await _cari(klien, sesi, sid, kode, kolom)
            if nilai is None:
                print(f"{nama:<12} {'tidak ditemukan':>43}")
                continue
            print(
                f"{nama:<12}"
                + "".join(f"{nilai[k]:>+10.1f}" for k in kolom)
            )
        return 0
    finally:
        await klien.aclose()


async def _cari(klien, sesi, sid, kode, kolom) -> dict[str, float] | None:
    halaman = 1
    while halaman <= 60:
        balas = await klien._post_json(  # noqa: SLF001
            sesi,
            f"{klien.base}/api/market-summary/summary/{sid}",
            {"page": halaman, "size": 20},
        )
        data = balas.get("data") or []
        for item in data:
            if str(item.get("symbol", "")).upper() == kode:
                return {k: float(item.get(k) or 0.0) for k in kolom}
        terakhir = (balas.get("meta") or {}).get("last_page") or halaman
        if halaman >= terakhir or not data:
            return None
        halaman += 1
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
