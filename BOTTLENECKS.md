# Bottleneck & Utang Teknis

Catatan hal-hal yang diketahui belum beres. Ditulis 11 Sep 2026 setelah
pipeline terbukti jalan end-to-end (IDX -> dedupe -> LangGraph -> Telegram),
diperbarui 13 Sep 2026.

Item yang sudah selesai ditandai **[SELESAI]** dan dipertahankan sebagai
catatan: alasan perbaikannya kerap masih relevan untuk keputusan berikutnya.

Dua bagian: **masalah operasional** (perilaku saat berjalan) dan **review
desain** (bentuk kode). Urutan dalam tiap bagian: dampak tertinggi di atas.

---

# Bagian I -- Masalah Operasional

## 1. Berita bisa hilang permanen saat poll gagal *(sebagian selesai)*

**Berkas:** `app/idx_client.py:84`, `app/main.py` (`poll_idx_safe`)

`_fetch_range` langsung `raise_for_status()`. Satu HTTP 503 transien dari
Cloudflare IDX membatalkan seluruh siklus poll. Sudah terjadi beberapa kali
dalam pemakaian normal.

Retry 403/429/503 dengan backoff pernah ada di versi `requests`, hilang saat
migrasi ke `curl_cffi`.

Bahayanya bukan error itu sendiri (scheduler lanjut menit berikutnya),
melainkan interaksi dengan `IDX_LOOKBACK_MINUTES=10`: bila pengumuman rilis
tepat saat poll gagal dan menit-menit berikutnya ikut gagal sampai lewat 10
menit, pengumuman itu **tidak pernah masuk** jendela dan hilang selamanya.

**Perbaikan:** retry 3-5x, backoff eksponensial, khusus status transien.

Terpasang 13 Sep: `IDXClient` mengulang maksimal empat kali pada `429`, `500`,
`502`, `503`, `504`, serta error jaringan `curl_cffi`. Backoff: 1, 2, 4 detik;
header `Retry-After` dihormati bila IDX mengirimkannya. `403` tidak diulang,
karena itu masalah autentikasi/fingerprint Cloudflare, bukan transien.

Tiga kasus diuji tanpa request IDX: `503 -> 200`, `429` dengan `Retry-After`,
dan `403` tanpa pengulangan. Risiko hilang tetap ada bila IDX gagal terus selama
semua empat percobaan dan seluruh jendela lookback; itu ditutup oleh item 2
(reprocess `failed`), yang belum dikerjakan.

---

## 2. Item berstatus `failed` tidak pernah dicoba ulang **[SELESAI 13 Sep]**

**Berkas:** `app/repository.py` (`insert_if_new`), `app/main.py` (`process`)

`insert_if_new` menulis baris **sebelum** diproses. Bila LLM atau Telegram
gagal, status jadi `failed` tetapi `id` sudah terlanjur ada di tabel. Poll
berikutnya membacanya sebagai `duplicate` dan melewatinya.

Trade-off ini disengaja (lebih baik kehilangan satu notifikasi daripada
membanjiri Telegram saat bug), tapi tidak ada jalan pemulihan otomatis. Saat
ini harus hapus baris manual lewat SQLite.

**Perbaikan:** job terpisah yang memindai `status IN ('failed')` dan
memprosesnya ulang, dengan batas percobaan.

Terpasang: setiap siklus poll lebih dulu mengambil maksimal 10 baris
`failed` dengan kurang dari tiga kegagalan total. Retry hanya menjalankan
LangGraph dan Telegram; webhook keluar tidak dikirim ulang. Kolom `attempts`
ditambahkan dengan migrasi otomatis pada database lama.

Dua perilaku diuji: item gagal bisa pulih menjadi `sent`, dan item berhenti
setelah tiga kegagalan total.

---

## 3. Rate limit IDX belum dihormati **[SELESAI 13 Sep]**

**Berkas:** `app/main.py` (scheduler), `.env` (`IDX_POLL_SECONDS`)

Interval sempat diturunkan ke 60 detik. Tiap poll menarik **seluruh
pengumuman hari itu** (`dateFrom`/`dateTo` IDX hanya granular harian, ~49 baris
per request, `PAGE_SIZE=500`), lalu 48 dari 49 langsung dibuang sebagai
duplikat. Beban ke IDX besar, manfaatnya nol.

