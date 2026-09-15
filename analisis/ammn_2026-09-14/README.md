# Analisis akumulasi AMMN — 14 September 2026

Laporan satu kali ini menggabungkan broker summary pada tangkapan layar dengan
OHLCV `AMMN.JK` dari Yahoo Finance. Angka broker ditranskripsikan manual dan
bersatuan **miliar rupiah**.

## Kesimpulan

AMMN menunjukkan **akumulasi berkelanjutan yang berakselerasi**, tetapi pada
14 September sebagian besar pembelian sudah tercermin dalam kenaikan harga.
Sinyalnya kuat sebagai bukti adanya arus beli melalui broker asing terpilih,
bukan bukti identitas pemilik manfaat tertentu.

- Net buy tetap positif pada semua jendela: Rp208,7 miliar (1d), Rp223,8 miliar
  (2d), Rp309,7 miliar (5d), dan Rp396,9 miliar (20d).
- Hari terakhir sendiri menyumbang sekitar **52,6%** net buy 20 hari. Ini
  menunjukkan pembelian terbaru sangat terkonsentrasi, bukan penyerapan yang
  merata sepanjang 20 hari.
- Net buy 5 hari setara **78,0%** dari net buy 20 hari. Akumulasi sudah ada
  sebelumnya, tetapi kecepatannya meningkat tajam pada lima hari terakhir.
- Rasio `bval/sval` naik dari 1,29x (20d) menjadi 1,59x (5d), 2,02x (2d), dan
  2,46x (1d). Tekanan beli makin dominan saat jendela diperpendek.
- Rata-rata beli naik dari Rp4.601,6 (20d) ke Rp4.980,5 (1d). Pembeli bersedia
  mengejar harga lebih tinggi, bukan hanya menunggu di bawah.

## Pembacaan harga

Pada 14 September, AMMN ditutup di Rp5.100, naik **4,94%** dari penutupan
sebelumnya. Volume mencapai 169.146.700 saham, atau **2,71x** rata-rata sesi
sebelumnya sejak 13 Agustus. Sejak 13 Agustus, harga naik **20,85%**.

Harga penutupan berada 5,5% di atas `bavg` lima hari Rp4.836 dan 10,8% di atas
`bavg` 20 hari Rp4.601,6. Artinya kelompok pembeli pada jendela tersebut secara
agregat sudah untung. Ini tetap mendukung momentum, tetapi memperbesar risiko
bahwa berita material berikutnya dipakai untuk distribusi atau ambil untung.

## Selisih antarjendela

Periode broker bertumpang tindih. Selisih berikut hanya pendekatan untuk melihat
kapan net buy terbentuk:

| Potongan waktu | Net buy perkiraan |
| --- | ---: |
| Hari terakhir | Rp208,7 miliar |
| Hari sebelumnya (`2d - 1d`) | Rp15,1 miliar |
| Hari ke-3 sampai ke-5 (`5d - 2d`) | Rp85,9 miliar |
| Hari ke-6 sampai ke-20 (`20d - 5d`) | Rp87,2 miliar |

Pola ini menunjukkan akumulasi lama memang ada, tetapi lonjakan terbesar terjadi
tepat pada hari terakhir. Dengan demikian, label yang paling tepat adalah
**akumulasi berkelanjutan dengan akselerasi satu hari**, bukan akumulasi senyap
yang merata.

## Konfirmasi setelah 14 September

Pertanyaan kunci setelah peristiwa: apakah net buy 1d/2d tetap positif pada
sesi setelah 14 September? Konfirmasi terbaik adalah melihat apakah net buy
1d/2d tetap positif setelah tanggal tersebut.

**Status 15 September (parsial, harga saja):**

- Penutupan tetap Rp5.100 (0,0%). Tidak ada lanjutan kenaikan, tetapi juga
  tidak ada pelepasan agresif.
- Titik terendah sesi Rp4.970 masih **2,77% di atas `bavg` 5 hari Rp4.836**.
  Area biaya rata-rata pembeli 5 hari belum ditembus, jadi tekanan jual belum
  menghapus keuntungan agregat kelompok pembeli.
- Volume 34,9 juta saham = **21% volume peristiwa** dan di bawah rata-rata
  sesi pra-peristiwa. Konsistensi dengan penyerapan, bukan distribusi,
  tetapi sampelnya kecil.

**Net buy 1d/2d setelah 14 September belum bisa dikonfirmasi.** Yahoo hanya
menyediakan OHLCV dan basis data proyek hanya menyimpan tabel `disclosures`.
Volume tidak bisa menjadi pengganti net buy karena penjual dan pembeli lokal
sama-sama menggerakkan volume. Sumber yang sah hanya tabel broker baru dari
aplikasi pemantauan (jendela Today dan 2d yang berakhir minimal 15 September).

**Matriks interpretasi begitu tabel baru tersedia:**

| Net buy Today & 2d setelah 14 Sep | Pembacaan |
| --- | --- |
| Keduanya positif | Akumulasi berlanjut, sinyal peristiwa terkonfirmasi. |
| Today negatif, 2d positif | Ambil untung ringan pada puncak, arus besar belum berbalik. |
| Keduanya negatif, 20d masih positif | Pola awal distribusi; perlakukan peristiwa sebagai penjual berita. |
| AMMN absen dari daftar | Arus beli berhenti, posisi didiamkan; sinyal melemah tanpa pelepasan. |

## Reproduksi

Jalankan dari root proyek:

```sh
.venv/Scripts/python.exe analisis/ammn_2026-09-14/analyze.py
```

Perintah tersebut mengambil ulang harga Yahoo Finance dan menulis
`harga_ammn.csv` serta `hasil.json` ke folder ini.

## Batasan

- Yahoo Finance merupakan sumber sekunder dan datanya dapat dikoreksi.
- Data broker berasal dari screenshot, bukan respons API yang tervalidasi.
- Kode broker tidak mengidentifikasi pemilik manfaat akhir.
- Semua jendela berakhir pada tanggal yang sama dan saling tumpang tindih.
- Laporan ini adalah analisis aliran dan harga, bukan rekomendasi investasi.
