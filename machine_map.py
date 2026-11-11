"""Sumber mapping OID, nama unit, dan IP Radio/Display/PLE/GPS.

Utama: query SQL Server (query_machine_ips.sql). IP GPS tidak ada di database, jadi dibaca dari Excel (dataoidip.xlsx,
kolom OID/Name + GPS atau G) dan digabung per OID (cadangan: per nama unit). Cadangan penuh: Excel (MACHINE_SOURCE=excel).
Hasil query terakhir yang berhasil disimpan ke machine_map_cache.json, dipakai jika SQL Server tidak terjangkau saat start.
"""
from __future__ import annotations

import ipaddress
import json
import time
from decimal import Decimal
from typing import Any

import pandas as pd

from config import (
    DEVICE_TYPES,
    EXCEL_FILE,
    GPS_EXCEL_FILE,
    GPS_SOURCE,
    MACHINE_CACHE_FILE,
    MACHINE_QUERY_FILE,
    MACHINE_REFRESH_MINUTES,
    MACHINE_SOURCE,
)

# Nama kolom hasil query -> tipe data internal (huruf kecil). Kolom OID dan nama unit wajib; GPS opsional.
SQL_COLUMN_ALIASES = {
    "OID": ("oid", "parentoid"),
    "Name": ("sn unit", "name", "snunit"),
    "R": ("ip wgb radio", "ip radio", "r"),
    "D": ("ip display", "d"),
    "P": ("ip ple", "p"),
    "G": ("ip gps", "gps", "g"),
}


def clean_cell(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def parse_oid(value: Any) -> int:
    text = clean_cell(value)
    if text is None:
        raise ValueError("OID kosong")
    return int(Decimal(text))


def _build(rows: list[dict[str, Any]], source: str, quiet: bool = False):
    """Menghasilkan (oid_map, oid_list_str, targets, unique_ips) dari baris mentah.

    rows: dict dengan kunci OID, Name, R, D, P, G (G boleh kosong) dan _ref (penunjuk lokasi untuk pesan).
    oid_map: {OID: {Name, R, D, P, G}}; targets: satu item per OID dan tipe perangkat;
    unique_ips: IP unik yang diperiksa satu kali per siklus.
    """
    def say(message: str) -> None:
        if not quiet:
            print(f"[{source}] {message}")

    oid_map: dict[int, dict[str, Any]] = {}
    oid_order: list[int] = []
    targets: list[dict[str, Any]] = []
    target_keys: set[tuple[int, str, str]] = set()
    unique_ips: set[str] = set()

    for row in rows:
        ref = row.get("_ref", "?")
        raw_oid = clean_cell(row.get("OID"))
        if raw_oid is None:
            say(f"{ref} dilewati: OID kosong")
            continue
        try:
            oid = parse_oid(raw_oid)
        except Exception:
            say(f"{ref} dilewati: OID tidak valid ({raw_oid!r})")
            continue
        name = clean_cell(row.get("Name")) or f"OID-{oid}"
        machine_info: dict[str, Any] = {"Name": name, **{t: None for t in DEVICE_TYPES}}

        for device_type in DEVICE_TYPES:
            raw_ip = clean_cell(row.get(device_type))
            if raw_ip is None:
                continue
            try:
                ip_address = str(ipaddress.ip_address(raw_ip))
            except ValueError:
                say(f"IP tidak valid pada {ref}, tipe {device_type}: {raw_ip!r}")
                continue

            machine_info[device_type] = ip_address
            key = (oid, device_type, ip_address)
            if key not in target_keys:
                targets.append({
                    "machine_oid": oid,
                    "machine_name": name,
                    "device_type": device_type,
                    "ip_address": ip_address,
                })
                target_keys.add(key)
            unique_ips.add(ip_address)

        if oid not in oid_map:
            oid_order.append(oid)
        oid_map[oid] = machine_info

    if not oid_map:
        raise ValueError(f"Tidak ada data OID valid dari {source}.")
    if not targets:
        raise ValueError(f"Tidak ada IP perangkat valid yang ditemukan dari {source}.")

    # Satu IP yang dipakai beberapa unit: ping hanya sekali, hasilnya dipakai semua unit itu. Biasanya salah data.
    owners: dict[str, set[int]] = {}
    for target in targets:
        owners.setdefault(target["ip_address"], set()).add(target["machine_oid"])
    duplicates = {ip: oids for ip, oids in owners.items() if len(oids) > 1}
    for ip, oids in list(duplicates.items())[:10]:
        names = ", ".join(oid_map[o]["Name"] for o in sorted(oids))
        say(f"PERINGATAN: IP {ip} dipakai oleh {len(oids)} unit ({names}). Periksa kemungkinan salah data.")
    if len(duplicates) > 10:
        say(f"PERINGATAN: ... dan {len(duplicates) - 10} IP ganda lainnya.")

    oid_list_str = ",".join(str(oid) for oid in oid_order)
    sorted_ips = sorted(
        unique_ips,
        key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))),
    )
    say(
        f"Berhasil memuat {len(oid_map)} OID, {len(targets)} mapping perangkat, "
        f"dan {len(sorted_ips)} IP unik."
    )
    return oid_map, oid_list_str, targets, sorted_ips


