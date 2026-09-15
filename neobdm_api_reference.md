# NeoBDM — API Reference for Agent Tooling

Hasil eksplorasi manual terhadap `neobdm.tech` (login sebagai member) menggunakan DevTools/network sniffing. Semua endpoint di bawah **butuh session cookie** (login lewat `/accounts/login/`, Django-style session + CSRF) — bukan API key publik. Jadi tool AI agent-mu perlu:

1. Login sekali (simulasikan submit form login, atau lebih simpel: login manual di browser lalu copy cookie session ke agent/script).
2. Simpan cookie `sessionid` (dan `csrftoken` untuk request `POST`/`PATCH`/`PUT`/`DELETE`, dikirim lewat header `X-CSRFToken`).
3. Semua response sukses punya *envelope* yang konsisten:
   ```json
   {
     "success": true,
     "message": "...",
     "data": [...],
     "errors": null,
     "meta": {...},
     "trace_id": "...",
     "timestamp": "...",
     "status": 200
   }
   ```

> Catatan etika: ini API internal, bukan publik/berdokumentasi resmi. Pakai wajar (jangan polling agresif/rate tinggi), dan cek ToS NeoBDM soal scraping/automasi sebelum dipakai produksi.

---

## 1. Endpoint paling berguna untuk analisis (REST bersih)

### Stock universe / index membership
```
GET /api/stock-universe
```
Return semua "universe" saham yang tersedia (COMPOSITE, sektor IDX seperti IDXENERGY/IDXFINANCE/dst, index custom user). Tiap item: `{id, name, stocks: [ticker,...]}`.
**Guna:** dapetin daftar ticker per sektor/index tanpa hardcode.

### Market Summary — metadata kolom
```
GET /api/market-summary/columns
```
Daftar ~50+ metrik yang tersedia (bandarmology-style): `field`, `title`, `desc`, `format` (`string|ufloat|float|percent|boolean`), dan `filter.ops` yang valid. Contoh field: `tval` (Total Value), `m_d_0`/`m_dn_0` (Bandar Flow hari ini & %-nya), `m_w_0`, dst.
**Guna:** tahu metrik apa saja yang bisa diminta/difilter sebelum bikin screener sendiri.

```
GET /api/market-summary/last-update
```
Timestamp update data terakhir.

### Screener (CRUD) — inti dari fitur screening
```
GET    /api/screeners                 -> list screener milik user
GET    /api/screeners/preset          -> list preset screener (Top Akum Bandar, Top Akum Asing, Saham Volatil, dll)
POST   /api/screeners/preset/{preset_id}   -> clone preset jadi screener baru milik user
GET    /api/screeners/{id}
PATCH  /api/screeners/{id}
```
Body screener (contoh dari hasil GET):
```json
{
  "id": "uuid",
  "name": "P - Market Summary (old)",
  "columns": ["is_pinky","is_crossing","is_liquid","m_dn_0", "..."],
  "filters": [{"op":"=","type":"boolean","unit":"","field":"is_comp_m","value":"true"}],
  "stock_universe_id": "uuid",
  "sort_field": "m_dn_0",
  "sort_direction": "desc"
}
```
**Guna:** agent bisa bikin screener custom secara programatik (pilih kolom, filter operator `>=,<=,>,<,=,!=`, sort) — ini paling powerful untuk "cari saham dengan kriteria X".

### Market Summary — data aktual (yang paling penting)
```
POST /api/market-summary/summary/{screener_id}
Body: {"page": 1, "size": 10, "sort_field": "m_dn_0", "sort_direction": "desc"}
```
Return baris data sesuai `columns` yang didefinisikan di screener tsb, contoh 1 baris:
```json
{
  "symbol": "TSPC",
  "is_pinky": false,
  "is_crossing": false,
  "is_liquid": true,
  "m_wn_3": 0.133, "m_wn_2": -0.006, "m_wn_1": 0.140,
  "m_dn_3": 0.186, "m_dn_2": 0.112, "m_dn_1": 0.214, "m_dn_0": 0.359,
  "pct_1": 0.0073, "close": 2750, "suspend": false, "special_notice": false
}
```
**Ini endpoint utama untuk "analisa data pasar"** — kombinasikan dengan Screener CRUD di atas untuk query fleksibel (mis. "saham liquid dengan bandar flow hari ini > 20%, urutkan desc").

