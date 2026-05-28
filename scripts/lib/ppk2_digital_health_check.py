"""
PPK2 D-channel health check (BETON-BARZEL with user wait loop).

If D-channels stuck at 0, prints clear instruction and waits for user
to unplug+replug PPK2. Re-checks until healthy, or user aborts.
"""
import sys, time, collections
import serial.tools.list_ports
from ppk2_api.ppk2_api import PPK2_API

def find_port():
    for p in serial.tools.list_ports.comports():
        try:
            if p.vid == 0x1915 and p.pid == 0xc00a:
                return p.device
        except Exception:
            pass
    return None

def check_health():
    """Return tuple (healthy: bool, unique_values: list)."""
    port = find_port()
    if not port:
        return False, []
    try:
        ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
        ppk2.get_modifiers()
        ppk2.set_source_voltage(3300)
        ppk2.use_source_meter()
        ppk2.toggle_DUT_power("ON")
        time.sleep(3)
        ppk2.start_measuring()
        time.sleep(0.3)
        seen = collections.Counter()
        t0 = time.time()
        while time.time() - t0 < 5:
            raw = ppk2.get_data()
            if raw:
                # Decode logic byte directly from raw 4-byte words.
                # get_samples() returns a broken (constant 0xFF) digital byte
                # on PPK2 fw 5390; the raw decode is correct. See Day 5.
                n = len(raw) - (len(raw) % 4)
                for i in range(0, n, 4):
                    seen[(int.from_bytes(raw[i:i+4], "little") >> 24) & 0xFF] += 1
            time.sleep(0.05)
        ppk2.stop_measuring()
        ppk2.toggle_DUT_power("OFF")
        ppk2.ser.close()
        unique = sorted(seen.keys())
        # At THIS stage no firmware is toggling PA0 yet, so we cannot require
        # signal diversity. We only require that the raw stream decodes and is
        # NOT permanently stuck at 0 (0 = D-channels physically dead/no VCC).
        # Real toggle validation happens per-cell after NRST starts firmware.
        total = sum(seen.values())
        stuck_zero = (len(unique) == 1 and unique[0] == 0)
        healthy = (total > 1000) and (not stuck_zero)
        return healthy, unique
    except Exception as e:
        print(f"  ✗ check failed: {e}", flush=True)
        return False, []

print("=" * 60)
print("  PPK2 D-channel health check (BETON-BARZEL)")
print("=" * 60)

MAX_RETRIES = 5
for attempt in range(1, MAX_RETRIES + 1):
    print(f"\nAttempt {attempt}/{MAX_RETRIES}: sampling 5s with DUT powered...")
    healthy, unique = check_health()
    print(f"  D-channel values seen: {unique}")
    if healthy:
        print(f"  ✓ PPK2 D-channels HEALTHY")
        sys.exit(0)
    
    print()
    print("  ✗ PPK2 D-channels STUCK at 0 — known PPK2 firmware bug.")
    print()
    print("  ACTION REQUIRED:")
    print("    1. UNPLUG PPK2 USB cable from computer")
    print("    2. Wait 10 seconds")
    print("    3. PLUG it back in")
    print("    4. Wait ~5s for USB re-enumeration")
    print()
    if attempt < MAX_RETRIES:
        try:
            input("  Press ENTER when done unplugging+replugging → ")
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted by user")
            sys.exit(2)
        # Verify PPK2 came back
        for i in range(30):
            if find_port():
                print(f"  ✓ PPK2 re-enumerated after {i*0.5:.1f}s")
                break
            time.sleep(0.5)
        else:
            print("  ⚠ PPK2 not seen after replug — check connection")
            continue
        # CRITICAL: PPK2 needs ~10s after re-plug for internal state to
        # stabilize before D-channel sampling works correctly. Re-plugging
        # is NOT instantaneous — the PPK2 firmware needs time to initialize.
        print("  ⏳ Waiting 10s for PPK2 internal stabilization...")
        time.sleep(10)
    else:
        print(f"  ✗ FAILED after {MAX_RETRIES} attempts — aborting")
        sys.exit(1)