# ---------------------------------------------------------------- Excel (cadangan)
def _excel_rows(excel_file) -> list[dict[str, Any]]:
    if not excel_file.exists():
        raise FileNotFoundError(
            f"File Excel tidak ditemukan: {excel_file}. "
            "Letakkan dataoidip.xlsx satu folder dengan project."
        )
    df = pd.read_excel(excel_file, dtype=str)
    df.columns = df.columns.astype(str).str.strip()
    if "G" not in df.columns:  # kolom GPS opsional, boleh bernama GPS
        gps_col = next((c for c in df.columns if c.upper() == "GPS"), None)
        if gps_col:
            df = df.rename(columns={gps_col: "G"})
    required = ["OID", "Name", "R", "D", "P"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Kolom Excel tidak lengkap. Kolom yang hilang: {missing}")
    rows = []
    for row_number, row in df.iterrows():
        item = {"_ref": f"baris {row_number + 2}"}
        item.update({key: row.get(key) for key in ("OID", "Name", *DEVICE_TYPES)})
        rows.append(item)
    return rows


def load_machine_map(excel_file=EXCEL_FILE):
    """Baca mapping dari Excel (MACHINE_SOURCE=excel)."""
    return _build(_excel_rows(excel_file), "Excel")


def load_if_changed(last_mtime, excel_file=EXCEL_FILE):
    """Muat ulang Excel hanya jika file berubah. Return (data_atau_None, mtime).

    Gagal baca (file sedang disimpan/rusak) -> data lama tetap dipakai, dicoba lagi siklus berikutnya.
    """
    try:
        mtime = excel_file.stat().st_mtime
    except OSError:
        return None, last_mtime
    if mtime == last_mtime:
        return None, last_mtime
    try:
        return load_machine_map(excel_file), mtime
    except Exception as exc:
        print(f"[Excel] Muat ulang gagal, data lama dipakai: {exc}")
        return None, last_mtime


# ---------------------------------------------------------------- SQL Server (utama)
def _sql_rows(connect=None, query_file=None) -> list[dict[str, Any]]:
    """Jalankan query mapping. `connect` bisa diganti untuk pengujian."""
    sql = (query_file or MACHINE_QUERY_FILE).read_text(encoding="utf-8-sig")
    if connect is None:
        from sql_server import connect
    conn = connect()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [str(c[0]) for c in cursor.description]
        lowered = {c.lower(): i for i, c in enumerate(columns)}
        index = {k: next((lowered[a] for a in aliases if a in lowered), None) for k, aliases in SQL_COLUMN_ALIASES.items()}
        missing = [k for k in ("OID", "Name", "R", "D", "P") if index[k] is None]
        if missing:
            raise ValueError(
                f"Kolom hasil query tidak lengkap (hilang: {missing}; diterima: {columns}). "
                "Kolom wajib: OID, SN Unit, IP WGB Radio, IP Display, IP PLE."
            )
        rows = []
        for number, raw in enumerate(cursor.fetchall(), start=1):
            item = {"_ref": f"baris query {number}"}
            item.update({k: (raw[i] if i is not None else None) for k, i in index.items()})
            rows.append(item)
        return rows
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()


def load_machine_map_sql(connect=None, query_file=None):
    """Baca mapping dari SQL Server tanpa cache (dipakai pengujian dan MapWatcher)."""
    return _build(_sql_rows(connect, query_file), "SQL")


def _save_cache(rows: list[dict[str, Any]]) -> None:
    try:
        payload = {"saved_at": time.time(), "rows": [{k: (None if v is None else str(v)) for k, v in r.items()} for r in rows]}
        tmp = MACHINE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(MACHINE_CACHE_FILE)
    except OSError as exc:
        print(f"[SQL] Cache mapping gagal disimpan: {exc}")


def _load_cache():
    try:
        payload = json.loads(MACHINE_CACHE_FILE.read_text(encoding="utf-8"))
        return payload["rows"], float(payload["saved_at"])
    except (OSError, ValueError, KeyError):
        return None, 0.0


def _read_gps_excel(path) -> tuple[dict[int, str], dict[str, str]]:
    """IP GPS dari Excel: ({OID: IP}, {NAMA_HURUF_BESAR: IP}). Butuh kolom GPS (atau G) dan OID dan/atau Name."""
    df = pd.read_excel(path, dtype=str)
    df.columns = df.columns.astype(str).str.strip()
    find = lambda *names: next((c for c in df.columns if c.upper() in names), None)
    gps_col, oid_col, name_col = find("GPS", "G"), find("OID"), find("NAME")
    if gps_col is None or (oid_col is None and name_col is None):
        raise ValueError("kolom GPS (atau G) dan OID/Name tidak ditemukan")
    by_oid: dict[int, str] = {}
    by_name: dict[str, str] = {}
    for _, row in df.iterrows():
        ip = clean_cell(row.get(gps_col))
        if ip is None:
            continue
        if oid_col is not None:
            try:
                by_oid[parse_oid(row.get(oid_col))] = ip
            except Exception:
                pass
        if name_col is not None:
            name = clean_cell(row.get(name_col))
            if name:
                by_name[name.upper()] = ip
    return by_oid, by_name


def _signature(data) -> list:
    return sorted((t["machine_oid"], t["device_type"], t["ip_address"], t["machine_name"]) for t in data[2])


class MapWatcher:
    """Menyimpan mapping unit dan memperbaruinya otomatis, tanpa restart.

    SQL: query ulang tiap MACHINE_REFRESH_MINUTES; gagal -> data lama dipakai. Hasil yang menyusut lebih dari separuh
    ditolak (kemungkinan query/server bermasalah). Excel: dibaca ulang saat file berubah.
    """

    def __init__(self, source: str | None = None, connect=None, refresh_minutes: float | None = None):
        self.source = (source or MACHINE_SOURCE).lower()
        self._connect = connect
        self._interval = (MACHINE_REFRESH_MINUTES if refresh_minutes is None else refresh_minutes) * 60
        self._last_try = time.monotonic()
        self._mtime = None
        self._raw = None  # baris query terakhir (tanpa IP GPS dari Excel)
        self._gps_file = GPS_EXCEL_FILE if (self.source == "sql" and GPS_SOURCE == "excel") else None
        self._gps = None  # ({OID: IP}, {NAMA: IP}) terakhir yang berhasil dibaca
        self._gps_mtime = None
        self._gps_checked = False
        self._gps_retry = 0.0
        self._announce = False
        if self.source == "excel":
            self.data = load_machine_map(EXCEL_FILE)
            self._mtime = EXCEL_FILE.stat().st_mtime
        elif self.source == "sql":
            self.data = self._first_sql_load()
        else:
            raise ValueError(f"MACHINE_SOURCE harus 'sql' atau 'excel', bukan {self.source!r}")

    # ---- IP GPS dari Excel ----
    def _gps_stat(self):
        try:
            return self._gps_file.stat().st_mtime
        except OSError:
            return None

    def _gps_changed(self) -> bool:
        """True bila Excel GPS belum pernah diperiksa atau file berubah (dicoba ulang tiap 60 detik jika gagal baca)."""
        if self._gps_file is None or time.monotonic() < self._gps_retry:
            return False
        return (not self._gps_checked) or self._gps_stat() != self._gps_mtime

    def _with_gps(self, rows):
        """Isi kolom G dari Excel untuk baris yang belum punya IP GPS (kolom IP GPS pada query diutamakan)."""
        if self._gps_file is None:
            return rows
        if self._gps_changed():
            self._gps_checked = True
            mtime = self._gps_stat()
            try:
                if mtime is None:
                    self._gps_mtime = None
                    raise FileNotFoundError(f"{self._gps_file.name} tidak ditemukan")
                self._gps = _read_gps_excel(self._gps_file)
                self._gps_mtime = mtime
                self._announce = True
            except FileNotFoundError as exc:
                print(f"[GPS] {exc}. GPS belum dipantau; letakkan dataoidip.xlsx di folder project.")
            except Exception as exc:
                self._gps_retry = time.monotonic() + 60
                print(f"[GPS] Excel GPS gagal dibaca ({exc}). " + ("Data GPS sebelumnya dipakai." if self._gps else "GPS belum dipantau."))
        if self._gps is None:
            return rows
        by_oid, by_name = self._gps
        merged, filled = [], 0
        for row in rows:
            row = dict(row)
            if clean_cell(row.get("G")) is None:
                try:
                    ip = by_oid.get(parse_oid(row.get("OID")))
                except Exception:
                    ip = None
                if ip is None:
                    ip = by_name.get((clean_cell(row.get("Name")) or "").upper())
                if ip:
                    row["G"] = ip
                    filled += 1
            merged.append(row)
        if self._announce:
            self._announce = False
            print(f"[GPS] {max(len(by_oid), len(by_name))} IP GPS dibaca dari {self._gps_file.name}; {filled} cocok dengan unit hasil query.")
        return merged

    def _first_sql_load(self):
        try:
            rows = _sql_rows(self._connect)
            data = _build(self._with_gps(rows), "SQL")
            _save_cache(rows)
            self._raw = rows
            return data
        except Exception as exc:
            rows, saved_at = _load_cache()
            if rows is None:
                raise RuntimeError(
                    f"Gagal memuat mapping unit dari SQL Server dan belum ada cache ({exc}). "
                    "Periksa .env, akses jaringan SQL Server, dan query_machine_ips.sql."
                ) from exc
            age_hours = (time.time() - saved_at) / 3600
            print(f"[SQL] PERINGATAN: SQL Server tidak terjangkau ({exc}). Memakai cache berumur {age_hours:.1f} jam.")
            self._raw = rows
            return _build(self._with_gps(rows), "Cache")

    def poll(self):
        """Mapping baru (tuple) jika berubah, selain itu None."""
        if self.source == "excel":
            data, self._mtime = load_if_changed(self._mtime)
            if data:
                self.data = data
            return data
        sql_due = time.monotonic() - self._last_try >= self._interval
        gps_due = self._gps_changed()
        if not sql_due and not gps_due:
            return None
        rows, fresh = self._raw, False
        if sql_due:
            self._last_try = time.monotonic()
            try:
                rows = _sql_rows(self._connect)
                _build(self._with_gps(rows), "SQL", quiet=True)
                fresh = True
            except Exception as exc:
                rows = self._raw
                print(f"[SQL] Muat ulang mapping gagal, data lama dipakai: {exc}")
        if not fresh and not gps_due:
            return None
        new = _build(self._with_gps(rows), "SQL", quiet=True)
        if fresh:
            old_count, new_count = len(self.data[0]), len(new[0])
            if old_count >= 10 and new_count < old_count * 0.5:
                print(f"[SQL] PERINGATAN: mapping turun dari {old_count} ke {new_count} unit; hasil baru diabaikan. Restart bila memang benar.")
                return None
        if _signature(new) == _signature(self.data):
            if fresh:
                self._raw = rows
            return None
        self.data = _build(self._with_gps(rows), "SQL")  # cetak ringkasan dan peringatan hanya saat ada perubahan
        if fresh:
            self._raw = rows
            _save_cache(rows)
        return self.data
