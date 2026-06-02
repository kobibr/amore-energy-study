#!/usr/bin/env python3
"""Verify the PPK2 is stably present AND openable 3x consecutive (up to 30s).
A transient USB re-enumeration (port briefly absent) is tolerated: it pauses
the streak rather than resetting it. Exit 0 only if 3 consecutive opens
succeed."""
import os, sys, time

def find_port():
    # Prefer autodetect by description; fall back to env even if the path
    # blinked out for a moment during re-enumeration.
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            d = (p.description or "")
            if "PPK" in d or "Nordic" in d:
                return p.device
    except Exception:
        pass
    env = os.environ.get("PPK2_PORT")
    return env if (env and os.path.exists(env)) else None

def try_open(port):
    try:
        from ppk2_api.ppk2_api import PPK2_API
        ppk = PPK2_API(port, timeout=2, write_timeout=2)
        ppk.get_modifiers()
        try: ppk.ser.close()
        except Exception: pass
        return True
    except Exception as e:
        print(f"[verify] open failed: {e}")
        return False

def main():
    deadline = time.time() + 30.0
    need, consecutive = 3, 0
    while time.time() < deadline:
        port = find_port()
        if not port:
            # Transient re-enumeration: wait, do NOT reset the streak.
            print(f"[verify] port absent (transient), streak held at {consecutive}/{need}")
            time.sleep(0.7)
            continue
        if try_open(port):
            consecutive += 1
            print(f"[verify] open OK on {port} ({consecutive}/{need})")
            if consecutive >= need:
                print("[verify] PPK2 stably present + openable")
                return 0
            time.sleep(0.5)
        else:
            consecutive = 0   # a real open FAILURE resets; a missing port does not
            time.sleep(1.0)
    print("[verify] PPK2 not stable within 30s")
    return 1

if __name__ == "__main__":
    sys.exit(main())
