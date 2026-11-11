"""Laporan availability bulanan ONB (Excel), dihitung dari tabel ringkasan harian availability_daily.

Aturan sama dengan laporan harian: jam istirahat tidak dihitung (sudah tidak masuk availability_daily), availability overall
hanya Radio + Display + PLE (GPS tampil terpisah), dan unit Breakdown dikecualikan dari overall PER HARI (berdasarkan tabel
bd_daily yang dicatat dashboard tiap hari). Bulan sebelum fitur ini ada tidak punya catatan BD, jadi tidak ada unit yang dikecualikan.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from config import DB_FILE, DEVICE_NAMES, DEVICE_TYPES, OVERALL_DEVICE_TYPES, TIMEZONE_NAME
from db import connect_db, counted_seconds_in_day, get_bd_days, month_range

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _pct(up: float, obs: float) -> float | None:
    return round(up / obs * 100.0, 4) if obs > 0 else None


def _cov(obs: float, expected: float) -> float:
    return round(min(100.0, obs / expected * 100.0), 4) if expected > 0 else 0.0


def build_monthly_report(month: str, machine_map: dict | None = None, db_path=DB_FILE, now: datetime | None = None) -> dict[str, Any]:
    if not MONTH_RE.match(month or ""):
        raise ValueError("Format bulan harus YYYY-MM, misalnya 2026-10.")
    now = now or datetime.now(LOCAL_TZ)
    first_iso, next_iso = month_range(month)
    first, last = date.fromisoformat(first_iso), date.fromisoformat(next_iso) - timedelta(days=1)
    if first > now.date():
        raise ValueError("Bulan itu belum dimulai.")
    last_counted = min(last, now.date())
    days = [first + timedelta(days=i) for i in range((last_counted - first).days + 1)]
    expected_day = {d.isoformat(): counted_seconds_in_day(d, until=now if d == now.date() else None) for d in days}

    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            "SELECT local_date, machine_oid, machine_name, device_type, ip_address, uptime_seconds, downtime_seconds, observed_seconds "
            "FROM availability_daily WHERE local_date >= ? AND local_date <= ? ORDER BY local_date",
            (first_iso, last.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError(f"Tidak ada data availability pada bulan {month}.")

    bd_days = get_bd_days(first_iso, last.isoformat(), db_path)
    types = [t for t in DEVICE_TYPES if any(r[3] == t for r in rows)]
    overall_types = [t for t in OVERALL_DEVICE_TYPES if t in types]

    names: dict[int, str] = {}
    dev: dict[tuple[int, str], dict[str, Any]] = {}
    day_type: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])   # (tanggal, tipe) -> [uptime, observed], tanpa unit BD
    for local_date, oid, name, dtype, ip, up, down, obs in rows:
        oid = int(oid)
        if name:
            names[oid] = name
        d = dev.setdefault((oid, dtype), {"ip": ip, "up": 0.0, "down": 0.0, "obs": 0.0, "days": set(), "exp_all": 0.0, "exp_net": 0.0})
        d["ip"] = ip
        d["up"] += up or 0.0
        d["down"] += down or 0.0
        d["obs"] += obs or 0.0
        d["days"].add(local_date)
        if (local_date, oid) not in bd_days:
            day_type[(local_date, dtype)][0] += up or 0.0
            day_type[(local_date, dtype)][1] += obs or 0.0
    if machine_map:
        for oid, info in machine_map.items():
            if oid in names or any(k[0] == oid for k in dev):
                names[oid] = info.get("Name") or names.get(oid) or f"OID-{oid}"

    # expected (denominator coverage): tiap perangkat x hari yang dihitung; versi "net" melewati hari BD unit itu
    for (oid, _), d in dev.items():
        for day in days:
            sec = expected_day[day.isoformat()]
            d["exp_all"] += sec
            if (day.isoformat(), oid) not in bd_days:
                d["exp_net"] += sec

    overall: dict[str, dict[str, Any]] = {}

    for t in types + ["combined"]:
        selected = [t] if t != "combined" else overall_types
        up = down = obs = exp = 0.0
        count = 0
        for r in rows:
            local_date, oid, _n, dtype, _ip, r_up, r_down, r_obs = r
            if dtype in selected and (local_date, int(oid)) not in bd_days:
                up += r_up or 0.0
                down += r_down or 0.0
                obs += r_obs or 0.0
        for (oid, dtype), d in dev.items():
            if dtype in selected:
                count += 1
                exp += d["exp_net"]
        overall[t] = {
            "devices": count,
            "uptime_seconds": up, "downtime_seconds": down, "observed_seconds": obs,
            "availability_pct": _pct(up, obs), "coverage_pct": _cov(obs, exp),
        }

    units = []
    for oid in sorted({k[0] for k in dev}, key=lambda o: (names.get(o) or "", o)):
        devices = {}
        u_up = u_down = u_obs = u_exp = 0.0
        observed_days: set[str] = set()
        for t in types:
            d = dev.get((oid, t))
            if not d:
                devices[t] = {"ip": None, "availability_pct": None, "downtime_seconds": 0.0, "observed_seconds": 0.0, "coverage_pct": 0.0}
                continue
            devices[t] = {"ip": d["ip"], "availability_pct": _pct(d["up"], d["obs"]), "downtime_seconds": d["down"],
                          "observed_seconds": d["obs"], "coverage_pct": _cov(d["obs"], d["exp_all"])}
            observed_days |= d["days"]
            if t in overall_types:
                u_up += d["up"]; u_down += d["down"]; u_obs += d["obs"]; u_exp += d["exp_all"]
        units.append({
            "oid": oid, "name": names.get(oid) or f"OID-{oid}", "devices": devices,
            "unit_availability_pct": _pct(u_up, u_obs), "unit_coverage_pct": _cov(u_obs, u_exp),
            "bd_days": sum(1 for (d, o) in bd_days if o == oid), "observed_days": len(observed_days),
        })

    daily = []
    for day in days:
        iso = day.isoformat()
        item: dict[str, Any] = {"date": iso, "bd_units": sum(1 for (d, _o) in bd_days if d == iso)}
        c_up = c_obs = 0.0
        for t in types:
            up, obs = day_type.get((iso, t), (0.0, 0.0))
            item[t] = _pct(up, obs)
            if t in overall_types:
                c_up += up; c_obs += obs
        item["combined"] = _pct(c_up, c_obs)
        daily.append(item)

    return {
        "month": month, "first_date": first_iso, "last_date": last.isoformat(),
        "complete": now.date() > last, "generated_at": now.isoformat(timespec="seconds"), "timezone": TIMEZONE_NAME,
        "types": types, "overall_types": overall_types, "overall": overall, "units": units, "daily": daily,
        "bd_unit_days": len({(d, o) for (d, o) in bd_days if (o in names)}),
        "row_count": len(rows),
    }


def build_monthly_excel(report: dict[str, Any]) -> BytesIO:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    summary, units_ws, daily_ws = wb.active, wb.create_sheet("Per Unit"), wb.create_sheet("Harian")
    summary.title = "Ringkasan"
    head_fill = PatternFill("solid", fgColor="1F3A4D")
    head_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    types, overall_types = report["types"], report["overall_types"]

    def header(ws, row, labels):
        for col, label in enumerate(labels, start=1):
            c = ws.cell(row, col, label)
            c.fill, c.font, c.border = head_fill, head_font, border
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def style_body(ws, first_row, last_row, ncols):
        for r in range(first_row, last_row + 1):
            for c in range(1, ncols + 1):
                ws.cell(r, c).border = border

    def colorize(ws, col, first_row, last_row):
        ref = f"{get_column_letter(col)}{first_row}:{get_column_letter(col)}{last_row}"
        ws.conditional_formatting.add(ref, CellIsRule(operator="greaterThan", formula=["90"], fill=PatternFill("solid", bgColor="C6EFCE")))
        ws.conditional_formatting.add(ref, CellIsRule(operator="between", formula=["80", "90"], fill=PatternFill("solid", bgColor="FFEB9C")))
        ws.conditional_formatting.add(ref, CellIsRule(operator="lessThan", formula=["80"], fill=PatternFill("solid", bgColor="FFC7CE")))

    def hours(seconds):
        return round((seconds or 0.0) / 3600.0, 2)

    # ---- Ringkasan
    summary["A1"] = f"AVAILABILITY ONB - {report['month']}"
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"] = f"Periode: {report['first_date']} s.d. {report['last_date']} ({report['timezone']}) - " + ("data lengkap" if report["complete"] else "BULAN BELUM SELESAI, angka masih berubah")
    summary["A3"] = f"Dibuat: {report['generated_at']}"
    labels = ["Perangkat", "Jumlah perangkat", "Uptime (jam)", "Downtime (jam)", "Observed (jam)", "Coverage (%)", "Availability (%)"]
    header(summary, 5, labels)
    rows = [(f"{t} - {DEVICE_NAMES[t]}" + ("" if t in overall_types else " (tidak masuk overall)"), t) for t in types]
    rows.append((" + ".join(overall_types) + " (Overall)", "combined"))
    for i, (label, key) in enumerate(rows, start=6):
        o = report["overall"][key]
        values = [label, o["devices"], hours(o["uptime_seconds"]), hours(o["downtime_seconds"]), hours(o["observed_seconds"]),
                  round(o["coverage_pct"], 2), None if o["availability_pct"] is None else round(o["availability_pct"], 2)]
        for col, v in enumerate(values, start=1):
            summary.cell(i, col, v)
    last = 5 + len(rows)
    style_body(summary, 6, last, len(labels))
    for r in range(6, last + 1):
        for col in (6, 7):
            summary.cell(r, col).number_format = '0.00"%"'
    colorize(summary, 7, 6, last)
    notes = [
        "Availability = total uptime / total waktu terpantau x 100%. Jam istirahat tidak dihitung.",
        "Overall hanya Radio + Display + PLE. GPS ditampilkan terpisah.",
        f"Unit Breakdown (BD) dikecualikan dari overall per hari: {report['bd_unit_days']} unit-hari. Bulan tanpa catatan BD tidak mengecualikan unit apa pun.",
        "Kolom Per Unit menampilkan seluruh hari unit itu, termasuk hari BD (informasi), dan hari BD ditulis terpisah.",
    ]
    for i, text in enumerate(notes, start=last + 2):
        summary.cell(i, 1, text)
    summary.column_dimensions["A"].width = 42
    for col in range(2, 8):
        summary.column_dimensions[get_column_letter(col)].width = 18

    # ---- Per Unit
    labels = ["OID", "Nama Unit"]
    for t in types:
        labels += [f"Availability {DEVICE_NAMES[t]} (%)", f"Downtime {DEVICE_NAMES[t]} (jam)"]
    labels += [f"Availability Unit {'+'.join(overall_types)} (%)", "Coverage Unit (%)", "Hari terpantau", "Hari BD"]
    header(units_ws, 1, labels)
    for i, u in enumerate(report["units"], start=2):
        values: list[Any] = [u["oid"], u["name"]]
        for t in types:
            d = u["devices"][t]
            values += [None if d["availability_pct"] is None else round(d["availability_pct"], 2), hours(d["downtime_seconds"])]
        values += [None if u["unit_availability_pct"] is None else round(u["unit_availability_pct"], 2), round(u["unit_coverage_pct"], 2),
                   u["observed_days"], u["bd_days"]]
        for col, v in enumerate(values, start=1):
            units_ws.cell(i, col, v)
    last_u = max(1, len(report["units"]) + 1)
    style_body(units_ws, 2, last_u, len(labels))
    pct_cols = [3 + 2 * i for i in range(len(types))] + [3 + 2 * len(types), 4 + 2 * len(types)]
    for r in range(2, last_u + 1):
        for col in pct_cols:
            units_ws.cell(r, col).number_format = '0.00"%"'
    for col in [3 + 2 * i for i in range(len(types))] + [3 + 2 * len(types)]:
        colorize(units_ws, col, 2, last_u)
    units_ws.freeze_panes = "C2"
    units_ws.auto_filter.ref = f"A1:{get_column_letter(len(labels))}{last_u}"
    units_ws.column_dimensions["A"].width = 13
    units_ws.column_dimensions["B"].width = 14
    for col in range(3, len(labels) + 1):
        units_ws.column_dimensions[get_column_letter(col)].width = 17

    # ---- Harian
    labels = ["Tanggal"] + [f"Availability {DEVICE_NAMES[t]} (%)" for t in types] + [f"Overall {'+'.join(overall_types)} (%)", "Unit BD dikecualikan"]
    header(daily_ws, 1, labels)
    for i, d in enumerate(report["daily"], start=2):
        values = [d["date"]] + [None if d[t] is None else round(d[t], 2) for t in types] + [None if d["combined"] is None else round(d["combined"], 2), d["bd_units"]]
        for col, v in enumerate(values, start=1):
            daily_ws.cell(i, col, v)
    last_d = max(1, len(report["daily"]) + 1)
    style_body(daily_ws, 2, last_d, len(labels))
    for r in range(2, last_d + 1):
        for col in range(2, 3 + len(types)):
            daily_ws.cell(r, col).number_format = '0.00"%"'
    for col in range(2, 3 + len(types)):
        colorize(daily_ws, col, 2, last_d)
    daily_ws.freeze_panes = "B2"
    daily_ws.column_dimensions["A"].width = 13
    for col in range(2, len(labels) + 1):
        daily_ws.column_dimensions[get_column_letter(col)].width = 19

    # nama unit berawalan '=' tidak boleh dibaca Excel sebagai formula
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
