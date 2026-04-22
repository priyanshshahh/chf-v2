#!/usr/bin/env python3
"""Verify the Streamlit dashboard starts and responds."""
import subprocess
import sys
import time
import urllib.request

PORT = 8506

proc = subprocess.Popen(
    [sys.executable, "-m", "streamlit", "run", "app/dashboard.py",
     "--server.port", str(PORT), "--server.headless", "true"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    cwd="."
)
print(f"Started streamlit PID={proc.pid}")

# Poll for up to 20 seconds
for i in range(20):
    time.sleep(1)
    try:
        code = urllib.request.urlopen(
            "http://localhost:" + str(PORT), timeout=2
        ).getcode()
        print(f"HTTP {code} — Dashboard is UP on port {PORT}")
        proc.terminate()
        proc.wait(timeout=5)
        print("Dashboard launch: PASS")
        sys.exit(0)
    except Exception:
        pass

# Timed out — collect output
proc.terminate()
try:
    out, _ = proc.communicate(timeout=5)
    print("--- streamlit output ---")
    print(out[:3000] if out else "(no output)")
except Exception as e:
    print(f"communicate error: {e}")

print("Dashboard launch: FAIL (did not respond within 20s)")
sys.exit(1)
