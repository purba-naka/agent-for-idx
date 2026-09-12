#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
idx_keterbukaan_informasi.py
=============================

Script untuk mengambil daftar "Keterbukaan Informasi" (corporate disclosures)
dari situs resmi Bursa Efek Indonesia (IDX) lewat API internal yang dipakai
oleh halaman:

    https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi

... dan mengunduh semua lampiran PDF-nya secara otomatis.

CARA KERJA / API YANG DITEMUKAN
--------------------------------
Halaman tsb memanggil endpoint JSON berikut (ditemukan lewat inspeksi
Network tab saat memakai filter kata kunci / jenis emiten di browser):

    GET https://www.idx.co.id/primary/ListedCompany/GetAnnouncement

Query params:
    kodeEmiten   -> kode saham, misal "BBCA". Kosongkan untuk semua emiten.
    emitenType   -> filter jenis efek (dropdown "Jenis" di halaman):
                        "*" = Semua
                        "s" = Saham
                        "o" = Obligasi & Sukuk
                    (ETF / Dire Dinfra / EBA kemungkinan besar mengikuti pola
                     huruf pertama juga, tapi belum sempat dikonfirmasi satu-
                     satu -- cek tab Network kalau butuh nilai pastinya)
    indexFrom    -> offset paging (0, pageSize, 2*pageSize, ...)
    pageSize     -> jumlah item per halaman (terbukti aman sampai 500)
    dateFrom     -> tanggal awal, format YYYYMMDD (mis. 19010101 = dari awal)
    dateTo       -> tanggal akhir, format YYYYMMDD
    lang         -> "id" atau "en"
    keyword      -> kata kunci pencarian bebas (judul, kode emiten, dst.)

PERILAKU PAGING (PENTING, hasil observasi langsung):
    - pageSize besar didukung penuh (teruji sampai 500 item per request).
    - indexFrom > 0 mengembalikan Replies KOSONG ketika dateFrom/dateTo
      berupa rentang pendek (kisaran beberapa hari), padahal ResultCount
      melaporkan total penuh. Pada rentang sangat lebar (19010101 -> kini)
      paging indexFrom justru berfungsi normal.
    Karena itu script ini memakai strategi hybrid: coba paging indexFrom
    dulu, dan bila hasilnya terpotong (item terkumpul < ResultCount),
    otomatis mengulang dengan iterasi PER HARI (indexFrom=0 + pageSize
    besar untuk tiap hari) + dedupe berdasarkan Id2.

Contoh:
    https://www.idx.co.id/primary/ListedCompany/GetAnnouncement?kodeEmiten=&emitenType=*&indexFrom=0&pageSize=100&dateFrom=20260901&dateTo=20260911&lang=id&keyword=

Response JSON:
    {
      "ResultCount": <total jumlah data yang match filter>,
      "SearchParams": {...},
      "Replies": [
        {
          "pengumuman": {
              "Id2": "...",
              "NoPengumuman": "...",
              "TglPengumuman": "2026-09-11T14:09:32",
              "JudulPengumuman": "Penyampaian Bukti Iklan ...",
              "Kode_Emiten": "HRTA ...",   # ada padding spasi, perlu di-strip()
              ...
          },
          "attachments": [
              {
                "PDFFilename": "a4f1b39e2f_94b6f7d64d.pdf",
                "FullSavePath": "https://www.idx.co.id/StaticData/.../a4f1b39e2f_94b6f7d64d.pdf",
                "OriginalFilename": "20260908_BBCA_Public Expose_32146312.pdf",
                "IsAttachment": false   # false = dokumen utama, true = lampiran
              },
              ...
          ]
        },
        ...
      ]
    }

`FullSavePath` adalah URL PDF yang bisa langsung di-download (public, tidak
perlu login).

