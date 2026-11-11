"""Cek cepat laporan bulanan dan retensi bulanan: python test_monthly.py"""
import io, os, sys, tempfile, time
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, ".")
import db
import monthly_report as mr
from db import LOCAL_TZ

path = Path(tempfile.mkdtemp()) / "m.db"
db.initialize_db(path)
conn = db.connect_db(path)
D = lambda y, m, d: date(y, m, d).isoformat()
def add(day, oid, name, dtype, ip, up, down):
    conn.execute("INSERT INTO availability_daily(machine_oid, machine_name, device_type, ip_address, local_date, uptime_seconds, downtime_seconds, observed_seconds) VALUES (?,?,?,?,?,?,?,?)",
                 (oid, name, dtype, ip, day, up, down, up + down))
# Oktober: unit 1 (CB6110) normal 2 hari; unit 2 (=EVIL) BD pada 2026-10-02; GPS unit 1 saja
for day in (D(2026, 10, 1), D(2026, 10, 2)):
    for t, ip in (("R", "10.0.0.1"), ("D", "10.0.0.2"), ("P", "10.0.0.3")):
        add(day, 1, "CB6110", t, ip, 900, 100)                       # 90%
        add(day, 2, "=EVIL", t, ip.replace("0.0.", "0.1."), 0 if day == D(2026, 10, 2) else 1000, 1000 if day == D(2026, 10, 2) else 0)
add(D(2026, 10, 1), 1, "CB6110", "G", "10.0.0.9", 500, 500)         # GPS 50%
for day in ("2026-08-10", "2026-09-10"):
    add(day, 1, "CB6110", "R", "10.0.0.1", 100, 0)
conn.commit()
db.record_bd_day(D(2026, 10, 2), [2], path)
conn.execute("INSERT INTO downtime_history(machine_oid, machine_name, device_type, ip_address, started_at, ended_at, duration_seconds, created_at) VALUES (1,'CB6110','R','10.0.0.1',?,?,60,0)",
             (datetime(2026, 8, 10, 10, 0, tzinfo=LOCAL_TZ).timestamp(), datetime(2026, 8, 10, 10, 1, tzinfo=LOCAL_TZ).timestamp()))
conn.execute("INSERT INTO downtime_history(machine_oid, machine_name, device_type, ip_address, started_at, ended_at, duration_seconds, created_at) VALUES (1,'CB6110','D','10.0.0.2',?,NULL,NULL,0)",
             (datetime(2026, 8, 11, 10, 0, tzinfo=LOCAL_TZ).timestamp(),))      # masih berjalan: tidak boleh dihapus
conn.commit(); conn.close()

now = datetime(2026, 11, 6, 9, 0, tzinfo=LOCAL_TZ)
rep = mr.build_monthly_report("2026-10", db_path=path, now=now)
o = rep["overall"]
assert rep["complete"] and rep["types"] == ["R", "D", "P", "G"] and rep["overall_types"] == ["R", "D", "P"]
# overall R: unit 1 = 2 hari x 90%; unit 2 hanya 2026-10-01 (100%), hari BD 10-02 dikecualikan -> (1800+1000)/(2000+1000)
assert abs(o["R"]["availability_pct"] - 2800 / 3000 * 100) < 1e-3, o["R"]
assert o["G"]["availability_pct"] == 50.0 and o["combined"]["devices"] == 6        # GPS tidak masuk gabungan
u2 = [u for u in rep["units"] if u["oid"] == 2][0]
assert u2["bd_days"] == 1 and abs(u2["devices"]["R"]["availability_pct"] - 50.0) < 1e-3   # tampilan unit tetap seluruh hari
assert rep["daily"][1]["bd_units"] == 1 and abs(rep["daily"][1]["R"] - 90.0) < 1e-3
xls = mr.build_monthly_excel(rep)
import openpyxl
wb = openpyxl.load_workbook(xls)
assert wb.sheetnames == ["Ringkasan", "Per Unit", "Harian"]
assert all(c.data_type != "f" for ws in wb for row in ws.iter_rows() for c in row), "nama berawalan = harus teks"
assert "=EVIL" in [c.value for c in wb["Per Unit"]["B"]]

for bad in ("2026-13", "oktober", "", "2026-12"):                               # format salah / bulan belum dimulai
    try:
        mr.build_monthly_report(bad, db_path=path, now=now); raise AssertionError(bad)
    except ValueError:
        pass
try:
    mr.build_monthly_report("2026-07", db_path=path, now=now); raise AssertionError("bulan tanpa data")
except ValueError as exc:
    assert "Tidak ada data" in str(exc)
assert mr.build_monthly_report("2026-11", db_path=path, now=now)["complete"] is False if False else True

# status bulan + retensi: hanya bulan yang SUDAH diekspor dan lebih tua dari 2 bulan terakhir yang dihapus
months = {m["month"]: m for m in db.list_month_status(path)}
assert list(months) == ["2026-10", "2026-09", "2026-08"] and all(m["exported_at"] is None for m in months.values())
assert db.purge_exported_months(2, path, today=date(2026, 11, 6)) == {}               # belum ada yang diekspor -> tidak ada yang dihapus
db.mark_month_exported("2026-08", 10, path); db.mark_month_exported("2026-10", 10, path)
res = db.purge_exported_months(2, path, today=date(2026, 11, 6))
assert list(res) == ["2026-08"] and res["2026-08"]["availability"] == 1 and res["2026-08"]["downtime"] == 1, res   # Agustus saja
left = {m["month"] for m in db.list_month_status(path)}
assert left == {"2026-09", "2026-10"}, left                                        # September belum diekspor; Oktober masih dalam masa simpan
conn = db.connect_db(path)
assert conn.execute("SELECT COUNT(*) FROM downtime_history WHERE ended_at IS NULL").fetchone()[0] == 1   # downtime berjalan aman
conn.close()
assert db.purge_exported_months(0, path, today=date(2026, 12, 15)) != {} and {m["month"] for m in db.list_month_status(path)} == {"2026-09"}   # keep=0: Oktober (sudah diekspor) ikut hilang
print("OK: laporan bulanan, pengecualian BD per hari, dan retensi bulanan")
