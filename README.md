# Agent Keterbukaan Informasi IDX

Agen yang memantau [Keterbukaan Informasi IDX](https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi),
membaca lampiran PDF pengumuman baru, menilai materialitasnya terhadap profil
minat Anda, meringkas yang lolos, lalu mengirimnya ke Telegram.

Tanpa antarmuka visual. Jalankan, biarkan hidup, notifikasi datang sendiri.

```
IDX API ──> dedupe ──> filter ──> baca PDF ──> triage ──> ringkas ──> Telegram
            SQLite                            (LLM)      (LLM)
                       └──────────── LangGraph ────────────┘
```

Triage adalah saringan utama: tiap pengumuman dinilai 1-5, dan yang di bawah
`AGENT_MIN_IMPORTANCE` berhenti di sana tanpa biaya peringkasan.

## Kenapa polling, bukan webhook

IDX tidak menyediakan webhook publik maupun websocket untuk keterbukaan
informasi. Satu-satunya jalan adalah memanggil endpoint JSON mereka secara
berkala. Aplikasi ini melakukannya tiap 3 menit dan memakai `Id2` dari IDX
sebagai kunci dedupe, sehingga pengumuman yang sama tidak pernah dikirim dua
kali meski ditarik ulang ratusan kali.

Endpoint `POST /webhook/idx` dan `WS /ws/idx` yang tersedia arahnya **masuk** ke
agen -- berguna bila Anda punya sumber lain yang ingin mendorong data ke sini.

## Kebutuhan