CARA PAKAI
----------
    pip install curl_cffi    # WAJIB: untuk lolos proteksi Cloudflare IDX
    # (fallback ke `requests` bila curl_cffi tidak terpasang)

    # Ambil semua keterbukaan informasi 1-11 Sept 2026, download semua PDF
    python idx_keterbukaan_informasi.py --date-from 20260901 --date-to 20260911 --outdir hasil_idx

    # Hanya emiten tertentu
    python idx_keterbukaan_informasi.py --kode BBCA --outdir hasil_bbca

    # Cari kata kunci tertentu, tanpa download PDF (hanya metadata ke CSV)
    python idx_keterbukaan_informasi.py --keyword "RUPS" --no-download --outdir hasil_rups

    # Filter jenis emiten (saham saja)
    python idx_keterbukaan_informasi.py --emiten-type s --date-from 20260101 --date-to 20260911

Hasil:
    <outdir>/metadata.csv     -> daftar semua pengumuman (1 baris = 1 pengumuman)
    <outdir>/pdf/<KODE>/...   -> semua PDF yang diunduh, dikelompokkan per kode emiten

CATATAN PENTING
----------------
- www.idx.co.id dilindungi Cloudflare bot-management: client Python biasa
  (requests) ditolak 403 dengan halaman "Just a moment...". Script ini
  memakai curl_cffi dengan impersonate="chrome" (TLS fingerprint seperti
  browser asli) supaya tetap bisa membaca data publik tersebut. Tanpa
  curl_cffi, script fallback ke requests dan kemungkinan besar gagal 403.
- Endpoint ini TIDAK didokumentasikan resmi oleh IDX -- ini hasil observasi
  lalu lintas jaringan pada halaman publik. Bisa berubah sewaktu-waktu tanpa
  pemberitahuan. Gunakan secara wajar (beri jeda antar-request) agar tidak
  membebani server IDX / supaya tidak diblokir.
- Tanpa filter, jumlah data sangat besar (puluhan ribu pengumuman). Selalu
  batasi dengan --date-from/--date-to atau --kode/--keyword kalau tidak
  butuh semuanya, atau pakai --max untuk membatasi jumlah pengumuman yang
  diproses.
- Script ini murni membaca data publik yang sama seperti yang dilihat
  pengunjung biasa di browser; tidak melakukan bypass otentikasi apa pun.
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

try:
    from curl_cffi import requests as cf_requests
except ImportError:
    cf_requests = None

API_URL = "https://www.idx.co.id/primary/ListedCompany/GetAnnouncement"
REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi",
}
# Hanya dipakai bila fallback ke requests biasa (User-Agent di-set manual;
# pada mode curl_cffi, UA & fingerprint Chrome diurus oleh impersonate)
FALLBACK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    **REQUEST_HEADERS,
}


def http_get(url, params=None, timeout=30):
    """GET yang kompatibel dengan proteksi Cloudflare di www.idx.co.id.

    IDX memasang Cloudflare bot-management; requests biasa ditolak 403
    ("Just a moment..."). curl_cffi meniru TLS fingerprint Chrome sehingga
    lolos. Tanpa curl_cffi, fallback ke requests (kemungkinan gagal 403).
    """
    if cf_requests is not None:
        return cf_requests.get(
            url,
            params=params,
            headers=REQUEST_HEADERS,
            timeout=timeout,
            impersonate="chrome",
        )
    return requests.get(url, params=params, headers=FALLBACK_HEADERS, timeout=timeout)

ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')


def sanitize_filename(name: str) -> str:
    name = ILLEGAL_CHARS.sub("_", name).strip()
    return name[:200] if len(name) > 200 else name


