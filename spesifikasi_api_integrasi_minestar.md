# Spesifikasi API & Integrasi MineStar (FMSONB)

Dokumen ini menjelaskan endpoint **REST API** yang disediakan oleh aplikasi Flask (`app.py`) serta panduan arsitektur integrasi antarmuka **Caterpillar MineStar** (`minestar_source.py` & `diagnose_minestar_interfaces.py`).

---

## 1. Spesifikasi REST API (`app.py`)

Base URL: `http://<server-ip>:5000/api/v1`

Semua respon format JSON menggunakan struktur standar berikut:
```json
{
  "status": "success | error",
  "data": {},
  "message": "Pesan deskriptif"
}
```

---

### 1.1. Health Check & Status System
* **Endpoint**: `GET /health`
* **Deskripsi**: Memeriksa status operasional server API dan koneksi basis data.
* **Respon (200 OK)**:
```json
{
  "status": "success",
  "data": {
    "app_status": "running",
    "db_connected": true,
    "timestamp": "2026-11-11T20:30:00Z"
  },
  "message": "System operational"
}
```

---

### 1.2. Daftar Unit Alat Berat (Machine List)
* **Endpoint**: `GET /machines`
* **Deskripsi**: Mengambil daftar seluruh unit yang terdaftar dari *cache* pemetaan.
* **Respon (200 OK)**:
```json
{
  "status": "success",
  "data": [
    {
      "machine_id": "DT101",
      "ip_address": "192.168.10.15",
      "snmp_oid": "1.3.6.1.4.1...",
      "unit_type": "Dump Truck"
    },
    {
      "machine_id": "EX202",
      "ip_address": "192.168.10.22",
      "snmp_oid": "1.3.6.1.4.1...",
      "unit_type": "Excavator"
    }
  ],
  "message": "Daftar unit berhasil dimuat"
}
```

---

### 1.3. Status Real-Time Unit Spesifik
* **Endpoint**: `GET /machines/<machine_id>/status`
* **Deskripsi**: Mendapatkan status konektivitas dan metrik GPS terkini untuk unit tertentu.
* **Respon (200 OK)**:
```json
{
  "status": "success",
  "data": {
    "machine_id": "DT101",
    "connection": {
      "status": "ONLINE",
      "latency_ms": 14.2,
      "last_seen": "2026-11-11T20:29:50Z"
    },
    "gps": {
      "latitude": -2.54892,
      "longitude": 115.48912,
      "speed_kmh": 22.5,
      "satellites": 12,
      "hdop": 0.8
    }
  },
  "message": "Status unit ditemukan"
}
```

---

### 1.4. Laporan Availability
* **Endpoint**: `GET /availability/report`
* **Query Parameters**:
  * `start_date` (string, required): Format `YYYY-MM-DD`
  * `end_date` (string, required): Format `YYYY-MM-DD`
  * `machine_id` (string, optional): Filter ID unit tertentu
* **Respon (200 OK)**:
```json
{
  "status": "success",
  "data": {
    "period": {
      "start": "2026-11-01",
      "end": "2026-11-10"
    },
    "summary": [
      {
        "machine_id": "DT101",
        "total_pings": 14400,
        "successful_pings": 13968,
        "availability_pct": 97.00,
        "uptime_hours": 232.8,
        "downtime_hours": 7.2
      }
    ]
  },
  "message": "Laporan availability berhasil digenerasi"
}
```

---

## 2. Integrasi Caterpillar MineStar

Modul integrasi MineStar (`minestar_source.py` & `minestar_sources.py`) bertugas melakukan sinkronisasi status unit FMSONB dengan ekosistem Cat MineStar Fleet/Health.

```
+------------------+         REST / TCP Socket        +----------------------+
| Cat MineStar     | <------------------------------> | FMSONB Integration   |
| System Server    |                                  | (minestar_source.py) |
+------------------+                                  +----------------------+
                                                                 |
                                                                 v
                                                      +----------------------+
                                                      | SQLite Database      |
                                                      | (monitoring.db)      |
                                                      +----------------------+
```

### 2.1. Komponen Integrasi

1. **`minestar_sources.py`**:
   * Menyimpan alamat server, *port*, token/kredensial autentikasi, serta pemetaan nama antarmuka Cat MineStar (Fleet, Health, Terrain).
2. **`minestar_source.py`**:
   * Modul kolektor data yang mengambil status operasional unit (contoh: status *Ready*, *Delay*, *Breakdown*, *PRD*) dari MineStar API dan mencocokkannya dengan status jaringan FMSONB.
3. **`diagnose_minestar_interfaces.py`**:
   * Skrip pengujian diagnostik untuk memverifikasi sambungan antarmuka sebelum mengaktifkan integrasi di lingkungan produksi.

---

### 2.2. Prosedur Uji Diagnostik MineStar

Untuk memastikan koneksi antarmuka MineStar berjalan tanpa kendala, jalankan skrip diagnostik melalui terminal:

```bash
python diagnose_minestar_interfaces.py
```

**Alur Pengujian Skrip Diagnostik:**

1. **Network Reachability Test**: Melakukan ping & tes socket TCP ke server MineStar.
2. **Authentication Verification**: Menguji keabsahan API Key/Token pengaksesan data.
3. **Data Schema Validation**: Memastikan struktur JSON data payload dari MineStar sesuai dengan skema yang dipersyaratkan oleh FMSONB.
4. **Log Reporting**: Menghasilkan log diagnostik dengan status `PASS` atau `FAIL` pada tiap antarmuka yang terkonfigurasi.