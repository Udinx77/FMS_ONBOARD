# Network Availability Monitoring – Integrated (V15)

> **Paket pembaruan:** ZIP revisi tidak menyertakan `.env`, `monitoring.db`, `collector.lock`, dan `machine_map_cache.json`. Saat mengekstrak ke folder project yang sudah ada, konfigurasi dan histori lokal tetap dipertahankan. **`dataoidip.xlsx` tetap diperlukan untuk IP GPS** (kolom `OID`/`Name` dan `GPS` atau `G`; IP GPS tidak ada di database). Mapping Radio, Display, dan PLE diambil langsung dari SQL Server.

Sistem memantau ping dan availability perangkat onboard (Radio, Display, PLE, GPS) per unit: Flask + Socket.IO untuk dashboard, SQL Server untuk data mesin dan mapping IP, Excel untuk IP GPS, asynchronous ICMP collector, dan SQLite untuk histori.

## 1. Arsitektur

```
SQL Server ──query_machine_ips.sql──► mapping unit (OID, nama, IP R/D/P) ─┐
dataoidip.xlsx ──kolom GPS──► IP GPS (digabung per OID) ─────────────────┴► collect_ping.py ──► monitoring.db (SQLite)
SQL Server ──query delay/GPS (app.py)────────────────────────────────────────────────────► app.py ──► dashboard (Socket.IO)
SQL Server ──query_gps.sql (tiap menit: PositionReport terakhir per unit; MACHINE_OID, TRUCK, SATELLITES)──► app.py ──► lampu G dan filter GPS
                                                                                      ▲ status + availability dibaca dari monitoring.db
```

- **Mapping unit** (OID, nama unit, IP Radio/Display/PLE): query `query_machine_ips.sql`, dijalankan oleh `app.py` dan `collect_ping.py`, diperbarui otomatis. **IP GPS** dibaca dari `dataoidip.xlsx` lalu digabung ke hasil query.
- **Data mesin** (delay, kategori delay, GPS): query di `app.py`, dijalankan tiap `SQL_REFRESH_SECONDS`.
- **Satelit GPS** (PositionReport terakhir tiap unit dari `mshist_rep.dbo.PRODUCTION_EVENT`; kolom `MACHINE_OID`, `TRUCK`, `SATELLITES`): query `query_gps.sql`, dijalankan `app.py` tiap `GPS_QUERY_REFRESH_MINUTES` (bawaan 1 menit) di thread terpisah. Query mengembalikan **semua unit MineStar**; unit tanpa PositionReport pada jendela waktu (`GPS_QUERY_WINDOW_MINUTES`, bawaan 1 menit) tetap tampil dengan `SATELLITES` NULL, sehingga lampu G merah.
- `collect_ping.py`: proses terpisah yang mem-ping semua IP unik dan menulis ke `monitoring.db`.
- `monitoring.db`: histori ping, status terakhir, periode downtime, dan ringkasan availability harian.
- `app.py`: menggabungkan data SQL Server dengan status/availability SQLite lalu mengirim event Socket.IO.

**Penting:** `app.py` tidak melakukan ping. Jalankan `collect_ping.py` dan `app.py` pada dua terminal terpisah.

## 2. Isi folder

- `app.py` – dashboard Flask-SocketIO, query data mesin, pengamanan akses, export.
- `collect_ping.py` – asynchronous ping, canary, retensi histori.
- `machine_map.py` – sumber mapping unit (SQL utama + IP GPS dari Excel, Excel penuh sebagai cadangan), cache, auto-refresh.
- `gps_metric.py` – membaca query GPS, aturan warna lampu G, cache. Cek hasil query: `python gps_metric.py`.
- `query_gps.sql` – query GPS dari user (isi tidak diubah; dua tanggal tetap diganti token `__START_WITA__` dan `__END_WITA__` yang diisi sistem). Bisa diganti kapan saja bila sumber GPS berubah.
- `monthly_report.py` – laporan availability bulanan (Excel).
- `run.bat` – menjalankan seluruh sistem dengan satu klik (Windows).
- `query_machine_ips.sql` – query mapping unit. Boleh diedit tanpa mengubah Python.
- `sql_server.py` – koneksi SQL Server bersama.
- `db.py` – skema dan transaksi SQLite.
- `availability.py`, `availability_report.py` – laporan availability (CLI, halaman, PDF, Excel).
- `backup_db.py` – backup `monitoring.db` saat sistem berjalan.
- `config.py`, `.env.example` – konfigurasi.
- `templates/`, `static/` – tampilan dan font (Inter, JetBrains Mono; dibundel agar tampil sama tanpa internet).
- `requirements.txt` (rentang versi), `requirements.lock.txt` (versi persis yang lulus tes).
- `test_*.py` – pengujian cepat (lihat bagian 12).

