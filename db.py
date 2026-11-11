"""Penyimpanan histori ping, status terkini, downtime, dan ringkasan availability."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import (
    BREAK_END,
    BREAK_START,
    DB_FILE,
    MAX_OBSERVATION_GAP_SECONDS,
    TIMEZONE_NAME,
)

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)


def connect_db(db_path: str | Path = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


_TYPE_TABLES = ("ping_history", "device_status", "downtime_history", "availability_daily")


def _with_migration(conn: sqlite3.Connection, schema: str) -> str:
    """DB lama membatasi device_type ke R/D/P (CHECK). Bangun ulang tabel agar tipe baru (GPS) bisa disimpan."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='device_status'"
    ).fetchone()
    if not row or "'R', 'D', 'P'" not in row[0]:  # sudah tanpa batas tipe -> tidak perlu migrasi
        return schema
    marks = ",".join("?" * len(_TYPE_TABLES))
    index_names = [r[0] for r in conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
        f"AND tbl_name IN ({marks})", _TYPE_TABLES)]
    script = "BEGIN;"
    script += "".join(f"ALTER TABLE {t} RENAME TO {t}_old;" for t in _TYPE_TABLES)
    script += "".join(f"DROP INDEX {name};" for name in index_names)
    script += schema
    script += "".join(f"INSERT INTO {t} SELECT * FROM {t}_old; DROP TABLE {t}_old;" for t in _TYPE_TABLES)
    return script + "COMMIT;"


