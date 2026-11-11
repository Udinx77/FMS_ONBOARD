"""Konfigurasi aplikasi monitoring jaringan."""
from datetime import time as _time
from pathlib import Path
import os
import secrets

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    # Variabel environment sistem tetap dapat digunakan tanpa python-dotenv.
    pass

EXCEL_FILE = Path(os.getenv("EXCEL_FILE", str(BASE_DIR / "dataoidip.xlsx")))
DB_FILE = Path(os.getenv("DB_FILE", str(BASE_DIR / "monitoring.db")))

# Semua tipe perangkat yang dipantau dari Excel. G (GPS) bersifat opsional.
DEVICE_TYPES = ("R", "D", "P", "G")
DEVICE_NAMES = {"R": "Radio", "D": "Display", "P": "PLE", "G": "GPS"}

# Hanya R/D/P yang dihitung dalam availability overall dan availability gabungan unit.
# GPS (G) tetap dipantau, disimpan di SQLite, dan ditampilkan pada rincian perangkat.
OVERALL_DEVICE_TYPES = ("R", "D", "P")

# Jam istirahat: data pada rentang ini tidak dihitung untuk availability dan coverage.
BREAK_START = _time.fromisoformat(os.getenv("BREAK_START", "12:00"))
BREAK_END = _time.fromisoformat(os.getenv("BREAK_END", "13:00"))

# Pengaturan collector ICMP
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
PING_COUNT = int(os.getenv("PING_COUNT", "5"))
PING_INTERVAL_SECONDS = float(os.getenv("PING_INTERVAL_SECONDS", "0.2"))
PING_TIMEOUT_SECONDS = float(os.getenv("PING_TIMEOUT_SECONDS", "1"))
CONCURRENT_TASKS = int(os.getenv("CONCURRENT_TASKS", "75"))
TRY_PRIVILEGED_ICMP_FIRST = os.getenv("TRY_PRIVILEGED_ICMP_FIRST", "true").lower() in {
    "1", "true", "yes", "y"
}

# Mengikuti aturan status pada script awal: loss <= 40% dianggap available.
FAILURE_LOSS_THRESHOLD_PERCENT = float(
    os.getenv("FAILURE_LOSS_THRESHOLD_PERCENT", "40")
)
RED_AFTER_MINUTES = int(os.getenv("RED_AFTER_MINUTES", "15"))

# Gap di atas nilai ini dianggap tidak terpantau dan tidak dimasukkan ke uptime/downtime.
MAX_OBSERVATION_GAP_SECONDS = int(os.getenv("MAX_OBSERVATION_GAP_SECONDS", "180"))
PING_HISTORY_RETENTION_DAYS = int(os.getenv("PING_HISTORY_RETENTION_DAYS", "30"))
# Site ADT (Kalimantan Selatan) memakai WITA (UTC+8). Batas hari, jam istirahat, dan bulan laporan mengikuti zona waktu ini.
TIMEZONE_NAME = os.getenv("TIMEZONE_NAME", "Asia/Makassar")

# Pengaturan SQL Server. Server default dipertahankan dari script awal.
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_SERVER = os.getenv("SQL_SERVER", "10.4.17.13")
SQL_DATABASE = os.getenv("SQL_DATABASE", "")
SQL_USERNAME = os.getenv("SQL_USERNAME", "")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "false").lower() in {
    "1", "true", "yes", "y"
}
SQL_ENCRYPT = os.getenv("SQL_ENCRYPT", "no")
SQL_TRUST_SERVER_CERTIFICATE = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "yes")
SQL_CONNECTION_TIMEOUT = int(os.getenv("SQL_CONNECTION_TIMEOUT", "10"))

# Interval refresh SQL Server/dashboard. Nilai 10 detik mengikuti script awal.
SQL_REFRESH_SECONDS = int(os.getenv("SQL_REFRESH_SECONDS", "10"))
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)  # kosong -> acak tiap start

