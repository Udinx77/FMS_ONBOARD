"""Flask-SocketIO dashboard yang mempertahankan pengambilan data SQL Server."""
from __future__ import annotations

import hmac
import io
import ipaddress
import logging
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pyodbc
from flask import Flask, Response, jsonify, render_template, request, send_file
from flask_socketio import SocketIO

from config import (
    ALLOWED_NETWORKS,
    AVAILABILITY_KEEP_MONTHS,
    AVAILABILITY_PURGE,
    GPS_ABSENT_IS_RED,
    BREAK_END,
    BREAK_START,
    CORS_ALLOWED_ORIGINS,
    DASHBOARD_PASSWORD,
    DASHBOARD_USER,
    DEVICE_TYPES,
    FLASK_HOST,
    FLASK_PORT,
    FLASK_SECRET_KEY,
    SQL_CONNECTION_TIMEOUT,
    SQL_DATABASE,
    SQL_DRIVER,
    SQL_ENCRYPT,
    SQL_PASSWORD,
    SQL_REFRESH_SECONDS,
    SQL_SERVER,
    SQL_TRUSTED_CONNECTION,
    SQL_TRUST_SERVER_CERTIFICATE,
    SQL_USERNAME,
    TIMEZONE_NAME,
)
from db import (
    add_months,
    counted_seconds_in_day,
    get_daily_availability_map,
    get_latest_scan,
    get_latest_status_map,
    initialize_db,
    list_month_status,
    mark_month_exported,
    record_bd_day,
)
from gps_metric import GpsCache, gps_error, gps_normal, gps_state
from monthly_report import build_monthly_excel, build_monthly_report
from machine_map import MapWatcher
from sql_server import connect as sql_connect
from availability_report import build_availability_report, build_excel_workbook, build_pdf_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("availability-dashboard")
LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)

app = Flask(__name__)
app.config["SECRET_KEY"] = FLASK_SECRET_KEY
socketio = SocketIO(
    app,
    cors_allowed_origins=CORS_ALLOWED_ORIGINS or None,  # None = hanya origin yang sama dengan server
    ping_timeout=60,
    ping_interval=25,
    async_mode="threading",
)

_ALLOWED_NETS = [ipaddress.ip_network(net, strict=False) for net in ALLOWED_NETWORKS]


def _access_denied() -> Response | None:
    """Response penolakan bila klien tidak boleh mengakses; None bila boleh. Dipakai HTTP dan Socket.IO."""
    if _ALLOWED_NETS:
        try:
            client = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            return Response("Forbidden", 403)
        if client.version == 6 and client.ipv4_mapped:
            client = client.ipv4_mapped
        if not any(client in net for net in _ALLOWED_NETS):
            LOGGER.warning("Akses ditolak dari %s (di luar ALLOWED_NETWORKS).", client)
            return Response("Forbidden", 403)
    if DASHBOARD_PASSWORD:
        auth = request.authorization
        ok = bool(auth) and hmac.compare_digest((auth.username or "").encode(), DASHBOARD_USER.encode()) \
            and hmac.compare_digest((auth.password or "").encode(), DASHBOARD_PASSWORD.encode())
        if not ok:
            return Response("Login diperlukan", 401, {"WWW-Authenticate": 'Basic realm="FMS Monitoring"'})
    return None


@app.before_request
def _guard():
    return _access_denied()


thread = None
thread_lock = threading.Lock()

# Sumber mapping unit: query SQL Server (MACHINE_SOURCE=sql, default) atau Excel; diperbarui otomatis tanpa restart.
_watcher = MapWatcher()
MACHINE_MAP, OID_LIST_STR = _watcher.data[0], _watcher.data[1]
_map_lock = threading.Lock()
initialize_db()


def refresh_machine_map():
    """Mapping unit berubah (query SQL atau Excel) -> dimuat ulang tanpa restart."""
    global MACHINE_MAP, OID_LIST_STR
    with _map_lock:
        data = _watcher.poll()
        if data:
            MACHINE_MAP, OID_LIST_STR = data[0], data[1]
            LOGGER.info("Mapping unit berubah; %s unit dimuat ulang.", len(MACHINE_MAP))


# Unit Breakdown (aturan sama dengan index.html) tidak dihitung di availability overall.
BD_CATEGORIES = {165096025, 165096022}
BD_CLASSES = {165097066, 165097069}
_bd = {"oids": set(), "at": 0.0}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _update_bd(rows):
    _bd["oids"] = {
        oid for row in rows
        if (oid := _to_int(row.get("MACHINE_OID"))) is not None
        and (_to_int(row.get("DELAYCATEGORY")) in BD_CATEGORIES
             or _to_int(row.get("DELAYCLASS_OID")) in BD_CLASSES)
    }
    _bd["at"] = time.time()


