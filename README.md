# Agent Keterbukaan Informasi IDX

Agen yang memantau [Keterbukaan Informasi IDX](https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi),
membaca lampiran PDF pengumuman baru, meringkasnya dengan LLM, lalu mengirim
hasilnya ke Telegram.

Tanpa antarmuka visual. Jalankan, biarkan hidup, notifikasi datang sendiri.

```
IDX API ──> dedupe ──> filter ──> baca PDF ──> ringkas (LLM) ──> Telegram
            SQLite              LangGraph
```

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

Dengan kedua filter kosong, sekitar 49 pengumuman per hari akan masuk Telegram
dan masing-masing memakai satu panggilan LLM. Isi minimal salah satunya bila
ingin lebih sepi.

## Struktur

| Berkas | Isi |
|---|---|
| `app/idx_client.py` | Klien API IDX; menembus Cloudflare via `curl_cffi` |
| `app/repository.py` | SQLite; dedupe atomik lewat `INSERT OR IGNORE` |
| `app/documents.py` | Unduh + ekstrak teks lampiran PDF |
| `app/graph.py` | LangGraph: filter -> baca dokumen -> ringkas -> kirim |
| `app/telegram.py` | Pengiriman pesan Telegram |
| `app/webhook.py` | Webhook keluar opsional (generic/Discord/Slack) |
| `app/main.py` | FastAPI + APScheduler |
| `app/config.py` | Konfigurasi lewat `.env` |

Catatan teknis yang tidak jelas dari kode disimpan sebagai komentar di tempatnya
-- misalnya kenapa `PAGE_SIZE` besar dipakai alih-alih paging `indexFrom`
(`app/idx_client.py`).

## Perilaku saat gagal

Dirancang agar kegagalan parsial tidak menjatuhkan keseluruhan:

- Konfigurasi LLM/Telegram tidak lengkap -> agen nonaktif, aplikasi tetap jalan.
- Lampiran PDF gagal diunduh atau dibaca -> diringkas dari metadata saja.
- Poll IDX gagal -> dicatat satu baris, siklus berikutnya lanjut.

Isi PDF diperlakukan sebagai data tidak tepercaya di system prompt, jadi
instruksi yang disisipkan di dalam dokumen tidak dituruti.

## Batasan yang diketahui

Belum ada retry saat IDX membalas 503, dan pengumuman berstatus `failed` tidak
dicoba ulang secara otomatis. Daftar lengkap beserta review desain ada di
[BOTTLENECKS.md](BOTTLENECKS.md).

## Keamanan

`.env` ada di `.gitignore` dan tidak pernah ikut ter-commit. Bila token
Telegram atau kunci API sempat terekspos, terbitkan ulang: BotFather
`/revoke` untuk bot, dan rotasi kunci di penyedia LLM Anda.

Jangan paparkan aplikasi ini ke internet tanpa `IDX_WEBHOOK_SECRET` yang kuat --
endpoint `/trigger` dan `/webhook/idx` dapat memicu panggilan LLM dan pesan
Telegram.
