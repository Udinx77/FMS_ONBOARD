"""Tampilkan nama interfaceName yang tersedia pada VM_MACHINE_COMMUNICATIONS MineStar."""
from __future__ import annotations

import logging
import sys

from minestar_source import fetch_distinct_interface_names

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def main() -> int:
    try:
        names = fetch_distinct_interface_names()
    except Exception as exc:
        logging.exception("Gagal membaca daftar interfaceName MineStar: %s", exc)
        return 1

    print("InterfaceName yang tersedia di MineStar:")
    for name in names:
        print(f" - {name}")
    if not names:
        print("Tidak ada interfaceName yang ditemukan.")
        return 2
    print("\nGunakan nilai yang sesuai pada MINSTAR_GPS_INTERFACE_NAME di file .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
