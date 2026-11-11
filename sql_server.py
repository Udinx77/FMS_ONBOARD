"""Koneksi SQL Server bersama: dipakai dashboard (app.py) dan pembaca mapping unit (machine_map.py)."""
from __future__ import annotations

from config import (
    SQL_CONNECTION_TIMEOUT,
    SQL_DATABASE,
    SQL_DRIVER,
    SQL_ENCRYPT,
    SQL_PASSWORD,
    SQL_SERVER,
    SQL_TRUST_SERVER_CERTIFICATE,
    SQL_TRUSTED_CONNECTION,
    SQL_USERNAME,
)


def build_connection_string() -> str:
    """Bentuk connection string dari konfigurasi; tidak menyimpan password di source."""
    if not SQL_DATABASE:
        raise RuntimeError(
            "SQL_DATABASE belum diisi. Atur variabel environment SQL_DATABASE "
            "atau isi nilai database pada config.py."
        )
    parts = [
        f"DRIVER={{{SQL_DRIVER}}}",
        f"SERVER={SQL_SERVER}",
        f"DATABASE={SQL_DATABASE}",
        f"Connection Timeout={SQL_CONNECTION_TIMEOUT}",
        f"Encrypt={SQL_ENCRYPT}",
        f"TrustServerCertificate={SQL_TRUST_SERVER_CERTIFICATE}",
    ]
    if SQL_TRUSTED_CONNECTION:
        parts.append("Trusted_Connection=yes")
    else:
        parts.extend([f"UID={SQL_USERNAME}", f"PWD={SQL_PASSWORD}"])
    return ";".join(parts) + ";"


def connect():
    import pyodbc  # diimpor di sini agar modul lain bisa dipakai/diuji tanpa driver ODBC

    return pyodbc.connect(build_connection_string())
