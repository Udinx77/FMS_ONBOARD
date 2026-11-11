# FMS_ONBOARD
FMS ONBOARD (Fleet Monitoring System) adalah sistem pemantauan perangkat jaringan berbasis Python (Flask & SQLite)
yang dirancang untuk memantau status konektivitas jaringan, ketersediaan availability, metrik GPS, serta integrasi antarmuka Caterpillar MineStar pada unit on-board alat berat tambang secara real-time.

## 📌 Fitur Utama

- **Monitoring Jaringan Real-Time**: Polling otomatis (`collect_ping.py`) untuk memantau latensi dan status *online/offline* unit alat berat.
- **Pelacakan Metrik GPS**: Pengumpulan data koordinat dan kualitas sinyal GPS unit (`gps_metric.py`).
- **Integrasi Cat MineStar**: Pembacaan data antarmuka MineStar (`minestar_source.py`) dan alat diagnosa koneksi (`diagnose_minestar_interfaces.py`).
- **Pengolahan & Pemetaan Unit**: Pemetaan otomatis IP dan OID SNMP unit dari berkas Excel (`dataoidip.xlsx`) dengan *caching* lokal (`machine_map_cache.json`).
- **Kalkulasi & Laporan Availability**: Perhitungan persentase *uptime* dan *downtime* unit harian, mingguan, hingga bulanan (`availability.py`, `availability_report.py`).
- **Dashboard Web & REST API**: Antarmuka visual Flask (`app.py`) untuk pemantauan visual serta konsumsi data via REST API.
- **Manajemen & Backup Basis Data**: Penyimpanan log berbasis SQLite (`monitoring.db`) dilengkapi skrip pembuatan cadangan otomatis (`backup_db.py`).

---

## 📁 Struktur Direktori

```text
fmsonb/
│
├── app.py                         # Aplikasi web Flask & penyedia REST API
├── config.py                      # Konfigurasi sistem & parameter aplikasi
├── db.py                          # Manajemen skema dan operasi basis data SQLite
├── monitoring.db                  # File basis data utama SQLite
├── backup_db.py                   # Skrip backup otomatis basis data
│
├── collect_ping.py                # Service polling ICMP ping ke perangkat unit
├── gps_metric.py                  # Service ekstraksi & pemrosesan metrik GPS
│
├── machine_map.py                 # Pengolah pemetaan IP/OID unit dari Excel
├── machine_map_cache.json         # Cache lokal daftar unit & IP
├── dataoidip.xlsx                 # Excel sumber mapping IP & OID perangkat
│
├── availability.py                # Algoritma kalkulasi ketersediaan (uptime/downtime)
├── availability_report.py         # Generator laporan availability
│
├── minestar_source.py             # Modul integrasi Caterpillar MineStar
├── minestar_sources.py            # Daftar sumber data MineStar
└── diagnose_minestar_interfaces.py# Tool diagnosa & validasi interface MineStar