503 yang muncul di log berkorelasi dengan interval rapat ini.

**Perbaikan:** kembalikan ke 180 detik. Pengumuman IDX tidak sedetik-genting.

`IDX_POLL_SECONDS=180` sejak 13 Sep. Masalah 1 (retry) tetap terbuka dan lebih
penting: interval longgar mengurangi frekuensi 503, tidak menghilangkannya.

---

## 4. Pemrosesan serial memperlambat poll

**Berkas:** `app/main.py` (`poll_idx`)

```python
results = [await process(item) for item in disclosures]
```

Berurutan. Tiap item bisa memakan download PDF (timeout 60 detik) + panggilan
LLM. Bila sepuluh pengumuman rilis bersamaan, siklus poll bisa lebih panjang
dari intervalnya sendiri.

`max_instances=1` + `coalesce=True` mencegah penumpukan job, tapi notifikasi
jadi telat.

**Perbaikan:** `asyncio.gather` dengan `Semaphore` (misal 3 bersamaan) agar
tidak membanjiri 9router maupun Telegram.

---

## 5. Hanya lampiran pertama yang dibaca *(sebagian selesai)*

**Berkas:** `app/documents.py` (`extract_primary_pdf`)

Mengambil `next()` lampiran pertama yang punya `FullSavePath`. Pengumuman IDX
sering punya banyak lampiran, dan yang pertama kerap hanya surat pengantar --
substansinya ada di lampiran kedua atau ketiga.

Ditambah cap `MAX_DOCUMENT_CHARS = 12_000`: laporan keuangan terpotong di
tengah, LLM meringkas dari potongan awal saja.

Terlihat di test pertama: ringkasan berisi "Emiten: tidak tercantum",
"Berlaku: belum tercantum" -- informasinya memang tidak ada di teks yang masuk.

**Perbaikan:** pilih lampiran berdasarkan nama/ukuran, atau gabungkan beberapa
lampiran dengan anggaran karakter.

### 5a. Lampiran selalu gagal diunduh **[SELESAI 13 Sep]**

Akar yang lebih dalam, tidak terdeteksi saat dokumen ini ditulis: `documents.py`
memakai `requests`, sehingga **semua** unduhan lampiran ditolak Cloudflare
dengan 403. Bukan sebagian teks yang hilang -- seluruhnya. Setiap penilaian
triage dibuat dari judul saja.

Diperbaiki dengan `curl_cffi` `impersonate="chrome"`, sama seperti `IDXClient`.
Probe: `requests` 403 `text/html` vs `curl_cffi` 200 `application/pdf` pada tiga
URL. Setelahnya 6/6 lampiran terbaca (2.015-12.000 karakter).

Sebaran skor triage tidak berubah, tetapi dasarnya berubah: alasan untuk KDTN
berpindah dari "tanpa data pihak, jumlah" menjadi "menjual 2,3 juta saham (0,19
poin persentase)".

Sisa yang belum: pemilihan lampiran dan `MAX_DOCUMENT_CHARS` (bagian utama di
atas). Satu dokumen sudah menyentuh cap 12.000 karakter.

**Pelajaran:** setiap klien HTTP baru ke `idx.co.id` wajib `curl_cffi`. Sudah
dicatat di `requirements.txt`.

---

## 6. Tidak ada pembatasan laju ke Telegram

**Berkas:** `app/telegram.py` (`send_telegram`)

Telegram membatasi ~30 pesan per detik dan ~20 pesan per menit untuk grup
yang sama. Tidak ada penanganan `429` maupun `retry_after` dari respons
Telegram. Bila banyak pengumuman rilis bersamaan (lazim jam 16.00-17.00 WIB),
sebagian akan ditolak dan berakhir `failed` -- lalu terkena masalah nomor 2.

**Perbaikan:** hormati `parameters.retry_after` dari respons Telegram, plus
jeda antar-pesan.

---

## 7. Filter masih menerima semua berita **[SELESAI 13 Sep]**

**Berkas:** `app/config.py`, `.env` (`IDX_ISSUERS`, `IDX_KEYWORDS`)

Keduanya kosong, artinya `Disclosure.is_relevant()` meloloskan semua. Sekitar
49 pengumuman per hari dikirim penuh ke Telegram, masing-masing memakai satu
panggilan LLM.