# Pembatasan akses dashboard (semua opsional; kosong = perilaku lama, terbuka untuk seluruh jaringan).
# ALLOWED_NETWORKS: daftar CIDR dipisah koma, mis. "10.4.0.0/16,127.0.0.1". Alamat lain ditolak (HTTP dan Socket.IO).
# DASHBOARD_USER/DASHBOARD_PASSWORD: bila password diisi, semua halaman meminta login Basic.
# CORS_ALLOWED_ORIGINS: kosong = hanya origin yang sama dengan server; isi bila dashboard diakses lewat domain/proxy lain.
def _csv(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


ALLOWED_NETWORKS = _csv("ALLOWED_NETWORKS")

# Sumber mapping unit (OID, nama, IP Radio/Display/PLE): "sql" = query ke SQL Server (default), "excel" = dataoidip.xlsx (cadangan).
MACHINE_SOURCE = os.getenv("MACHINE_SOURCE", "sql").strip().lower()
MACHINE_QUERY_FILE = Path(os.getenv("MACHINE_QUERY_FILE", str(BASE_DIR / "query_machine_ips.sql")))
MACHINE_REFRESH_MINUTES = float(os.getenv("MACHINE_REFRESH_MINUTES", "10"))
MACHINE_CACHE_FILE = Path(os.getenv("MACHINE_CACHE_FILE", str(BASE_DIR / "machine_map_cache.json")))
# IP GPS tidak ada di database: dibaca dari Excel (kolom OID/Name + GPS atau G) lalu digabung ke hasil query SQL. "none" = GPS tidak dipantau.
GPS_SOURCE = os.getenv("GPS_SOURCE", "excel").strip().lower()

# Jumlah satelit GPS dari query SQL (query_gps.sql; kolom OID, nama truk, Satellites; data per menit, nilai terbaru dipakai).
# Lampu G: NULL atau < GPS_YELLOW_MIN merah, GPS_YELLOW_MIN sampai < GPS_GREEN_MIN kuning (13-14), >= GPS_GREEN_MIN hijau (15 ke atas).
GPS_QUERY_FILE = Path(os.getenv("GPS_QUERY_FILE", str(BASE_DIR / "query_gps.sql")))
GPS_METRIC_COLUMN = os.getenv("GPS_METRIC_COLUMN", "Satellites").strip()          # kolom nilai pada hasil query (kosong = nonaktif)
GPS_METRIC_AGGREGATE = os.getenv("GPS_METRIC_AGGREGATE", "last").strip().lower()  # last (baris terbaru), avg, min, max, sum
GPS_GREEN_MIN = float(os.getenv("GPS_GREEN_MIN", "15"))
GPS_YELLOW_MIN = float(os.getenv("GPS_YELLOW_MIN", "13"))
GPS_ABSENT_IS_RED = os.getenv("GPS_ABSENT_IS_RED", "false").strip().lower() in {"1", "true", "yes", "on"}   # unit tidak ada di hasil query: merah (true) atau ikut ping (false)
GPS_QUERY_WINDOW_MINUTES = float(os.getenv("GPS_QUERY_WINDOW_MINUTES", "1"))      # jendela data PositionReport; unit tanpa data di jendela ini = NULL = merah
GPS_QUERY_REFRESH_MINUTES = float(os.getenv("GPS_QUERY_REFRESH_MINUTES", "1"))    # data per menit

# Retensi bulanan: data availability bulan yang SUDAH diekspor dan lebih tua dari N bulan terakhir dihapus otomatis.
# Bulan yang belum diekspor tidak pernah dihapus.
AVAILABILITY_PURGE = os.getenv("AVAILABILITY_PURGE", "true").strip().lower() in {"1", "true", "yes", "on"}
AVAILABILITY_KEEP_MONTHS = int(os.getenv("AVAILABILITY_KEEP_MONTHS", "2"))
GPS_EXCEL_FILE = Path(os.getenv("GPS_EXCEL_FILE", str(EXCEL_FILE)))

# Canary: IP yang pasti hidup (mis. gateway PC monitor). Jika SEMUA canary 100% loss, scan dibatalkan karena masalahnya di jaringan PC monitor.
CANARY_IPS = _csv("CANARY_IPS")
CORS_ALLOWED_ORIGINS = _csv("CORS_ALLOWED_ORIGINS")
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
