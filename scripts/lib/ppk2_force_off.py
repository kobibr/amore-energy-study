#!/usr/bin/env python3
"""Force PPK2 DUT power OFF (clean slate). Uses shared open_clean (drains
dirty buffer, picks the lowest ttyACM measurement port)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppk2_open import open_clean, find_ppk2_port

def main():
    voltage = int(os.environ.get("PPK2_VOLTAGE_MV", "3300"))
    port = find_ppk2_port(os.environ.get("PPK2_PORT"))
    if not port:
        print("[force-off] no PPK2 port found"); return 1
    try:
        ppk = open_clean(port, voltage_mv=voltage, source_meter=True)
        ppk.toggle_DUT_power("OFF")
        print(f"[force-off] PPK2 DUT power OFF on {port}")
        try: ppk.ser.close()
        except Exception: pass
        return 0
    except Exception as e:
        print(f"[force-off] failed: {e}"); return 1

if __name__ == "__main__":
    sys.exit(main())
