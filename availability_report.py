"""Menyusun rekap availability keseluruhan dan per unit dari SQLite."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from config import DB_FILE, DEVICE_NAMES, DEVICE_TYPES, OVERALL_DEVICE_TYPES, TIMEZONE_NAME
from db import counted_seconds_in_day, get_daily_availability_map, get_latest_status_map, get_latest_scan

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)


def report_period_seconds(report_date: date, now: datetime | None = None) -> float:
    """Durasi periode yang menjadi denominator coverage (jam istirahat tidak dihitung)."""
    now = now or datetime.now(LOCAL_TZ)
    return counted_seconds_in_day(report_date, until=now)


def _availability(uptime: float, observed: float) -> float | None:
    return (uptime / observed * 100.0) if observed > 0 else None


def _coverage(observed: float, expected: float) -> float:
    if expected <= 0:
        return 0.0
    return min(100.0, max(0.0, observed / expected * 100.0))


def _device_metrics(
    machine_oid: int,
    device_type: str,
    ip_address: str | None,
    availability_map: dict,
    latest_status_map: dict,
    period_seconds: float,
) -> dict[str, Any]:
    key = (machine_oid, device_type, ip_address)
    item = availability_map.get(key) if ip_address else None
    status_item = latest_status_map.get(key) if ip_address else None
    uptime = float(item["uptime_seconds"]) if item else 0.0
    downtime = float(item["downtime_seconds"]) if item else 0.0
    observed = float(item["observed_seconds"]) if item else 0.0
    last_checked = None
    if status_item and status_item.get("last_checked_at") is not None:
        last_checked = datetime.fromtimestamp(
            float(status_item["last_checked_at"]), LOCAL_TZ
        ).isoformat(timespec="seconds")

    return {
        "device_type": device_type,
        "ip_address": ip_address,
        "status": status_item.get("status", "N/A") if status_item else "N/A",
        "is_available": bool(status_item["is_available"]) if status_item else None,
        "packet_loss_pct": float(status_item["packet_loss_pct"]) if status_item else None,
        "packets_sent": int(status_item["packets_sent"]) if status_item else None,
        "packets_received": int(status_item["packets_received"]) if status_item else None,
        "avg_rtt_ms": float(status_item["avg_rtt_ms"]) if status_item and status_item["avg_rtt_ms"] is not None else None,
        "last_checked_at": last_checked,
        "uptime_seconds": uptime,
        "downtime_seconds": downtime,
        "observed_seconds": observed,
        "availability_pct": _availability(uptime, observed),
        "coverage_pct": _coverage(observed, period_seconds) if ip_address else 0.0,
    }


def build_availability_report(
    report_date: date,
    machine_map: dict[int, dict[str, Any]],
    db_path=DB_FILE,
    exclude_oids: set[int] | None = None,
) -> dict[str, Any]:
    """Agregasi weighted per tipe perangkat, semua perangkat, dan setiap unit.

    Availability = total uptime / total observed seconds. Coverage dihitung
    terhadap seluruh periode kalender yang tersedia untuk tanggal laporan.
    Unit/IP tanpa histori tetap disertakan sebagai N/A dengan coverage 0%.
    """
    exclude_oids = exclude_oids or set()  # unit Breakdown: tampil di rincian, tidak masuk overall
    now = datetime.now(LOCAL_TZ)
    if report_date > now.date():
        raise ValueError("Tanggal laporan tidak boleh berada di masa depan.")

    # Semua tipe yang benar-benar dikonfigurasi ditampilkan dalam rincian perangkat.
    # Availability overall/gabungan unit hanya memakai R/D/P; GPS tetap menjadi rincian.
    types = tuple(t for t in DEVICE_TYPES if any(m.get(t) for m in machine_map.values()))
    overall_types = tuple(
        t for t in OVERALL_DEVICE_TYPES if any(m.get(t) for m in machine_map.values())
    )
    period_seconds = report_period_seconds(report_date, now)
    availability_map = get_daily_availability_map(report_date.isoformat(), db_path)
    latest_status_map = get_latest_status_map(db_path)
    latest_scan = get_latest_scan(db_path)

    # Agregasi overall hanya untuk R/D/P. Tipe G (GPS) tidak boleh masuk denominator
    # maupun numerator overall, tetapi metrik GPS tetap tersedia di unit["devices"].
    aggregate: dict[str, dict[str, float | int]] = {
        key: {
            "target_count": 0,
            "devices_with_data": 0,
            "uptime_seconds": 0.0,
            "downtime_seconds": 0.0,
            "observed_seconds": 0.0,
        }
        for key in overall_types
    }

    unit_rows: list[dict[str, Any]] = []
    for oid, machine in sorted(machine_map.items(), key=lambda pair: (str(pair[1].get("Name", "")), pair[0])):
        name = str(machine.get("Name") or f"OID-{oid}")
        devices: dict[str, dict[str, Any]] = {}
        unit_uptime = unit_downtime = unit_observed = 0.0
        configured_device_count = 0  # jumlah target R/D/P untuk coverage availability gabungan unit
        excluded = oid in exclude_oids

        for device_type in types:
            ip_address = machine.get(device_type)
            metric = _device_metrics(oid, device_type, ip_address, availability_map, latest_status_map, period_seconds)
            devices[device_type] = metric

            # GPS diproses dan disimpan sebagai rincian, tetapi tidak dijumlahkan ke total R/D/P.
            if device_type not in overall_types:
                continue

            if ip_address:
                configured_device_count += 1
            if not excluded:
                if ip_address:
                    aggregate[device_type]["target_count"] += 1
                if metric["observed_seconds"] > 0:
                    aggregate[device_type]["devices_with_data"] += 1
                aggregate[device_type]["uptime_seconds"] += metric["uptime_seconds"]
                aggregate[device_type]["downtime_seconds"] += metric["downtime_seconds"]
                aggregate[device_type]["observed_seconds"] += metric["observed_seconds"]

            # Availability gabungan per unit hanya mencakup perangkat R, D, dan P.
            unit_uptime += metric["uptime_seconds"]
            unit_downtime += metric["downtime_seconds"]
            unit_observed += metric["observed_seconds"]

        unit_expected = period_seconds * configured_device_count
        unit_rows.append({
            "machine_oid": int(oid),
            "machine_name": name,
            "devices": devices,
            "unit_uptime_seconds": unit_uptime,
            "unit_downtime_seconds": unit_downtime,
            "unit_observed_seconds": unit_observed,
            "unit_availability_pct": _availability(unit_uptime, unit_observed),
            "unit_coverage_pct": _coverage(unit_observed, unit_expected),
            "configured_device_count": configured_device_count,
            "excluded": excluded,
        })

    overall_by_type: dict[str, dict[str, Any]] = {}
    for device_type in overall_types:
        item = aggregate[device_type]
        target_count = int(item["target_count"])
        expected = period_seconds * target_count
        observed = float(item["observed_seconds"])
        uptime = float(item["uptime_seconds"])
        downtime = float(item["downtime_seconds"])
        overall_by_type[device_type] = {
            "target_count": target_count,
            "devices_with_data": int(item["devices_with_data"]),
            "uptime_seconds": uptime,
            "downtime_seconds": downtime,
            "observed_seconds": observed,
            "expected_seconds": expected,
            "availability_pct": _availability(uptime, observed),
            "coverage_pct": _coverage(observed, expected),
        }

    total_targets = sum(int(aggregate[t]["target_count"]) for t in overall_types)
    total_uptime = sum(float(aggregate[t]["uptime_seconds"]) for t in overall_types)
    total_downtime = sum(float(aggregate[t]["downtime_seconds"]) for t in overall_types)
    total_observed = sum(float(aggregate[t]["observed_seconds"]) for t in overall_types)
    total_expected = period_seconds * total_targets
    overall_combined = {
        "target_count": total_targets,
        "devices_with_data": sum(int(aggregate[t]["devices_with_data"]) for t in overall_types),
        "uptime_seconds": total_uptime,
        "downtime_seconds": total_downtime,
        "observed_seconds": total_observed,
        "expected_seconds": total_expected,
        "availability_pct": _availability(total_uptime, total_observed),
        "coverage_pct": _coverage(total_observed, total_expected),
    }

    scan_info = None
    if latest_scan:
        scan_info = dict(latest_scan)
        for field in ("started_at", "finished_at"):
            value = scan_info.get(field)
            scan_info[field.replace("_at", "_local")] = (
                datetime.fromtimestamp(float(value), LOCAL_TZ).isoformat(timespec="seconds")
                if value is not None else None
            )

    generated_at = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    return {
        "date": report_date.isoformat(),
        "timezone": TIMEZONE_NAME,
        "generated_at": generated_at,
        "period_seconds": period_seconds,
        "unit_count": len(machine_map),
        "device_types": list(types),  # semua tipe, termasuk GPS, untuk tabel rincian
        "overall_device_types": list(overall_types),  # hanya R/D/P untuk rekap keseluruhan
        "excluded_count": sum(1 for oid in machine_map if oid in exclude_oids),
        "latest_status_count": len(latest_status_map),
        "latest_scan": scan_info,
        "overall": {**overall_by_type, "combined": overall_combined},
        "units": unit_rows,
    }


def _hours(seconds: float) -> float:
    return round(float(seconds) / 3600.0, 4)


def build_excel_workbook(report: dict[str, Any]):
    """Buat workbook xlsx yang dapat langsung diunduh dari endpoint Flask."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.utils import get_column_letter
    from config import BREAK_END, BREAK_START

    wb = Workbook()
    summary = wb.active
    summary.title = "Overall Summary"
    details = wb.create_sheet("Unit Details")
    unit_summary = wb.create_sheet("Unit Summary")

    dark = "182433"
    blue = "234A69"
    light_blue = "DCEAF5"
    white = "FFFFFF"
    green = "00E676"
    yellow = "FFD600"
    red = "FF1744"
    gray = "E5E7EB"
    thin_gray = Side(style="thin", color="D1D5DB")

    def style_title(ws, cell_range: str, title: str):
        ws.merge_cells(cell_range)
        cell = ws[cell_range.split(":")[0]]
        cell.value = title
        cell.fill = PatternFill("solid", fgColor=dark)
        cell.font = Font(name="Aptos Display", size=16, bold=True, color=white)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[cell.row].height = 30

    def style_header(ws, row_num: int, start_col: int, end_col: int):
        for col in range(start_col, end_col + 1):
            cell = ws.cell(row_num, col)
            cell.fill = PatternFill("solid", fgColor=blue)
            cell.font = Font(bold=True, color=white)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin_gray)
        ws.row_dimensions[row_num].height = 32

    def style_data(ws, min_row: int, max_row: int, min_col: int, max_col: int):
        for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
            for cell in row:
                cell.alignment = Alignment(vertical="center")
                cell.border = Border(bottom=Side(style="hair", color="374151"))

    def add_availability_cf(ws, column: int, first_row: int, last_row: int):
        if last_row < first_row:
            return
        rng = f"{get_column_letter(column)}{first_row}:{get_column_letter(column)}{last_row}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["90"], fill=PatternFill("solid", fgColor=green), font=Font(color="064E3B", bold=True)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="between", formula=["80", "90"], fill=PatternFill("solid", fgColor=yellow), font=Font(color="3F3500", bold=True)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["80"], fill=PatternFill("solid", fgColor=red), font=Font(color=white, bold=True)))

    report_date = report["date"]
    types = report["device_types"]
    overall_types = report.get("overall_device_types", [t for t in types if t in OVERALL_DEVICE_TYPES])
    n = len(types)
    style_title(summary, "A1:H1", "NETWORK AVAILABILITY - OVERALL SUMMARY")
    summary["A2"] = "Tanggal"
    summary["B2"] = report_date
    summary["D2"] = "Timezone"
    summary["E2"] = report["timezone"]
    summary["G2"] = "Jumlah unit"
    summary["H2"] = report["unit_count"]
    for cell_ref in ("A2", "D2", "G2"):
        summary[cell_ref].font = Font(bold=True, color=blue)

    summary_headers = ["Perangkat", "Target IP", "IP Ada Data", "Uptime (jam)", "Downtime (jam)", "Observed (jam)", "Coverage (%)", "Availability (%)"]
    summary.append([])
    summary.append(summary_headers)
    style_header(summary, 4, 1, len(summary_headers))

    combined_label = " + ".join(overall_types) + " (Gabungan)"
    summary_rows = []
    for key, label in [(t, f"{t} - {DEVICE_NAMES[t]}") for t in overall_types] + [("combined", combined_label)]:
        item = report["overall"][key]
        summary_rows.append([
            label,
            item["target_count"],
            item["devices_with_data"],
            _hours(item["uptime_seconds"]),
            _hours(item["downtime_seconds"]),
            _hours(item["observed_seconds"]),
            round(item["coverage_pct"], 2),
            round(item["availability_pct"], 2) if item["availability_pct"] is not None else None,
        ])
    for row in summary_rows:
        summary.append(row)
    last_sum = 4 + len(summary_rows)
    style_data(summary, 5, last_sum, 1, 8)
    add_availability_cf(summary, 8, 5, last_sum)
    summary.freeze_panes = "A5"
    summary.auto_filter.ref = f"A4:H{last_sum}"
    summary.column_dimensions["A"].width = 25
    for col in range(2, 9):
        summary.column_dimensions[get_column_letter(col)].width = 18
    notes = [
        ("Metode", "Weighted availability = total uptime / total observed time × 100%."),
        ("Coverage", "Observed time / expected monitoring time × 100%. Coverage rendah berarti periode belum terpantau penuh."),
        ("Catatan", "Perangkat tanpa histori pada tanggal laporan ditampilkan N/A dan coverage 0%; tidak diasumsikan online/offline."),
        ("Breakdown", f"{report['excluded_count']} unit Breakdown (BD) tidak dihitung di availability overall; tetap tampil di rincian dengan tanda (BD)."),
        ("GPS", "Availability GPS tetap tersedia pada rincian per perangkat, tetapi tidak masuk overall maupun availability gabungan unit. Overall hanya menghitung R + D + P."),
        ("Jam istirahat", f"Data {BREAK_START:%H:%M}-{BREAK_END:%H:%M} tidak dihitung untuk availability maupun coverage."),
    ]
    first_note = last_sum + 2
    for offset, (title, text) in enumerate(notes):
        r = first_note + offset
        summary.cell(r, 1, title).font = Font(bold=True, color=blue)
        summary.cell(r, 2, text).alignment = Alignment(wrap_text=True, vertical="top")
        summary.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
        summary.row_dimensions[r].height = 30

    # Rincian per unit: OID, Nama, lalu 5 kolom per tipe (IP, Availability, Uptime, Downtime, Coverage), lalu total unit.
    detail_headers = ["OID", "Nama Unit"]
    for t in types:
        detail_headers += [f"IP {t}", f"Availability {t} (%)", f"Uptime {t} (jam)", f"Downtime {t} (jam)", f"Coverage {t} (%)"]
    detail_headers += [f"Availability Unit {'+'.join(overall_types)} (%)", f"Coverage Unit {'+'.join(overall_types)} (%)"]
    details.append(detail_headers)
    style_header(details, 1, 1, len(detail_headers))
    for unit in report["units"]:
        row = [unit["machine_oid"], unit["machine_name"] + (" (BD)" if unit["excluded"] else "")]
        for t in types:
            d = unit["devices"][t]
            row += [
                d["ip_address"] or "-",
                round(d["availability_pct"], 2) if d["availability_pct"] is not None else None,
                _hours(d["uptime_seconds"]), _hours(d["downtime_seconds"]), round(d["coverage_pct"], 2),
            ]
        row += [
            round(unit["unit_availability_pct"], 2) if unit["unit_availability_pct"] is not None else None,
            round(unit["unit_coverage_pct"], 2),
        ]
        details.append(row)
    last_detail = max(1, details.max_row)
    style_data(details, 2, last_detail, 1, len(detail_headers))
    details.freeze_panes = "C2"
    details.auto_filter.ref = f"A1:{get_column_letter(len(detail_headers))}{last_detail}"
    details.column_dimensions["A"].width = 13
    details.column_dimensions["B"].width = 19
    for col in range(3, len(detail_headers) + 1):
        details.column_dimensions[get_column_letter(col)].width = 18
    detail_pct_cols = []
    for i in range(n):
        base = 3 + 5 * i
        details.column_dimensions[get_column_letter(base)].width = 16
        detail_pct_cols += [base + 1, base + 4]
    detail_pct_cols += [3 + 5 * n, 4 + 5 * n]
    for col in [3 + 5 * i + 1 for i in range(n)] + [3 + 5 * n]:
        add_availability_cf(details, col, 2, last_detail)

    unit_headers = ["OID", "Nama Unit"] + [f"Availability {t} (%)" for t in types] + [
        f"Availability Gabungan {'+'.join(overall_types)} (%)",
        f"Coverage Gabungan {'+'.join(overall_types)} (%)",
        f"Uptime Gabungan {'+'.join(overall_types)} (jam)",
        f"Downtime Gabungan {'+'.join(overall_types)} (jam)",
        f"Observed Gabungan {'+'.join(overall_types)} (jam)"]
    unit_summary.append(unit_headers)
    style_header(unit_summary, 1, 1, len(unit_headers))
    for unit in report["units"]:
        devices = unit["devices"]
        unit_summary.append([
            unit["machine_oid"], unit["machine_name"] + (" (BD)" if unit["excluded"] else ""),
            *[round(devices[t]["availability_pct"], 2) if devices[t]["availability_pct"] is not None else None for t in types],
            round(unit["unit_availability_pct"], 2) if unit["unit_availability_pct"] is not None else None,
            round(unit["unit_coverage_pct"], 2),
            _hours(unit["unit_uptime_seconds"]),
            _hours(unit["unit_downtime_seconds"]),
            _hours(unit["unit_observed_seconds"]),
        ])
    last_unit = max(1, unit_summary.max_row)
    style_data(unit_summary, 2, last_unit, 1, len(unit_headers))
    unit_summary.freeze_panes = "C2"
    unit_summary.auto_filter.ref = f"A1:{get_column_letter(len(unit_headers))}{last_unit}"
    for col in range(1, len(unit_headers) + 1):
        unit_summary.column_dimensions[get_column_letter(col)].width = 13 if col == 1 else 20 if col <= 2 + n else 23
    for col in range(3, 4 + n):
        add_availability_cf(unit_summary, col, 2, last_unit)
    unit_pct_cols = list(range(3, 5 + n))

    # Number formats: values are stored as 0-100, not fractions.
    for ws, cols, first, last in [
        (summary, (7, 8), 5, last_sum),
        (details, detail_pct_cols, 2, last_detail),
        (unit_summary, unit_pct_cols, 2, last_unit),
    ]:
        for col in cols:
            for row_idx in range(first, last + 1):
                ws.cell(row_idx, col).number_format = '0.00"%"'

    # Nama unit berawalan '=' akan ditulis openpyxl sebagai formula; paksa jadi teks biasa.
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream



