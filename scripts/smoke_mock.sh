#!/usr/bin/env bash
# smoke_mock.sh
#
# Broad shallow smoke test - NO PPK2 hardware required.
# Tests: build + Python pipeline + analysis with synthetic data.
#
# Total runtime: ~60-90 seconds.
#
# Exit code: 0 = pass, 1 = at least one fail, 2 = setup error

set -uo pipefail

ES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW="${ES}/firmware/amore-fw"
LOG_DIR="${ES}/logs/smoke_mock_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${LOG_DIR}"

RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'; BLU='\033[94m'; CYN='\033[96m'; RST='\033[0m'

PASS_COUNT=0; FAIL_COUNT=0; SKIP_COUNT=0
declare -a STEP_NAMES
declare -a STEP_RESULTS
declare -a STEP_DETAILS

header() { echo -e "\n${BLU}══════ $* ══════${RST}"; }
step()   { echo -e "${CYN}── $* ──${RST}"; CURRENT_STEP="$*"; }
ok()     { echo -e "${GRN}✓ PASS${RST}: $*"; STEP_NAMES+=("${CURRENT_STEP}"); STEP_RESULTS+=("PASS"); STEP_DETAILS+=("$*"); PASS_COUNT=$((PASS_COUNT+1)); }
fail()   { echo -e "${RED}✗ FAIL${RST}: $*"; STEP_NAMES+=("${CURRENT_STEP}"); STEP_RESULTS+=("FAIL"); STEP_DETAILS+=("$*"); FAIL_COUNT=$((FAIL_COUNT+1)); }
skip()   { echo -e "${YLW}- SKIP${RST}: $*"; STEP_NAMES+=("${CURRENT_STEP}"); STEP_RESULTS+=("SKIP"); STEP_DETAILS+=("$*"); SKIP_COUNT=$((SKIP_COUNT+1)); }

echo -e "${BLU}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  AmorE Smoke Mock — Broad, Shallow (no PPK2 required)            ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                          ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${RST}"
echo "Log dir: ${LOG_DIR}"

# === A: ENVIRONMENT ===
header "SECTION A — Environment"

step "A.1 Toolchain present"
if command -v arm-none-eabi-gcc >/dev/null 2>&1 && \
   command -v cmake >/dev/null 2>&1; then
    ok "GCC + CMake present"
else
    fail "Missing toolchain"; exit 2
fi

step "A.2 Python venv"
cd "${ES}"
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
    if python3 -c "import pytest, numpy" 2>/dev/null; then
        ok "venv active with pytest+numpy"
    else
        fail "venv missing deps"
    fi
else
    fail ".venv not found"
fi

# === B: BUILD ===
header "SECTION B — Firmware Build"

step "B.1 Clean build directories"
cd "${FW}"
rm -rf build/smoke_bn254 build/smoke_bls 2>/dev/null
ok "Clean done"

step "B.2 Build BN254 (-O2 default)"
if cmake -B build/smoke_bn254 \
          -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-stm32f4.cmake \
          -DCURVE=BN254 > "${LOG_DIR}/build_bn254.log" 2>&1 && \
   cmake --build build/smoke_bn254 --target amore_bn254.elf -j >> "${LOG_DIR}/build_bn254.log" 2>&1; then
    SIZE=$(arm-none-eabi-size build/smoke_bn254/amore_bn254.elf 2>/dev/null | awk 'NR==2{print $1}')
    ok ".text=${SIZE} bytes"
else
    fail "BN254 build failed - see ${LOG_DIR}/build_bn254.log"
fi

step "B.3 Build BLS12-381 (-O3 Release)"
if cmake -B build/smoke_bls \
          -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-stm32f4.cmake \
          -DCURVE=BLS12_381 \
          -DCMAKE_BUILD_TYPE=Release > "${LOG_DIR}/build_bls.log" 2>&1 && \
   cmake --build build/smoke_bls --target amore_bls12_381.elf -j >> "${LOG_DIR}/build_bls.log" 2>&1; then
    SIZE=$(arm-none-eabi-size build/smoke_bls/amore_bls12_381.elf 2>/dev/null | awk 'NR==2{print $1}')
    ok ".text=${SIZE} bytes"
else
    fail "BLS12-381 build failed - see ${LOG_DIR}/build_bls.log"
fi

# === C: UNIT TESTS ===
header "SECTION C — Unit Tests"

cd "${ES}"

step "C.1 analysis/tests"
if python3 -m pytest analysis/tests/ -q --tb=no 2>&1 > "${LOG_DIR}/pytest_analysis.log"; then
    PASSED=$(grep -oE '[0-9]+ passed' "${LOG_DIR}/pytest_analysis.log" | grep -oE '[0-9]+' | head -1)
    ok "${PASSED} tests passed"
else
    FAILED=$(grep -oE '[0-9]+ failed' "${LOG_DIR}/pytest_analysis.log" | grep -oE '[0-9]+' | head -1)
    fail "${FAILED:-?} failed - see ${LOG_DIR}/pytest_analysis.log"
fi

step "C.2 ppk2-control/tests"
if python3 -m pytest measurement/ppk2-control/tests/ -q --tb=no 2>&1 > "${LOG_DIR}/pytest_ppk2control.log"; then
    PASSED=$(grep -oE '[0-9]+ passed' "${LOG_DIR}/pytest_ppk2control.log" | grep -oE '[0-9]+' | head -1)
    ok "${PASSED} tests passed"
else
    FAILED=$(grep -oE '[0-9]+ failed' "${LOG_DIR}/pytest_ppk2control.log" | grep -oE '[0-9]+' | head -1)
    fail "${FAILED:-?} failed"
fi

# === D: ENERGY PIPELINE (MOCK) ===
header "SECTION D — Mock Energy Pipeline"

step "D.1 Synthetic trace generation"
python3 > "${LOG_DIR}/synth_gen.log" 2>&1 << 'PYEOF'
import sys
sys.path.insert(0, '.')
try:
    from analysis.fixtures.synthetic_cells import synthesize_mode_a_trace, CellSpec
    rows = synthesize_mode_a_trace(curve="BN254", n=1, seed=42)
    print(f"Generated {len(rows)} rows")
    if len(rows) < 100:
        print(f"FAIL: too few rows ({len(rows)})")
        sys.exit(1)
    # Verify structure: each row should be (timestamp_us, current_uA, voltage_V, gpio_byte)
    first = rows[0]
    last = rows[-1]
    print(f"First row: {first}")
    print(f"Last row: {last}")
    if len(first) < 4:
        print("FAIL: row format wrong")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then
    ROWS=$(grep 'Generated' "${LOG_DIR}/synth_gen.log" | awk '{print $2}')
    ok "${ROWS} synthetic rows generated"
else
    fail "synthesize_mode_a_trace broken - see ${LOG_DIR}/synth_gen.log"
fi

step "D.2 Energy computation non-zero"
python3 > "${LOG_DIR}/energy_compute.log" 2>&1 << 'PYEOF'
import sys
sys.path.insert(0, '.')
try:
    from analysis.parse_traces import Phase
    from analysis.compute_energy import phase_energy, compute_trace

    # Build synthetic phases mimicking a single round
    phases = [
        Phase(gpio_byte=0, start_us=0,        end_us=100_000,   samples=100, mean_current_uA=50_000.0,  mean_voltage_V=3.3),
        Phase(gpio_byte=1, start_us=100_000,  end_us=200_000,   samples=100, mean_current_uA=85_000.0,  mean_voltage_V=3.3),
        Phase(gpio_byte=2, start_us=200_000,  end_us=300_000,   samples=100, mean_current_uA=85_000.0,  mean_voltage_V=3.3),
        Phase(gpio_byte=0, start_us=300_000,  end_us=400_000,   samples=100, mean_current_uA=50_000.0,  mean_voltage_V=3.3),
    ]

    total = compute_trace(phases)
    print(f"Total energy: {total.total_energy_J*1000:.3f} mJ")
    print(f"Phases: {len(total.per_phase)}, gpio_bytes: {list(total.by_gpio_byte.keys())}")

    if total.total_energy_J <= 0:
        print("FAIL: zero or negative energy")
        sys.exit(1)
    if total.total_energy_J > 10:  # 10 J upper bound for a quick trace
        print(f"FAIL: unrealistically high ({total.total_energy_J} J)")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then
    ENERGY=$(grep 'Total energy:' "${LOG_DIR}/energy_compute.log" | awk '{print $3}')
    ok "${ENERGY} mJ (non-zero, sane)"
else
    fail "compute_trace broken - see ${LOG_DIR}/energy_compute.log"
fi

step "D.3 Sleep model BatchModel"
python3 > "${LOG_DIR}/sleep_model.log" 2>&1 << 'PYEOF'
import sys
sys.path.insert(0, '.')
try:
    from analysis.sleep_model import BatchModel, find_crossover, analyze

    # Realistic-ish numbers (joules)
    # E_setup=1mJ, E_setup_per_round=2mJ, E_verify=1mJ, E_direct=10mJ
    m = BatchModel(1e-3, 2e-3, 1e-3, 10e-3)

    e1   = m.e_per_round(1)
    e10  = m.e_per_round(10)
    asym = m.asymptote()
    print(f"AmorE e_per_round(1)  = {e1*1000:.3f} mJ")
    print(f"AmorE e_per_round(10) = {e10*1000:.3f} mJ")
    print(f"Asymptote             = {asym*1000:.3f} mJ")

    if e1 <= 0 or e10 <= 0 or asym <= 0:
        print("FAIL: zero or negative")
        sys.exit(1)
    if not (e10 < e1):
        print(f"FAIL: amortization not working (e10 >= e1)")
        sys.exit(1)
    if not (asym < e10):
        print(f"FAIL: asymptote not below e10")
        sys.exit(1)

    # Test find_crossover
    e_direct = 10e-3
    cross = find_crossover(m, e_direct, n_max=1000)
    print(f"Crossover N* (direct={e_direct*1000} mJ): {cross}")

    print("PASS")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then
    ok "BatchModel + find_crossover work"
else
    fail "sleep_model broken - see ${LOG_DIR}/sleep_model.log"
fi

step "D.4 Plot script import (no display)"
python3 > "${LOG_DIR}/plot_import.log" 2>&1 << 'PYEOF'
import sys, os
sys.path.insert(0, '.')
import matplotlib
matplotlib.use('Agg')

try:
    from analysis import plot_crossover
    from analysis import plot_energy_per_round
    from analysis import plot_phase_breakdown
    from analysis import plot_mode_comparison
    print("All 4 plot modules import cleanly")
    print("PASS")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then
    ok "All 4 plot modules import cleanly"
else
    fail "plot import failed"
fi

# === SUMMARY ===
header "SUMMARY"
echo ""
for i in "${!STEP_NAMES[@]}"; do
    name="${STEP_NAMES[$i]}"
    result="${STEP_RESULTS[$i]}"
    detail="${STEP_DETAILS[$i]}"
    case "$result" in
        PASS) echo -e "  ${GRN}✓${RST} ${name}: ${detail}" ;;
        FAIL) echo -e "  ${RED}✗${RST} ${name}: ${detail}" ;;
        SKIP) echo -e "  ${YLW}-${RST} ${name}: ${detail}" ;;
    esac
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GRN}${PASS_COUNT} PASS${RST}  ${RED}${FAIL_COUNT} FAIL${RST}  ${YLW}${SKIP_COUNT} SKIP${RST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Log: ${LOG_DIR}"
echo ""

if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}❌ REGRESSION DETECTED${RST}"
    exit 1
else
    echo -e "${GRN}✅ All pass - no mock-side regressions${RST}"
    exit 0
fi