Rencana berikutnya: node triage berbasis LLM yang menilai materialitas
(bukan sekadar cocok kata kunci), dengan ambang skor. Dibahas lalu ditunda.

Node `triage` terpasang 13 Sep. Menilai 1-5 terhadap `AGENT_PROFILE`, berhenti
di bawah `AGENT_MIN_IMPORTANCE` (default 3) sebelum biaya peringkasan. Kalibrasi
atas 12 disclosure nyata: sebaran `{1: 9, 2: 2, 3: 1}`, 1 dari 12 terkirim.

Catatan implementasi: endpoint 9router menerima permintaan `json_schema` tetapi
tetap membalas prosa, sehingga `method="function_calling"` wajib. `json_mode`
juga gagal.

`IDX_ISSUERS` dan `IDX_KEYWORDS` sengaja dibiarkan kosong -- triage menggantikan
perannya. Konsekuensinya: mematikan `AGENT_LLM_TRIAGE` kini membuat **semua**
pengumuman lolos ke Telegram.

---

## 8. `WebhookNotifier` tidak terpakai

**Berkas:** `app/webhook.py`, `app/config.py`, `app/main.py:79-89`

Sisa dari iterasi sebelum Telegram dipilih. `WEBHOOK_URL` kosong, jadi tiap
disclosure selalu memberi status `no_webhook`. Menambah cabang di `process()`
dan kolom konfigurasi yang tidak dipakai.

**Perbaikan:** hapus, atau pertahankan bila nanti Discord/Slack diperlukan.

---

## 9. `idx_keterbukaan_informasi.py` tertinggal di root

Script referensi asli dari pengguna. Sudah digantikan `app/idx_client.py`.
Tidak diimpor siapa pun.

Catatan 13 Sep: masih ada. Menjalankannya saat server hidup memicu 503 karena
menembak API IDX bersamaan dengan poller.

---

## Hal yang sudah aman (operasional)

- Dedupe atomik lewat `INSERT OR IGNORE` + primary key `Id2`.
- 403 Cloudflare teratasi dengan `curl_cffi` `impersonate="chrome"`, pada API
  maupun unduhan lampiran.
- Triage gagal berarti berita tetap lolos (fail-open), bukan hilang diam-diam.
- Kegagalan `build_graph()` tidak mematikan aplikasi (mode webhook-only).
- Kegagalan ekstraksi PDF tidak memblokir notifikasi (`text = ""`).
- Isi PDF diperlakukan sebagai data tidak tepercaya di system prompt.
- Poll pertama jadi baseline, tidak membanjiri Telegram saat startup.
- Pesan Telegram dipotong 4096 karakter.

---

# Bagian II -- Review Desain

Bagian di atas soal perilaku saat berjalan. Bagian ini soal bentuk kode:
apakah tiap module punya interface kecil dengan implementasi banyak di
baliknya, dan apakah seam-nya berada di tempat yang benar.

Kosakata: *module* (apa pun yang punya interface + implementasi), *interface*
(semua yang harus diketahui caller, termasuk mode error), *depth* (banyak
perilaku di balik interface kecil), *seam* (tempat perilaku bisa diganti tanpa
mengedit di situ), *leverage* (keuntungan caller), *locality* (keuntungan
pemelihara).

## Peta kedalaman

| Module | Interface | Depth | Catatan |
|---|---|---|---|
| `IDXClient` | `fetch_recent() -> list[Disclosure]` | Dalam | Menyembunyikan Cloudflare, WIB, paging, mapping |
| `Repository` | 6 metode | Sedang | `mark_*` dangkal; dedupe kuat |
| `build_graph` | `(Settings) -> CompiledGraph` | Dalam | Seam salah tempat |
| `extract_primary_pdf` | `(Disclosure) -> str` | Dalam | Kontrak error bocor |
| `send_telegram` | `(Settings, str) -> None` | Dangkal | Pass-through `httpx` |
| `WebhookNotifier` | `send() -> bool` | Dalam | Nol pemakai |
| `app/main.py` | modul-level | -- | Tidak dapat diuji |

Kesimpulan umum: struktur file rapi, tiap module bertanggung jawab atas satu
hal, tidak ada file gemuk. Masalahnya kedalaman yang tidak merata, ditambah
satu module yang tidak bisa diuji sama sekali.

---

## D1. `app/main.py` tidak punya seam **[SELESAI 13 Sep]**

**Berkas:** `app/main.py:20-52`

