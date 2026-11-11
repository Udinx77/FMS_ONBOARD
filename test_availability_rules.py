"""Cek cepat jam istirahat dan tipe GPS: python test_availability_rules.py"""
import tempfile
import time as _t
from datetime import datetime, date
from pathlib import Path

from config import BREAK_END, BREAK_START
import db
from db import LOCAL_TZ, _split_interval_by_local_day, connect_db, counted_seconds_in_day, initialize_db

def ts(h, m, s=0, day=date(2026, 11, 3)):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=LOCAL_TZ).timestamp()

assert (BREAK_START.hour, BREAK_END.hour) == (12, 13), "uji ini mengasumsikan jam istirahat 12:00-13:00"
# hari penuh: 24 jam dikurangi 1 jam istirahat
assert counted_seconds_in_day(date(2026, 11, 3)) == 23 * 3600

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "t.db"
    initialize_db(path)
    conn = connect_db(path)
    with conn:
        # 11:59 -> 13:01 : hanya 1 menit sebelum 12:00 dan 1 menit setelah 13:00 yang dihitung
        _split_interval_by_local_day(ts(11, 59), ts(13, 1), 1, "CT1", "G", "10.0.0.9", True, conn)
        # seluruhnya di dalam jam istirahat -> tidak ada yang dicatat
        _split_interval_by_local_day(ts(12, 10), ts(12, 20), 2, "CT2", "G", "10.0.0.8", False, conn)
    row = conn.execute("SELECT uptime_seconds, downtime_seconds, observed_seconds FROM availability_daily WHERE machine_oid=1").fetchone()
    assert tuple(row) == (120.0, 0.0, 120.0), tuple(row)
    assert conn.execute("SELECT COUNT(*) FROM availability_daily WHERE machine_oid=2").fetchone()[0] == 0
    conn.close()

    # tipe G (GPS) bisa disimpan
    now = _t.time()
    db.save_scan_results(now - 60, now, 1, [{
        "machine_oid": 1, "machine_name": "CT1", "device_type": "G", "ip_address": "10.0.0.9",
        "packets_sent": 5, "packets_received": 5, "packet_loss_pct": 0.0,
    }], 40, 15, 180, db_path=path)
    assert (1, "G", "10.0.0.9") in db.get_latest_status_map(path)
print("OK: jam istirahat dan tipe GPS")
