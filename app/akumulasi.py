"""Baca jejak akumulasi institusi dari tabel stalking broker.

Satu tabel tidak memberi tahu apa pun; yang memberi tahu adalah selisih antar
periode. Modul ini hanya aritmatika -- tidak menyentuh jaringan, tidak memanggil
LLM. Sumber tabelnya ditentukan pemanggil (lihat `MarketData` di pipeline).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

# Urut dari jendela terpendek. Periode saling tumpang tindih: 5d adalah bagian
# dari 60d, bukan potongan terpisah -- lihat _lonjakan_tunggal().
PERIODE = ("5d", "10d", "20d", "60d")

# Ambang kebisingan. Di bawah ini emiten dianggap tidak terpantau: nilainya
# terlalu kecil untuk membedakan keputusan institusi dari sisa order harian.
MIN_NETVAL = 5.0  # miliar rupiah

# bval/sval di sekitar 1 berarti broker berputar di dua arah (rotasi portofolio,
# pemindahan inventaris) -- arah netval-nya tidak bermakna.
CHURN_BAWAH, CHURN_ATAS = 0.77, 1.30

# 5d yang hampir sebesar 60d berarti satu order besar, bukan penyerapan bertahap.
RASIO_LONJAKAN = 0.85

Lintasan = Literal[
    "akumulasi_berkelanjutan",
    "akumulasi_selesai",
    "akumulasi_baru",
    "distribusi",
    "tidak_terpantau",
]

PENJELASAN: dict[Lintasan, str] = {
    "akumulasi_berkelanjutan": "institusi menyerap sejak jendela panjang dan masih membeli",
    "akumulasi_selesai": "institusi sudah menumpuk lalu berhenti; posisi didiamkan",
    "akumulasi_baru": "pembelian baru muncul di jendela pendek, belum terkonfirmasi",
    "distribusi": "net beli di jendela panjang berbalik jadi net jual di jendela pendek",
    "tidak_terpantau": "tidak ada aliran institusi yang berarti",
}


@dataclass(frozen=True)
class BarisBroker:
    """Satu baris tabel stalking. Nilai dalam miliar rupiah, harga dalam rupiah."""

    symbol: str
    netval: float
    bval: float
    sval: float
    bavg: float
    savg: float

    @property
    def rasio(self) -> float | None:
        """bval/sval. None bila tidak ada penjualan sama sekali (tak terdefinisi)."""
        return self.bval / self.sval if self.sval else None


@dataclass(frozen=True)
class Jejak:
    """Posisi satu emiten di satu periode."""

    periode: str
    peringkat: int
    dari: int
    netval: float
    bavg: float
    rasio: float | None


@dataclass(frozen=True)
class Akumulasi:
    kode: str
    lintasan: Lintasan
    jejak: list[Jejak] = field(default_factory=list)
    catatan: list[str] = field(default_factory=list)

    def ringkas(self) -> str:
        """Blok fakta untuk prompt LLM; angka mentah selalu ikut.

        Label tanpa angka adalah tempat halusinasi bersembunyi -- peringkas
        harus bisa dibantah oleh datanya sendiri.
        """
        baris = [f"Lintasan: {self.lintasan} ({PENJELASAN[self.lintasan]})"]
        for jejak in self.jejak:
            rasio = f"{jejak.rasio:.1f}x" if jejak.rasio is not None else "-"
            baris.append(
                f"  {jejak.periode:>4}: #{jejak.peringkat}/{jejak.dari} | "
                f"netval {jejak.netval:+.1f} M | bavg {jejak.bavg:.0f} | bval/sval {rasio}"
            )
        baris.extend(f"  catatan: {catatan}" for catatan in self.catatan)
        return "\n".join(baris)


def analisis(
    kode: str,
    tabel: Mapping[str, Sequence[BarisBroker]],
    harga_terakhir: float | None = None,
) -> Akumulasi:
    """Cocokkan jejak satu emiten di beberapa periode dengan pola lintasan.

    `tabel` memetakan periode ("5d", "20d", ...) ke daftar baris hasil stalking.
    Periode yang tidak disertakan diperlakukan sebagai tidak diketahui, bukan
    sebagai nol -- kesimpulan menyesuaikan dengan apa yang ada.
    """
    kode = kode.upper()
    jejak = [
        temuan
        for periode in PERIODE
        if (temuan := _jejak_di(kode, periode, tabel.get(periode))) is not None
    ]
    netval = {temuan.periode: temuan.netval for temuan in jejak}
    return Akumulasi(
        kode=kode,
        lintasan=_lintasan(netval),
        jejak=jejak,
        catatan=_catatan(jejak, netval, harga_terakhir),
    )


def _jejak_di(
    kode: str, periode: str, baris: Sequence[BarisBroker] | None
) -> Jejak | None:
    """Peringkat dihitung ulang di sini, tidak mewarisi urutan sumber.

    Peringkat diukur terhadap sisi yang sama: net buyer diperingkat di antara
    net buyer, net seller di antara net seller. "#2 dari 180" pada baris negatif
    berarti penjual kedua terbesar.
    """
    if not baris:
        return None
    target = next((item for item in baris if item.symbol.upper() == kode), None)
    if target is None or abs(target.netval) < MIN_NETVAL:
        return None

    sesisi = [item for item in baris if (item.netval >= 0) == (target.netval >= 0)]
    sesisi.sort(key=lambda item: abs(item.netval), reverse=True)
    return Jejak(
        periode=periode,
        peringkat=sesisi.index(target) + 1,
        dari=len(sesisi),
        netval=target.netval,
        bavg=target.bavg,
        rasio=target.rasio,
    )


def _lintasan(netval: Mapping[str, float]) -> Lintasan:
    """Bandingkan jendela pendek dengan jendela panjang.

    Urutan pemeriksaan disengaja: pembalikan arah (distribusi) diputuskan lebih
    dulu, karena net beli 60 hari yang berbalik jual dalam 5 hari adalah kabar
    yang berlawanan dengan angka 60 harinya sendiri.
    """
    if not netval:
        return "tidak_terpantau"

    pendek = [netval[periode] for periode in ("5d", "10d") if periode in netval]
    panjang = [netval[periode] for periode in ("20d", "60d") if periode in netval]
    beli_pendek = any(nilai > 0 for nilai in pendek)
    jual_pendek = any(nilai < 0 for nilai in pendek)
    beli_panjang = any(nilai > 0 for nilai in panjang)

    if jual_pendek and beli_panjang:
        return "distribusi"
    if jual_pendek:
        return "distribusi" if not beli_pendek else "tidak_terpantau"
    if beli_pendek and beli_panjang:
        return "akumulasi_berkelanjutan"
    if beli_pendek:
        return "akumulasi_baru"
    if beli_panjang:
        return "akumulasi_selesai"
    return "tidak_terpantau"


def _catatan(
    jejak: Sequence[Jejak], netval: Mapping[str, float], harga: float | None
) -> list[str]:
    catatan: list[str] = []
    if terdekat := next(iter(jejak), None):
        catatan.extend(_catatan_harga(terdekat, harga))
        if terdekat.rasio is not None and CHURN_BAWAH < terdekat.rasio < CHURN_ATAS:
            catatan.append(
                f"bval/sval {terdekat.rasio:.1f}x -- dua arah hampir seimbang, "
                "lebih mirip rotasi daripada arah baru"
            )
    if _lonjakan_tunggal(netval):
        catatan.append(
            "netval 5d hampir sebesar jendela panjang -- kemungkinan satu order "
            "besar, bukan penyerapan bertahap"
        )
    return catatan


def _catatan_harga(jejak: Jejak, harga: float | None) -> list[str]:
    """bavg terhadap harga sekarang: sudah untung, impas, atau masih di bawah air."""
    if not harga or not jejak.bavg:
        return []
    selisih = (harga - jejak.bavg) / jejak.bavg * 100
    if selisih > 5:
        return [
            f"bavg {jejak.bavg:.0f} vs harga {harga:.0f} ({selisih:+.1f}%) -- "
            "pembeli sudah untung, berita bisa jadi jalan keluar"
        ]
    if selisih < -5:
        return [
            f"bavg {jejak.bavg:.0f} vs harga {harga:.0f} ({selisih:+.1f}%) -- "
            "pembeli masih di bawah air namun tetap menambah"
        ]
    return [
        f"bavg {jejak.bavg:.0f} vs harga {harga:.0f} ({selisih:+.1f}%) -- "
        "masih membangun posisi di sekitar harga pasar"
    ]


def _lonjakan_tunggal(netval: Mapping[str, float]) -> bool:
    """True bila jendela pendek menjelaskan hampir seluruh jendela panjang.

    Karena periode tumpang tindih, satu order raksasa lima hari lalu muncul di
    keempat tabel dan menyamar sebagai akumulasi panjang. Akumulasi sejati
    tumbuh bertahap, sehingga 5d jauh lebih kecil dari 60d.
    """
    pendek = netval.get("5d")
    panjang = next((netval[periode] for periode in ("60d", "20d") if periode in netval), None)
    if pendek is None or panjang is None or pendek <= 0 or panjang <= 0:
        return False
    return pendek / panjang >= RASIO_LONJAKAN
