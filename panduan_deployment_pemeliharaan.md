# Panduan Deployment & Pemeliharaan Operasional (FMSONB)

Dokumen ini berisi panduan komprehensif mengenai tata cara *deployment* di lingkungan produksi, manajemen layanan latar belakang (*systemd services*), otomatisasi pencadangan (*cron jobs*), rotasi log, serta prosedur pemulihan bencana (*disaster recovery*) pada sistem **FMSONB** (*Fleet Monitoring System - On-Board*).

---

## 1. Arsitektur Deployment Produksi

Di lingkungan produksi, skrip Python tidak dijalankan secara manual dari terminal interaktif. Komponen FMSONB dibagi menjadi tiga layanan utama:

```
                  +-----------------------------------+
                  |      Nginx / Reverse Proxy        |
                  +-----------------------------------+
                                    |
                                    v (Port 5000)
                  +-----------------------------------+
                  |      Gunicorn / Web Server        |
                  |             (app.py)              |
                  +-----------------------------------+
                                    |
     +------------------------------+------------------------------+
     |                              |                              |
     v                              v                              v
+------------------+      +------------------+          +------------------+
| Ping Collector   |      | GPS Collector    |          | SQLite Database  |
| (collect_ping.py)|      | (gps_metric.py)  |          | (monitoring.db)  |
+------------------+      +------------------+          +------------------+
```

---

## 2. Konfigurasi Layanan Systemd (Background Services)

Untuk memastikan aplikasi dan *worker* pemantauan berjalan secara otomatis saat server dinyalakan (*booting*) serta melakukan *auto-restart* jika terjadi kendala/crash, gunakan **Systemd**.

Buat berkas unit service di lokasi `/etc/systemd/system/`:

### 2.1. Layanan Web Dashboard & API (`fmsonb-web.service`)
Buat berkas `/etc/systemd/system/fmsonb-web.service`:

```ini
[Unit]
Description=FMSONB Web Dashboard and API Service
After=network.target

[Service]
User=fmsuser
Group=fmsuser
WorkingDirectory=/opt/fmsonb
Environment="PATH=/opt/fmsonb/venv/bin"
ExecStart=/opt/fmsonb/venv/bin/gunicorn --workers 4 --bind 0.0.0.0:5000 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

### 2.2. Layanan Worker Ping (`fmsonb-ping.service`)
Buat berkas `/etc/systemd/system/fmsonb-ping.service`:

*(Catatan: Layanan pengujian ICMP ping memerlukan kapabilitas raw socket jika tidak dijalankan sebagai root)*.

```ini
[Unit]
Description=FMSONB Connectivity Monitoring Worker (ICMP Ping)
After=network.target fmsonb-web.service

[Service]
User=fmsuser
Group=fmsuser
WorkingDirectory=/opt/fmsonb
Environment="PATH=/opt/fmsonb/venv/bin"
ExecStart=/opt/fmsonb/venv/bin/python collect_ping.py
AmbientCapabilities=CAP_NET_RAW
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

### 2.3. Layanan Worker Telemetri GPS (`fmsonb-gps.service`)
Buat berkas `/etc/systemd/system/fmsonb-gps.service`:

```ini
[Unit]
Description=FMSONB GPS Metrics Polling Worker
After=network.target fmsonb-ping.service

[Service]
User=fmsuser
Group=fmsuser
WorkingDirectory=/opt/fmsonb
Environment="PATH=/opt/fmsonb/venv/bin"
ExecStart=/opt/fmsonb/venv/bin/python gps_metric.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

### 2.4. Aktivasi dan Pengelolaan Layanan

Jalankan perintah berikut untuk mengaktifkan dan memulai seluruh layanan:

```bash
# Reload daemon systemd untuk membaca service baru
sudo systemctl daemon-reload

# Aktifkan service agar otomatis jalan saat boot
sudo systemctl enable fmsonb-web fmsonb-ping fmsonb-gps

# Jalankan seluruh service
sudo systemctl start fmsonb-web fmsonb-ping fmsonb-gps