def initialize_db(db_path: str | Path = DB_FILE) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect_db(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_with_migration(conn,
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                finished_at REAL,
                duration_seconds REAL,
                target_count INTEGER NOT NULL DEFAULT 0,
                checked_count INTEGER NOT NULL DEFAULT 0,
                scan_status TEXT NOT NULL,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS ping_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                checked_at REAL NOT NULL,
                machine_oid INTEGER NOT NULL,
                machine_name TEXT,
                device_type TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                packets_sent INTEGER NOT NULL,
                packets_received INTEGER NOT NULL,
                packet_loss_pct REAL NOT NULL,
                avg_rtt_ms REAL,
                min_rtt_ms REAL,
                max_rtt_ms REAL,
                jitter_ms REAL,
                is_available INTEGER NOT NULL CHECK(is_available IN (0, 1)),
                FOREIGN KEY(scan_id) REFERENCES scan_runs(id)
            );

            CREATE TABLE IF NOT EXISTS device_status (
                machine_oid INTEGER NOT NULL,
                machine_name TEXT,
                device_type TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                status TEXT NOT NULL,
                is_available INTEGER NOT NULL CHECK(is_available IN (0, 1)),
                fail_since REAL,
                last_checked_at REAL NOT NULL,
                packets_sent INTEGER NOT NULL,
                packets_received INTEGER NOT NULL,
                packet_loss_pct REAL NOT NULL,
                avg_rtt_ms REAL,
                PRIMARY KEY(machine_oid, device_type, ip_address)
            );

            CREATE TABLE IF NOT EXISTS downtime_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_oid INTEGER NOT NULL,
                machine_name TEXT,
                device_type TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                duration_seconds REAL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS availability_daily (
                machine_oid INTEGER NOT NULL,
                machine_name TEXT,
                device_type TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                local_date TEXT NOT NULL,
                uptime_seconds REAL NOT NULL DEFAULT 0,
                downtime_seconds REAL NOT NULL DEFAULT 0,
                observed_seconds REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(machine_oid, device_type, ip_address, local_date)
            );

            CREATE TABLE IF NOT EXISTS bd_daily (
                local_date TEXT NOT NULL,
                machine_oid INTEGER NOT NULL,
                PRIMARY KEY(local_date, machine_oid)
            );

            CREATE TABLE IF NOT EXISTS monthly_exports (
                month TEXT PRIMARY KEY,
                exported_at REAL NOT NULL,
                rows INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_ping_history_time
                ON ping_history(checked_at);
            CREATE INDEX IF NOT EXISTS idx_ping_history_device_time
                ON ping_history(machine_oid, device_type, ip_address, checked_at);
            CREATE INDEX IF NOT EXISTS idx_downtime_device_start
                ON downtime_history(machine_oid, device_type, ip_address, started_at);
            CREATE INDEX IF NOT EXISTS idx_availability_date
                ON availability_daily(local_date, machine_oid, device_type);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_downtime_per_device
                ON downtime_history(machine_oid, device_type, ip_address)
                WHERE ended_at IS NULL;
            """
        ))
        conn.commit()
    finally:
        conn.close()


def _break_overlap(seg_start_ts: float, seg_end_ts: float, local_date) -> float:
    """Detik pada segmen yang jatuh di jam istirahat hari itu."""
    break_start = datetime.combine(local_date, BREAK_START, tzinfo=LOCAL_TZ).timestamp()
    break_end = datetime.combine(local_date, BREAK_END, tzinfo=LOCAL_TZ).timestamp()
    return max(0.0, min(seg_end_ts, break_end) - max(seg_start_ts, break_start))


def counted_seconds_in_day(local_date, until: datetime | None = None) -> float:
    """Durasi hari itu yang dihitung (sampai `until` bila diberikan), tanpa jam istirahat."""
    start = datetime.combine(local_date, time.min, tzinfo=LOCAL_TZ)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    if until is not None:
        end = min(end, until)
    start_ts, end_ts = start.timestamp(), end.timestamp()
    return max(0.0, (end_ts - start_ts) - _break_overlap(start_ts, end_ts, local_date))


def _split_interval_by_local_day(
    start_ts: float,
    end_ts: float,
    machine_oid: int,
    machine_name: str | None,
    device_type: str,
    ip_address: str,
    was_available: bool,
    conn: sqlite3.Connection,
) -> None:
    """Alokasikan interval terpantau ke ringkasan harian, termasuk lintas tengah malam."""
    cursor_ts = start_ts
    while cursor_ts < end_ts:
        local_dt = datetime.fromtimestamp(cursor_ts, LOCAL_TZ)
        next_day = local_dt.date() + timedelta(days=1)
        next_midnight = datetime.combine(next_day, time.min, tzinfo=LOCAL_TZ).timestamp()
        segment_end = min(end_ts, next_midnight)
        # Jam istirahat tidak dihitung sebagai uptime, downtime, maupun observed.
        seconds = max(0.0, segment_end - cursor_ts - _break_overlap(cursor_ts, segment_end, local_dt.date()))
        local_date = local_dt.date().isoformat()

        if seconds > 0:
            uptime = seconds if was_available else 0.0
            downtime = 0.0 if was_available else seconds
            conn.execute(
                """INSERT INTO availability_daily
                   (machine_oid, machine_name, device_type, ip_address, local_date,
                    uptime_seconds, downtime_seconds, observed_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(machine_oid, device_type, ip_address, local_date)
                   DO UPDATE SET
                     machine_name=excluded.machine_name,
                     uptime_seconds=availability_daily.uptime_seconds + excluded.uptime_seconds,
                     downtime_seconds=availability_daily.downtime_seconds + excluded.downtime_seconds,
                     observed_seconds=availability_daily.observed_seconds + excluded.observed_seconds""",
                (machine_oid, machine_name, device_type, ip_address, local_date,
                 uptime, downtime, seconds),
            )
        cursor_ts = segment_end


def _close_open_downtime(
    conn: sqlite3.Connection,
    key: tuple[int, str, str],
    ended_at: float,
    fail_since: float | None,
) -> None:
    started = fail_since if fail_since is not None else ended_at
    conn.execute(
        """UPDATE downtime_history
           SET ended_at=?, duration_seconds=?
           WHERE id=(
               SELECT id FROM downtime_history
               WHERE machine_oid=? AND device_type=? AND ip_address=?
                 AND ended_at IS NULL
               ORDER BY id DESC LIMIT 1
           )""",
        (ended_at, max(0.0, ended_at - started), *key),
    )


def save_scan_results(
    started_at: float,
    finished_at: float,
    target_count: int,
    rows: list[dict[str, Any]],
    failure_threshold_percent: float,
    red_after_minutes: int,
    max_observation_gap_seconds: int = MAX_OBSERVATION_GAP_SECONDS,
    db_path: str | Path = DB_FILE,
) -> int:
    """Simpan satu siklus hasil ping secara atomik dan perbarui summary availability."""
    conn = connect_db(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """INSERT INTO scan_runs
                   (started_at, finished_at, duration_seconds, target_count,
                    checked_count, scan_status)
                   VALUES (?, ?, ?, ?, ?, 'SUCCESS')""",
                (started_at, finished_at, max(0.0, finished_at - started_at),
                 target_count, len(rows)),
            )
            scan_id = int(cursor.lastrowid)

            history_values = []
            for item in rows:
                loss = float(item["packet_loss_pct"])
                is_available = loss <= failure_threshold_percent
                history_values.append((
                    scan_id, finished_at, item["machine_oid"], item["machine_name"],
                    item["device_type"], item["ip_address"], item["packets_sent"],
                    item["packets_received"], loss, item.get("avg_rtt_ms"),
                    item.get("min_rtt_ms"), item.get("max_rtt_ms"),
                    item.get("jitter_ms"), 1 if is_available else 0,
                ))

            conn.executemany(
                """INSERT INTO ping_history
                   (scan_id, checked_at, machine_oid, machine_name, device_type,
                    ip_address, packets_sent, packets_received, packet_loss_pct,
                    avg_rtt_ms, min_rtt_ms, max_rtt_ms, jitter_ms, is_available)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                history_values,
            )

            for item in rows:
                oid = int(item["machine_oid"])
                device_type = item["device_type"]
                ip_address = item["ip_address"]
                machine_name = item.get("machine_name")
                key = (oid, device_type, ip_address)
                loss = float(item["packet_loss_pct"])
                is_available = loss <= failure_threshold_percent

                previous = conn.execute(
                    """SELECT is_available, fail_since, last_checked_at
                       FROM device_status
                       WHERE machine_oid=? AND device_type=? AND ip_address=?""",
                    key,
                ).fetchone()

                previous_fail_since = None
                gap_seconds = None
                previous_is_available = None
                if previous:
                    previous_is_available = bool(previous["is_available"])
                    previous_fail_since = (
                        float(previous["fail_since"])
                        if previous["fail_since"] is not None else None
                    )
                    gap_seconds = finished_at - float(previous["last_checked_at"])

                    # Status sampel sebelumnya dianggap berlaku hingga sampel baru,
                    # hanya jika jedanya masih dalam batas pengamatan yang valid.
                    if 0 < gap_seconds <= max_observation_gap_seconds:
                        _split_interval_by_local_day(
                            float(previous["last_checked_at"]), finished_at,
                            oid, machine_name, device_type, ip_address,
                            previous_is_available, conn,
                        )

                # Downtime: jeda scan yang terlalu panjang memutus kesinambungan event.
                stale_gap = gap_seconds is not None and (
                    gap_seconds <= 0 or gap_seconds > max_observation_gap_seconds
                )
                fail_since = None

                if previous and not previous_is_available and stale_gap:
                    # Akhiri event lama pada waktu pemeriksaan terakhir yang diketahui.
                    _close_open_downtime(
                        conn, key, float(previous["last_checked_at"]), previous_fail_since
                    )
                    if not is_available:
                        fail_since = finished_at
                        conn.execute(
                            """INSERT INTO downtime_history
                               (machine_oid, machine_name, device_type, ip_address,
                                started_at, created_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (oid, machine_name, device_type, ip_address, finished_at, finished_at),
                        )

                elif is_available:
                    if previous and not previous_is_available:
                        _close_open_downtime(conn, key, finished_at, previous_fail_since)
                    fail_since = None

                else:  # Sampel saat ini mengalami gangguan.
                    if previous and not previous_is_available:
                        fail_since = previous_fail_since or finished_at
                    else:
                        fail_since = finished_at
                        conn.execute(
                            """INSERT INTO downtime_history
                               (machine_oid, machine_name, device_type, ip_address,
                                started_at, created_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (oid, machine_name, device_type, ip_address, finished_at, finished_at),
                        )

                if is_available:
                    status = "Green" if loss == 0 else "Yellow"
                else:
                    elapsed = max(0.0, finished_at - (fail_since or finished_at))
                    status = "Red" if elapsed >= red_after_minutes * 60 else "Orange"

                conn.execute(
                    """INSERT INTO device_status
                       (machine_oid, machine_name, device_type, ip_address, status,
                        is_available, fail_since, last_checked_at, packets_sent,
                        packets_received, packet_loss_pct, avg_rtt_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(machine_oid, device_type, ip_address) DO UPDATE SET
                         machine_name=excluded.machine_name,
                         status=excluded.status,
                         is_available=excluded.is_available,
                         fail_since=excluded.fail_since,
                         last_checked_at=excluded.last_checked_at,
                         packets_sent=excluded.packets_sent,
                         packets_received=excluded.packets_received,
                         packet_loss_pct=excluded.packet_loss_pct,
                         avg_rtt_ms=excluded.avg_rtt_ms""",
                    (oid, machine_name, device_type, ip_address, status,
                     1 if is_available else 0, fail_since, finished_at,
                     int(item["packets_sent"]), int(item["packets_received"]),
                     loss, item.get("avg_rtt_ms")),
                )

        return scan_id
    finally:
        conn.close()


def record_failed_scan(
    started_at: float,
    finished_at: float,
    target_count: int,
    error_message: str,
    db_path: str | Path = DB_FILE,
) -> None:
    conn = connect_db(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO scan_runs
                   (started_at, finished_at, duration_seconds, target_count,
                    checked_count, scan_status, error_message)
                   VALUES (?, ?, ?, ?, 0, 'FAILED', ?)""",
                (started_at, finished_at, max(0.0, finished_at - started_at),
                 target_count, str(error_message)[:1000]),
            )
    finally:
        conn.close()


def get_latest_status_map(db_path: str | Path = DB_FILE) -> dict[tuple[int, str, str], dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM device_status").fetchall()
        return {
            (int(row["machine_oid"]), row["device_type"], row["ip_address"]): dict(row)
            for row in rows
        }
    finally:
        conn.close()


def get_daily_availability_map(
    local_date: str,
    db_path: str | Path = DB_FILE,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM availability_daily WHERE local_date=?""",
            (local_date,),
        ).fetchall()
        result = {}
        for row in rows:
            observed = float(row["observed_seconds"])
            uptime = float(row["uptime_seconds"])
            result[(int(row["machine_oid"]), row["device_type"], row["ip_address"])] = {
                "uptime_seconds": uptime,
                "downtime_seconds": float(row["downtime_seconds"]),
                "observed_seconds": observed,
                "availability_pct": uptime / observed * 100.0 if observed > 0 else None,
            }
        return result
    finally:
        conn.close()


def get_latest_scan(db_path: str | Path = DB_FILE) -> dict[str, Any] | None:
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def cleanup_old_history(retention_days: int, db_path: str | Path = DB_FILE) -> tuple[int, int]:
    """Hapus histori ping mentah yang lebih tua; ringkasan harian tetap disimpan."""
    if retention_days <= 0:
        return 0, 0  # 0 atau negatif berarti retensi tanpa batas.
    import time

    cutoff = time.time() - retention_days * 86400
    conn = connect_db(db_path)
    try:
        with conn:
            deleted_history = conn.execute(
                "DELETE FROM ping_history WHERE checked_at < ?", (cutoff,)
            ).rowcount
            deleted_scans = conn.execute(
                "DELETE FROM scan_runs WHERE finished_at < ?", (cutoff,)
            ).rowcount
        return max(0, deleted_history), max(0, deleted_scans)
    finally:
        conn.close()


# ---------------------------------------------------------------- laporan bulanan dan retensi bulanan
def add_months(month: str, n: int) -> str:
    """'2026-11' + n bulan -> 'YYYY-MM'."""
    total = int(month[:4]) * 12 + (int(month[5:7]) - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(month: str) -> tuple[str, str]:
    """(tanggal pertama bulan itu, tanggal pertama bulan berikutnya), format ISO."""
    return f"{month}-01", f"{add_months(month, 1)}-01"


def record_bd_day(local_date: str, oids, db_path: str | Path = DB_FILE) -> None:
    """Catat unit Breakdown pada tanggal itu (dipakai laporan bulanan agar pengecualian BD per hari, bukan per saat laporan)."""
    oids = [int(o) for o in oids]
    if not oids:
        return
    conn = connect_db(db_path)
    try:
        with conn:
            conn.executemany("INSERT OR IGNORE INTO bd_daily(local_date, machine_oid) VALUES (?, ?)", [(local_date, o) for o in oids])
    finally:
        conn.close()


def get_bd_days(first_date: str, last_date: str, db_path: str | Path = DB_FILE) -> set[tuple[str, int]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute("SELECT local_date, machine_oid FROM bd_daily WHERE local_date >= ? AND local_date <= ?", (first_date, last_date)).fetchall()
        return {(r[0], int(r[1])) for r in rows}
    finally:
        conn.close()


def mark_month_exported(month: str, rows: int, db_path: str | Path = DB_FILE) -> None:
    import time as _time
    conn = connect_db(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO monthly_exports(month, exported_at, rows) VALUES (?, ?, ?) "
                "ON CONFLICT(month) DO UPDATE SET exported_at = excluded.exported_at, rows = excluded.rows",
                (month, _time.time(), int(rows)),
            )
    finally:
        conn.close()


def list_month_status(db_path: str | Path = DB_FILE) -> list[dict]:
    """Bulan yang punya data availability, terbaru dulu, beserta status ekspor."""
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            "SELECT substr(a.local_date, 1, 7) AS month, MIN(a.local_date) AS first_date, MAX(a.local_date) AS last_date, "
            "COUNT(DISTINCT a.local_date) AS days, e.exported_at "
            "FROM availability_daily a LEFT JOIN monthly_exports e ON e.month = substr(a.local_date, 1, 7) "
            "GROUP BY substr(a.local_date, 1, 7) ORDER BY month DESC"
        ).fetchall()
        return [{"month": r[0], "first_date": r[1], "last_date": r[2], "days": r[3], "exported_at": r[4]} for r in rows]
    finally:
        conn.close()


def purge_exported_months(keep_months: int, db_path: str | Path = DB_FILE, today: date | None = None) -> dict[str, dict[str, int]]:
    """Hapus data bulan yang sudah diekspor dan lebih tua dari `keep_months` bulan terakhir (bulan berjalan tidak dihitung).

    Bulan yang belum diekspor tidak pernah dihapus. Downtime yang masih berjalan (belum selesai) tidak dihapus.
    Return {bulan: {availability, bd, downtime}} untuk bulan yang ada barisnya.
    """
    if keep_months < 0:
        return {}
    today = today or datetime.now(LOCAL_TZ).date()
    cutoff = add_months(f"{today.year:04d}-{today.month:02d}", -keep_months)  # bulan < cutoff boleh dihapus
    conn = connect_db(db_path)
    result: dict[str, dict[str, int]] = {}
    try:
        months = [r[0] for r in conn.execute("SELECT month FROM monthly_exports WHERE month < ? ORDER BY month", (cutoff,)).fetchall()]
        for month in months:
            first, nxt = month_range(month)
            start_ts = datetime.combine(date.fromisoformat(first), time.min, tzinfo=LOCAL_TZ).timestamp()
            end_ts = datetime.combine(date.fromisoformat(nxt), time.min, tzinfo=LOCAL_TZ).timestamp()
            with conn:
                a = conn.execute("DELETE FROM availability_daily WHERE local_date >= ? AND local_date < ?", (first, nxt)).rowcount
                b = conn.execute("DELETE FROM bd_daily WHERE local_date >= ? AND local_date < ?", (first, nxt)).rowcount
                d = conn.execute(
                    "DELETE FROM downtime_history WHERE started_at >= ? AND started_at < ? AND ended_at IS NOT NULL", (start_ts, end_ts)
                ).rowcount
            if max(a, 0) or max(b, 0) or max(d, 0):
                result[month] = {"availability": max(a, 0), "bd": max(b, 0), "downtime": max(d, 0)}
        return result
    finally:
        conn.close()
