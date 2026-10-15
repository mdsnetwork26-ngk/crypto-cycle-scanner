# Crypto Cycle Scanner

Alat bantu **riset** trading kripto yang berjalan di PC server anda sendiri.
Aplikasi memindai koin yang terdaftar di **Indodax (pair IDR)**, mencocokkan
fase/keadaannya saat ini dengan **perpustakaan episode historis multi-koin**,
lalu melaporkan apa yang *nyata* terjadi pada episode-episode yang mirip itu
**1 bulan (30 hari)** atau **1 tahun (365 hari)** kemudian.

> ⚠️ **Bukan rekomendasi finansial.** Output adalah statistik episode historis
> yang mirip, bukan ramalan harga. Selalu periksa episode analog asalnya di
> halaman detail sebelum mengambil keputusan apa pun.

## Cara kerja (ringkas)

1. **Semesta koin** = pair IDR aktif di Indodax. Stablecoin, token leveraged,
   dan saham tokenisasi disaring keluar; kandidat diurutkan dari volume harian
   terbesar (likuiditas), dengan ambang minimum yang bisa diatur.
2. **Perpustakaan episode**: untuk ±20 koin acuan berumur panjang
   (BTC, ETH, SOL, …), setiap minggu historis direkam keadaannya sebagai
   15 fitur teknikal/fase (drawdown dari puncak, RSI, posisi vs MA-50/200,
   kontraksi volatilitas, tren volume, kekuatan relatif vs BTC, dst.) —
   dihitung **hanya dari data sampai tanggal tersebut** (tanpa kebocoran masa
   depan) — lalu diberi label hasil nyatanya +30/+365 hari.
3. **Pencocokan dua tahap** untuk tiap kandidat hari ini: jarak fitur
   terstandardisasi (robust median/IQR), lalu kemiripan bentuk grafik
   (DTW atas 26 titik mingguan log-harga ternormalisasi).
4. **Laporan potensi** = distribusi hasil nyata para analog terdekat:
   probabilitas naik, skenario P25/median/P75 (dikonversi ke harga IDR),
   penurunan terdalam khas analog, plus daftar episode analognya
   (koin + tanggal + hasil) agar transparan dan bisa dinilai sendiri.
   Solana akhir 2022 tetap ditampilkan sebagai *pembanding favorit* di
   halaman detail, tetapi **bukan** penentu skor.

## Menjalankan lokal (uji)

```bash
cd crypto-cycle-scanner
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env

# mode demo (data sintetis, tanpa internet) untuk melihat tampilan:
DEMO_MODE=1 venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8020

# data Indodax asli:
venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8020
```

Buka `http://localhost:8020`, pilih horizon, klik **Pindai sekarang**.
Pemindaian pertama mengambil candle ±3–10 tahun untuk koin acuan dan ±3 tahun
untuk kandidat (butuh beberapa menit); hasil & candle tersimpan di SQLite
(`data/scanner.db`) sehingga pemindaian berikutnya jauh lebih cepat.

## Deploy di PC server (systemd, pola sama dengan ISP-OPS)

```bash
sudo mkdir -p /opt/crypto-cycle-scanner
sudo cp -r . /opt/crypto-cycle-scanner/
cd /opt/crypto-cycle-scanner
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # sesuaikan PORT/DATABASE_PATH bila perlu
```

`/etc/systemd/system/crypto-scanner.service`:

```ini
[Unit]
Description=Crypto Cycle Scanner
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/crypto-cycle-scanner
EnvironmentFile=/opt/crypto-cycle-scanner/.env
ExecStart=/opt/crypto-cycle-scanner/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8020
Restart=on-failure
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-scanner
```

Lalu jalankan pemindaian berkala (mis. tiap pagi) dengan timer/cron yang
memanggil endpoint, atau cukup klik tombol pindai dari dasbor:

```bash
curl -X POST -d "horizon_days=30&max_coins=40&min_volume_idr=100000000" \
  http://localhost:8020/scan/sync
```

## Tes

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest tests -q   # memakai mode demo, tanpa jaringan
```

## Batasan yang disadari (jujur)

- “Puncak sepanjang masa” dihitung dari data yang diambil (±3 tahun untuk
  kandidat, ±10 tahun untuk acuan BTC) — bukan ATH global sejak lahir koin.
- Jumlah episode acuan bergantung pada umur listing di Indodax; koin yang
  baru listing < ±1 tahun tidak bisa dinilai (riwayat fitur minimal 365 hari).
- Kemiripan fase ≠ kepastian. Kripto sangat berisiko; kerugian bisa total.
  Gunakan ini hanya sebagai salah satu bahan riset.
