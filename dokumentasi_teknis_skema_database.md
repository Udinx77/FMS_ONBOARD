# Dokumentasi Teknis & Skema Database (FMSONB)

Dokumen ini berisi spesifikasi teknis tingkat dalam (*deep dive*) mengenai arsitektur perangkat lunak, detail skema basis data SQLite, alur pemrosesan data, serta mekanisme *caching* unit pada proyek **FMSONB** (*Fleet Monitoring System - On-Board*).

---

## 1. Modul Teknis & Alur Eksekusi

Sistem FMSONB dibangun menggunakan pendekatan modular berbasis skrip Python terpisah untuk memisahkan antara *web presentation*, *background workers*, dan *data processing*.

### A. Web Server & Endpoint (`app.py`)
* **Framework**: Flask (WSGI Server / Production via Gunicorn/Waitress).
* **Fungsi**:
  * Melayani antarmuka web dashboard untuk pemantauan operasional.
  * Menyediakan REST API JSON untuk integrasi dengan sistem pihak ketiga.
  * Memanggil modul `availability.py` untuk menghasilkan agregasi data *real-time*.

### B. Worker Pemantauan Jaringan (`collect_ping.py`)
* **Mekanisme**: Threading / Asynchronous ICMP Polling.
* **Alur kerja**:
  1. Membaca daftar unit aktif dari `machine_map_cache.json`.
  2. Melakukan panggilan ICMP ping secara paralel ke IP address tiap unit.
  3. Menghitung *latency* (ms) dan mendeteksi status *Online* (1) / *Offline* (0).
  4. Mencatat hasil pemantauan ke tabel `ping_logs` pada `monitoring.db`.

### C. Worker Telemetri GPS (`gps_metric.py`)
* **Fungsi**: Polling metrik GPS perangkat *on-board* (via SNMP/Socket UDP/HTTP API bergantung pada tipe hardware).
* **Hasil Tangkapan Data**: Koordinat (Latitude, Longitude), Kecepatan (Kmh), Kualitas Sinyal (HDOP/VDOP), dan Jumlah Satelit Terhubung.
* **Penyimpanan**: Menuliskan data ke tabel `gps_metrics` pada `monitoring.db`.

### D. Pengolah Pemetaan Unit (`machine_map.py`)
* **Input**: Berkas Microsoft Excel `dataoidip.xlsx`.
* **Proses**:
  1. Membaca lembar kerja yang berisi daftar ID Alat Berat, IP Address, SNMP OID, serta Lokasi Operasional.
  2. Melakukan validasi format IP address dan ID unit.
  3. Mengekspor data terstruktur ke `machine_map_cache.json` untuk akses I/O cepat oleh *worker*.

---

## 2. Skema Basis Data (`monitoring.db`)

Basis data menggunakan **SQLite 3**. Struktur tabel dirancang terorganisir untuk query agregasi ketersediaan yang efisien.

### 2.1. Tabel `ping_logs`
Menyimpan riwayat pengujian ping konektivitas unit.

| Nama Kolom | Tipe Data | Atribut | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | ID unik log |
| `machine_id` | `TEXT` | `NOT NULL` | Kode/ID unit alat berat (contoh: `DT102`, `EX305`) |
| `ip_address` | `TEXT` | `NOT NULL` | Alamat IP unit perangkat *on-board* |
| `latency_ms` | `REAL` | `NULLABLE` | Waktu respon ping dalam milidetik (null jika timeout) |
| `status` | `INTEGER` | `NOT NULL` | `1` = Online (Reply), `0` = Offline (Timeout/Unreachable) |
| `timestamp` | `DATETIME` | `DEFAULT CURRENT_TIMESTAMP` | Waktu perekaman data |

**Indeks:**
* `idx_ping_machine_time`: `CREATE INDEX idx_ping_machine_time ON ping_logs(machine_id, timestamp);`

---

### 2.2. Tabel `gps_metrics`
Menyimpan riwayat data koordinat dan kualitas sinyal GPS unit.

| Nama Kolom | Tipe Data | Atribut | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | ID unik log GPS |
| `machine_id` | `TEXT` | `NOT NULL` | Kode/ID unit alat berat |
| `latitude` | `REAL` | `NOT NULL` | Koordinat Garis Lintang |
| `longitude` | `REAL` | `NOT NULL` | Koordinat Garis Bujur |
| `speed_kmh` | `REAL` | `DEFAULT 0.0` | Kecepatan unit saat ini |
| `satellites` | `INTEGER` | `DEFAULT 0` | Jumlah satelit aktif yang terhubung |
| `hdop` | `REAL` | `NULLABLE` | Horizontal Dilution of Precision (kualitas sinyal GPS) |
| `timestamp` | `DATETIME` | `DEFAULT CURRENT_TIMESTAMP` | Waktu perekaman data GPS |

**Indeks:**
* `idx_gps_machine_time`: `CREATE INDEX idx_gps_machine_time ON gps_metrics(machine_id, timestamp);`

---

### 2.3. Tabel `availability_summary`
Menyimpan hasil kalkulasi ketersediaan teragregasi per hari/bulan untuk pelaporan cepat tanpa perlu melakukan query ke `ping_logs` jutaan baris.

| Nama Kolom | Tipe Data | Atribut | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | ID unik laporan |
| `machine_id` | `TEXT` | `NOT NULL` | Kode/ID unit alat berat |
| `period_date` | `DATE` | `NOT NULL` | Tanggal periode laporan (`YYYY-MM-DD`) |
| `total_ping_count` | `INTEGER` | `NOT NULL` | Total permintaan ping yang dikirim |
| `successful_pings` | `INTEGER` | `NOT NULL` | Total respon ping sukses (*online*) |
| `uptime_seconds` | `INTEGER` | `NOT NULL` | Estimasi total durasi unit terhubung (detik) |
| `downtime_seconds` | `INTEGER` | `NOT NULL` | Estimasi total durasi unit terputus (detik) |
| `availability_pct` | `REAL` | `NOT NULL` | Persentase *availability* ($A$) |

---

## 3. Formulasi & Algoritma Kalkulasi Availability

Ketersediaan perangkat (*Availability*) dihitung berdasarkan rentang sampel log ICMP ping dalam periode yang ditentukan ($T_{\text{start}}$ hingga $T_{\text{end}}$).

### Formula Matematis:

$$
A = \left( \frac{\sum_{i=1}^{N} S_i}{N} \right) \times 100\%
$$

Dimana:
* $S_i \in \{0, 1\}$ adalah status koneksi pada sampel ke-$i$ ($1$ jika respons received, $0$ jika timeout).
* $N$ adalah total sampel data ping pada rentang waktu terpilih.

Durasi *Uptime* ($T_{\text{up}}$) dan *Downtime* ($T_{\text{down}}$) diestimasi dengan interval polling $I$ (detik):

$$
T_{\text{up}} = \left( \sum_{i=1}^{N} S_i \right) \times I
$$

$$
T_{\text{down}} = \left( N - \sum_{i=1}^{N} S_i \right) \times I
$$

---

## 4. Prosedur Backup & Pemulihan (`backup_db.py`)

1. **Backup Otomatis**:
   * Skrip `backup_db.py` membuat salinan file `monitoring.db` menggunakan SQLite Online Backup API (`sqlite3.backup()`) untuk mencegah kecacatan data (*data corruption*) akibat proses *write* bersamaan.
   * Nama file cadangan mengikuti format: `monitoring_backup_YYYYMMDD_HHMMSS.db`.
2. **Rotasi Log**:
   * Berkas cadangan yang lebih tua dari 30 hari dieliminasi secara otomatis oleh skrip pembersih.