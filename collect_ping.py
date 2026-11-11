"""Async ICMP collector untuk IP R/D/P dan GPS opsional; terpisah dari Flask app."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path

from icmplib import async_multiping

from config import (
    BASE_DIR,
    CANARY_IPS,
    CONCURRENT_TASKS,
    FAILURE_LOSS_THRESHOLD_PERCENT,
    MAX_OBSERVATION_GAP_SECONDS,
    PING_COUNT,
    PING_INTERVAL_SECONDS,
    PING_TIMEOUT_SECONDS,
    AVAILABILITY_KEEP_MONTHS,
    AVAILABILITY_PURGE,
    PING_HISTORY_RETENTION_DAYS,
    RED_AFTER_MINUTES,
    SCAN_INTERVAL_SECONDS,
    TRY_PRIVILEGED_ICMP_FIRST,
)
from db import cleanup_old_history, initialize_db, purge_exported_months, record_failed_scan, save_scan_results
from machine_map import MapWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("ping-collector")


def host_to_result(host, address: str) -> dict:
    sent = int(getattr(host, "packets_sent", PING_COUNT) or PING_COUNT)
    received = int(getattr(host, "packets_received", 0) or 0)
    raw_loss = getattr(host, "packet_loss", 1.0)
    loss_ratio = 1.0 if raw_loss is None else float(raw_loss)
    loss_pct = min(100.0, max(0.0, loss_ratio * 100.0))

    def metric(name: str):
        value = getattr(host, name, None)
        if value is None or received == 0:
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return round(value, 3)

    return {
        "ip_address": address,
        "packets_sent": sent,
        "packets_received": received,
        "packet_loss_pct": round(loss_pct, 2),
        "avg_rtt_ms": metric("avg_rtt"),
        "min_rtt_ms": metric("min_rtt"),
        "max_rtt_ms": metric("max_rtt"),
        "jitter_ms": metric("jitter"),
    }


class CanaryFailed(Exception):
    """Semua canary tidak merespons: masalah ada di jaringan PC monitor, bukan di perangkat unit."""


def check_canary(results: dict, canary_ips: list[str]) -> None:
    if canary_ips and all(results[ip]["packet_loss_pct"] >= 100 for ip in canary_ips):
        raise CanaryFailed(f"semua canary tidak merespons ({', '.join(canary_ips)})")


async def ping_all(unique_ips: list[str]) -> dict[str, dict]:
    """Periksa semua IP dengan concurrency dibatasi; fallback raw ke unprivileged."""
    if not unique_ips:
        return {}

    options = {
        "count": PING_COUNT,
        "interval": PING_INTERVAL_SECONDS,
        "timeout": PING_TIMEOUT_SECONDS,
        "concurrent_tasks": CONCURRENT_TASKS,
    }
    hosts = None
    privileged_error = None

    if TRY_PRIVILEGED_ICMP_FIRST:
        try:
            hosts = await async_multiping(unique_ips, privileged=True, **options)
        except Exception as exc:
            privileged_error = exc
            LOGGER.warning("Ping privileged gagal (%s); mencoba unprivileged.", exc)

    if hosts is None:
        try:
            hosts = await async_multiping(unique_ips, privileged=False, **options)
        except Exception as exc:
            if privileged_error:
                raise RuntimeError(
                    "Mode ICMP privileged dan unprivileged gagal. "
                    f"Privileged: {privileged_error}; unprivileged: {exc}"
                ) from exc
            raise

    result_map: dict[str, dict] = {}
    for host in hosts:
        address = str(ipaddress.ip_address(host.address))
        result_map[address] = host_to_result(host, address)

    # Pastikan setiap target memiliki hasil. Target yang tidak dikembalikan library
    # dicatat sebagai 100% loss hanya jika keseluruhan pemanggilan ping berhasil.
    for address in unique_ips:
        if address not in result_map:
            result_map[address] = {
                "ip_address": address,
                "packets_sent": PING_COUNT,
                "packets_received": 0,
                "packet_loss_pct": 100.0,
                "avg_rtt_ms": None,
                "min_rtt_ms": None,
                "max_rtt_ms": None,
                "jitter_ms": None,
            }
    return result_map


async def run_collector() -> None:
    watcher = MapWatcher()  # query SQL Server (default) atau Excel; diperbarui otomatis tanpa restart
    _, _, targets, unique_ips = watcher.data
    initialize_db()
    deleted_rows, deleted_scans = cleanup_old_history(PING_HISTORY_RETENTION_DAYS)
    if deleted_rows or deleted_scans:
        LOGGER.info("Retensi database: %s histori dan %s scan lama dihapus.", deleted_rows, deleted_scans)
    last_cleanup_at = time.time()
    last_purge_at = 0.0

    LOGGER.info(
        "Collector aktif | IP unik=%s | mapping=%s | jeda setelah scan selesai=%ss | "
        "paket=%s | timeout=%ss | concurrency=%s",
        len(unique_ips), len(targets), SCAN_INTERVAL_SECONDS, PING_COUNT,
        PING_TIMEOUT_SECONDS, CONCURRENT_TASKS,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_stop(*_):
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows tidak mendukung add_signal_handler untuk semua signal.
            pass

    while not stop_event.is_set():
        # Mapping unit berubah (query SQL atau Excel) saat collector jalan -> daftar IP ikut diperbarui, tanpa restart.
        reloaded = watcher.poll()
        if reloaded:
            _, _, targets, unique_ips = reloaded
            LOGGER.info("Mapping unit berubah; target dimuat ulang | IP unik=%s | mapping=%s", len(unique_ips), len(targets))

        cycle_start_mono = loop.time()
        cycle_started_at = time.time()  # timestamp kalender untuk SQLite
        LOGGER.info("Memulai pemeriksaan seluruh %s IP unik...", len(unique_ips))

        try:
            ping_list = list(unique_ips) + [ip for ip in CANARY_IPS if ip not in set(unique_ips)]
            results = await ping_all(ping_list)
            check_canary(results, CANARY_IPS)
            finished_at = time.time()
            db_rows = [
                {**target, **results[target["ip_address"]]}
                for target in targets
            ]

            scan_id = save_scan_results(
                started_at=cycle_started_at,
                finished_at=finished_at,
                target_count=len(targets),
                rows=db_rows,
                failure_threshold_percent=FAILURE_LOSS_THRESHOLD_PERCENT,
                red_after_minutes=RED_AFTER_MINUTES,
                max_observation_gap_seconds=MAX_OBSERVATION_GAP_SECONDS,
            )

            duration = loop.time() - cycle_start_mono
            available = sum(
                1 for item in db_rows
                if item["packet_loss_pct"] <= FAILURE_LOSS_THRESHOLD_PERCENT
            )
            unavailable = len(db_rows) - available
            LOGGER.info(
                "Scan #%s selesai dalam %.2fs | mapping=%s | available=%s | gangguan=%s",
                scan_id, duration, len(db_rows), available, unavailable,
            )
        except asyncio.CancelledError:
            raise
        except CanaryFailed as exc:
            finished_at = time.time()
            LOGGER.warning("Scan dibatalkan: %s. Jaringan PC monitor bermasalah; status dan availability tidak diubah.", exc)
            try:
                record_failed_scan(started_at=cycle_started_at, finished_at=finished_at,
                                   target_count=len(targets), error_message=f"CANARY: {exc}")
            except Exception:
                LOGGER.exception("Gagal menyimpan catatan scan yang dibatalkan ke SQLite.")
        except Exception as exc:
            finished_at = time.time()
            LOGGER.exception("Scan gagal. Status perangkat tidak diubah.")
            try:
                record_failed_scan(
                    started_at=cycle_started_at,
                    finished_at=finished_at,
                    target_count=len(targets),
                    error_message=str(exc),
                )
            except Exception:
                LOGGER.exception("Gagal menyimpan catatan scan yang gagal ke SQLite.")

        # Retensi bulanan: hapus data availability bulan yang SUDAH diekspor dan lebih tua dari AVAILABILITY_KEEP_MONTHS bulan.
        if AVAILABILITY_PURGE and time.time() - last_purge_at >= 86400:
            last_purge_at = time.time()
            try:
                for month, counts in purge_exported_months(AVAILABILITY_KEEP_MONTHS).items():
                    LOGGER.info(
                        "Retensi bulanan %s (sudah diekspor): %s availability, %s BD, %s downtime dihapus.",
                        month, counts["availability"], counts["bd"], counts["downtime"],
                    )
            except Exception:
                LOGGER.exception("Retensi bulanan gagal; collector tetap berjalan.")

        # Bersihkan histori mentah setiap 24 jam selama collector tetap berjalan.
        if PING_HISTORY_RETENTION_DAYS > 0 and time.time() - last_cleanup_at >= 86400:
            try:
                old_rows, old_scans = cleanup_old_history(PING_HISTORY_RETENTION_DAYS)
                LOGGER.info(
                    "Retensi database: %s histori dan %s scan lama dihapus.",
                    old_rows, old_scans,
                )
            except Exception:
                LOGGER.exception("Pembersihan histori lama gagal; collector tetap berjalan.")
            last_cleanup_at = time.time()

        # Scan berikutnya dimulai SCAN_INTERVAL_SECONDS setelah scan ini SELESAI (bukan sejak dimulai).
        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    LOGGER.info("Collector dihentikan.")


def acquire_single_instance_lock():
    """Mencegah dua collector mem-ping IP yang sama secara bersamaan."""
    lock_path = BASE_DIR / "collector.lock"
    handle = open(lock_path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError(
            "Collector lain kemungkinan masih berjalan. Pastikan hanya satu "
            "instance collect_ping.py yang aktif."
        ) from exc


def release_single_instance_lock(handle) -> None:
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


def main() -> int:
    lock_handle = None
    try:
        lock_handle = acquire_single_instance_lock()
        asyncio.run(run_collector())
        return 0
    except KeyboardInterrupt:
        LOGGER.info("Collector dihentikan oleh pengguna.")
        return 0
    except Exception:
        LOGGER.exception("Collector tidak dapat dijalankan.")
        return 1
    finally:
        release_single_instance_lock(lock_handle)


if __name__ == "__main__":
    sys.exit(main())