## 3. Persiapan

Disarankan Python 3.10 atau lebih baru.

1. Ekstrak project ke folder kerja.
2. Salin `.env.example` menjadi `.env`, isi `SQL_DATABASE`, `SQL_USERNAME`, `SQL_PASSWORD`. Server default `10.4.17.13`. Jangan membagikan `.env`.
3. Instal Microsoft ODBC Driver 17 for SQL Server jika belum ada. Disarankan akun SQL **read-only**.
4. Instal dependensi:

   ```powershell
   python -m pip install -r requirements.txt
   ```

   Untuk versi persis yang diuji: `python -m pip install -r requirements.lock.txt` (lalu pasang `pyodbc`).

Jika memakai Windows Authentication, set `SQL_TRUSTED_CONNECTION=true`.

## 4. Menjalankan sistem

**Satu klik (Windows):** klik dua kali `run.bat`. Skrip ini mencari Python, memeriksa `.env`, memasang dependensi bila belum ada, membuka jendela **FMS Collector Ping** dan **FMS Dashboard**, lalu membuka browser di `http://localhost:<FLASK_PORT>/` (port dibaca dari `.env`, bawaan 5001). Untuk menghentikan, tutup kedua jendela itu.

**Manual:** buka **dua terminal** pada folder project.

```powershell
python collect_ping.py     # terminal 1
python app.py              # terminal 2
```

Buka `http://localhost:5001` (atau alamat server). Saat start pertama, jalankan collector lebih dulu agar migrasi database tidak berbarengan.

Backup harian (jadwalkan di Windows Task Scheduler):

```powershell
python backup_db.py
```

Hasil di folder `backups/`, hanya `BACKUP_KEEP` (default 14) file terbaru yang disimpan.

## 5. Sumber mapping unit (SQL Server)

`query_machine_ips.sql` harus menghasilkan kolom:

| Kolom | Wajib | Keterangan |
|---|---|---|
| `OID` | ya | OID mesin (`parentOID`). Ditambahkan ke query awal karena dashboard mencocokkan data delay berdasarkan OID. |
| `SN Unit` | ya | Nama unit, mis. CB6122. |
| `IP WGB Radio` | ya | Tipe **R** (Radio). Dihitung query dari IP Display dikurangi 1 pada oktet terakhir. |
| `IP Display` | ya | Tipe **D**. |
| `IP PLE` | ya | Tipe **P**. |
| `IP GPS` | tidak | Tipe **G**. Query bawaan tidak menghasilkannya (IP GPS tidak ada di database); IP GPS diambil dari Excel (lihat di bawah). Jika query kelak punya kolom ini, nilainya diutamakan daripada Excel. |

Perilaku:

- **Auto-refresh:** query diulang tiap `MACHINE_REFRESH_MINUTES` (default 10). Unit baru, unit hilang, atau IP berubah terbaca tanpa restart (dashboard dan collector masing-masing).
- **Gagal query:** data lama tetap dipakai dan log menampilkan `[SQL] Muat ulang mapping gagal`.
- **Perlindungan:** hasil baru yang menyusut lebih dari separuh jumlah unit ditolak (kemungkinan query atau server bermasalah). Restart bila penyusutan itu memang benar.
- **Cache:** hasil query terakhir yang berhasil disimpan ke `machine_map_cache.json`. Jika SQL Server tidak terjangkau saat start, cache dipakai (log: `Memakai cache berumur ...`). Tanpa cache dan tanpa SQL Server, sistem berhenti dengan pesan jelas.
- **IP ganda:** IP yang dipakai lebih dari satu unit dicetak sebagai `PERINGATAN` di log.
- **Cakupan unit:** query mengembalikan semua mesin yang punya interface `Assignment` dan `Health`. Periksa jumlah unit saat start; tambahkan filter di SQL bila ada mesin non-FMS atau yang sudah tidak aktif.
- **IP GPS dari Excel:** `dataoidip.xlsx` (satu folder dengan project, atau `GPS_EXCEL_FILE`) dibaca untuk kolom `GPS` (atau `G`) dan dicocokkan ke unit hasil query berdasarkan `OID`; jika OID tidak cocok, dicocokkan berdasarkan `Name`/`SN Unit` (huruf besar-kecil diabaikan). Kolom `R`, `D`, `P` di Excel tidak dipakai pada mode ini. Unit tanpa IP GPS tidak dipantau GPS-nya (lampu G tidak muncul). Excel dibaca ulang otomatis saat file berubah, tanpa query SQL dan tanpa restart. Jika file tidak ada atau rusak, sistem tetap berjalan: GPS belum dipantau atau data GPS terakhir dipakai (log `[GPS] ...`). Log saat start: `[GPS] N IP GPS dibaca ...; M cocok dengan unit hasil query` — jika M lebih kecil dari N, ada baris Excel yang OID dan namanya tidak ada di hasil query.
- **Cadangan Excel:** `MACHINE_SOURCE=excel` memakai `dataoidip.xlsx` seperti versi lama (kolom `OID`, `Name`, `R`, `D`, `P`, `GPS` opsional; dibaca ulang saat file berubah).