def build_pdf_report(report: dict[str, Any]):
    """Buat laporan PDF landscape berisi ringkasan dan status SQLite per IP."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    output = BytesIO()
    page_size = landscape(A3)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"Network Availability Report {report['date']}",
        author="FMS Onboard Monitoring",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=17, leading=20, textColor=colors.HexColor("#182433"),
        alignment=TA_LEFT, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="ReportSub", parent=styles["Normal"], fontSize=8, leading=11,
        textColor=colors.HexColor("#475569"), spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="CellSmall", parent=styles["Normal"], fontSize=6.5, leading=8,
        textColor=colors.HexColor("#172033"),
    ))
    styles.add(ParagraphStyle(
        name="CellSmallCenter", parent=styles["CellSmall"], alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="CellHeader", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=6.5, leading=8, textColor=colors.white, alignment=TA_CENTER,
    ))

    dark = colors.HexColor("#182433")
    blue = colors.HexColor("#234A69")
    light = colors.HexColor("#EAF0F5")
    green = colors.HexColor("#B7F7D0")
    yellow = colors.HexColor("#FFF0A6")
    red = colors.HexColor("#FFC2CC")
    gray = colors.HexColor("#E5E7EB")

    def fmt_pct(value):
        return f"{float(value):.2f}%" if value is not None else "N/A"

    def fmt_hours(value):
        return f"{float(value) / 3600.0:.2f}"

    def status_color(status):
        return {
            "Green": green, "Yellow": yellow, "Orange": colors.HexColor("#FFD7B0"),
            "Red": red,
        }.get(status, gray)

    story = [
        Paragraph("NETWORK AVAILABILITY & SQLITE STATUS REPORT", styles["ReportTitle"]),
        Paragraph(
            f"Tanggal availability: <b>{report['date']}</b> &nbsp;&nbsp; "
            f"Timezone: <b>{report['timezone']}</b> &nbsp;&nbsp; "
            f"Dibuat: <b>{report['generated_at']}</b> &nbsp;&nbsp; "
            f"Total unit: <b>{report['unit_count']}</b>",
            styles["ReportSub"],
        ),
    ]

    scan = report.get("latest_scan")
    if scan:
        scan_text = (
            f"Scan SQLite terakhir: <b>{scan.get('scan_status', 'N/A')}</b> | "
            f"Mulai: {scan.get('started_local') or 'N/A'} | "
            f"Selesai: {scan.get('finished_local') or 'Belum selesai'} | "
            f"Target: {scan.get('target_count', 0)} | "
            f"Diperiksa: {scan.get('checked_count', 0)} | "
            f"Durasi: {float(scan.get('duration_seconds') or 0):.2f} detik"
        )
    else:
        scan_text = "Scan SQLite terakhir: belum ada data pemeriksaan."
    bd_note = (f"Unit Breakdown (BD) tidak dihitung di availability overall: <b>{report['excluded_count']}</b> unit "
               "(ditandai (BD) pada rincian). GPS (G) tetap ditampilkan pada rincian, tetapi tidak masuk availability overall; "
               "overall hanya menghitung R + D + P.")
    story.extend([Paragraph(scan_text, styles["ReportSub"]), Paragraph(bd_note, styles["ReportSub"]), Spacer(1, 5)])

    summary_rows = [[
        Paragraph("PERANGKAT", styles["CellHeader"]),
        Paragraph("TARGET IP", styles["CellHeader"]),
        Paragraph("ADA DATA", styles["CellHeader"]),
        Paragraph("UPTIME (JAM)", styles["CellHeader"]),
        Paragraph("DOWNTIME (JAM)", styles["CellHeader"]),
        Paragraph("OBSERVED (JAM)", styles["CellHeader"]),
        Paragraph("COVERAGE", styles["CellHeader"]),
        Paragraph("AVAILABILITY", styles["CellHeader"]),
    ]]
    overall_types = report.get("overall_device_types", [t for t in report["device_types"] if t in OVERALL_DEVICE_TYPES])
    for key, label in [(t, f"{t} - {DEVICE_NAMES[t]}") for t in overall_types] + [("combined", "GABUNGAN " + " + ".join(overall_types))]:
        item = report["overall"][key]
        summary_rows.append([
            label, str(item["target_count"]), str(item["devices_with_data"]),
            fmt_hours(item["uptime_seconds"]), fmt_hours(item["downtime_seconds"]),
            fmt_hours(item["observed_seconds"]), fmt_pct(item["coverage_pct"]),
            fmt_pct(item["availability_pct"]),
        ])
    summary_table = Table(summary_rows, colWidths=[55*mm, 22*mm, 22*mm, 27*mm, 30*mm, 30*mm, 27*mm, 30*mm], repeatRows=1)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), blue),
        ("BACKGROUND", (0, 1), (-1, -1), light),
        ("BACKGROUND", (0, len(overall_types) + 1), (-1, len(overall_types) + 1), colors.HexColor("#DCE7F1")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([summary_table, Spacer(1, 9)])
    story.append(Paragraph("RINCIAN STATUS PERANGKAT DARI SQLITE", styles["Heading2"]))
    story.append(Paragraph(
        "Availability dihitung dari histori tanggal laporan. Status, packet loss, jumlah paket, RTT, "
        "dan waktu pemeriksaan merupakan status SQLite terbaru ketika laporan dibuat. "
        "N/A berarti belum tersedia data, bukan berarti perangkat online atau offline.",
        styles["ReportSub"],
    ))
    story.append(Spacer(1, 4))

    headers = ["OID", "UNIT", "TYPE", "IP ADDRESS", "STATUS", "LOSS", "RX/TX", "AVG RTT", "AVAIL.", "UPTIME h", "DOWNTIME h", "COVERAGE", "LAST CHECK"]
    table_data = [[Paragraph(h, styles["CellHeader"]) for h in headers]]
    status_row_indices = []
    for unit in report["units"]:
        for device_type in report["device_types"]:
            dev = unit["devices"][device_type]
            loss = f"{dev['packet_loss_pct']:.1f}%" if dev["packet_loss_pct"] is not None else "N/A"
            packets = (
                f"{dev['packets_received']}/{dev['packets_sent']}"
                if dev["packets_sent"] is not None else "N/A"
            )
            rtt = f"{dev['avg_rtt_ms']:.1f} ms" if dev["avg_rtt_ms"] is not None else "N/A"
            last_check = dev["last_checked_at"] or "N/A"
            if last_check != "N/A":
                last_check = last_check.replace("T", " ")
            row_index = len(table_data)
            table_data.append([
                str(unit["machine_oid"]),
                Paragraph(escape(str(unit["machine_name"])) + (" (BD)" if unit["excluded"] else ""), styles["CellSmall"]),
                device_type,
                dev["ip_address"] or "-",
                dev["status"],
                loss,
                packets,
                rtt,
                fmt_pct(dev["availability_pct"]),
                fmt_hours(dev["uptime_seconds"]),
                fmt_hours(dev["downtime_seconds"]),
                fmt_pct(dev["coverage_pct"]),
                Paragraph(last_check, styles["CellSmall"]),
            ])
            status_row_indices.append((row_index, dev["status"]))

    available_width = page_size[0] - doc.leftMargin - doc.rightMargin
    col_widths = [42, 72, 28, 72, 48, 43, 43, 48, 48, 48, 52, 48, 105]
    scale = available_width / sum(col_widths)
    col_widths = [w * scale for w in col_widths]
    detail_table = LongTable(table_data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), dark),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("FONTSIZE", (0, 1), (-1, -1), 6),
        ("LEADING", (0, 1), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (11, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row_index, status in status_row_indices:
        detail_style.append(("BACKGROUND", (4, row_index), (4, row_index), status_color(status)))
    detail_table.setStyle(TableStyle(detail_style))
    story.append(detail_table)

    def add_page_number(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(12 * mm, 7 * mm, "FMS Onboard Monitoring | Sumber status: SQLite lokal")
        canvas.drawRightString(page_size[0] - 12 * mm, 7 * mm, f"Halaman {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    output.seek(0)
    return output
