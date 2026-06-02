#!/usr/bin/env python3
"""Force PPK2 DUT power OFF (clean slate).
Reconstructed from the inline force-off logic in full_regression.sh's trap.
Autodetects the PPK2 (or uses PPK2_PORT env). Prints status; exits 0 on success."""
import os, sys

def find_port():
    env = os.environ.get("PPK2_PORT")
    if env and os.path.exists(env):
        return env
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            d = (p.description or "")
            if "PPK" in d or "Nordic" in d:
                return p.device
    except Exception as e:
        print(f"[force-off] autodetect failed: {e}")
    return env

def main():
    voltage = int(os.environ.get("PPK2_VOLTAGE_MV", "3300"))
    port = find_port()
    if not port:
        print("[force-off] no PPK2 port found"); return 1
    try:
        from ppk2_api.ppk2_api import PPK2_API
        ppk = PPK2_API(port, timeout=2, write_timeout=2)
        ppk.get_modifiers()
        ppk.set_source_voltage(voltage)
        ppk.use_source_meter()
        ppk.toggle_DUT_power("OFF")
        print(f"[force-off] PPK2 DUT power OFF on {port}")
        try: ppk.ser.close()
        except Exception: pass
        return 0
    except Exception as e:
        print(f"[force-off] failed: {e}"); return 1

if __name__ == "__main__":
    sys.exit(main())