## 6. Parameter utama

Ubah lewat `.env` (contoh lengkap di `.env.example`):

```ini
MACHINE_SOURCE=sql
MACHINE_REFRESH_MINUTES=10
GPS_SOURCE=excel
GPS_EXCEL_FILE=
SCAN_INTERVAL_SECONDS=60
PING_COUNT=5
PING_INTERVAL_SECONDS=0.2
PING_TIMEOUT_SECONDS=1
CONCURRENT_TASKS=75
FAILURE_LOSS_THRESHOLD_PERCENT=40
RED_AFTER_MINUTES=15
MAX_OBSERVATION_GAP_SECONDS=180
PING_HISTORY_RETENTION_DAYS=30
BREAK_START=12:00
BREAK_END=13:00
CANARY_IPS=
SQL_REFRESH_SECONDS=10
TIMEZONE_NAME=Asia/Makassar
GPS_METRIC_COLUMN=Satellites
GPS_METRIC_AGGREGATE=last
GPS_GREEN_MIN=15
GPS_YELLOW_MIN=13
GPS_ABSENT_IS_RED=false
GPS_QUERY_REFRESH_MINUTES=1
GPS_QUERY_WINDOW_MINUTES=1
AVAILABILITY_PURGE=true
AVAILABILITY_KEEP_MONTHS=2
```

- `GPS_SOURCE=excel` membaca IP GPS dari Excel; `none` menonaktifkan pemantauan GPS. `GPS_EXCEL_FILE` kosong = `dataoidip.xlsx` di folder project.
- **`TIMEZONE_NAME`:** bawaan sekarang `Asia/Makassar` (WITA, zona waktu site ADT). Batas hari, jam istirahat 12:00-13:00, dan bulan laporan mengikuti zona ini. Jika `.env` lama masih berisi `Asia/Jakarta`, ubah ke `Asia/Makassar`.
- **GPS satelit:** `GPS_METRIC_COLUMN` nama kolom jumlah satelit pada hasil `query_gps.sql`; `GPS_METRIC_AGGREGATE=last` memakai baris terbaru per unit (`avg`, `min`, `max`, `sum` untuk menggabung beberapa baris); `GPS_GREEN_MIN` dan `GPS_YELLOW_MIN` ambang warna; `GPS_ABSENT_IS_RED=true` membuat unit yang tidak muncul di hasil query berwarna merah (bawaan false: ikut ping GPS). `GPS_QUERY_WINDOW_MINUTES` adalah jendela data PositionReport yang dilihat query (bawaan 1 menit): unit yang tidak mengirim laporan posisi selama jendela itu dianggap tanpa data satelit (merah). Perbesar bila laporan posisi jarang.
- **Retensi bulanan:** `AVAILABILITY_PURGE=true` menghapus data availability bulan yang **sudah diekspor** dan lebih tua dari `AVAILABILITY_KEEP_MONTHS` bulan terakhir (bawaan 2). Bulan yang belum diekspor tidak pernah dihapus.
- `SCAN_INTERVAL_SECONDS` adalah jeda setelah scan **selesai** sampai scan berikutnya mulai. Jarak antar sampel = durasi scan + jeda, dan harus lebih kecil dari `MAX_OBSERVATION_GAP_SECONDS`.
- `CONCURRENT_TASKS=75` membatasi ping yang berjalan bersamaan. Durasi scan naik kira-kira linear dengan jumlah IP (±0,05 detik per IP pada 75 tugas); untuk lebih dari ±800 IP uji durasi scan dan naikkan bila perlu.
- Disarankan `PING_HISTORY_RETENTION_DAYS=7`: laporan memakai ringkasan harian, bukan histori mentah, dan 1.104 perangkat menghasilkan ratusan MB per hari.

