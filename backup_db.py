"""Backup monitoring.db saat collector dan dashboard tetap berjalan: python backup_db.py

Jadwalkan harian (Windows Task Scheduler). Menyimpan ke folder backups/ dan menghapus yang lebih lama dari BACKUP_KEEP file.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from config import BASE_DIR, DB_FILE


def backup(db_file: Path = DB_FILE, folder: Path | None = None, keep: int | None = None) -> Path:
    folder = Path(folder or os.getenv("BACKUP_DIR", str(BASE_DIR / "backups")))
    keep = keep if keep is not None else int(os.getenv("BACKUP_KEEP", "14"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"monitoring_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(db_file)
    copy = sqlite3.connect(target)
    try:
        with copy:
            source.backup(copy)  # salinan konsisten walau database sedang ditulis (WAL)
    finally:
        copy.close()
        source.close()
    for old in sorted(folder.glob("monitoring_*.db"))[:-keep] if keep > 0 else []:
        old.unlink()
    return target


if __name__ == "__main__":
    if not Path(DB_FILE).exists():
        sys.exit(f"Database tidak ditemukan: {DB_FILE}")
    print(f"Backup selesai: {backup()}")
