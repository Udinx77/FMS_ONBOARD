"""Sumber mapping OID/nama/IP perangkat langsung dari database MineStar via SQL Server."""
from __future__ import annotations

import ipaddress
import logging
from collections import defaultdict
from typing import Any

import pyodbc

from config import (
    DEVICE_TYPES,
    MINSTAR_DISPLAY_INTERFACE_NAME,
    MINSTAR_GPS_INTERFACE_NAME,
    MINSTAR_PLE_INTERFACE_NAME,
    MINSTAR_RADIO_INTERFACE_NAME,
    SQL_CONNECTION_TIMEOUT,
    SQL_DATABASE,
    SQL_DRIVER,
    SQL_ENCRYPT,
    SQL_PASSWORD,
    SQL_SERVER,
    SQL_TRUSTED_CONNECTION,
    SQL_TRUST_SERVER_CERTIFICATE,
    SQL_USERNAME,
)

LOGGER = logging.getLogger("minestar-source")


def build_sql_connection_string() -> str:
    """Susun connection string SQL Server dari config/.env."""
    if not SQL_DATABASE:
        raise RuntimeError(
            "SQL_DATABASE belum diisi. Isi nama database pada file .env."
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


def get_sql_connection():
    """Buka koneksi MineStar SQL Server."""
    return pyodbc.connect(
        build_sql_connection_string(),
        timeout=SQL_CONNECTION_TIMEOUT,
    )


# InterfaceName dapat disesuaikan dari .env. Nama default mengikuti query yang
# diberikan: Assignment = Display, Health = PLE, GPS = GPS, Radio = opsional.
IP_MAPPING_QUERY = r"""
SELECT
    mc.[parentOID] AS MACHINE_OID,
    mr.[name] AS MACHINE_NAME,
    LTRIM(RTRIM(mc.[interfaceName])) AS INTERFACE_NAME,
    ip_data.IP_ADDRESS
FROM [].[dbo].[VM_MACHINE_COMMUNICATIONS] AS mc
LEFT JOIN [].[dbo].[V_MACHINE_REFERENCE] AS mr
    ON mc.[parentOID] = mr.[OID]
CROSS APPLY (
    SELECT LTRIM(RTRIM(REPLACE(
        REPLACE(mc.[communications], 'tmac://', ''),
        'tcp://', ''
    ))) AS RAW_COMMUNICATION
) AS cleaned
CROSS APPLY (
    SELECT LEFT(
        cleaned.RAW_COMMUNICATION,
        CHARINDEX(':', cleaned.RAW_COMMUNICATION + ':') - 1
    ) AS IP_ADDRESS
) AS ip_data
WHERE UPPER(LTRIM(RTRIM(mc.[interfaceName]))) IN (?, ?, ?, ?)
  AND mc.[communications] IS NOT NULL
  AND LTRIM(RTRIM(mc.[communications])) <> ''
  AND mr.[name] IS NOT NULL
ORDER BY mc.[parentOID], mc.[interfaceName]
"""

DISTINCT_INTERFACES_QUERY = r"""
SELECT DISTINCT LTRIM(RTRIM([interfaceName])) AS INTERFACE_NAME
FROM [].[dbo].[VM_MACHINE_COMMUNICATIONS]
WHERE [interfaceName] IS NOT NULL
ORDER BY LTRIM(RTRIM([interfaceName]))
"""


def _normalize_ip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        LOGGER.warning("IP MineStar tidak valid dan dilewati: %r", text)
        return None



def derive_radio_ip(display_ip: str | None) -> str | None:
    """Pertahankan aturan query lama: IP WGB Radio = IP Display - 1 di oktet terakhir."""
    if not display_ip:
        return None
    try:
        address = ipaddress.ip_address(display_ip)
    except ValueError:
        return None
    if address.version != 4:
        LOGGER.warning("IP Display %s bukan IPv4; IP Radio tidak dapat diturunkan.", display_ip)
        return None
    octets = display_ip.split(".")
    last_octet = int(octets[3])
    if last_octet < 1:
        LOGGER.warning(
            "IP Display %s berakhiran .0; aturan Radio = Display - 1 tidak valid.",
            display_ip,
        )
        return None
    octets[3] = str(last_octet - 1)
    return ".".join(octets)


def build_machine_map_from_rows(rows) -> tuple[dict, tuple[int, ...], list[dict], list[str]]:
    """Konversi baris communications MineStar menjadi peta OID dan target ping."""
    interface_to_type = {
        MINSTAR_RADIO_INTERFACE_NAME.strip().upper(): "R",
        MINSTAR_DISPLAY_INTERFACE_NAME.strip().upper(): "D",
        MINSTAR_PLE_INTERFACE_NAME.strip().upper(): "P",
        MINSTAR_GPS_INTERFACE_NAME.strip().upper(): "G",
    }
    grouped: dict[int, dict[str, Any]] = {}
    found_interface_names: set[str] = set()

    for row in rows:
        try:
            oid = int(row.MACHINE_OID)
        except (TypeError, ValueError, AttributeError):
            LOGGER.warning("Baris communications tanpa parentOID valid dilewati.")
            continue

        name = str(getattr(row, "MACHINE_NAME", None) or f"OID-{oid}").strip()
        interface_name = str(getattr(row, "INTERFACE_NAME", "") or "").strip().upper()
        ip_address = _normalize_ip(getattr(row, "IP_ADDRESS", None))
        if not interface_name or not ip_address:
            continue

        found_interface_names.add(interface_name)
        device_type = interface_to_type.get(interface_name)
        if device_type is None:
            continue

        item = grouped.setdefault(
            oid,
            {"Name": name, **{device_type_key: None for device_type_key in DEVICE_TYPES}},
        )
        # Bila terdapat lebih dari satu IP untuk interface yang sama, jangan
        # menimpa diam-diam; gunakan IP pertama dari hasil query yang terurut.
        existing = item.get(device_type)
        if existing and existing != ip_address:
            LOGGER.warning(
                "OID %s memiliki beberapa IP untuk interface %s (%s, %s); memakai IP pertama.",
                oid, interface_name, existing, ip_address,
            )
        elif not existing:
            item[device_type] = ip_address

    # Jika MineStar tidak menyediakan interface Radio secara eksplisit,
    # pertahankan konvensi pada query yang diberikan: Radio = Display - 1.
    for oid, item in grouped.items():
        if not item.get("R"):
            item["R"] = derive_radio_ip(item.get("D"))

    # Bangun satu target per OID + tipe perangkat. IP unik diping sekali,
    # tetapi hasilnya dapat disimpan ke beberapa OID jika alamatnya sama.
    targets: list[dict[str, Any]] = []
    unique_ips: set[str] = set()
    for oid, item in sorted(grouped.items()):
        name = item.get("Name") or f"OID-{oid}"
        for device_type in DEVICE_TYPES:
            ip_address = item.get(device_type)
            if not ip_address:
                continue
            targets.append({
                "machine_oid": oid,
                "machine_name": name,
                "device_type": device_type,
                "ip_address": ip_address,
            })
            unique_ips.add(ip_address)

    if not grouped:
        raise RuntimeError(
            "Query MineStar tidak menghasilkan mapping perangkat. Periksa tabel "
            "VM_MACHINE_COMMUNICATIONS, interfaceName, dan konfigurasi .env."
        )
    if not unique_ips:
        raise RuntimeError(
            "Mapping OID/nama ditemukan, tetapi tidak ada IP R/D/P/G valid."
        )

    oid_list = tuple(sorted(grouped.keys()))
    sorted_ips = sorted(
        unique_ips,
        key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))),
    )
    gps_count = sum(1 for item in grouped.values() if item.get("G"))
    LOGGER.info(
        "MineStar mapping berhasil | unit=%s | target OID-device=%s | IP unik=%s | "
        "unit dengan GPS IP=%s | interface terdeteksi=%s",
        len(grouped), len(targets), len(sorted_ips), gps_count,
        ", ".join(sorted(found_interface_names)) or "-",
    )
    if gps_count == 0:
        LOGGER.warning(
            "Tidak ada IP GPS (G) yang ditemukan. Pastikan MINSTAR_GPS_INTERFACE_NAME "
            "sesuai dengan nilai interfaceName MineStar."
        )

    return grouped, oid_list, targets, sorted_ips