## 7. Logika availability

- Satu siklus memeriksa setiap IP unik satu kali dengan 5 paket. IP yang sama pada beberapa mapping hanya diping sekali.
- Packet loss `<= 40%` dianggap available; `> 40%` gangguan.
- `Green`: loss 0%. `Yellow`: loss di atas 0% sampai 40%. `Orange`: loss di atas 40% dan gangguan belum 15 menit. `Red`: gangguan minimal 15 menit.
- Downtime event dimulai saat gangguan pertama terdeteksi; warna merah hanya eskalasi alarm.
- Interval antara dua pemeriksaan berurutan memakai status pemeriksaan sebelumnya. Jeda lebih dari 180 detik dianggap tidak terpantau (bukan uptime maupun downtime).
- `Availability = uptime / observed time x 100%`. `Coverage = observed time / durasi periode x 100%`. Baca bersama; coverage rendah berarti pengamatan belum lengkap.
- **Jam istirahat** 12:00-13:00 (zona waktu lokal) tidak dihitung untuk availability maupun coverage. Ping, warna status, dan riwayat downtime tetap berjalan. Hanya scan baru yang mengikuti aturan ini.
- **Availability overall** (dashboard, laporan, PDF, Excel) dan availability gabungan per unit hanya menghitung **Radio + Display + PLE**. GPS tetap dipantau dan tampil sebagai rincian tersendiri.
- Unit **Breakdown (BD)** tidak masuk availability overall. BD ditentukan dari `DELAYCATEGORY` 165096025/165096022 atau `DELAYCLASS_OID` 165097066/165097069, memakai status saat laporan dibuat (berlaku seharian).
- Sampel pertama hanya menetapkan titik awal; akumulasi dimulai setelah sampel berikutnya.

## 8. Dashboard

- **Kategori:** Breakdown ditentukan dari delay SQL Server. Need Check dipicu status Red dan Monitoring dipicu Orange pada Radio, Display, atau PLE. Status GPS saja tidak mengubah kategori dan tidak memunculkan badge RTO.
- **Urutan** di Need Check, Monitoring, dan Breakdown: unit **loader** (kode CB, HB, KB) dulu, lalu RTO terlama; sisanya (hauler) menyusul. Kategori Normal diurutkan menurut kode dan nama.
- **Filter unit:** `Semua`, kode unit (CB, HB, KB, CT, KT), lalu **GPS normal** dan **GPS error** di sebelahnya. **GPS normal** = lampu G hijau (satelit 15 ke atas; tanpa data satelit: ping GPS Green atau Yellow). **GPS error** = lampu G merah (satelit NULL atau kurang dari 13; tanpa data satelit: ping GPS Red). Kuning (satelit 13-14) bukan error dan bukan normal. Kedua filter saling meniadakan, bisa digabung dengan filter kode dan kolom pencarian, dan `Semua` membersihkan seluruh filter. Tombolnya tidak muncul bila tidak ada unit yang bisa dinilai.
- **Motion:** gerak tenang dan hanya saat ada kejadian: angka tile dan availability keseluruhan berjalan halus ke nilai baru (bump pada tile yang berubah, denyut merah saat Need Check bertambah); lampu yang berubah warna berdenyut (pop); unit yang baru masuk Need Check diberi cincin merah dua kali; titik di baris status berdenyut tiap data baru; kartu masuk berurutan saat halaman dibuka dan saat filter diganti; kartu terangkat saat disorot dan menekan saat diklik; availability tumbuh dan menutup halus saat kartu diklik. Hanya `transform` dan `opacity`; tidak ada animasi yang berputar terus-menerus. Jika sistem operasi mengaktifkan *reduce motion*, semua gerak dimatikan.
- **Tile kategori** menampilkan jumlah unit dan rinciannya, mis. `Loader 51: 18 CB, 16 HB, 17 KB` dan `Hauler 40: 17 CT, 23 KT`.
- **Kartu unit ringkas:** kartu hanya memuat nama, badge, dan lampu OP, HS, R, D, P, G. **Klik kartu** (atau Enter/Spasi) untuk menampilkan availability Radio, Display, PLE, dan GPS; klik lagi untuk menutup. Kartu yang dibuka menutup sendiri setelah 60 detik. Header dan tile tidak berubah.
- **Lampu G:** jika data satelit tersedia: **NULL atau kurang dari 13 = merah, 13-14 = kuning, 15 ke atas = hijau**. Tooltip lampu menampilkan jumlah satelit. Unit yang tidak ada di hasil query memakai warna ping GPS seperti sebelumnya.
- **Layar besar:** ukuran teks mengikuti lebar layar (22 px pada 1920 px, dirancang untuk monitor 43 inci dari jarak 2 m). Ubah lewat URL, mis. `http://IP:5001/?size=1.3` (nilai 0.8 sampai 2.5). Zoom browser tidak mengubah ukuran.
- **Banner peringatan** muncul jika scan terakhir gagal atau dibatalkan canary, atau hasil ping lebih lama dari 3 menit (collector kemungkinan mati).
- Kartu yang statusnya baru berubah diberi sorotan singkat.

