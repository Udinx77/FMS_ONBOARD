"""Cek cepat canary collector: python test_canary.py"""
import asyncio, os, sqlite3, sys, tempfile, time
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
xlsx = tmp / "map.xlsx"
import pandas as pd
pd.DataFrame([["CT1", 1, "10.0.0.1", "10.0.0.2", "10.0.0.3"]], columns=["Name", "OID", "R", "D", "P"]).to_excel(xlsx, index=False)
os.environ.update(MACHINE_SOURCE="excel", EXCEL_FILE=str(xlsx), DB_FILE=str(tmp / "t.db"), SCAN_INTERVAL_SECONDS="1", CANARY_IPS="10.9.9.9")
sys.path.insert(0, ".")
import collect_ping as cp

# unit: canary mati semua -> CanaryFailed; satu hidup -> lolos
res = lambda loss: {"packet_loss_pct": loss}
try:
    cp.check_canary({"a": res(100), "b": res(100)}, ["a", "b"]); raise AssertionError("harus gagal")
except cp.CanaryFailed:
    pass
cp.check_canary({"a": res(100), "b": res(0)}, ["a", "b"]); cp.check_canary({}, [])

# integrasi: canary mati -> scan dicatat FAILED 'CANARY', tidak ada data ping tersimpan; canary hidup -> scan SUCCESS
state = {"canary_alive": False}
async def fake_ping(ips):
    return {ip: {"ip_address": ip, "packets_sent": 5, "packets_received": 0 if (ip == "10.9.9.9" and not state["canary_alive"]) else 5,
                 "packet_loss_pct": 100.0 if (ip == "10.9.9.9" and not state["canary_alive"]) else 0.0,
                 "avg_rtt_ms": 1.0, "min_rtt_ms": 1.0, "max_rtt_ms": 1.0, "jitter_ms": 0.0} for ip in ips}
cp.ping_all = fake_ping

async def main():
    task = asyncio.ensure_future(cp.run_collector())
    await asyncio.sleep(2.5); state["canary_alive"] = True; await asyncio.sleep(2.5)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
asyncio.run(main())
c = sqlite3.connect(os.environ["DB_FILE"])
runs = c.execute("SELECT scan_status, error_message FROM scan_runs ORDER BY id").fetchall()
assert runs[0][0] == "FAILED" and runs[0][1].startswith("CANARY"), runs
assert runs[-1][0] == "SUCCESS", runs
first_ok = c.execute("SELECT MIN(scan_id) FROM ping_history").fetchone()[0]
failed_ids = [r[0] for r in c.execute("SELECT id FROM scan_runs WHERE scan_status='FAILED'")]
assert first_ok not in failed_ids and c.execute("SELECT COUNT(*) FROM ping_history WHERE ip_address='10.9.9.9'").fetchone()[0] == 0   # canary tidak disimpan sebagai perangkat
print("OK: canary membatalkan scan saat jaringan PC monitor bermasalah")
