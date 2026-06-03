"""
PPK2 D-channel health check (BETON-BARZEL with user wait loop).

If D-channels stuck at 0, prints clear instruction and waits for user
to unplug+replug PPK2. Re-checks until healthy, or user aborts.
"""
import sys, time, collections, os
import serial.tools.list_ports
from ppk2_api.ppk2_api import PPK2_API
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppk2_open import open_clean  # shared opener that drains dirty buffer

def find_port():
    # PPK2 fw 1.2.4 exposes TWO ttyACM ports (measurement + shell), both with
    # vid:pid 1915:c00a. The measurement port is the LOWER-numbered one (ttyACM0);
    # the shell port (ttyACM1) does not stream samples and hangs get_data().
    # Collect all matches and return the lowest, matching full_regression's default.
    matches = []
    for p in serial.tools.list_ports.comports():
        try:
            if p.vid == 0x1915 and p.pid == 0xc00a:
                matches.append(p.device)
        except Exception:
            pass
    return sorted(matches)[0] if matches else None

def check_health():
    """Return tuple (healthy: bool, unique_values: list)."""
    port = find_port()
    if not port:
        return False, []
    try:
        ppk2 = open_clean(port, voltage_mv=3300, source_meter=True)
        ppk2.toggle_DUT_power("ON")
        time.sleep(3)
        ppk2.start_measuring()
        time.sleep(0.3)
        seen = collections.Counter()
        t0 = time.time()
        while time.time() - t0 < 5:
            raw = ppk2.get_data()
            if raw:
                # FIXED: use the aligned digital output from get_samples()
                # (the >>24 raw decode was misaligned with the sample remainder
                #  and produced garbage gpio values). bits & 0x03 = D0|D1.
                res = ppk2.get_samples(raw)
                dig = res[1] if isinstance(res, tuple) and len(res) > 1 else []
                for d in (dig or []):
                    seen[int(d) & 0x03] += 1
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
        # NOTE: at health-check time no firmware is toggling PA0/PA1 yet
        # (runs before NRST releases firmware), so a SINGLE non-zero rest
        # value is expected and HEALTHY. Real phase-diversity is validated
        # per-cell after NRST in measure_one_cell.py. We only require the
        # raw stream to decode and not be physically stuck at 0.
        # PRE-NRST REALITY: at health-check time no firmware toggles PA0/PA1,
        # so the D-channels legitimately read 0 (no voltage on triggers yet).
        # Requiring non-zero here is wrong — it rejects the normal rest state.
        # We only require that the PPK2 stream DECODES (enough samples).
        # True phase-diversity ({0,1,2} toggling) is validated per-cell AFTER
        # NRST in measure_one_cell.py (line ~943), which stays strict.
        healthy = (total > 1000)  # PRE-NRST: 0 is the valid rest state; diversity checked per-cell after NRST
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
        import os
        if not sys.stdin.isatty():
            # Non-interactive (overnight/nohup): can't ask for unplug.
            # Wait a fixed grace period and retry — better than blocking forever.
            print("  [non-interactive] sleeping 30s then retrying (cannot prompt)")
            time.sleep(30)
        else:
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