## 9. Pengamanan dan keandalan

Semua opsional di `.env`; kosong berarti perilaku lama (terbuka).

- `ALLOWED_NETWORKS` (CIDR dipisah koma, mis. `10.4.0.0/16,127.0.0.1`) membatasi alamat yang boleh membuka dashboard, laporan, export, dan Socket.IO.
- `DASHBOARD_PASSWORD` (dan `DASHBOARD_USER`) mengaktifkan login Basic. Belum ada HTTPS, jadi password terkirim polos di jaringan; pasangkan dengan `ALLOWED_NETWORKS`.
- Socket.IO hanya menerima origin yang sama dengan server. Isi `CORS_ALLOWED_ORIGINS` bila diakses lewat domain atau proxy lain.
- `FLASK_SECRET_KEY` kosong -> dibuat acak tiap start.
- **Canary:** isi `CANARY_IPS` dengan IP yang pasti hidup (mis. gateway PC monitor). Jika semua canary 100% loss, scan dibatalkan dan dicatat `FAILED` dengan pesan `CANARY`, sehingga status dan availability tidak berubah dan tidak muncul downtime palsu saat jaringan PC monitor sendiri bermasalah.
- **Export** PDF/Excel dilayani dari cache 30 detik dan dibuat satu per satu, sehingga klik berulang tidak membebani CPU.
- Export Excel: nama unit berawalan `=` menjadi teks (bukan formula); PDF: tanda `<` `&` di nama unit di-escape.
- **Koneksi SQL:** disarankan `SQL_ENCRYPT=yes` dan `SQL_TRUST_SERVER_CERTIFICATE=no` setelah sertifikat SQL Server valid (uji koneksi dulu). Nilai bawaan mempertahankan perilaku lama.

## 10. Laporan availability

```powershell
python availability.py
python availability.py --date 2026-11-03
python availability.py --device-type R
python availability.py --oid 123456789
```

Halaman `/report/sqlite`, `/export/availability.pdf`, dan `/export/availability.xlsx` memuat rincian per unit. Endpoint tambahan: `/api/status/latest`, `/api/availability/today`, `/api/availability/summary`, dan `/health`.

**Export bulanan ONB:** di halaman `/report/sqlite` ada kotak *Export bulanan ONB*: pilih bulan lalu klik **Export Excel bulanan** (`/export/availability_monthly.xlsx?month=YYYY-MM`). File berisi sheet Ringkasan, Per Unit, dan Harian; overall hanya Radio + Display + PLE, GPS terpisah, jam istirahat tidak dihitung, dan unit Breakdown dikecualikan **per hari** (dashboard mencatat unit BD tiap hari; bulan sebelum V12 tidak punya catatan BD). Mengekspor **bulan yang sudah lewat** menandainya sebagai sudah diekspor; mengekspor bulan berjalan tidak. Collector menghapus otomatis (sekali sehari) data availability, catatan BD, dan downtime yang sudah selesai pada bulan yang sudah diekspor dan lebih tua dari `AVAILABILITY_KEEP_MONTHS` bulan terakhir. Halaman laporan menampilkan bulan yang belum diekspor.

## 11. Event Socket.IO

- `database_data`: baris mesin dari SQL Server digabung status/availability SQLite. Field lama (`MACHINE_OID`, `NAME`, `DELAYCATEGORY`, `DELAYCLASS_OID`, `GPSStatus`, `R_status`, `D_status`, `P_status`, `*_fail_mins`) dipertahankan. Tiap tipe `R|D|P|G` menyediakan `_ip`, `_status`, `_packet_loss`, `_packets_sent`, `_packets_received`, `_avg_rtt`, `_last_check`, `_availability`, `_coverage`, `_uptime_seconds`, `_downtime_seconds`.
- `availability_overall`: ringkasan overall, jumlah unit BD yang dikecualikan, dan jam istirahat.
- `collector_status`: scan terakhir (`status`, `finished_at`, `error`) untuk banner peringatan.
- `database_error`: pesan jika query SQL Server gagal.

