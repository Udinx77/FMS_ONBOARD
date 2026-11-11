"""Cek cepat IP GPS dari Excel yang digabung ke hasil query SQL: python test_gps_excel.py"""
import os, sys, tempfile
from pathlib import Path
import pandas as pd

tmp = Path(tempfile.mkdtemp())
os.environ["MACHINE_CACHE_FILE"] = str(tmp / "cache.json")
sys.path.insert(0, ".")
import machine_map as mm

XLSX = tmp / "dataoidip.xlsx"
mm.GPS_SOURCE, mm.GPS_EXCEL_FILE = "excel", XLSX

COLS = ["OID", "SN Unit", "IP WGB Radio", "IP Display", "IP PLE"]
ROWS = [(101, "CB6110", "10.4.25.21", "10.4.25.22", "10.4.25.23"),
        (102, "CT7261", "10.4.21.247", "10.4.21.248", "10.4.21.249"),
        (103, "KB6201", "10.4.22.1", "10.4.22.2", "10.4.22.3"),
        (104, "HB6300", "10.4.23.1", "10.4.23.2", "10.4.23.3")]

class Cur:
    def __init__(self, c, r): self.description, self._r = [(x,) for x in c], r
    def execute(self, sql): pass
    def fetchall(self): return self._r
    def close(self): pass
class Conn:
    def __init__(self, c, r): self._c, self._r = c, r
    def cursor(self): return Cur(self._c, self._r)
    def close(self): pass
calls = []
def fake(columns=COLS, rows=ROWS):
    def connect():
        calls.append(1); return Conn(columns, rows)
    return connect

def write(rows, gps_header="GPS"):
    pd.DataFrame(rows, columns=["Name", "OID", "R", "D", "P", gps_header]).to_excel(XLSX, index=False)

def gps_of(data):
    return {t["machine_name"]: t["ip_address"] for t in data[2] if t["device_type"] == "G"}

# Excel: CB6110 cocok per OID; CT7261 OID salah tapi nama sama -> cocok per nama; KB6201 kosong; unit hanya di Excel diabaikan
write([["CB6110", 101, "", "", "", "10.4.90.1"], ["CT7261", 99999, "", "", "", "10.4.90.2"],
       ["KB6201", 103, "", "", "", None], ["ZZ0001", 555, "", "", "", "10.4.90.9"]])
w = mm.MapWatcher(source="sql", connect=fake(), refresh_minutes=60)
assert gps_of(w.data) == {"CB6110": "10.4.90.1", "CT7261": "10.4.90.2"}, gps_of(w.data)
assert w.data[0][103]["G"] is None and w.data[0][104]["G"] is None                       # unit tanpa IP GPS: tidak dipantau
assert len(w.data[2]) == 4 * 3 + 2                                                         # 12 target R/D/P + 2 GPS
assert w.poll() is None and len(calls) == 1                                                # tidak ada perubahan, SQL tidak ditanya ulang

# Excel diedit (IP GPS diganti, unit baru dapat GPS) -> terbaca tanpa query SQL dan tanpa restart
write([["CB6110", 101, "", "", "", "10.4.90.77"], ["HB6300", 104, "", "", "", "10.4.90.4"]], gps_header="G")
os.utime(XLSX, (os.stat(XLSX).st_mtime + 5,) * 2)
data = w.poll()
assert data is not None and gps_of(data) == {"CB6110": "10.4.90.77", "HB6300": "10.4.90.4"}, gps_of(w.data)
assert len(calls) == 1, "edit Excel tidak boleh memicu query SQL"

# Excel rusak -> data GPS lama dipertahankan
XLSX.write_bytes(b"rusak"); os.utime(XLSX, (os.stat(XLSX).st_mtime + 5,) * 2)
assert w.poll() is None and gps_of(w.data) == {"CB6110": "10.4.90.77", "HB6300": "10.4.90.4"}

# kolom IP GPS pada query diutamakan daripada Excel
write([["CB6110", 101, "", "", "", "10.4.90.1"]])
q = mm.MapWatcher(source="sql", connect=fake(COLS + ["IP GPS"], [r + ("10.4.91.1" if r[0] == 101 else None,) for r in ROWS]), refresh_minutes=60)
assert gps_of(q.data) == {"CB6110": "10.4.91.1"}, gps_of(q.data)

# file Excel tidak ada -> sistem tetap jalan tanpa GPS; muncul belakangan -> terbaca
XLSX.unlink()
n = mm.MapWatcher(source="sql", connect=fake(), refresh_minutes=60)
assert gps_of(n.data) == {} and len(n.data[2]) == 12
write([["CB6110", 101, "", "", "", "10.4.90.5"]])
assert gps_of(n.poll()) == {"CB6110": "10.4.90.5"}

# SQL mati saat start: cache query dipakai dan GPS Excel tetap digabung
def boom(): raise OSError("SQL Server tidak terjangkau")
c = mm.MapWatcher(source="sql", connect=boom, refresh_minutes=60)
assert gps_of(c.data) == {"CB6110": "10.4.90.5"} and len(c.data[0]) == 4

# GPS_SOURCE=none -> GPS tidak dipantau
mm.GPS_SOURCE = "none"
assert gps_of(mm.MapWatcher(source="sql", connect=fake(), refresh_minutes=60).data) == {}
print("OK: IP GPS dari Excel digabung ke hasil query SQL")
