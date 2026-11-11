# FMSONB - Fleet Monitoring System (On-Board)

**FMSONB** adalah sistem pemantauan berbasis Python (Flask & SQLite) yang dirancang khusus untuk mengawasi status konektivitas jaringan, ketersediaan (*availability*), metrik sinyal GPS, serta integrasi antarmuka sistem Caterpillar MineStar pada unit *on-board* alat berat dan kendaraan operasional tambang secara *real-time*.

---

## 📑 Daftar Isi

- [Fitur Utama](#-fitur-utama)
- [Struktur Proyek](#-struktur-proyek)
- [Prasyarat Sistem](#-prasyarat-sistem)
- [Panduan Instalasi](#-panduan-instalasi)
- [Panduan Penggunaan](#-panduan-penggunaan)
- [Formulasi Perhitungan Availability](#-formulasi-perhitungan-availability)
- [Skema Basis Data & Pemetaan Unit](#-skema-basis-data--pemetaan-unit)
- [Konfigurasi Sistem](#-konfigurasi-sistem)
- [Lisensi & Pemeliharaan](#-lisensi--pemeliharaan)

## 📚 Dokumentasi Lanjutan

- [Dokumentasi Teknis & Skema Database](./dokumentasi_teknis_skema_database.md) – Penjelasan detail struktur modul, arsitektur data, dan skema tabel SQLite (`monitoring.db`).
- [Spesifikasi API & Integrasi MineStar](./spesifikasi_api_integrasi_minestar.md) – Detail endpoint REST API (`app.py`), format payload JSON, dan alat diagnosa antarmuka Caterpillar MineStar.

---

## 📌 Fitur Utama

- **Monitoring Jaringan Real-Time**: Service latar belakang (`collect_ping.py`) melakukan polling ICMP ping secara berkala untuk memantau latensi, *packet loss*, dan status *online/offline* tiap unit.
- **Ekstraksi Metrik GPS**: Mengumpulkan data posisi koordinat, kecepatan, dan kualitas sinyal GPS dari perangkat *on-board* (`gps_metric.py`).
- **Integrasi Cat MineStar**: Modul pembaca antarmuka data MineStar (`minestar_source.py`) dan alat uji diagnostik antarmuka (`diagnose_minestar_interfaces.py`).
- **Pemetaan & Caching Unit**: Pengolah otomatis data pemetaan IP & OID SNMP dari berkas Excel (`dataoidip.xlsx`) yang disimpan dalam format *cache* JSON lokal (`machine_map_cache.json`).
- **Perhitungan Availability & Laporan**: Algoritma kalkulasi ketersediaan perangkat (*uptime* vs *downtime*) harian, mingguan, dan bulanan (`availability.py`, `availability_report.py`).
- **Dashboard Web & REST API**: Dashboard visual berbasis Flask (`app.py`) untuk pemantauan operasional serta penyediaan *endpoint* JSON REST API.
- **Manajemen Database & Cadangan**: Pengelolaan log berbasis SQLite (`monitoring.db`) dilengkapi skrip pembuatan cadangan otomatis (`backup_db.py`).

---

## 📁 Struktur Proyek

```text
fmsonb/
│
├── app.py                         # Server web Flask dan penyedia REST API
├── config.py                      # Konfigurasi parameter & konstanta sistem
├── db.py                          # Modul manajemen skema & query SQLite
├── monitoring.db                  # File basis data utama SQLite
├── backup_db.py                   # Skrip backup otomatis basis data
│
├── collect_ping.py                # Service pemantauan konektivitas (ICMP ping)
├── gps_metric.py                  # Service ekstraksi & pemrosesan metrik GPS
│
├── machine_map.py                 # Pengolah pemetaan IP/OID unit dari berkas Excel
├── machine_map_cache.json         # Berkas cache lokal pemetaan unit
├── dataoidip.xlsx                 # Sumber data Excel untuk IP & OID perangkat
│
├── availability.py                # Algoritma perhitungan ketersediaan unit
├── availability_report.py         # Generator laporan ketersediaan perangkat
│
├── minestar_source.py             # Modul integrasi Caterpillar MineStar
├── minestar_sources.py            # Konfigurasi sumber data MineStar
└── diagnose_minestar_interfaces.py# Utilitas tes koneksi & validasi antarmuka MineStar
```

---

## 🛠️ Prasyarat Sistem

Sebelum menjalankan aplikasi, pastikan sistem Anda memenuhi spesifikasi berikut:

* **Sistem Operasi**: Linux (Ubuntu 20.04+ / Debian 11+ direkomendasikan) atau Windows 10/11 / Windows Server
* **Python**: Versi 3.8 atau lebih baru
* **Database**: SQLite 3.x
* **Library Python Utama**:
  * `Flask` (Web framework)
  * `pandas` & `openpyxl` (Pengolahan data Excel)
  * `requests` (HTTP requests)

---

## 🚀 Panduan Instalasi

### 1. Clone Repositori
```bash
git clone https://github.com/username/fmsonb.git
cd fmsonb
```

### 2. Buat Virtual Environment
```bash
# Membuat virtual environment
python -m venv venv

# Aktivasi virtual environment:
# Pada Linux / macOS:
source venv/bin/activate

# Pada Windows (Command Prompt):
venv\Scripts\activate.bat

# Pada Windows (PowerShell):
venv\Scripts\Activate.ps1
```

### 3. Install Dependensi
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*(Catatan: Jika berkas `requirements.txt` belum ada, instal dependensi manual via `pip install Flask pandas openpyxl requests`)*

### 4. Persiapan Berkas Pemetaan
Pastikan berkas `dataoidip.xlsx` berada di direktori akar proyek. Berkas ini berisi daftar alat berat, IP Address, dan OID SNMP perangkat *on-board*. Berkas `machine_map_cache.json` akan dibuat secara otomatis ketika sistem dijalankan pertama kali.

---

## 💻 Panduan Penggunaan

### 1. Menjalankan Dashboard Web
Jalankan aplikasi Flask untuk membuka interface web dashboard:
```bash
python app.py
```
Aplikasi secara bawaan dapat diakses pada peramban web di `http://localhost:5000`.

### 2. Menjalankan Service Monitoring (Background Tasks)
Jalankan skrip polling secara berkala. Pada lingkungan produksi, disarankan menggunakan *process manager* seperti Systemd atau Supervisor.

**Service Monitoring Jaringan (Ping):**
```bash
python collect_ping.py
```

**Service Monitoring Sinyal GPS:**
```bash
python gps_metric.py
```

### 3. Menjalankan Diagnosa Antarmuka MineStar
Gunakan skrip pengujian untuk memverifikasi koneksi dan data dari Cat MineStar:
```bash
python diagnose_minestar_interfaces.py
```

### 4. Menjalankan Backup Database Manual
Untuk melakukan pencadangan file `monitoring.db`:
```bash
python backup_db.py
```

---

## 📊 Formulasi Perhitungan Availability

Tingkat ketersediaan koneksi perangkat *on-board* dihitung menggunakan persentase rasio antara jumlah respons ping yang diterima dengan total permintaan ping yang seharusnya dikirim dalam rentang waktu tertentu.

Persamaan matematis perhitungan ketersediaan ($A$):

$$A = \left( \frac{N_{\text{success}}}{N_{\text{total}}} \right) \times 100\%$$

Keterangan:
* $A$: Persentase *Availability* (%)
* $N_{\text{success}}$: Jumlah respons ping sukses (unit status *online*)
* $N_{\text{total}}$: Total akumulasi percobaan ping yang dijadwalkan dalam periode pengamatan

---

## 🗄️ Skema Basis Data & Pemetaan Unit

### Ringkasan Tabel `monitoring.db`
1. **`ping_logs`**: Menyimpan timestamp, IP unit, latensi (ms), dan status koneksi (1 = Online, 0 = Offline).
2. **`gps_metrics`**: Menyimpan timestamp, ID unit, koordinat latitude/longitude, kecepatan, serta indikator kualitas sinyal GPS.
3. **`availability_summary`**: Rekapitulasi persentase *uptime*, *downtime*, dan total durasi terhubung harian/bulanan per unit.

### Alur Pemetaan Data Unit
```text
[dataoidip.xlsx] ──> [machine_map.py] ──> [machine_map_cache.json] ──> [Service Monitoring]
```

---

## ⚙️ Konfigurasi Sistem

Pengaturan utama aplikasi tersimpan dalam berkas `config.py`. Anda dapat menyesuaikan parameter berikut:

* `PING_INTERVAL`: Interval polling pengujian jaringan (dalam detik).
* `DATABASE_PATH`: Lokasi file database SQLite (`monitoring.db`).
* `BACKUP_DIR`: Direktori tujuan penyimpan berkas cadangan database.
* `MINESTAR_API_URL`: URL endpoint antarmuka Cat MineStar.

---

## 🛡️ Lisensi & Pemeliharaan

Hak cipta dan pemeliharaan proyek ini berada di bawah pengawasan tim IT Operations & Fleet Management System. Untuk bantuan atau pelaporan kendala teknis, silakan hubungi tim administrator sistem.
