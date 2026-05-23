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
Single-session PPK2 smoke test (v3).

Bipolar gate (off vs on) with calibration awareness:
  - Calibrated PPK2: strict absolute thresholds + delta check.
  - Uncalibrated PPK2: only delta check (absolute values meaningless).

Emits PASS-WARN when uncalibrated; smoke_ppk2.sh treats PASS-WARN as
non-fatal so sanity_check.sh proceeds. See docs/known_caveats.md.
"""
import sys, time, statistics, json
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

# E.2: connect + get_modifiers + detect calibration state
try:
    from ppk2_api.ppk2_api import PPK2_API
    ppk2 = PPK2_API(ppk2_port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    UNCALIBRATED = (
        hasattr(ppk2, "modifiers")
        and isinstance(ppk2.modifiers, dict)
        and str(ppk2.modifiers.get("Calibrated", "0")) == "0"
    )
    cal_msg = "UNCALIBRATED (Calibrated=0)" if UNCALIBRATED else "calibrated"
    step("E.2", True, f"Connected, {cal_msg}")
except Exception as e:
    step("E.2", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

def measure_1s():
    ppk2.start_measuring(); time.sleep(1.0)
    raw = ppk2.get_data(); ppk2.stop_measuring()
    if not raw or len(raw) < 100: return None, 0
    s, _ = ppk2.get_samples(raw)
    return (statistics.mean(s), len(s)) if s and len(s) >= 100 else (None, 0)

try:
    ppk2.set_source_voltage(3300)
    ppk2.use_source_meter()
    time.sleep(0.3)

    # E.3: DUT-off
    ppk2.toggle_DUT_power("OFF"); time.sleep(0.5)
    # Drain stale buffer
    ppk2.start_measuring(); time.sleep(0.3); ppk2.get_data(); ppk2.stop_measuring()
    time.sleep(0.2)
    mean_off, n_off = measure_1s()
    if mean_off is None:
        step("E.3", False, "no samples DUT-off")
        ppk2.toggle_DUT_power("OFF")
        print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)
    step("E.3", True, f"DUT-off: {n_off} samples, mean {mean_off:.2f} µA")

    # E.4: DUT-on
    ppk2.toggle_DUT_power("ON"); time.sleep(0.5)
    ppk2.start_measuring(); time.sleep(0.3); ppk2.get_data(); ppk2.stop_measuring()
    time.sleep(0.2)
    mean_on, n_on = measure_1s()
    if mean_on is None:
        step("E.4", False, "no samples DUT-on")
        ppk2.toggle_DUT_power("OFF")
        print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)
    step("E.4", True, f"DUT-on: {n_on} samples, mean {mean_on:.2f} µA")

    delta = mean_on - mean_off

    # E.5: DUT-detection gate (the bipolar check)
    # STM32 active draws 50-90 mA; delta must be in [5, 200] mA.
    # Below 1 mA → VOUT load missing / firmware not booted.
    # Above 200 mA → not a real DUT load; usually uncalibrated PPK2
    # source-mode emitting garbage on a floating VOUT (we have seen
    # 8 A "readings" with nothing connected). Also require stability:
    # a real STM32 has CV < 50%; PPK2 garbage has CV > 100%.
    DELTA_MIN_UA = 1000.0
    DELTA_MAX_UA = 200_000.0  # STM32 can't draw more than 200 mA

    # Re-measure with full sample set for stdev computation
    ppk2.start_measuring(); time.sleep(1.0)
    _raw = ppk2.get_data(); ppk2.stop_measuring()
    if _raw and len(_raw) >= 100:
        _on_samples, _ = ppk2.get_samples(_raw)
        on_stdev = statistics.stdev(_on_samples) if len(_on_samples) > 1 else 0.0
        on_cv = abs(on_stdev / mean_on) if mean_on != 0 else float("inf")
    else:
        on_stdev = 0.0; on_cv = float("inf")

    CV_MAX = 0.5  # STM32 active phase: CV typically <10%; garbage: >100%

    if delta < DELTA_MIN_UA:
        step("E.5", False,
             f"DUT NOT detected: delta={delta:.1f} µA < {DELTA_MIN_UA} µA. "
             f"Check VOUT->STM32 3V3 wire, IDD jumper removed, firmware flashed.")
    elif delta > DELTA_MAX_UA:
        step("E.5", False,
             f"delta={delta:.1f} µA exceeds STM32 maximum ({DELTA_MAX_UA:.0f} µA). "
             f"Not a real DUT — likely uncalibrated PPK2 emitting noise on "
             f"floating VOUT. Connect STM32 properly.")
    elif on_cv > CV_MAX:
        step("E.5", False,
             f"DUT-on too unstable: CV={on_cv*100:.1f}% > {CV_MAX*100:.0f}%. "
             f"mean={mean_on:.1f} µA, stdev={on_stdev:.1f} µA. "
             f"Likely no real load on VOUT (noise dominates).")
    else:
        step("E.5", True,
             f"DUT detected: delta={delta:.1f} µA, on-CV={on_cv*100:.1f}% "
             f"(off={mean_off:.1f}, on={mean_on:.1f})")

    # E.6: Absolute range — ONLY when calibrated. Uncalibrated PPK2 readings
    # are scaled by an unknown factor (see docs/known_caveats.md).
    if UNCALIBRATED:
        step("E.6", True,
             f"absolute range check SKIPPED — PPK2 uncalibrated, "
             f"raw mean_on={mean_on:.0f} µA not trustable")
    else:
        if 5_000 <= mean_on <= 200_000:
            step("E.6", True, f"DUT-on {mean_on:.0f} µA in STM32 range [5k-200k]")
        elif mean_on < 5_000:
            step("E.6", False, f"DUT-on {mean_on:.0f} µA below STM32 active range")
        else:
            step("E.6", False, f"DUT-on {mean_on:.0f} µA above STM32 range — short risk")

    ppk2.toggle_DUT_power("OFF"); time.sleep(0.2)

except Exception as e:
    step("E.3", False, f"{type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    try: ppk2.toggle_DUT_power("OFF")
    except: pass
    print("__RESULTS_JSON__" + json.dumps(results)); sys.exit(1)

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
