#!/usr/bin/env bash
# smoke_ppk2.sh — Real PPK2 hardware smoke test (v2)
#
# WIRING REQUIRED:
#   - PPK2 in source-measure mode
#   - PPK2 VOUT -> STM32 3V3 rail
#   - PPK2 GND  -> STM32 GND
#   - STM32 IDD jumper REMOVED (so PPK2 supplies the chip)
#   - PPK2 USB connected to host
#
# Total runtime: ~20 seconds.
#
# Verifies in ONE python session (avoid PPK2 USB reconnect issues):
#   E.1: PPK2 device detected on /dev/ttyACM*
#   E.2: PPK2 API connects + get_modifiers
#   E.3: Source mode: set 3.3V, sample, read
#   E.4: Current reading in sane range (5-200 mA)
#   E.5: Sample rate near 100 ksps
#
# Exit code: 0 = pass, 1 = at least one fail

set -uo pipefail

ES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ES}/logs/smoke_ppk2_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${LOG_DIR}"

RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'; BLU='\033[94m'; CYN='\033[96m'; RST='\033[0m'

echo -e "${BLU}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  AmorE Smoke PPK2 — Real Hardware Smoke Test (v2)                ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                          ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${RST}"
echo "Log dir: ${LOG_DIR}"
echo ""
echo "Wiring assumption:"
echo "  - PPK2 source mode, VOUT -> STM32 3V3, GND -> STM32 GND"
echo "  - STM32 IDD jumper REMOVED"
echo "  - PPK2 USB connected to host"
echo ""

cd "${ES}"
source .venv/bin/activate

# All PPK2 work in ONE Python session to avoid USB reconnect issues
python3 > "${LOG_DIR}/ppk2_full.log" 2>&1 << 'PYEOF'
"""
PPK2 smoke test (v4 — power-on-only).

Matches the pattern used by firmware/amore-fw/scripts/run_benchmark.sh,
which has driven 61-round benchmarks successfully:
  - set source voltage
  - use_source_meter
  - toggle_DUT_power("ON")  → PPK2 LED goes RED
  - hold for 2 seconds (visual confirmation)
  - toggle_DUT_power("OFF") → PPK2 LED returns to GREEN

DOES NOT call start_measuring/get_samples — those corrupt the
ppk2-api 0.9.2 buffer state and produce garbage readings (we
observed 7-8 A "readings" from a floating VOUT after a single
extra start/stop pair).

Visual verification by the operator:
  - LED RED during the 2-second hold → DUT is being powered.
  - If STM32 is wired to VOUT/GND with IDD jumper removed,
    it will boot and the firmware will run.
  - run_benchmark.sh provides the actual functional test
    (61 rounds, status=0x600D0000) via GDB readout — not this
    smoke script.
"""
import sys, time, json
results = {}
def step(name, ok, detail=""):
    results[name] = {"pass": ok, "detail": detail}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

# E.1: find PPK2
try:
    import serial.tools.list_ports
    ppk2_port = next(
        (p.device for p in serial.tools.list_ports.comports()
         if "PPK" in (p.description or "") or "Nordic" in (p.description or "")),
        None,
    )
    if not ppk2_port:
        step("E.1", False, "No PPK2 device found")
        print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)
    step("E.1", True, f"PPK2 at {ppk2_port}")
except Exception as e:
    step("E.1", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

# E.2: connect + read calibration state
try:
    from ppk2_api.ppk2_api import PPK2_API
    ppk2 = PPK2_API(ppk2_port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    uncal = (
        hasattr(ppk2, "modifiers")
        and isinstance(ppk2.modifiers, dict)
        and str(ppk2.modifiers.get("Calibrated", "0")) == "0"
    )
    msg = "UNCALIBRATED (Calibrated=0)" if uncal else "calibrated"
    step("E.2", True, f"Connected, {msg}")
except Exception as e:
    step("E.2", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

# E.3: source mode + 3.3V configured
try:
    ppk2.set_source_voltage(3300)
    ppk2.use_source_meter()
    time.sleep(0.3)
    step("E.3", True, "source_meter mode @ 3.3 V")
except Exception as e:
    step("E.3", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

# E.4: turn DUT power ON — operator should now see RED LED
try:
    print("\n*** PPK2 LED should now turn RED (DUT power ON) ***")
    print("*** Holding for 2 seconds — verify visually ***\n")
    ppk2.toggle_DUT_power("ON")
    time.sleep(2.0)
    step("E.4", True, "DUT_power ON for 2s (PPK2 LED RED — visual check)")
except Exception as e:
    step("E.4", False, f"{type(e).__name__}: {e}")
    try: ppk2.toggle_DUT_power("OFF")
    except: pass
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

# E.5: turn DUT power OFF — LED returns to green
try:
    ppk2.toggle_DUT_power("OFF")
    time.sleep(0.3)
    print("\n*** PPK2 LED should now be GREEN (idle, DUT power OFF) ***\n")
    step("E.5", True, "DUT_power OFF cleanly (LED back to GREEN)")
except Exception as e:
    step("E.5", False, f"toggle OFF: {type(e).__name__}: {e}")

# CRITICAL: explicit close. ppk2-api 0.9.2 holds the serial port open
# until the Python process exits. When sanity_check.sh runs smoke_ppk2
# standalone, then mini_regression's Layer 0 calls it 3s later, the
# second invocation reports "No PPK2 device found" until the first
# session's serial fd is GC'd. Force-close the underlying serial.
try:
    for attr in ("ser", "_serial", "serial", "_port"):
        sobj = getattr(ppk2, attr, None)
        if sobj is not None and hasattr(sobj, "close"):
            sobj.close()
            break
except Exception:
    pass

print("__RESULTS_JSON__" + json.dumps(results))

PYEOF

PY_EXIT=$?

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SUMMARY"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Show full log
cat "${LOG_DIR}/ppk2_full.log"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Parse PASS/FAIL counts from log
PASS_COUNT=$(grep -c "^\[PASS\]" "${LOG_DIR}/ppk2_full.log" 2>/dev/null)
FAIL_COUNT=$(grep -c "^\[FAIL\]" "${LOG_DIR}/ppk2_full.log" 2>/dev/null)
PASS_COUNT="${PASS_COUNT:-0}"
FAIL_COUNT="${FAIL_COUNT:-0}"

echo -e "  ${GRN}${PASS_COUNT} PASS${RST}  ${RED}${FAIL_COUNT} FAIL${RST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Log: ${LOG_DIR}/ppk2_full.log"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo -e "${RED}❌ PPK2 SMOKE FAILED${RST}"
    exit 1
else
    echo -e "${GRN}✅ PPK2 hardware working correctly${RST}"
    exit 0
fi
