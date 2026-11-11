"""Cek cepat backup_db: python test_backup.py"""
import sqlite3, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, ".")
import backup_db
tmp = Path(tempfile.mkdtemp()); src = tmp / "monitoring.db"
c = sqlite3.connect(src); c.execute("PRAGMA journal_mode=WAL"); c.execute("CREATE TABLE t(x)"); c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(1000)]); c.commit()
for _ in range(4):
    out = backup_db.backup(src, tmp / "b", keep=3); time.sleep(1.05)
files = sorted((tmp / "b").glob("monitoring_*.db"))
assert len(files) == 3 and out in files                                   # yang lama dihapus
assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1000
print("OK: backup SQLite online dan rotasi")