def get_bd_oids():
    """Cache dari polling dashboard; jika basi (>60 dtk, mis. tanpa klien) tanya SQL Server."""
    if time.time() - _bd["at"] > 60:
        rows = fetch_data_from_db(OID_LIST_STR)
        if rows is not None:
            _update_bd(rows)
        else:
            _bd["at"] = time.time()  # tahan percobaan ulang 60 dtk
    return _bd["oids"]


GPS = GpsCache(connect=sql_connect)  # nilai GPS dari query_gps.sql; nonaktif bila GPS_METRIC_COLUMN kosong
_bd_recorded: tuple = (None, frozenset())


def _record_bd_today() -> None:
    """Catat unit BD hari ini ke SQLite (dipakai laporan bulanan); hanya saat ada perubahan."""
    global _bd_recorded
    today = datetime.now(LOCAL_TZ).date().isoformat()
    current = (today, frozenset(_bd["oids"]))
    if current == _bd_recorded:
        return
    record_bd_day(today, _bd["oids"])
    _bd_recorded = current


def _report(report_date):
    refresh_machine_map()
    return build_availability_report(report_date, MACHINE_MAP, exclude_oids=get_bd_oids())


# QUERY SQL SERVER DIPERTAHANKAN DARI SCRIPT AWAL.
sql_query_template = """
WITH FilteredDelays AS (
    SELECT DISTINCT
        P.TARGET_MACHINE AS MACHINE_OID,
        J1.NAME AS DELAY_NAME,
        J1.DELAYCATEGORY,
        J1.DELAYCLASS_OID
    FROM
        [].[dbo].[DELAY] AS P
    LEFT JOIN
        [].[dbo].[DELAYCLASS] AS J1
        ON P.DELAYCLASS = J1.DELAYCLASS_OID
    LEFT JOIN
        [].[dbo].[DELAYCATEGORY] AS J3
        ON J1.DELAYCATEGORY = J3.DELAYCATEGORY_OID
    WHERE
        P.STATUS = 3
),
MachinesWithGPS AS (
    SELECT DISTINCT
        b.MACHINE_OID
    FROM
        [].[dbo].[MACHINE_ONBOARD_HARDWARE] AS hw
    INNER JOIN
        [].[dbo].[MACHINE] AS b
        ON hw.OID = b.MACHINE_OID
    WHERE
        hw.CONFIGURATION_NAME = 'gps'
)
SELECT
    a.MACHINE_OID,
    b.DELAY_NAME,
    b.DELAYCATEGORY,
    a.NAME,
    d.MACHINE_OID as GPSStatus,
    b.DELAYCLASS_OID
FROM
    [].[dbo].MACHINE AS a
LEFT JOIN
    FilteredDelays AS b
    ON a.MACHINE_OID = b.MACHINE_OID
LEFT JOIN
    MachinesWithGPS AS d
    ON d.MACHINE_OID = a.MACHINE_OID
WHERE
    a.MACHINE_OID IN ({oid_list_placeholder})
ORDER BY
    a.MACHINE_OID DESC;
"""


