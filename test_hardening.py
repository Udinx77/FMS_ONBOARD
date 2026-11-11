"""Cek cepat pengamanan dashboard dan input Excel: python test_hardening.py"""
import base64, contextlib, io, os, sys, tempfile, types
from datetime import date
from pathlib import Path

import pandas as pd

tmp = Path(tempfile.mkdtemp())
os.environ["DB_FILE"] = str(tmp / "t.db")
os.environ["EXCEL_FILE"] = str(tmp / "map.xlsx")
os.environ["MACHINE_SOURCE"] = "excel"                  # tes ini memakai Excel sebagai sumber mapping
sys.modules.setdefault("pyodbc", types.SimpleNamespace(Error=Exception, connect=None))  # SQL Server tidak diperlukan di tes
rows = [["CT1", 1, "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"],
        ['=HYPERLINK("http://x.example","a")', 2, "10.0.1.1", "10.0.1.2", "10.0.1.3", "10.0.1.4"],
        ["A&B <b>x</b>", 3, "10.0.2.1", "10.0.2.2", "10.0.2.3", "10.0.0.4"]]          # GPS CT1 = GPS unit ke-3 (IP ganda)
pd.DataFrame(rows, columns=["Name", "OID", "R", "D", "P", "GPS"]).to_excel(os.environ["EXCEL_FILE"], index=False)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import app
assert "PERINGATAN: IP 10.0.0.4 dipakai oleh 2 unit" in buf.getvalue(), buf.getvalue()      # IP ganda terdeteksi

client = app.app.test_client()
# 1. akses default (tanpa pembatasan) tetap terbuka
assert client.get("/health").status_code == 200
# 2. allowlist: alamat di luar CIDR ditolak, di dalam diterima, IPv4-mapped IPv6 ikut dikenali
import ipaddress
app._ALLOWED_NETS = [ipaddress.ip_network("10.4.0.0/16")]
assert client.get("/health", environ_overrides={"REMOTE_ADDR": "192.168.1.5"}).status_code == 403
assert client.get("/health", environ_overrides={"REMOTE_ADDR": "10.4.7.9"}).status_code == 200
assert client.get("/health", environ_overrides={"REMOTE_ADDR": "::ffff:10.4.7.9"}).status_code == 200
app._ALLOWED_NETS = []
# 3. login Basic opsional
app.DASHBOARD_USER, app.DASHBOARD_PASSWORD = "admin", "rahasia"
assert client.get("/health").status_code == 401
bad = {"Authorization": "Basic " + base64.b64encode(b"admin:salah").decode()}
good = {"Authorization": "Basic " + base64.b64encode(b"admin:rahasia").decode()}
assert client.get("/health", headers=bad).status_code == 401
assert client.get("/health", headers=good).status_code == 200
app.DASHBOARD_PASSWORD = ""
# 4. CORS: Origin asing tidak diterima untuk Socket.IO
r = client.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": "http://evil.example"})
assert r.status_code != 200 and "evil.example" not in (r.headers.get("Access-Control-Allow-Origin") or ""), (r.status_code, dict(r.headers))
assert client.get("/socket.io/?EIO=4&transport=polling").status_code == 200                 # tanpa Origin (akses normal) tetap bisa
# 5. export: nama unit berawalan '=' menjadi teks (bukan formula), PDF tidak error karena tanda < & >
import io as _io, openpyxl
report = app.build_availability_report(date.today(), app.MACHINE_MAP)
wb = openpyxl.load_workbook(_io.BytesIO(app.build_excel_workbook(report).getvalue()))
formulas = [c.coordinate for ws in wb for row in ws.iter_rows() for c in row if c.data_type == "f"]
assert not formulas, formulas
assert app.build_pdf_report(report).getvalue().startswith(b"%PDF")
# 6. export: permintaan berulang dalam 30 detik dilayani dari cache (pembuatan hanya sekali)
calls = []
builder = lambda: calls.append(1) or b"data"
assert app._cached_export("tes", date.today(), builder) == b"data" and app._cached_export("tes", date.today(), builder) == b"data"
assert len(calls) == 1, calls
r1 = client.get("/export/availability.xlsx"); r2 = client.get("/export/availability.xlsx")
assert r1.status_code == 200 and r1.data == r2.data
print("OK: pengamanan dashboard dan input Excel")
