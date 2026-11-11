"""Regression test: GPS tidak masuk availability overall atau gabungan unit."""
from datetime import datetime
from pathlib import Path
import tempfile

from openpyxl import load_workbook

from availability_report import build_availability_report, build_excel_workbook, build_pdf_report
from db import connect_db, initialize_db
from config import TIMEZONE_NAME
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
REPORT_DATE = datetime.now(LOCAL_TZ).date()

MACHINE_MAP = {
    1: {"Name": "CT1", "R": "10.0.0.1", "D": "10.0.0.2", "P": "10.0.0.3", "G": "10.0.0.4"},
    2: {"Name": "CT2", "R": "10.0.0.5", "D": "10.0.0.6", "P": "10.0.0.7", "G": "10.0.0.8"},
    3: {"Name": "CT3", "R": "10.0.0.9", "D": "10.0.0.10", "P": "10.0.0.11", "G": "10.0.0.12"},
}

with tempfile.TemporaryDirectory() as tmp:
    db_path = Path(tmp) / "availability-test.db"
    initialize_db(db_path)
    conn = connect_db(db_path)
    try:
        # observed=100 untuk tiap perangkat. CT3 disimulasikan sebagai Breakdown.
        uptime = {
            (1, "R"): 90, (1, "D"): 80, (1, "P"): 100, (1, "G"): 0,
            (2, "R"): 80, (2, "D"): 100, (2, "P"): 100, (2, "G"): 0,
            (3, "R"): 0, (3, "D"): 0, (3, "P"): 0, (3, "G"): 100,
        }
        with conn:
            for oid, machine in MACHINE_MAP.items():
                for dtype in ("R", "D", "P", "G"):
                    conn.execute(
                        """INSERT INTO availability_daily
                           (machine_oid,machine_name,device_type,ip_address,local_date,
                            uptime_seconds,downtime_seconds,observed_seconds)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (oid, machine["Name"], dtype, machine[dtype], REPORT_DATE.isoformat(),
                         uptime[(oid, dtype)], 100 - uptime[(oid, dtype)], 100),
                    )
    finally:
        conn.close()

    report = build_availability_report(
        REPORT_DATE, MACHINE_MAP, db_path=db_path, exclude_oids={3}
    )

    # GPS remains in per-device detail but is not an overall aggregation key.
    assert report["device_types"] == ["R", "D", "P", "G"], report["device_types"]
    assert report["overall_device_types"] == ["R", "D", "P"]
    assert set(report["overall"]) == {"R", "D", "P", "combined"}

    # Overall excludes CT3 (BD) and GPS. Weighted R/D/P results:
    assert round(report["overall"]["R"]["availability_pct"], 2) == 85.00
    assert round(report["overall"]["D"]["availability_pct"], 2) == 90.00
    assert round(report["overall"]["P"]["availability_pct"], 2) == 100.00
    assert round(report["overall"]["combined"]["availability_pct"], 2) == 91.67
    assert report["overall"]["combined"]["target_count"] == 6
    assert report["overall"]["combined"]["devices_with_data"] == 6
    assert report["overall"]["combined"]["observed_seconds"] == 600

    # Unit combined availability is R+D+P only; CT1 = (90+80+100)/300 = 90%.
    ct1 = next(unit for unit in report["units"] if unit["machine_oid"] == 1)
    assert round(ct1["unit_availability_pct"], 2) == 90.00
    assert ct1["devices"]["G"]["availability_pct"] == 0.0  # GPS detail retained.
    assert ct1["unit_observed_seconds"] == 300  # GPS observed time excluded.

    # Excel summary omits GPS from overall, while detail sheets retain GPS columns.
    workbook_stream = build_excel_workbook(report)
    wb = load_workbook(workbook_stream, data_only=True)
    summary_labels = [wb["Overall Summary"].cell(r, 1).value for r in range(5, 9)]
    assert summary_labels == ["R - Radio", "D - Display", "P - PLE", "R + D + P (Gabungan)"]
    detail_headers = [c.value for c in wb["Unit Details"][1]]
    assert "IP G" in detail_headers and "Availability G (%)" in detail_headers
    unit_headers = [c.value for c in wb["Unit Summary"][1]]
    assert "Availability G (%)" in unit_headers
    assert "Availability Gabungan R+D+P (%)" in unit_headers

    # PDF generation still includes detailed GPS and RDP-only overall summary.
    pdf_stream = build_pdf_report(report)
    assert pdf_stream.getvalue().startswith(b"%PDF")
    assert len(pdf_stream.getvalue()) > 1000

# Frontend regression checks: category and RTO logic must only use R/D/P.
html = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
assert "const OVERALL_TYPES = ['R', 'D', 'P'];" in html
assert "else if (corePings.includes('Red')) cat = 'need';" in html
assert "else if (corePings.includes('Orange')) cat = 'monitoring';" in html
assert "Object.values(ping).includes('Red')" not in html
assert "OVERALL_TYPES.some((k) => u.ping[k] === 'Red')" in html
print("OK: overall R+D+P saja; GPS tetap di detail dan GPS RTO tidak memicu Need Check/Monitoring")