- Python 3.11+
- Endpoint LLM kompatibel OpenAI (9router, LiteLLM, OpenAI, dll)
- Bot Telegram dari [@BotFather](https://t.me/BotFather)

## Instalasi

```sh
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt         # Linux/macOS

cp .env.example .env
```

Isi `.env`. Dua nilai yang perlu langkah tambahan:

**`TELEGRAM_CHAT_ID`** -- kirim `/start` ke bot Anda, lalu buka
`https://api.telegram.org/bot<TOKEN>/getUpdates` dan ambil
`result[0].message.chat.id`. Hati-hati: itu **bukan** `update_id`.

**`IDX_WEBHOOK_SECRET`** -- bangkitkan dengan:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Menjalankan

```sh
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Poll pertama saat startup dijadikan **baseline**: pengumuman yang sudah ada
dicatat tanpa dikirim, supaya Telegram tidak dibanjiri puluhan pesan lama.
Sejak poll kedua, hanya pengumuman yang terbit dalam
`IDX_LOOKBACK_MINUTES` terakhir yang dinotifikasi.

### Endpoint

| Route | Fungsi |
|---|---|
| `GET /health` | Status agen, hasil poll terakhir |
| `GET /stats` | Jumlah disclosure per status |
| `POST /trigger` | Poll manual sekarang |
| `POST /webhook/idx` | Dorong disclosure dari luar |
| `WS /ws/idx` | Sama, lewat websocket |

Semua kecuali `/health` memerlukan header `X-IDX-Webhook-Secret`.

```sh
curl -X POST http://127.0.0.1:8000/trigger -H "X-IDX-Webhook-Secret: $SECRET"
```

## Konfigurasi penting

| Variabel | Default | Keterangan |
|---|---|---|
| `IDX_POLL_SECONDS` | `180` | Jangan terlalu rapat; IDX membalas 503 bila diserbu |
| `IDX_LOOKBACK_MINUTES` | `10` | Jendela umur pengumuman yang layak dinotifikasi |
| `IDX_ISSUERS` | kosong | Kosong = semua emiten |
| `IDX_KEYWORDS` | kosong | Kosong = semua topik |
| `AGENT_PROFILE` | investor ritel | Profil minat Anda; dasar penilaian triage |
| `AGENT_MIN_IMPORTANCE` | `3` | Ambang skor 1-5. `3` longgar, `4` ketat |
| `AGENT_LLM_TRIAGE` | `true` | `false` = lewati triage, kembali ke filter kata kunci |

IDX menerbitkan sekitar 49 pengumuman per hari. Dengan kedua filter kata kunci
kosong, semuanya masuk ke triage, tetapi hanya sebagian kecil yang lolos ke
Telegram -- kalibrasi atas 12 pengumuman nyata memberi sebaran skor
`{1: 9, 2: 2, 3: 1}`. Naikkan `AGENT_MIN_IMPORTANCE` ke `4` bila masih ramai;
turunkan ke `2` bila terlalu sepi.

`AGENT_PROFILE` ditulis bebas dalam Bahasa Indonesia. Semakin spesifik
(sektor, ukuran posisi, aksi korporasi yang diincar), semakin tajam triage.

## Struktur

| Berkas | Isi |
|---|---|
| `app/idx_client.py` | Klien API IDX; menembus Cloudflare via `curl_cffi` |
| `app/repository.py` | SQLite; dedupe atomik lewat `INSERT OR IGNORE` |
| `app/documents.py` | Unduh + ekstrak teks lampiran PDF (maks 3, total 12.000 karakter) |
| `app/graph.py` | LangGraph: filter -> baca dokumen -> triage -> ringkas -> kirim |
| `app/pipeline.py` | Dedupe, status DB, reprocess `failed`, webhook keluar |
| `app/telegram.py` | Pengiriman pesan Telegram |
| `app/webhook.py` | Webhook keluar opsional (generic/Discord/Slack) |
| `app/main.py` | FastAPI + APScheduler (adapter, tanpa logika bisnis) |
| `app/config.py` | Konfigurasi lewat `.env` |

Catatan teknis yang tidak jelas dari kode disimpan sebagai komentar di tempatnya
-- misalnya kenapa `PAGE_SIZE` besar dipakai alih-alih paging `indexFrom`
(`app/idx_client.py`).

## Perilaku saat gagal

Dirancang agar kegagalan parsial tidak menjatuhkan keseluruhan:

- Konfigurasi LLM/Telegram tidak lengkap -> agen nonaktif, aplikasi tetap jalan.
- Lampiran PDF gagal diunduh atau dibaca -> dilewati, lampiran lain tetap dibaca;
  bila semua gagal, penilaian memakai metadata saja.
- Poll IDX membalas 429/5xx -> diulang maksimal 4x dengan backoff 1/2/4 detik dan
  menghormati `Retry-After`. `403` tidak diulang.
- Telegram membalas 429/5xx -> diulang 3x. `4xx` lain permanen, pengumuman
  ditandai `failed`.
- Pengumuman berstatus `failed` dicoba ulang maksimal 3x pada poll berikutnya
  (maks 10 baris per siklus). Webhook keluar tidak dikirim ulang.

Isi PDF diperlakukan sebagai data tidak tepercaya di system prompt, jadi
instruksi yang disisipkan di dalam dokumen tidak dituruti.

## Tes

```sh
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Seluruh tes berjalan offline -- IDX, 9router, dan Telegram semuanya dipalsukan.
Jangan menjalankan graph tanpa menyuntik `notifier` palsu: pesan akan benar-benar
terkirim ke Telegram.

## Batasan yang diketahui

Pemrosesan masih berurutan satu per satu, dan belum ada jeda global untuk
menahan burst >20 pesan per menit ke Telegram. Daftar lengkap beserta review
desain ada di [BOTTLENECKS.md](BOTTLENECKS.md).

## Keamanan

`.env` ada di `.gitignore` dan tidak pernah ikut ter-commit. Bila token
Telegram atau kunci API sempat terekspos, terbitkan ulang: BotFather
`/revoke` untuk bot, dan rotasi kunci di penyedia LLM Anda.

Jangan paparkan aplikasi ini ke internet tanpa `IDX_WEBHOOK_SECRET` yang kuat --
endpoint `/trigger` dan `/webhook/idx` dapat memicu panggilan LLM dan pesan
Telegram.
