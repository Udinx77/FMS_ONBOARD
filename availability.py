"""Laporan availability harian dari tabel ringkasan SQLite."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta

from config import DB_FILE, DEVICE_TYPES, TIMEZONE_NAME
from db import connect_db, counted_seconds_in_day, initialize_db

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:  # pragma: no cover - Python 3.9+ seharusnya menyediakan zoneinfo
    LOCAL_TZ = None


def _period_seconds(report_date: date) -> float:
    now = datetime.now(LOCAL_TZ)
    return max(1.0, counted_seconds_in_day(report_date, until=now))


def fetch_report(report_date: date, db_path=DB_FILE, device_type=None, oid=None):
    initialize_db(db_path)
    conn = connect_db(db_path)
    try:
        sql = """SELECT machine_oid, machine_name, device_type, ip_address,
                        uptime_seconds, downtime_seconds, observed_seconds
                 FROM availability_daily WHERE local_date=?"""
        params: list = [report_date.isoformat()]
        if device_type:
            sql += " AND device_type=?"
            params.append(device_type)
        if oid is not None:
            sql += " AND machine_oid=?"
            params.append(oid)
        sql += " ORDER BY machine_oid, device_type, ip_address"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    period_seconds = _period_seconds(report_date)
    report = []
    for row in rows:
        observed = float(row["observed_seconds"])
        uptime = float(row["uptime_seconds"])
        downtime = float(row["downtime_seconds"])
        report.append({
            "machine_oid": int(row["machine_oid"]),
            "machine_name": row["machine_name"],
            "device_type": row["device_type"],
            "ip_address": row["ip_address"],
            "uptime_seconds": uptime,
            "downtime_seconds": downtime,
            "observed_seconds": observed,
            "availability_pct": uptime / observed * 100.0 if observed > 0 else None,
            "coverage_pct": min(100.0, observed / period_seconds * 100.0),
        })
    return report


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}j {minutes}m {secs}d"


def main() -> int:
    parser = argparse.ArgumentParser(description="Laporan availability per perangkat R/D/P/GPS; overall hanya menghitung R/D/P")
    parser.add_argument("--date", help="Tanggal laporan YYYY-MM-DD; default hari ini")
    parser.add_argument("--device-type", choices=list(DEVICE_TYPES))
    parser.add_argument("--oid", type=int)
    parser.add_argument("--db", default=str(DB_FILE))
    args = parser.parse_args()

    report_date = date.fromisoformat(args.date) if args.date else datetime.now(LOCAL_TZ).date()
    rows = fetch_report(report_date, args.db, args.device_type, args.oid)
    print(f"\nAVAILABILITY REPORT | {report_date.isoformat()} | {TIMEZONE_NAME}")
    print("=" * 116)
    print(
        f"{'OID':<12} {'MESIN':<18} {'TIPE':<6} {'IP':<16} "
        f"{'UPTIME':<15} {'DOWNTIME':<15} {'AVAILABILITY':<15} {'COVERAGE':<10}"
    )
    print("-" * 116)
    for row in rows:
        availability = (
            f"{row['availability_pct']:.2f}%"
            if row["availability_pct"] is not None else "N/A"
        )
        print(
            f"{row['machine_oid']:<12} {str(row['machine_name'])[:17]:<18} "
            f"{row['device_type']:<6} {row['ip_address']:<16} "
            f"{format_duration(row['uptime_seconds']):<15} "
            f"{format_duration(row['downtime_seconds']):<15} "
            f"{availability:<15} {row['coverage_pct']:>6.2f}%"
        )
    if not rows:
        print("Belum ada ringkasan availability untuk filter/periode tersebut.")
    print("\nAvailability = uptime / observed time x 100%.")
    print("Coverage menunjukkan bagian periode laporan yang benar-benar terpantau.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