def fetch_data_from_db(oid_list_str: str):
    """Mengambil mesin, delay, kategori delay, delay class, dan GPS dari SQL Server."""
    if not oid_list_str:
        LOGGER.error("Daftar OID kosong; query SQL tidak dijalankan.")
        return None

    sql_query = sql_query_template.format(oid_list_placeholder=oid_list_str)
    conn = None
    cursor = None
    try:
        conn = sql_connect()
        cursor = conn.cursor()
        cursor.execute(sql_query)
        columns = [column[0] for column in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return results
    except (pyodbc.Error, RuntimeError) as exc:
        LOGGER.error("Gagal mengambil data SQL Server: %s", exc)
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _enrich_sql_rows(db_rows: list[dict]) -> list[dict]:
    """Gabungkan row SQL dengan status dan availability dari SQLite."""
    now_ts = time.time()
    now_local = datetime.now(LOCAL_TZ)
    today = now_local.date().isoformat()
    # Denominator coverage: waktu berjalan hari ini tanpa jam istirahat.
    seconds_elapsed_today = max(1.0, counted_seconds_in_day(now_local.date(), until=now_local))

    latest_status = get_latest_status_map()
    daily_availability = get_daily_availability_map(today)
    final_data: list[dict] = []

    for source_row in db_rows:
        row = {key: _json_safe(value) for key, value in source_row.items()}
        try:
            oid = int(row["MACHINE_OID"])
        except (KeyError, TypeError, ValueError):
            oid = None
        machine_info = MACHINE_MAP.get(oid) if oid is not None else None
        just_updated = False

        for device_type in DEVICE_TYPES:
            ip_address = machine_info.get(device_type) if machine_info else None
            status_item = (
                latest_status.get((oid, device_type, ip_address))
                if oid is not None and ip_address else None
            )
            availability_item = (
                daily_availability.get((oid, device_type, ip_address))
                if oid is not None and ip_address else None
            )

            # Nama field status/fail_mins dipertahankan agar kompatibel dengan UI lama.
            row[f"{device_type}_ip"] = ip_address
            row[f"{device_type}_status"] = status_item["status"] if status_item else "N/A"
            fail_since = status_item.get("fail_since") if status_item else None
            row[f"{device_type}_fail_mins"] = (
                max(0, int((now_ts - float(fail_since)) / 60)) if fail_since else 0
            )
            row[f"{device_type}_packet_loss"] = (
                float(status_item["packet_loss_pct"]) if status_item else None
            )
            row[f"{device_type}_packets_sent"] = (
                int(status_item["packets_sent"]) if status_item else None
            )
            row[f"{device_type}_packets_received"] = (
                int(status_item["packets_received"]) if status_item else None
            )
            row[f"{device_type}_avg_rtt"] = (
                status_item["avg_rtt_ms"] if status_item else None
            )
            row[f"{device_type}_last_check"] = (
                datetime.fromtimestamp(float(status_item["last_checked_at"]), LOCAL_TZ).isoformat()
                if status_item else None
            )
            row[f"{device_type}_availability"] = (
                round(availability_item["availability_pct"], 2)
                if availability_item and availability_item["availability_pct"] is not None
                else None
            )
            row[f"{device_type}_coverage"] = (
                round(
                    min(100.0, availability_item["observed_seconds"] / seconds_elapsed_today * 100.0),
                    2,
                )
                if availability_item else 0.0
            )
            row[f"{device_type}_uptime_seconds"] = (
                round(availability_item["uptime_seconds"], 1) if availability_item else 0.0
            )
            row[f"{device_type}_downtime_seconds"] = (
                round(availability_item["downtime_seconds"], 1) if availability_item else 0.0
            )
            row[f"{device_type}_observed_seconds"] = (
                round(availability_item["observed_seconds"], 1) if availability_item else 0.0
            )

            if status_item:
                age = now_ts - float(status_item["last_checked_at"])
                if 0 <= age <= 75:
                    just_updated = True

        if not machine_info:
            for device_type in DEVICE_TYPES:
                row[f"{device_type}_status"] = "N/A"
                row[f"{device_type}_fail_mins"] = 0
                row[f"{device_type}_availability"] = None
                row[f"{device_type}_coverage"] = 0.0

        # Satelit GPS (query_gps.sql): lampu G dan filter GPS di dashboard. Unit tanpa data satelit memakai ping GPS.
        gps_found, gps_value = GPS.get(_to_int(row.get("MACHINE_OID")), row.get("NAME") or (machine_info or {}).get("Name"))
        if not gps_found and GPS_ABSENT_IS_RED and GPS.enabled and GPS.ok:
            gps_found = True  # unit tidak muncul di hasil query dianggap tanpa data satelit (merah)
        row["GPS_VALUE"] = gps_value if gps_found else None
        row["GPS_STATE"] = gps_state(gps_value, gps_found)
        row["GPS_NORMAL"] = gps_normal(gps_found, gps_value, bool(row.get("G_ip")), row.get("G_status"))
        row["GPS_ERROR"] = gps_error(gps_found, gps_value, bool(row.get("G_ip")), row.get("G_status"))

        row["just_updated"] = just_updated
        final_data.append(row)

    return final_data


def emit_overall_availability():
    """Kirim agregat availability langsung dari SQLite melalui Socket.IO.

    Dashboard tidak perlu memanggil endpoint JSON terpisah; event ini independen
    dari keberhasilan query SQL Server.
    """
    report = _report(datetime.now(LOCAL_TZ).date())
    socketio.emit("availability_overall", {
        "date": report["date"],
        "timezone": report["timezone"],
        "overall": report["overall"],
        "unit_count": report["unit_count"],
        "excluded_count": report["excluded_count"],
        "break": f"{BREAK_START:%H:%M}-{BREAK_END:%H:%M}",
    })


def emit_collector_status():
    """Scan terakhir collector (SUCCESS/FAILED + pesan) supaya dashboard bisa menampilkan penyebab data tidak diperbarui."""
    scan = get_latest_scan()
    socketio.emit("collector_status", {
        "status": scan["scan_status"] if scan else None,
        "finished_at": scan["finished_at"] if scan else None,
        "error": (scan.get("error_message") or "") if scan else "",
    })


def data_updater_task():
    """Polling SQL Server dan SQLite; event database_data dipertahankan."""
    LOGGER.info("Background task dashboard dimulai.")
    while True:
        cycle_started = time.monotonic()

        # Kirim persentase dari SQLite terlebih dahulu; tidak menunggu query SQL Server.
        try:
            emit_overall_availability()
        except Exception:
            LOGGER.exception("Gagal mengirim overall availability dari SQLite.")
        try:
            emit_collector_status()
        except Exception:
            LOGGER.exception("Gagal mengirim status collector.")

        try:
            db_rows = fetch_data_from_db(OID_LIST_STR)
            if db_rows is None:
                socketio.emit("database_error", {
                    "message": "Gagal mengambil data dari SQL Server. Periksa koneksi dan konfigurasi."
                })
            else:
                _update_bd(db_rows)
                try:
                    _record_bd_today()
                except Exception:
                    LOGGER.exception("Gagal mencatat unit BD harian.")
                GPS.refresh_async()
                final_data = _enrich_sql_rows(db_rows)
                socketio.emit("database_data", final_data)
                LOGGER.info("Mengirim %s baris data SQL + status SQLite ke dashboard.", len(final_data))
        except Exception:
            LOGGER.exception("Kesalahan pada background task dashboard.")
            socketio.emit("database_error", {"message": "Terjadi kesalahan pada proses pembaruan dashboard."})

        elapsed = time.monotonic() - cycle_started
        socketio.sleep(max(0.5, SQL_REFRESH_SECONDS - elapsed))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/availability/today")
def api_availability_today():
    """Endpoint tambahan untuk UI/reporter; tidak mengganti event lama."""
    now_local = datetime.now(LOCAL_TZ)
    data = get_daily_availability_map(now_local.date().isoformat())
    rows = []
    for (oid, device_type, ip_address), item in data.items():
        rows.append({
            "machine_oid": oid,
            "device_type": device_type,
            "ip_address": ip_address,
            "uptime_seconds": round(item["uptime_seconds"], 1),
            "downtime_seconds": round(item["downtime_seconds"], 1),
            "observed_seconds": round(item["observed_seconds"], 1),
            "availability_pct": round(item["availability_pct"], 2)
            if item["availability_pct"] is not None else None,
        })
    return jsonify({"date": now_local.date().isoformat(), "timezone": TIMEZONE_NAME, "data": rows})


@app.route("/api/status/latest")
def api_status_latest():
    status_map = get_latest_status_map()
    rows = []
    for (oid, device_type, ip_address), item in status_map.items():
        rows.append({
            "machine_oid": oid,
            "machine_name": item["machine_name"],
            "device_type": device_type,
            "ip_address": ip_address,
            "status": item["status"],
            "packet_loss_pct": item["packet_loss_pct"],
            "packets_sent": item["packets_sent"],
            "packets_received": item["packets_received"],
            "avg_rtt_ms": item["avg_rtt_ms"],
            "last_checked_at": datetime.fromtimestamp(
                float(item["last_checked_at"]), LOCAL_TZ
            ).isoformat(),
        })
    return jsonify({"data": rows})



def _requested_report_date():
    """Parse tanggal laporan; default ke tanggal lokal hari ini."""
    raw_date = request.args.get("date")
    today = datetime.now(LOCAL_TZ).date()
    if not raw_date:
        return today
    try:
        parsed = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError("Format tanggal harus YYYY-MM-DD.") from exc
    if parsed > today:
        raise ValueError("Tanggal laporan tidak boleh berada di masa depan.")
    return parsed


@app.route("/api/availability/summary")
def api_availability_summary():
    """Ringkasan weighted availability R/D/P dan rincian semua unit."""
    try:
        report_date = _requested_report_date()
        report = _report(report_date)
        return jsonify(report)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        LOGGER.exception("Gagal menyusun ringkasan availability.")
        return jsonify({"error": "Gagal membaca ringkasan availability dari SQLite."}), 500


@app.route("/report/sqlite")
def sqlite_status_report():
    """Halaman laporan detail status SQLite; tidak memakai endpoint JSON."""
    try:
        report_date = _requested_report_date()
        report = _report(report_date)
        return render_template("sqlite_report.html", report=report, monthly=_monthly_context())
    except ValueError as exc:
        return str(exc), 400
    except Exception:
        LOGGER.exception("Gagal membuka halaman laporan SQLite.")
        return "Gagal membaca laporan SQLite. Periksa log aplikasi.", 500


def _monthly_context() -> dict:
    """Data kotak export bulanan pada halaman laporan: bulan yang punya data dan status ekspornya."""
    today = datetime.now(LOCAL_TZ).date()
    current = f"{today.year:04d}-{today.month:02d}"
    months = []
    for item in list_month_status():
        stamp = item["exported_at"]
        months.append({
            **item, "final": item["month"] < current,
            "exported_label": datetime.fromtimestamp(stamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M") if stamp else None,
        })
    return {"current": current, "default": add_months(current, -1), "months": months,
            "pending": [m["month"] for m in months if m["final"] and not m["exported_at"]],
            "keep": AVAILABILITY_KEEP_MONTHS, "purge": AVAILABILITY_PURGE}


EXPORT_CACHE_SECONDS = 30
_export_cache: dict[tuple, tuple[float, bytes]] = {}
_export_lock = threading.Lock()


def _cached_export(kind: str, report_date: date, build) -> bytes:
    """Export yang sama dalam 30 detik dilayani dari cache; pembuatan diserialkan agar CPU tidak habis."""
    key = (kind, report_date)
    with _export_lock:
        hit = _export_cache.get(key)
        if hit and time.time() - hit[0] < EXPORT_CACHE_SECONDS:
            return hit[1]
        data = build()
        _export_cache[key] = (time.time(), data)
        for old_key in [k for k, v in _export_cache.items() if time.time() - v[0] > 300]:
            _export_cache.pop(old_key, None)
        return data


@app.route("/export/availability.pdf")
def export_availability_pdf():
    """Unduh laporan PDF keseluruhan availability dan rincian status SQLite."""
    try:
        report_date = _requested_report_date()
        data = _cached_export("pdf", report_date, lambda: build_pdf_report(_report(report_date)).getvalue())
        filename = f"availability_sqlite_report_{report_date.isoformat()}.pdf"
        return send_file(
            io.BytesIO(data),
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )
    except ValueError as exc:
        return str(exc), 400
    except Exception:
        LOGGER.exception("Gagal membuat export PDF availability.")
        return "Gagal membuat PDF. Periksa log aplikasi dan pastikan reportlab terpasang.", 500


@app.route("/export/availability.xlsx")
def export_availability_excel():
    """Unduh workbook rekap keseluruhan dan rincian availability per unit."""
    try:
        report_date = _requested_report_date()
        data = _cached_export("xlsx", report_date, lambda: build_excel_workbook(_report(report_date)).getvalue())
        filename = f"availability_report_{report_date.isoformat()}.xlsx"
        return send_file(
            io.BytesIO(data),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        LOGGER.exception("Gagal membuat export availability.")
        return jsonify({"error": "Gagal membuat file Excel availability."}), 500


@app.route("/export/availability_monthly.xlsx")
def export_availability_monthly():
    """Rekap availability ONB satu bulan (?month=YYYY-MM). Bulan yang sudah lewat dicatat sebagai sudah diekspor (syarat penghapusan otomatis)."""
    month = (request.args.get("month") or "").strip()
    try:
        refresh_machine_map()
        info: dict = {}

        def build() -> bytes:
            report = build_monthly_report(month, MACHINE_MAP)
            info["rows"] = report["row_count"]
            return build_monthly_excel(report).getvalue()

        data = _cached_export("xlsx_month", month, build)
        today = datetime.now(LOCAL_TZ).date()
        if month < f"{today.year:04d}-{today.month:02d}":
            mark_month_exported(month, info.get("rows", 0))
        return send_file(
            io.BytesIO(data),
            as_attachment=True,
            download_name=f"availability_ONB_{month}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        LOGGER.exception("Gagal membuat export availability bulanan.")
        return jsonify({"error": "Gagal membuat file Excel availability bulanan."}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "latest_scan": get_latest_scan()})


@socketio.on("connect")
def handle_connect():
    global thread
    if _access_denied() is not None:
        return False  # tolak koneksi Socket.IO; data tidak dikirim
    with thread_lock:
        if thread is None:
            LOGGER.info("Klien pertama terhubung; memulai polling SQL Server.")
            thread = socketio.start_background_task(target=data_updater_task)
    LOGGER.info("Klien dashboard terhubung.")


if __name__ == "__main__":
    LOGGER.info("Menjalankan dashboard di http://%s:%s", FLASK_HOST, FLASK_PORT)
    # use_reloader=False mencegah background task ganda saat mode development.
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
