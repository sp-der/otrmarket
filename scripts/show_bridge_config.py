from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / ".env")
key = values.get("OTR_BRIDGE_KEY") or ""
password = values.get("DASHBOARD_PASSWORD") or ""

print("OTR NinjaTrader bridge configuration")
print("Bridge key configured:", bool(key))
print("Dashboard password configured:", bool(password))
if key:
    print("OTR_BRIDGE_KEY=" + key)