### Rotation Chart (RRG — Relative Rotation Graph)
```
GET /api/stock-universe/rotation
GET /api/rotation-chart/{stock_universe_id}?limit=8&weekly=false&liquid_only=false&hide_weak=false&hide_abnormal=false&force_composite_benchmark=false
```
Response `data`: `{date: [...], rs_ratio: {...}, rs_mom: {...}, benchmark: "..."}` — data RRG (relative strength ratio & momentum) per saham/sektor terhadap benchmark, per tanggal.
**Guna:** deteksi rotasi sektor/saham (leading/lagging/improving/weakening) secara kuantitatif.

### Broker Summary
```
GET  /broker_summary/ticker-choices/
POST /api/broker-summary
```
**Guna:** breakdown transaksi per broker untuk 1 ticker (siapa net buy/sell) — data khas "bandarmology".

### Inventory (kepemilikan broker dari waktu ke waktu)
```
GET /api/brokers/inventory       -> daftar kode broker (untuk dropdown, ratusan broker)
```
Dipakai di halaman Inventory Chart & Compare Inventory (posisi akumulasi/distribusi broker tertentu terhadap saham tertentu dari waktu ke waktu).

### Ticker & index autocomplete (choices generik)
```
GET /dashboard/~get-choices/
```
Return array besar `{value, text}` — index (IHSG, COMPOSITE, ISSI, LQ45, dll) + ribuan ticker saham. Berguna sebagai lookup/validasi ticker.

---

## 2. Endpoint chart-heavy (Django-Plotly-Dash — lebih ribet dipakai programatik)

Beberapa halaman visual pakai `django_plotly_dash`, bukan REST biasa. Pola umum per halaman:
```
GET  /django_plotly_dash/app/{app_name}/_dash-layout
GET  /django_plotly_dash/app/{app_name}/_dash-dependencies
POST /django_plotly_dash/app/{app_name}/_dash-update-component   <-- endpoint data aktual
```
`app_name` per fitur:
| Fitur | app_name |
|---|---|
| Sector Activity | `sa_app` |
| Broker Stalker | `bs_app` |
| Transaction Chart | `tc_app` |
| Seasonality Table | `st_app` |
| Balance Position Chart | `bp_app` |

`_dash-update-component` butuh payload spesifik format Dash (`output`, `inputs`, `changedPropIds`, dsb) yang menjiplak state komponen UI React/Dash — **jauh lebih ribet & rapuh untuk dipakai sebagai tool API** dibanding endpoint REST di atas. Kalau memang butuh data dari sini, cara paling praktis: buka network tab pas isi form di UI-nya, copy persis payload yang terkirim, baru bikin tool dengan payload tetap (ganti ticker/parameter yang berubah saja).

Rekomendasi: **prioritaskan endpoint di bagian 1** untuk tooling agent karena jauh lebih stabil (REST biasa, JSON in/out, tidak bergantung state komponen UI).

---

## 3. Fitur lain (kurang relevan untuk analisis data / tidak jadi API terpisah)

- **Watchlist** — sudah dideprecate, digantikan custom Stock Universe di Market Summary.
- **Money Management (`/calculator/`)** — kalkulator posisi trading, murni client-side, tidak fetch data.
- **Done Detail Visualization** — pakai endpoint `~update` (pola Django form-ajax, mis. `/done_detail/~update`) yang trigger dari pemilihan ticker; sama seperti Dash, lebih cocok direplikasi manual per-payload kalau memang dibutuhkan.

---

## 4. Ringkasan endpoint prioritas untuk tool AI agent

| Endpoint | Method | Guna utama |
|---|---|---|
| `/api/stock-universe` | GET | Daftar universe/sektor + anggota ticker |
| `/api/market-summary/columns` | GET | Daftar metrik/kolom yang bisa dipakai |
| `/api/screeners`, `/api/screeners/{id}` | GET/PATCH | Kelola definisi screener (kolom, filter, sort) |
| `/api/screeners/preset` | GET | Daftar preset screener siap pakai |
| `/api/market-summary/summary/{screener_id}` | POST | **Data pasar aktual** sesuai screener (inti analisis) |
| `/api/rotation-chart/{universe_id}` | GET | Data RRG (rotasi sektor/saham) |
| `/api/broker-summary` | POST | Breakdown transaksi per broker per ticker |
| `/api/brokers/inventory` | GET | Daftar broker (untuk inventory/compare) |
| `/dashboard/~get-choices/` | GET | Lookup ticker & index |

Endpoint-endpoint ini sudah cukup untuk agent yang bisa: cari saham sesuai kriteria bandarmology, tarik data harga/flow harian, analisa rotasi sektor, dan lihat aktivitas broker — tanpa perlu menyentuh endpoint Dash yang rapuh.
