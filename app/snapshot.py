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


def nilai_rekomendasi_matang(settings: Settings, repository: Repository) -> int:
    """Nilai tesis setelah tiga snapshot bursa berikutnya tersedia.

    NeoBDM tidak menyediakan net flow 1d/2d historis. Karena itu bukti yang sah
    adalah apakah rolling netval 5d masih positif pada H+3, bukan angka harian
    yang direkonstruksi secara palsu.
    """
    tanggal = repository.tanggal_snapshot(settings.neobdm_kategori, limit=100)
    if len(tanggal) < 4:
        return 0
    urut = sorted(tanggal)
    dinilai = 0
    for item in repository.rekomendasi_belum_dinilai(urut[-3]):
        try:
            indeks = urut.index(item["tanggal_snapshot"])
        except ValueError:
            continue
        if indeks + 3 >= len(urut):
            continue
        tanggal_uji = urut[indeks + 3]
        snapshot = repository.snapshot(settings.neobdm_kategori, tanggal_uji)
        if snapshot is None:
            continue
        _, tabel = snapshot
        baris = next(
            (
                row
                for row in tabel.get("5d", [])
                if row.symbol.upper() == item["issuer"]
            ),
            None,
        )
        netval = baris.netval if baris else 0.0
        lintasan = item["lintasan"]
        if lintasan == "distribusi":
            hasil = "bertahan" if netval <= 0 else "terbantah"
        elif lintasan.startswith("akumulasi_"):
            hasil = "bertahan" if netval > 0 else "terbantah"
        else:
            hasil = "tidak_dapat_dinilai"
        repository.nilai_rekomendasi(
            item["disclosure_id"],
            hasil,
            f"H+3 {tanggal_uji}: netval 5d {netval:+.1f} M",
        )
        dinilai += 1
    return dinilai


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
        dinilai = nilai_rekomendasi_matang(settings, repository)
        logger.info(
            "snapshot %s (%s): %s baris, %s emiten, %s rekomendasi dinilai",
            tanggal,
            kategori,
            jumlah,
            len(harga),
            dinilai,
        )
        return tanggal
    except NeoBDMError as error:
        # Sumber sekunder: kegagalan tidak boleh menghentikan scheduler yang
        # juga memegang polling IDX.
        logger.warning("snapshot gagal: %s", error)
        return None
    finally:
        await klien.aclose()
