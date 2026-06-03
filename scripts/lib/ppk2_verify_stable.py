#!/usr/bin/env python3
"""Verify the PPK2 is stably present AND openable 3x consecutive (up to 30s).
A transient USB re-enumeration (port briefly absent) is tolerated: it pauses
the streak rather than resetting it. Exit 0 only if 3 consecutive opens
succeed."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppk2_open import open_clean, find_ppk2_port

def find_port():
    return find_ppk2_port(os.environ.get("PPK2_PORT"))

def try_open(port):
    try:
        ppk = open_clean(port)
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