```python
settings = get_settings()
repository = Repository(settings.database_path)
scheduler = AsyncIOScheduler()
webhook_notifier = WebhookNotifier(settings) if settings.webhook_url else None
graph = _build_graph()
```

Lima dependensi dibuat saat **import** dan disimpan sebagai state modul.
`process()` dan `poll_idx()` membacanya dari global, bukan menerimanya sebagai
parameter. Ini melanggar prinsip *accept dependencies, don't create them*.

Akibatnya: menguji `process()` mengharuskan menyalakan seluruh aplikasi,
menyentuh SQLite asli, memanggil LLM asli, dan mengirim Telegram asli. Terbukti
saat pengujian pertama -- satu-satunya cara adalah script yang menembak server
hidup lewat HTTP.

`process()` justru logika paling berharga di codebase (orkestrasi dedupe, dua
channel, pemetaan status), tetapi paling sulit disentuh.

**Perbaikan:** module `Pipeline` yang menerima `repository`, `graph`, dan
`notifier` lewat konstruktor. Interface satu metode:
`process(disclosure) -> str`. `main.py` menyusut menjadi adapter HTTP +
scheduler.

Terpasang di `app/pipeline.py`. `Pipeline` menerima `repository`, graph, dan
webhook notifier lewat konstruktor; HTTP endpoint dan poller di `main.py`
memanggil `pipeline.process()`. Lima cabang diuji lewat `tests/test_pipeline.py`
tanpa IDX, 9router, atau Telegram: baseline/duplikat, filter, triage, sukses,
dan error agen.

Saat menambahkan retry IDX atau proses ulang `failed`, tes dapat menyuntikkan
adapter palsu tanpa menyentuh state modul atau database produksi.

---

## D2. Seam LLM berada di dalam graph, bukan di sekelilingnya **[SELESAI]**

**Berkas:** `app/graph.py:26-32`

`build_graph` membuat sendiri `ChatOpenAI`, sehingga model palsu tidak bisa
disuntikkan. Untuk memeriksa apakah node `filter` benar memotong item tidak
relevan, 9router tetap harus hidup.

Ini seam **nyata**, bukan hipotetis: sudah ada dua adapter potensial (9router
untuk produksi, fake untuk tes).

**Perbaikan:** `build_graph(settings, model=None)`, default membuat `ChatOpenAI`.

Terpasang, beserta seam `notifier` dengan alasan yang sama: menguji graph tanpa
suntikan pernah benar-benar mengirim pesan uji ke Telegram pengguna. Keduanya
terbukti dipakai saat kalibrasi triage -- 12 disclosure dinilai tanpa satu pun
notifikasi terkirim.

---

## D3. Kontrak error `extract_primary_pdf` bocor **[SELESAI]**

**Berkas:** `app/graph.py:46-51`, `app/documents.py`

```python
except (OSError, ValueError):
    text = ""
```

Terverifikasi lewat interpreter:

```text
PdfReadError MRO: ['PdfReadError', 'PyPdfError', 'Exception', ...]
caught by (OSError, ValueError)? False
```

`pypdf.PdfReadError` tidak tertangkap. PDF rusak atau terenkripsi akan lolos,
membunuh pemrosesan disclosure tersebut, menandainya `failed` -- lalu terkena
masalah nomor 2 di Bagian I dan tidak pernah dicoba ulang.

Akarnya soal interface, bukan kelalaian: caller dipaksa menebak kelas exception
apa saja yang mungkin keluar, dan menebak salah. **Interface mencakup mode
error**, bukan hanya tipe kembalian. Module ini menjanjikan `-> str` tetapi
diam-diam melempar exception dari dua library berbeda.

**Perbaikan:** tangani di dalam `extract_primary_pdf`, kembalikan `""`. Satu
tempat, bukan sebanyak jumlah caller.

Terpasang: unduh+parse dipindah ke `_download_and_parse`, semua exception
ditangkap di pemanggilnya. Kontrak kini eksplisit di docstring: *tidak pernah
melempar*.

Manfaatnya langsung terbukti: migrasi `requests` -> `curl_cffi` (5a) hanya
menyentuh `_download_and_parse`. `graph.py` tidak berubah sama sekali, meski
kelas exception yang dilempar ikut berganti library.

---

## D4. `Repository.mark_*` dangkal

