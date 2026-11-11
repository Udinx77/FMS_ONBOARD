"""Cek cepat route export bulanan dan kotak export di halaman laporan: python test_monthly_app.py"""
import os, sys, tempfile, types
from datetime import datetime
from pathlib import Path
import pandas as pd

tmp = Path(tempfile.mkdtemp())
pd.DataFrame([["CB6110", 101, "10.0.0.1", "10.0.0.2", "10.0.0.3", None]], columns=["Name", "OID", "R", "D", "P", "GPS"]).to_excel(tmp / "map.xlsx", index=False)
os.environ.update(MACHINE_SOURCE="excel", EXCEL_FILE=str(tmp / "map.xlsx"), DB_FILE=str(tmp / "t.db"), MACHINE_CACHE_FILE=str(tmp / "c.json"))
sys.modules["pyodbc"] = types.SimpleNamespace(Error=Exception, connect=None)
sys.path.insert(0, ".")
import app, db
from db import LOCAL_TZ

now = datetime.now(LOCAL_TZ)
last_month = db.add_months(f"{now.year:04d}-{now.month:02d}", -1)
cur_month = f"{now.year:04d}-{now.month:02d}"
conn = db.connect_db()
for day, t, ip in ((f"{last_month}-05", "R", "10.0.0.1"), (f"{last_month}-05", "D", "10.0.0.2"), (f"{cur_month}-01", "R", "10.0.0.1")):
    conn.execute("INSERT INTO availability_daily VALUES (101,'CB6110',?,?,?,900,100,1000)", (t, ip, day))
conn.commit(); conn.close()

c = app.app.test_client()
page = c.get("/report/sqlite").data.decode()
assert "Export bulanan ONB" in page and last_month in page and "Belum diekspor" in page and 'type="month"' in page

r = c.get(f"/export/availability_monthly.xlsx?month={last_month}")
assert r.status_code == 200 and r.data[:2] == b"PK" and f"availability_ONB_{last_month}.xlsx" in r.headers["Content-Disposition"]
status = {m["month"]: m for m in db.list_month_status()}
assert status[last_month]["exported_at"] is not None                       # bulan lewat: tercatat sudah diekspor
r = c.get(f"/export/availability_monthly.xlsx?month={cur_month}")
assert r.status_code == 200 and {m["month"]: m for m in db.list_month_status()}[cur_month]["exported_at"] is None   # bulan berjalan: tidak dicatat
assert "Sudah diekspor" in c.get("/report/sqlite").data.decode()
for bad, code in (("2026-13", 400), ("abc", 400), ("", 400), ("2999-01", 400), ("2001-01", 400)):
    assert c.get(f"/export/availability_monthly.xlsx?month={bad}").status_code == code, bad
print("OK: export bulanan, status ekspor, dan kotak export di halaman laporan")