# Periksa status masing-masing service
sudo systemctl status fmsonb-web
sudo systemctl status fmsonb-ping
sudo systemctl status fmsonb-gps
```

---

## 3. Otomatisasi Task (Cron Jobs)

Beberapa tugas rutin perlu dijalankan secara berkala menggunakan `cron`.

Buka editor crontab untuk pengguna `fmsuser`:
```bash
crontab -e
```

Tambahkan baris berikut:

```cron
# 1. Eksekusi Backup Database harian setiap jam 00:00
0 0 * * * /opt/fmsonb/venv/bin/python /opt/fmsonb/backup_db.py >> /var/log/fmsonb/backup.log 2>&1

# 2. Rekapitulasi Laporan Availability harian setiap jam 01:00
0 1 * * * /opt/fmsonb/venv/bin/python /opt/fmsonb/availability_report.py --daily >> /var/log/fmsonb/reports.log 2>&1

# 3. Pembersihan log cadangan database tua (>30 hari) setiap hari Minggu jam 02:00
0 2 * * 0 find /opt/fmsonb/backups/ -name "*.db" -mtime +30 -exec rm {} \;
```

---

## 4. Rotasi Log & Pemeliharaan Disk (Logrotate)

Untuk mencegah berkas log membengkak dan memenuhi ruang simpan disk (*storage*), konfigurasikan perkakas `logrotate`.

Buat berkas baru `/etc/logrotate.d/fmsonb`:

```text
/var/log/fmsonb/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 fmsuser fmsuser
}
```

---

## 5. Prosedur Diagnostic & Troubleshooting

### 5.1. Memeriksa Integritas Basis Data SQLite
Jika terjadi kegagalan pembacaan database atau mati listrik mendadak, periksa integritas berkas `monitoring.db`:

```bash
sqlite3 /opt/fmsonb/monitoring.db "PRAGMA integrity_check;"
```
*Jika output menampilkan `ok`, basis data dalam keadaan sehat.*

### 5.2. Mengatasi Permasalahan "Database is Locked"
SQLite dapat mengalami keteruncian (*lock*) jika beberapa proses menulis secara bersamaan melebihi batas waktu tunggu (*timeout*).
* **Solusi**: Pastikan WAL mode (*Write-Ahead Logging*) diaktifkan pada modul `db.py`:
  ```sql
  PRAGMA journal_mode=WAL;
  ```

### 5.3. Penanganan Izin Ping (ICMP Socket Issue)
Jika `collect_ping.py` mengeluarkan pesan kesalahan `PermissionError: [Errno 1] Operation not permitted`, berikan izin eksekusi raw socket pada *binary* Python:

```bash
sudo setcap cap_net_raw+ep /opt/fmsonb/venv/bin/python3
```

---

## 6. Prosedur Pemulihan Bencana (Disaster Recovery)

Jika berkas utama `monitoring.db` mengalami kerusakan total (*corrupted*), ikuti langkah pemulihan berikut:

1. **Hentikan Seluruh Layanan FMSONB**:
   ```bash
   sudo systemctl stop fmsonb-web fmsonb-ping fmsonb-gps
   ```

2. **Isolasi Berkas Database Rusak**:
   ```bash
   mv /opt/fmsonb/monitoring.db /opt/fmsonb/monitoring.db.corrupted_$(date +%Y%m%d)
   ```

3. **Restorasi dari Berkas Cadangan Terbaru**:
   ```bash
   # Cari file backup terbaru
   LATEST_BACKUP=$(ls -t /opt/fmsonb/backups/monitoring_backup_*.db | head -n 1)

   # Salin file backup menjadi database aktif
   cp "$LATEST_BACKUP" /opt/fmsonb/monitoring.db
   chown fmsuser:fmsuser /opt/fmsonb/monitoring.db
   ```

4. **Verifikasi dan Jalankan Kembali Layanan**:
   ```bash
   sqlite3 /opt/fmsonb/monitoring.db "PRAGMA integrity_check;"
   sudo systemctl start fmsonb-web fmsonb-ping fmsonb-gps
   ```