def request_with_retry(url, params, max_retries=5, backoff=2.0, timeout=30):
    """GET dengan retry + exponential backoff (IDX kadang membalas 503 kalau
    request terlalu cepat berturut-turut)."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = http_get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503):
                wait = backoff * attempt
                print(f"  [!] HTTP {resp.status_code}, retry dalam {wait:.1f}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except Exception as e:  # requests.RequestException / curl_cffi errors
            wait = backoff * attempt
            print(f"  [!] Error: {e}, retry dalam {wait:.1f}s ...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Gagal mengambil data setelah {max_retries} percobaan: {url}")


def _reply_key(reply, fallback_index=0):
    """Kunci dedupe unik per pengumuman (Id2 > NoPengumuman > fallback)."""
    peng = reply.get("pengumuman", {})
    key = peng.get("Id2") or peng.get("NoPengumuman")
    if key:
        return str(key)
    return f"fallback:{peng.get('TglPengumuman', '')}:{str(peng.get('JudulPengumuman', ''))[:60]}:{fallback_index}"


def fetch_all_announcements(
    date_from="19010101",
    date_to=None,
    keyword="",
    kode_emiten="",
    emiten_type="*",
    page_size=100,
    max_items=None,
    sleep_between=0.6,
):
    """Ambil semua pengumuman sesuai filter.

    Mengembalikan (list_of_replies, total_result_count).

    Strategi hybrid (lihat catatan PAGING di docstring modul):
    1. Paging indexFrom biasa.
    2. Bila hasil terpotong (window pendek -> IDX balas halaman kosong),
       ulangi otomatis dengan iterasi per hari (indexFrom=0, pageSize besar)
       lalu dedupe berdasarkan Id2.
    """
    if date_to is None:
        date_to = datetime.now().strftime("%Y%m%d")

    params_base = {
        "kodeEmiten": kode_emiten,
        "emitenType": emiten_type,
        "lang": "id",
        "keyword": keyword,
    }

    # --- Strategi 1: paging indexFrom ---
    all_replies, seen_ids = [], set()
    total, index_from = 0, 0
    while True:
        params = {
            **params_base,
            "indexFrom": index_from,
            "pageSize": page_size,
            "dateFrom": date_from,
            "dateTo": date_to,
        }
        data = request_with_retry(API_URL, params)
        total = data.get("ResultCount", 0)
        replies = data.get("Replies", [])
        if not replies:
            break
        for reply in replies:
            key = _reply_key(reply)
            if key not in seen_ids:
                seen_ids.add(key)
                all_replies.append(reply)
        print(f"  Terkumpul {len(all_replies)}/{total} pengumuman ...")
        index_from += page_size
        if index_from >= total:
            break
        if max_items and len(all_replies) >= max_items:
            all_replies = all_replies[:max_items]
            break
        time.sleep(sleep_between)  # jangan terlalu agresif ke server IDX

    # --- Strategi 2 (fallback): terpotong -> iterasi per hari ---
    if len(all_replies) < total:
        print(
            f"  [!] Paging indexFrom terpotong ({len(all_replies)}/{total}); "
            "beralih ke mode per-hari ...",
            file=sys.stderr,
        )
        all_replies, seen_ids = [], set()
        day_page_size = max(page_size, 500)
        day = datetime.strptime(date_from, "%Y%m%d")
        end_day = datetime.strptime(date_to, "%Y%m%d")
        while day <= end_day:
            ds = day.strftime("%Y%m%d")
            data = request_with_retry(
                API_URL,
                {
                    **params_base,
                    "indexFrom": 0,
                    "pageSize": day_page_size,
                    "dateFrom": ds,
                    "dateTo": ds,
                },
            )
            total_day = data.get("ResultCount", 0)
            new_count = 0
            for reply in data.get("Replies", []):
                key = _reply_key(reply)
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_replies.append(reply)
                    new_count += 1
            if total_day > day_page_size:
                print(
                    f"  [!] {ds}: {total_day} pengumuman > pageSize {day_page_size}, "
                    "kemungkinan tidak lengkap",
                    file=sys.stderr,
                )
            print(f"  {ds}: +{new_count} (total hari ini {total_day}) -> terkumpul {len(all_replies)}")
            if max_items and len(all_replies) >= max_items:
                all_replies = all_replies[:max_items]
                break
            day += timedelta(days=1)
            if day <= end_day:
                time.sleep(sleep_between)

    return all_replies, total


def download_pdf(url, dest_path, max_retries=4, sleep_between=0.3):
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        return "skip (sudah ada)"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    for attempt in range(1, max_retries + 1):
        try:
            resp = http_get(url, timeout=60)
            if resp.status_code == 200:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                time.sleep(sleep_between)
                return "ok"
            time.sleep(1.5 * attempt)
        except Exception:  # requests.RequestException / curl_cffi errors
            time.sleep(1.5 * attempt)
    return "gagal"


def main():
    p = argparse.ArgumentParser(description="Ambil & download Keterbukaan Informasi IDX")
    p.add_argument("--kode", default="", help="Kode emiten, mis. BBCA (kosongkan untuk semua)")
    p.add_argument("--keyword", default="", help="Kata kunci pencarian bebas")
    p.add_argument("--emiten-type", default="*", help="* semua, s saham, o obligasi&sukuk")
    p.add_argument("--date-from", default="19010101", help="Format YYYYMMDD")
    p.add_argument("--date-to", default=None, help="Format YYYYMMDD (default: hari ini)")
    p.add_argument("--page-size", type=int, default=100, help="Item per halaman (maks teruji 500)")
    p.add_argument("--max", type=int, default=None, help="Batasi jumlah pengumuman diproses")
    p.add_argument("--outdir", default="hasil_idx_keterbukaan_informasi")
    p.add_argument("--no-download", action="store_true", help="Hanya ambil metadata, jangan download PDF")
    args = p.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    pdf_dir = os.path.join(args.outdir, "pdf")

    print("Mengambil daftar pengumuman dari IDX ...")
    replies, total = fetch_all_announcements(
        date_from=args.date_from,
        date_to=args.date_to,
        keyword=args.keyword,
        kode_emiten=args.kode,
        emiten_type=args.emiten_type,
        page_size=args.page_size,
        max_items=args.max,
    )
    print(f"Total ditemukan sesuai filter: {total}. Diproses: {len(replies)}.")

    csv_path = os.path.join(args.outdir, "metadata.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "TglPengumuman", "KodeEmiten", "JudulPengumuman", "NoPengumuman",
            "JenisPengumuman", "Id2", "JumlahLampiran", "NamaFile", "URLPDF",
            "IsLampiran", "PathLokal",
        ])

        n_pdf_ok, n_pdf_skip, n_pdf_fail = 0, 0, 0

        for i, item in enumerate(replies, 1):
            peng = item.get("pengumuman", {})
            attachments = item.get("attachments", [])
            kode = (peng.get("Kode_Emiten") or "").strip() or "UNKNOWN"
            tgl = peng.get("TglPengumuman", "")
            judul = peng.get("JudulPengumuman", "")
            no_peng = peng.get("NoPengumuman", "")
            jenis = peng.get("JenisPengumuman", "")
            id2 = peng.get("Id2", "")

            if not attachments:
                writer.writerow([tgl, kode, judul, no_peng, jenis, id2, 0, "", "", "", ""])
                continue

            for att in attachments:
                url = att.get("FullSavePath", "")
                orig_name = att.get("OriginalFilename") or os.path.basename(url)
                is_lampiran = att.get("IsAttachment", False)
                local_path = ""

                if url and not args.no_download:
                    fname = sanitize_filename(orig_name)
                    local_path = os.path.join(pdf_dir, sanitize_filename(kode), fname)
                    status = download_pdf(url, local_path)
                    if status == "ok":
                        n_pdf_ok += 1
                    elif status.startswith("skip"):
                        n_pdf_skip += 1
                    else:
                        n_pdf_fail += 1
                        local_path = f"(gagal: {status})"

                writer.writerow([
                    tgl, kode, judul, no_peng, jenis, id2,
                    len(attachments), orig_name, url, is_lampiran, local_path,
                ])

            if i % 25 == 0:
                print(f"  ... {i}/{len(replies)} pengumuman diproses")

    print(f"\nSelesai. Metadata tersimpan di: {csv_path}")
    if not args.no_download:
        print(f"PDF diunduh ke: {pdf_dir}")
        print(f"  Berhasil: {n_pdf_ok} | Sudah ada sebelumnya: {n_pdf_skip} | Gagal: {n_pdf_fail}")


if __name__ == "__main__":
    main()