## 12. Pengujian

```powershell
python test_machine_map.py
python test_sql_source.py
python test_gps_excel.py
python test_availability_rules.py
python test_overall_availability_rules.py
python test_hardening.py
python test_canary.py
python test_backup.py
python -m py_compile app.py collect_ping.py db.py machine_map.py sql_server.py availability.py availability_report.py config.py backup_db.py
```

SQL Server asli tidak diperlukan; sumber mapping SQL diuji dengan koneksi tiruan.
Tes V12: `python test_gps_metric.py`, `python test_monthly.py`, `python test_monthly_app.py`.

## 13. Catatan operasional

- Jalankan hanya satu `collect_ping.py` (ada lock file).
- Database SQLite harus di disk lokal, bukan network share. WAL aktif agar dashboard dapat membaca saat collector menulis.
- Collector menghapus histori ping mentah yang lebih tua dari `PING_HISTORY_RETENTION_DAYS` saat start dan tiap 24 jam; `availability_daily` menyimpan ringkasan sehingga laporan lama tetap ada. `0` menonaktifkan penghapusan.
- Jika ICMP membutuhkan hak Administrator, jalankan terminal collector dengan hak yang sesuai; collector mencoba mode unprivileged jika privileged gagal.
- Jika SQL Server tidak dapat diakses, dashboard tetap hidup tetapi data SQL tidak diperbarui. Periksa `.env`, jaringan, nama database, kredensial, dan ODBC Driver.
- Simpan project di git (`.gitignore` sudah mengecualikan `.env`, database, cache, dan backup) agar riwayat perubahan terlacak.

## 14. Belum dikerjakan

- HTTPS dan server produksi (saat ini server pengembangan Werkzeug).
- Mengganti password SQL yang pernah tersimpan di `.env` yang dibagikan.
- Status perangkat basi ditandai abu-abu saat collector mati (saat ini baru banner).
- Cek `python gps_metric.py` pada mesin dengan akses SQL Server asli: kolom hasil query, jumlah unit, dan sebaran warna lampu G. Sumber IP GPS (ping) masih Excel karena database tidak menyimpannya.

## 15. Riwayat singkat

- **V7:** overall R+D+P, GPS tidak memicu Need Check/Monitoring.
- **V9:** tampilan layar besar, urutan loader, rincian tile, pengamanan akses, export aman.
- **V10:** mapping unit dari SQL Server (auto-refresh + cache), canary dan banner collector, cache export, backup, batas versi dependency.
- **V11:** IP GPS dari Excel digabung ke hasil query SQL (per OID, cadangan per nama; hot-reload), filter GPS di dashboard, `run.bat` untuk menjalankan sistem dengan satu klik.
- **V12:** kartu unit ringkas (availability tampil saat diklik), lampu G dari jumlah satelit (NULL/<13 merah, 13-14 kuning, 15+ hijau, data per menit), filter GPS memakai aturan itu, export availability bulanan (Excel) dengan penghapusan otomatis bulan yang sudah diekspor, zona waktu bawaan WITA.
- **V15:** filter **GPS error** (lampu G merah) di samping **GPS normal**; micro-interaction/motion dashboard (lihat bagian Dashboard).
- **V14:** jendela PositionReport 1 menit dan satelit dikumpulkan tiap menit (data basi lebih dari 3 menit tidak dipakai); nama unit di kartu lebih besar; angka availability keseluruhan memakai font nama unit (Inter); saturasi warna indikator diturunkan (merah paling banyak).
- **V13:** query GPS revisi dari user dipasang di `query_gps.sql` (PositionReport terakhir per unit, semua unit MineStar); jendela data PositionReport 1 menit dan data satelit dikumpulkan tiap menit (`GPS_QUERY_WINDOW_MINUTES`, `GPS_QUERY_REFRESH_MINUTES`); data satelit yang lebih tua dari 3 menit tanpa refresh berhasil tidak dipakai (lampu G kembali ke ping GPS). Indikator lebih lembut (saturasi diturunkan, merah paling banyak), nama unit di kartu lebih besar, dan angka availability keseluruhan memakai font nama unit.
