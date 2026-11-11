"""Cek cepat hot-reload Excel: python test_machine_map.py"""
import os, tempfile
from pathlib import Path
import pandas as pd
from machine_map import load_if_changed

def write(path, rows):
    pd.DataFrame(rows, columns=["Name", "OID", "R", "D", "P"]).to_excel(path, index=False)

with tempfile.TemporaryDirectory() as tmp:
    xlsx = Path(tmp) / "map.xlsx"
    write(xlsx, [["CT1", 1, "10.0.0.1", "10.0.0.2", "10.0.0.3"]])
    data, mtime = load_if_changed(None, xlsx)
    assert len(data[0]) == 1 and len(data[3]) == 3             # muat pertama
    assert load_if_changed(mtime, xlsx) == (None, mtime)        # tidak berubah -> tidak dimuat ulang
    write(xlsx, [["CT1", 1, "10.0.0.1", "10.0.0.2", "10.0.0.3"], ["CT2", 2, "10.0.0.4", "10.0.0.5", "10.0.0.6"]])
    os.utime(xlsx, (mtime + 5, mtime + 5))
    data, mtime2 = load_if_changed(mtime, xlsx)
    assert len(data[0]) == 2 and len(data[3]) == 6 and mtime2 != mtime   # unit baru terbaca
    xlsx.write_bytes(b"rusak"); os.utime(xlsx, (mtime2 + 5, mtime2 + 5))
    assert load_if_changed(mtime2, xlsx) == (None, mtime2)      # file rusak -> data lama dipertahankan
print("OK: hot-reload Excel")
