"""Cek cepat sumber mapping SQL (tanpa SQL Server asli): python test_sql_source.py"""
import os, sys, tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["MACHINE_CACHE_FILE"] = str(tmp / "cache.json")
sys.path.insert(0, ".")
import machine_map as mm

COLS = ["OID", "SN Unit", "IP WGB Radio", "IP Display", "IP PLE"]
ROWS = [(165098101, "CB6110", "10.4.25.21", "10.4.25.22", "10.4.25.23"),
        (165098102, "CT7261", "10.4.21.247", "10.4.21.248", None)]          # unit tanpa PLE


class FakeCursor:
    def __init__(self, columns, rows): self.description, self._rows = [(c,) for c in columns], rows
    def execute(self, sql): assert "SELECT" in sql.upper()
    def fetchall(self): return self._rows
    def close(self): pass

class FakeConn:
    def __init__(self, columns, rows): self._c, self._r = columns, rows
    def cursor(self): return FakeCursor(self._c, self._r)
    def close(self): pass

def fake(columns=COLS, rows=ROWS): return lambda: FakeConn(columns, rows)
def boom():
    raise OSError("SQL Server tidak terjangkau")

# 1. baris query -> mapping
oid_map, oid_list, targets, ips = mm.load_machine_map_sql(fake())
assert oid_map[165098101] == {"Name": "CB6110", "R": "10.4.25.21", "D": "10.4.25.22", "P": "10.4.25.23", "G": None}
assert len(targets) == 5 and len(ips) == 5 and oid_list == "165098101,165098102"

# 2. kolom OID hilang -> pesan jelas
try:
    mm.load_machine_map_sql(fake(COLS[1:], [r[1:] for r in ROWS]))
    raise AssertionError("seharusnya gagal")
except ValueError as exc:
    assert "OID" in str(exc)

# 3. kolom GPS opsional ikut dibaca
_, _, t, _ = mm.load_machine_map_sql(fake(COLS + ["IP GPS"], [r + ("10.4.90.1",) for r in ROWS]))
assert sum(x["device_type"] == "G" for x in t) == 2

# 4. start tanpa SQL: tanpa cache -> error; setelah sekali sukses -> pakai cache
try:
    mm.MapWatcher(source="sql", connect=boom); raise AssertionError("seharusnya gagal")
except RuntimeError as exc:
    assert "cache" in str(exc)
w = mm.MapWatcher(source="sql", connect=fake(), refresh_minutes=0)          # sukses -> cache tertulis
w2 = mm.MapWatcher(source="sql", connect=boom)                              # SQL mati -> cache dipakai
assert len(w2.data[0]) == 2

# 5. poll: tidak berubah -> None; unit baru -> terbaca; gagal -> data lama; menyusut >50% -> ditolak
assert w.poll() is None
more = ROWS + [(165098103, "KB6201", "10.4.22.1", "10.4.22.2", "10.4.22.3")]
w._connect = fake(rows=more); assert len(w.poll()[0]) == 3
w._connect = boom; assert w.poll() is None and len(w.data[0]) == 3
big = [(1000 + i, f"CB{6000+i}", f"10.5.0.{i}", f"10.5.1.{i}", f"10.5.2.{i}") for i in range(1, 21)]
w._connect = fake(rows=big); assert len(w.poll()[0]) == 20
w._connect = fake(rows=big[:5]); assert w.poll() is None and len(w.data[0]) == 20      # turun dari 20 ke 5 -> ditolak
print("OK: sumber mapping SQL, cache, dan auto-refresh")
