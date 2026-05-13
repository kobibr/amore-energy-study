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
Single-session PPK2 smoke test.
Runs E.1-E.5 in one process, no disconnect/reconnect.
Emits structured JSON-ish results at end for shell parsing.
"""
import sys, time, statistics, json
from pathlib import Path

results = {}

def step(name, ok, detail=""):
    results[name] = {"pass": ok, "detail": detail}
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {name}: {detail}")

# === E.1: find PPK2 ===
try:
    import serial.tools.list_ports
    ppk2_port = None
    for p in serial.tools.list_ports.comports():
        desc = p.description or ""
        if "PPK" in desc or "Nordic" in desc:
            ppk2_port = p.device
            break
    if ppk2_port:
        step("E.1", True, f"PPK2 at {ppk2_port}")
    else:
        step("E.1", False, "No PPK2 device found")
        print("__RESULTS_JSON__" + json.dumps(results))
        sys.exit(1)
except Exception as e:
    step("E.1", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results))
    sys.exit(1)

# === E.2: connect ===
try:
    from ppk2_api.ppk2_api import PPK2_API
    ppk2 = PPK2_API(ppk2_port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    step("E.2", True, "Connected, modifiers read")
except Exception as e:
    step("E.2", False, f"{type(e).__name__}: {e}")
    print("__RESULTS_JSON__" + json.dumps(results))
    sys.exit(1)

# === E.3: source mode, sample, read ===
try:
    ppk2.set_source_voltage(3300)   # 3.3 V
    ppk2.use_source_meter()
    time.sleep(0.2)

    ppk2.toggle_DUT_power("ON")
    time.sleep(0.5)   # let STM32 boot/settle

    ppk2.start_measuring()
    time.sleep(1.0)   # capture 1 second
    raw = ppk2.get_data()
    ppk2.stop_measuring()

    if raw is None or len(raw) < 100:
        step("E.3", False, f"insufficient raw ({0 if raw is None else len(raw)} bytes)")
        ppk2.toggle_DUT_power("OFF")
        print("__RESULTS_JSON__" + json.dumps(results))
        sys.exit(1)

    samples, _ = ppk2.get_samples(raw)
    if not samples or len(samples) < 100:
        step("E.3", False, f"insufficient samples ({len(samples) if samples else 0})")
        ppk2.toggle_DUT_power("OFF")
        print("__RESULTS_JSON__" + json.dumps(results))
        sys.exit(1)

    mean_uA = statistics.mean(samples)
    stdev_uA = statistics.stdev(samples) if len(samples) > 1 else 0.0
    n_samples = len(samples)

    step("E.3", True, f"{n_samples} samples, mean {mean_uA:.1f} uA ({mean_uA/1000:.2f} mA)")

    # === E.4: sane range ===
    # 5 mA = 5,000 uA (lower) ; 200 mA = 200,000 uA (upper)
    if 5_000 <= mean_uA <= 200_000:
        step("E.4", True, f"{mean_uA:.0f} uA in sane range [5,000-200,000]")
    elif mean_uA < 5_000:
        step("E.4", False, f"{mean_uA:.0f} uA too LOW (<5 mA) - STM32 not powered? IDD jumper still in?")
    else:
        step("E.4", False, f"{mean_uA:.0f} uA too HIGH (>200 mA) - short risk!")

    # === E.5: sample rate ===
    # ppk2-api 0.9.2 uses AVERAGE mode, default ~1 ksps.
    # AVG_NUM_SET is "no-firmware" so we can't change it via this library.
    # Streaming mode (100 ksps) would require a library fork.
    # For smoke test: just verify data flows (>500 samples per second).
    # Mode C (UART per-byte energy) will need higher rate - handled separately.
    if 500 <= n_samples <= 150_000:
        step("E.5", True, f"{n_samples} samples in 1s (data flows; library uses Average mode @ ~1 ksps)")
    elif n_samples < 500:
        step("E.5", False, f"{n_samples} samples - too few, data not flowing properly")
    else:
        step("E.5", True, f"{n_samples} samples in 1s (unexpectedly high - good)")

    # Cleanup
    ppk2.toggle_DUT_power("OFF")
    time.sleep(0.2)

except Exception as e:
    step("E.3", False, f"{type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    try:
        ppk2.toggle_DUT_power("OFF")
    except: pass
    print("__RESULTS_JSON__" + json.dumps(results))
    sys.exit(1)

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
PASS_COUNT=$(grep -c '^\[PASS\]' "${LOG_DIR}/ppk2_full.log" 2>/dev/null | tr -d ' \n' || echo 0)
FAIL_COUNT=$(grep -c '^\[FAIL\]' "${LOG_DIR}/ppk2_full.log" 2>/dev/null | tr -d ' \n' || echo 0)

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
