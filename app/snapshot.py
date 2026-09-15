"""Job harian: simpan aliran dana satu hari ke SQLite.

Kenapa dijadwalkan, bukan ditarik saat berita datang: NeoBDM hanya end-of-day.
Menariknya per disclosure menghasilkan angka yang persis sama namun dengan
~47 permintaan per berita alih-alih per hari.

Kenapa disimpan, bukan sekadar di-cache: peringkat hari ini tidak berarti tanpa
peringkat kemarin, dan konfirmasi pasca-peristiwa ("aliran tetap positif dua
hari setelah berita?") mustahil dijawab kolom kumulatif yang selalu melihat ke
belakang dari hari ini. Data yang tidak ditangkap hari ini hilang selamanya.
"""

from __future__ import annotations

import logging

from .config import Settings
from .neobdm import NeoBDMClient, NeoBDMError
from .repository import Repository

logger = logging.getLogger("idx")


async def tarik_snapshot(
    settings: Settings, repository: Repository, paksa: bool = False
) -> str | None:
    """Tarik dan simpan snapshot hari ini; None bila dilewati atau gagal.

    Dilewati bila server belum memperbarui datanya -- job berjalan tiap hari
    termasuk akhir pekan dan hari libur bursa, dan menarik ulang tanggal yang
    sama hanya membuang ~47 permintaan.
    """
    if not settings.neobdm_enabled:
        return None

    klien = NeoBDMClient(
        settings.neobdm_base_url,
        settings.neobdm_username,
        settings.neobdm_password,
        settings.neobdm_kategori,
    )
    kategori = settings.neobdm_kategori
    try:
        tanggal = await klien.tanggal_data()
        if not paksa and tanggal in repository.tanggal_snapshot(kategori, limit=5):
            logger.debug("snapshot %s sudah ada; lewati", tanggal)
            return None

        tabel = await klien.tabel_akumulasi()
        harga = await klien.harga_semua()
        jumlah = repository.simpan_snapshot(tanggal, kategori, tabel, harga)
        logger.info(
            "snapshot %s (%s): %s baris, %s emiten", tanggal, kategori, jumlah, len(harga)
        )
        return tanggal
    except NeoBDMError as error:
        # Sumber sekunder: kegagalan tidak boleh menghentikan scheduler yang
        # juga memegang polling IDX.
        logger.warning("snapshot gagal: %s", error)
        return None
    finally:
        await klien.aclose()