def fetch_minestar_machine_map():
    """Ambil mapping mesin dan IP langsung dari MineStar, tanpa Excel."""
    interface_names = (
        MINSTAR_RADIO_INTERFACE_NAME.strip().upper(),
        MINSTAR_DISPLAY_INTERFACE_NAME.strip().upper(),
        MINSTAR_PLE_INTERFACE_NAME.strip().upper(),
        MINSTAR_GPS_INTERFACE_NAME.strip().upper(),
    )
    if len(set(interface_names)) != 4:
        raise RuntimeError(
            "Nama interface Radio/Display/PLE/GPS pada config.py atau .env harus berbeda."
        )

    conn = None
    cursor = None
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        cursor.execute(IP_MAPPING_QUERY, interface_names)
        rows = cursor.fetchall()
        return build_machine_map_from_rows(rows)
    except pyodbc.Error as exc:
        raise RuntimeError(f"Gagal mengambil mapping IP dari MineStar: {exc}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def fetch_distinct_interface_names() -> list[str]:
    """Diagnostic: daftar interfaceName yang benar-benar ada di MineStar."""
    conn = None
    cursor = None
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        cursor.execute(DISTINCT_INTERFACES_QUERY)
        return [str(row.INTERFACE_NAME).strip() for row in cursor.fetchall() if row.INTERFACE_NAME]
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