**Berkas:** `app/repository.py:60-84`

`mark_processed`, `mark_skipped`, `mark_failed` masing-masing satu `UPDATE`
yang nyaris identik. Tiga metode interface untuk tiga baris SQL -- interface
hampir sekompleks implementasinya.

Bandingkan `insert_if_new`: satu metode, menyembunyikan atomisitas
cek-dan-tulis, semantik `rowcount`, dan perilaku primary key. Itu leverage
nyata.

Efek sampingnya: `process()` menjadi mesin status -- menghitung status lalu
memilih metode mana yang dipanggil. Pengetahuan tentang status tersebar di dua
module.

---

## D5. `send_telegram` terlalu dangkal untuk bebannya

**Berkas:** `app/telegram.py`

Nyaris murni pass-through ke `httpx`. Tanpa retry, tanpa penanganan
`retry_after`, tanpa pembatasan laju, dan membuat-membuang client tiap
panggilan. Masalah nomor 6 di Bagian I adalah gejalanya.

Bila retry dan rate limit ditambahkan nanti, semuanya harus masuk ke module ini
agar locality terjaga -- jangan disebar ke node `notify`.

Dipanggil langsung dari dalam graph, sehingga punya masalah seam yang sama
dengan D2: tidak bisa diganti fake saat pengujian.

---

## D6. `WebhookNotifier` adalah seam hipotetis

**Berkas:** `app/webhook.py`

144 baris, tiga format payload, retry lengkap dengan `Retry-After`.
Implementasi paling matang di codebase. Jumlah pemakai: nol.

Aturannya: satu adapter berarti seam hipotetis, dua adapter berarti seam nyata.
Di sini bahkan nol. Ia menambah cabang di `process()`, empat field konfigurasi,
dan status `no_webhook` di setiap baris log.

Catatan: kualitas retry-nya lebih tinggi daripada `send_telegram` yang justru
dipakai. Polanya layak dipindahkan ke sana.

---

## D7. `Settings` sebagai parameter menyembunyikan kopling

**Berkas:** hampir semua module

`send_telegram` butuh 2 field tetapi menerima 20; `build_graph` butuh 5.

Interface tampak kecil (`(Settings, str)`) padahal sebenarnya besar: caller
harus tahu field mana yang relevan, dan setiap tes harus membangun `Settings`
penuh. Ini **depth semu**.

---

## Hal yang sudah benar secara desain

- `IDXClient` adalah module dalam yang baik: interface satu metode tanpa
  parameter, menyembunyikan evasi Cloudflare, konversi WIB, keputusan
  `PAGE_SIZE`, dan pemetaan JSON ke `Disclosure`. Komentar `PAGE_SIZE`
  mengunci pengetahuan tentang perilaku API yang tidak terduga di dalam module.
- `Disclosure.is_relevant()` menempatkan aturan kecocokan di model, bukan di
  caller. Satu tempat, dipakai `graph` dan `process`.
- Struktur graph sehat: node murni, state eksplisit, edge conditional memotong
  lebih awal untuk menghemat token.
- Degradasi bertahap (`graph = None`, `text = ""`) menunjukkan pemikiran yang
  benar soal kegagalan parsial.

---

## Prioritas desain

1. ~~**D3** -- perbaiki tangkapan `PdfReadError`.~~ **Selesai.**
2. ~~**D2** -- suntikkan `model` ke `build_graph`.~~ **Selesai**, plus seam
   `notifier`.
3. ~~**D1** -- ekstrak `Pipeline` dari `main.py`.~~ **Selesai**, dengan lima
   tes tanpa dependency eksternal.
4. **D6** -- putuskan nasib `WebhookNotifier`: hapus atau pakai.

D1-D3 sudah membayar dirinya sendiri (lihat catatan di masing-masing). Langkah
berikutnya dapat menambah retry dan reprocess lewat seam yang teruji.

---

## Urutan kerja yang disarankan

1. **Operasional 5 -- pemilihan lampiran + anggaran karakter.** Menaikkan
   kualitas triage, bukan keandalan.
2. **D6 + operasional 8, 9 -- bersih-bersih.** Putuskan nasib
   `WebhookNotifier`, lalu hapus atau arsipkan script root yang usang.
3. **Operasional 6 -- rate limit Telegram.** Penting bila triage atau retry
   menghasilkan banyak pesan dalam satu siklus.
