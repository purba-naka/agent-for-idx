"""Analisis satu kali AMMN dari broker summary dan harga Yahoo Finance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.akumulasi import BarisBroker, analisis

KODE = "AMMN"
TANGGAL = "2026-09-14"
OUTPUT = Path(__file__).resolve().parent

# Transkripsi empat tangkapan layar. Angka bernilai miliar rupiah.
BROKER = {
    "1d": BarisBroker(KODE, 208.7, 351.5, 142.8, 4980.5, 4970.1),
    "2d": BarisBroker(KODE, 223.8, 442.8, 219.0, 4942.7, 4919.2),
    "5d": BarisBroker(KODE, 309.7, 834.3, 524.6, 4836.0, 4815.0),
    "20d": BarisBroker(KODE, 396.9, 1780.6, 1383.7, 4601.6, 4535.8),
}


def ambil_harga() -> list[dict[str, float | int | str]]:
    """Ambil OHLCV cukup panjang untuk memberi konteks sebelum 14 September."""
    frame = yf.download(
        f"{KODE}.JK",
        start="2026-08-13",
        end="2026-09-16",  # batas akhir yfinance eksklusif
        auto_adjust=False,
        progress=False,
    )
    if frame.empty:
        raise RuntimeError("Yahoo Finance tidak mengembalikan harga AMMN")

    # yfinance 1.x mengembalikan MultiIndex walau hanya satu ticker.
    if frame.columns.nlevels > 1:
        frame.columns = frame.columns.get_level_values(0)

    frame = frame.reset_index()
    hasil: list[dict[str, float | int | str]] = []
    for _, row in frame.iterrows():
        hasil.append(
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            }
        )
    return hasil


def hitung(harga: list[dict[str, float | int | str]]) -> dict[str, object]:
    terakhir = harga[-1]
    pertama = harga[0]
    hari_peristiwa = next(item for item in harga if item["date"] == TANGGAL)
    sebelum_peristiwa = harga[harga.index(hari_peristiwa) - 1]
    volume_sebelum = [int(item["volume"]) for item in harga if item["date"] < TANGGAL]
    rata_volume = sum(volume_sebelum) / len(volume_sebelum)

    tabel = {
        "5d": [BROKER["5d"]],
        "20d": [BROKER["20d"]],
    }
    akumulasi = analisis(KODE, tabel, harga_terakhir=float(terakhir["close"]))

    return {
        "ticker": f"{KODE}.JK",
        "tanggal_broker": TANGGAL,
        "tanggal_harga_terakhir": terakhir["date"],
        "broker": {
            periode: {
                "netval_miliar": baris.netval,
                "bval_miliar": baris.bval,
                "sval_miliar": baris.sval,
                "bavg": baris.bavg,
                "savg": baris.savg,
                "rasio_beli_jual": round(baris.rasio, 4) if baris.rasio else None,
            }
            for periode, baris in BROKER.items()
        },
        "harga": {
            "close": terakhir["close"],
            "return_hari_peristiwa_persen": round(
                (float(hari_peristiwa["close"]) / float(sebelum_peristiwa["close"]) - 1)
                * 100,
                2,
            ),
            "return_setelah_peristiwa_persen": round(
                (float(terakhir["close"]) / float(hari_peristiwa["close"]) - 1) * 100,
                2,
            ),
            "return_sejak_13_agustus_persen": round(
                (float(terakhir["close"]) / float(pertama["close"]) - 1) * 100, 2
            ),
            "volume_hari_peristiwa": hari_peristiwa["volume"],
            "volume_setelah_peristiwa": terakhir["volume"],
            "rasio_volume_peristiwa_vs_rata_sebelumnya": round(
                int(hari_peristiwa["volume"]) / rata_volume, 2
            ),
            "rasio_volume_setelah_vs_peristiwa": round(
                int(terakhir["volume"]) / int(hari_peristiwa["volume"]), 2
            ),
            "low_setelah_peristiwa": terakhir["low"],
            "low_vs_bavg_5d_persen": round(
                (float(terakhir["low"]) / BROKER["5d"].bavg - 1) * 100, 2
            ),
        },
        "konfirmasi_setelah_peristiwa": {
            "harga_bertahan_di_atas_bavg_5d": float(terakhir["low"]) > BROKER["5d"].bavg,
            "volume_kembali_normal": int(terakhir["volume"]) < rata_volume,
            "net_buy_1d_2d_tetap_positif": None,
            "status": "parsial; data broker setelah 14 September belum tersedia",
        },
        "lintasan": akumulasi.lintasan,
        "fakta_akumulasi": akumulasi.ringkas(),
    }


def main() -> None:
    harga = ambil_harga()
    hasil = hitung(harga)
    (OUTPUT / "harga_ammn.csv").write_text(
        "date,open,high,low,close,volume\n"
        + "\n".join(
            f'{x["date"]},{x["open"]:.0f},{x["high"]:.0f},{x["low"]:.0f},'
            f'{x["close"]:.0f},{x["volume"]}'
            for x in harga
        )
        + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "hasil.json").write_text(
        json.dumps(hasil, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(hasil, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
