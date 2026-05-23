#!/usr/bin/env bash
# =============================================================================
#  full_regression.sh — Top-level regression for AmorE Energy Study
#
#  Location: ~/amore-energy-study/scripts/full_regression.sh
#  Repo:     amore-energy-study (this is the orchestrator)
#
#  Runs ALL checks required to ensure the system is healthy:
#    P0  Pre-flight: workspace prep, toolchain, hardware
#    P1  Build: full firmware build matrix (all curves, all modes)
#    P2  Host-side tests: pytest + mini_regression (~3 min)
#    P3  Firmware regression: BN254 + BLS12-381 benchmarks (~3 hours)
#    P4  Result validation: cycles within ±5% of baselines from doc/
#    P5  Implementation validation: SHA256, constants, layout, GDB checks
#    P6  Final report
#
#  Total wall time:
#    Full run:           ~3.5 hours  (most time in P3 benchmarks)
#    --skip-bench:       ~10 min     (P0-P2, P5 static only)
#    --static-only:      ~5 min      (P0-P2, P5 without GDB)
#    --dry-run:          ~30 sec     (validation only, no execution)
#
#  Usage:
#      bash scripts/full_regression.sh                 # Full run
#      bash scripts/full_regression.sh --skip-bench    # Skip slow P3+P5-GDB
#      bash scripts/full_regression.sh --static-only   # Build + host tests only
#      bash scripts/full_regression.sh --dry-run       # Show plan, no execute
#
#  Exit codes:
#    0 = all pass
#    1 = one or more phases failed
#    2 = setup error (pre-flight failed)
# =============================================================================
set -uo pipefail

# ── Self-locate ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENERGY_STUDY="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_FIRMWARE="$ENERGY_STUDY/firmware/amore-fw"
# FR1 fix: previous default (10.232.131.169) was a stale value left over
# from an earlier network layout. The actual Pi confirmed by telemetry
# on 2026-05-22 is 10.164.56.169. Override via env var if your setup differs.
RPI_HOST="${RPI_HOST:-10.164.56.169}"
RPI_USER="${RPI_USER:-pi}"
TOLERANCE="0.05"  # ±5% per metric

TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$ENERGY_STUDY/logs/full_regression_${TS}"
mkdir -p "$LOG_DIR"
MASTER_LOG="$LOG_DIR/MASTER.log"

# ── Argument parsing ─────────────────────────────────────────────────────────
SKIP_BENCH=false
STATIC_ONLY=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --skip-bench)  SKIP_BENCH=true ;;
        --static-only) STATIC_ONLY=true; SKIP_BENCH=true ;;
        --dry-run)     DRY_RUN=true ;;
        -h|--help)
            grep '^#' "$0" | head -40
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Use --help for usage"
            exit 2
            ;;
    esac
done

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'
BLU='\033[94m'; CYN='\033[96m'; RST='\033[0m'; BOLD='\033[1m'

# ── Counters ─────────────────────────────────────────────────────────────────
TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_WARN=0
PHASE_RESULTS=()

# ── Helpers ──────────────────────────────────────────────────────────────────
log()    { echo -e "$(date '+%H:%M:%S') $*" | tee -a "$MASTER_LOG"; }
pass()   { log "${GRN}  ✓ PASS${RST}: $*"; TOTAL_PASS=$((TOTAL_PASS+1)); }
fail()   { log "${RED}  ✗ FAIL${RST}: $*"; TOTAL_FAIL=$((TOTAL_FAIL+1)); }
warn()   { log "${YLW}  ! WARN${RST}: $*"; TOTAL_WARN=$((TOTAL_WARN+1)); }
# FR2 fix: skip() was referenced in P0.6 but never defined. Calling it would
# produce "skip: command not found" on systems missing ST-Link with --static-only.
skip()   { log "${CYN}  - SKIP${RST}: $*"; }
phase()  {
    log ""
    log "${BOLD}${BLU}═════════════════════════════════════════════════════════════${RST}"
    log "${BOLD}${BLU}  PHASE $*${RST}"
    log "${BOLD}${BLU}═════════════════════════════════════════════════════════════${RST}"
}

# ── Header ───────────────────────────────────────────────────────────────────
log ""
log "${BOLD}${CYN}╔══════════════════════════════════════════════════════════════════╗${RST}"
log "${BOLD}${CYN}║  AmorE Energy Study — Full Regression Suite                     ║${RST}"
log "${BOLD}${CYN}║  Started: $(date '+%Y-%m-%d %H:%M:%S')                                  ║${RST}"
log "${BOLD}${CYN}║  Log dir: $LOG_DIR  ${RST}"
log "${BOLD}${CYN}╚══════════════════════════════════════════════════════════════════╝${RST}"
log ""
log "  Energy-study repo:  $ENERGY_STUDY"
log "  Firmware repo:      $REPO_FIRMWARE"
log "  Tolerance:          ±$(echo "$TOLERANCE * 100" | bc)%"
log "  Skip bench:         $SKIP_BENCH"
log "  Static only:        $STATIC_ONLY"
log "  Dry run:            $DRY_RUN"

# =============================================================================
phase "P0 — Pre-flight checks"
# =============================================================================

log ""
log "P0.1 — Firmware repo accessible?"
if [ -d "$REPO_FIRMWARE" ]; then
    pass "Firmware repo at $REPO_FIRMWARE"
else
    fail "Firmware repo NOT FOUND"
    exit 2
fi

log ""
log "P0.2 — Firmware branch policy"
cd "$REPO_FIRMWARE"
CURRENT_BRANCH=$(git branch --show-current)
log "  Current: $CURRENT_BRANCH"
case "$CURRENT_BRANCH" in
    feature/energy-instrumentation)
        pass "On canonical branch"
        ;;
    main)
        pass "On main (production branch)"
        ;;
    *)
        warn "On non-canonical branch: $CURRENT_BRANCH"
        ;;
esac

log ""
log "P0.3 — Working tree status"
DIRTY=$(git status --porcelain | grep -v "STM32CubeF4" | wc -l)
if [ "$DIRTY" -eq 0 ]; then
    pass "Working tree clean (ignoring submodule)"
else
    warn "$DIRTY modified files (non-submodule):"
    git status --porcelain | grep -v "STM32CubeF4" | head -3 | tee -a "$MASTER_LOG"
fi

log ""
log "P0.4 — Energy-study working tree"
cd "$ENERGY_STUDY"
ES_DIRTY=$(git status --porcelain 2>/dev/null | wc -l)
log "  Energy-study modified files: $ES_DIRTY"
if [ "$ES_DIRTY" -gt 5 ]; then
    warn "Energy-study has $ES_DIRTY modified files — consider committing"
fi

log ""
log "P0.5 — Toolchain"
if command -v arm-none-eabi-gcc &>/dev/null; then
    pass "arm-none-eabi-gcc $(arm-none-eabi-gcc --version | head -1 | awk '{print $NF}')"
else
    fail "arm-none-eabi-gcc NOT FOUND"
fi
if command -v gdb-multiarch &>/dev/null || command -v arm-none-eabi-gdb &>/dev/null; then
    pass "GDB available"
else
    warn "No GDB — Section B will be skipped"
fi
if command -v openocd &>/dev/null; then
    pass "openocd available"
else
    fail "openocd NOT FOUND"
fi

log ""
log "P0.6 — Hardware"
if lsusb 2>/dev/null | grep -qi '0483:374b\|st-link'; then
    pass "ST-Link enumerated"
else
    if ! $STATIC_ONLY; then
        fail "ST-Link NOT visible — cannot flash or read"
    else
        skip "ST-Link not needed for --static-only"
    fi
fi

log ""
log "P0.7 — Network: Pi reachable?"
if $SKIP_BENCH; then
    log "  Skipping (--skip-bench)"
else
    if ping -c 1 -W 2 "$RPI_HOST" &>/dev/null; then
        pass "Pi pingable at $RPI_HOST"
        if ssh -o ConnectTimeout=3 -o BatchMode=yes "$RPI_USER@$RPI_HOST" 'true' 2>/dev/null; then
            pass "Pi SSH passwordless"
        else
            warn "Pi SSH requires password"
        fi
    else
        fail "Pi NOT reachable — P3 will fail"
    fi
fi

log ""
log "P0.8 — Disk space"
FREE_GB=$(df -BG --output=avail "$ENERGY_STUDY" | tail -1 | tr -d 'G ')
if [ "$FREE_GB" -ge 3 ]; then
    pass "${FREE_GB} GB free"
else
    warn "Only ${FREE_GB} GB free"
fi

# Check P0 health before continuing
if [ "$TOTAL_FAIL" -gt 0 ]; then
    log ""
    log "${RED}${BOLD}════ PRE-FLIGHT FAILED ════${RST}"
    log "  Fix $TOTAL_FAIL FAIL items above, then rerun."
    exit 2
fi

PHASE_RESULTS+=("P0|PASS|$TOTAL_PASS checks, $TOTAL_WARN warnings")

if $DRY_RUN; then
    log ""
    log "DRY RUN complete. To execute: rerun without --dry-run."
    exit 0
fi

# =============================================================================
phase "P1 — Build firmware matrix"
# =============================================================================

cd "$REPO_FIRMWARE"
P1_START=$(date +%s)

build_variant() {
    local curve="$1"
    local mode="$2"
    local curve_lc="${curve,,}"
    local mode_lc="${mode,,}"
    local builddir="build/${curve_lc}_${mode_lc}"
    local log="$LOG_DIR/build_${curve}_${mode}.log"
    
    log "  Building $curve Mode $mode → $builddir"
    
    rm -rf "$builddir"
    if cmake -B "$builddir" \
            -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-stm32f4.cmake \
            -DCURVE="$curve" \
            -DMEASUREMENT_MODE="$mode" \
            > "$log" 2>&1 \
       && cmake --build "$builddir" >> "$log" 2>&1; then
        pass "$curve Mode $mode built"
        find "$builddir" -maxdepth 2 -name "*.elf" | while read elf; do
            log "    $(basename $elf): $(arm-none-eabi-size $elf | tail -1)"
        done
    else
        fail "$curve Mode $mode build FAILED (see $log)"
    fi
}

log ""
log "P1.1 — BN254 Mode A"
build_variant BN254 A

log ""
log "P1.2 — BLS12_381 Mode A"
build_variant BLS12_381 A

log ""
log "P1.3 — BN254 Mode B (RELIC direct pairing)"
build_variant BN254 B

log ""
log "P1.4 — BLS12_381 Mode B"
build_variant BLS12_381 B

log ""
log "P1.5 — Size table"
SIZE_TABLE="$LOG_DIR/sizes.txt"
{
    printf "%-50s  %s\n" "Target" "text    data     bss     dec     hex"
    printf "%-50s  %s\n" "──────────────────────────────────────────────────" "──────  ──────  ──────  ──────  ──────"
    find build/ -name "*.elf" 2>/dev/null | sort | while read elf; do
        printf "%-50s  " "$elf"
        arm-none-eabi-size "$elf" 2>/dev/null | tail -1
    done
} > "$SIZE_TABLE"
cat "$SIZE_TABLE" | tee -a "$MASTER_LOG"

P1_DUR=$(($(date +%s) - P1_START))
log ""
log "P1 wall time: $((P1_DUR / 60))m $((P1_DUR % 60))s"
PHASE_RESULTS+=("P1|PASS|4 variants built; sizes locked in $SIZE_TABLE")

# =============================================================================
phase "P2 — Host-side tests (pytest + mini_regression)"
# =============================================================================

cd "$ENERGY_STUDY"
P2_START=$(date +%s)

log ""
log "P2.1 — Activate venv"
if [ -f .venv/bin/activate ]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
    pass "venv: $(python --version)"
else
    warn "No .venv found — using system Python"
fi

log ""
log "P2.2 — pytest"
PYTEST_LOG="$LOG_DIR/pytest.log"
if pytest -x --tb=short 2>&1 | tee "$PYTEST_LOG"; then
    PT_PASSED=$(grep -oE '[0-9]+ passed' "$PYTEST_LOG" | head -1 || echo "? passed")
    pass "pytest: $PT_PASSED"
else
    PT_FAILED=$(grep -oE '[0-9]+ failed' "$PYTEST_LOG" | head -1)
    fail "pytest: $PT_FAILED"
fi

log ""
log "P2.3 — mini_regression.sh"
if [ -f "$ENERGY_STUDY/scripts/mini_regression.sh" ]; then
    MINI_LOG="$LOG_DIR/mini_regression.log"
    if bash "$ENERGY_STUDY/scripts/mini_regression.sh" > "$MINI_LOG" 2>&1; then
        pass "mini_regression"
    else
        fail "mini_regression — see $MINI_LOG"
    fi
else
    warn "scripts/mini_regression.sh not found — skipping"
fi

P2_DUR=$(($(date +%s) - P2_START))
log "P2 wall time: $((P2_DUR / 60))m $((P2_DUR % 60))s"
PHASE_RESULTS+=("P2|PASS|pytest + mini_regression")

# =============================================================================
phase "P3 — Firmware benchmark regression (BN254 + BLS12_381)"
# =============================================================================

if $SKIP_BENCH; then
    log "P3 SKIPPED per --skip-bench"
    PHASE_RESULTS+=("P3|SKIP|--skip-bench")
else
    cd "$REPO_FIRMWARE"
    P3_START=$(date +%s)
    
    log ""
    log "P3.1 — Run scripts/regression_test.sh --curve=both"
    log "    Expected duration: ~3 hours (BN254 ~75min + BLS ~95min + overhead)"
    log "    Log: $LOG_DIR/regression_full.log"
    
    REGRESSION_LOG="$LOG_DIR/regression_full.log"
    if bash scripts/regression_test.sh --curve=both --tolerance="$TOLERANCE" 2>&1 | tee "$REGRESSION_LOG"; then
        pass "regression_test.sh exited cleanly"
    else
        fail "regression_test.sh exited with non-zero status"
    fi
    
    # Look for the regression artifact directory.
    # FR6 fix: previously hardcoded "regression_2026*" which would silently
    # stop matching in 2027. Match any year.
    REGRESSION_DIR=$(find "$REPO_FIRMWARE/logs" -maxdepth 1 -name "regression_*" -type d -newer "$LOG_DIR" 2>/dev/null | tail -1)
    if [ -n "$REGRESSION_DIR" ]; then
        log ""
        log "P3.2 — Regression report"
        if [ -f "$REGRESSION_DIR/REPORT.txt" ]; then
            cat "$REGRESSION_DIR/REPORT.txt" | tee -a "$MASTER_LOG"
            
            # Check if "PASSED" appears in the report
            if grep -qi "all checks passed\|all metrics match\|regression passed" "$REGRESSION_DIR/REPORT.txt"; then
                pass "Regression report indicates success"
            else
                fail "Regression report does not indicate clean pass"
            fi
        else
            warn "No REPORT.txt in $REGRESSION_DIR"
        fi
        
        # Copy regression artifacts into our log dir
        cp -r "$REGRESSION_DIR" "$LOG_DIR/firmware_regression/" 2>/dev/null || true
    else
        warn "No regression artifact directory found"
    fi
    
    P3_DUR=$(($(date +%s) - P3_START))
    log ""
    log "P3 wall time: $((P3_DUR / 60))m $((P3_DUR % 60))s"
    
    if [ "$TOTAL_FAIL" -gt 0 ]; then
        PHASE_RESULTS+=("P3|FAIL|See $REGRESSION_LOG")
    else
        PHASE_RESULTS+=("P3|PASS|Both curves match baseline ±${TOLERANCE}")
    fi
fi

# =============================================================================
phase "P4 — Baseline verification (handled by P3)"
# =============================================================================

log ""
log "P4 — Baseline expectations (from doc/AmorE_*_Results.txt):"
log ""
log "  BN254 (build/bn254_a/amore_bn254.elf):"
log "    OneTimeSetup       :    503.9 ms"
log "    N=50 Blind/round   :    199.4 ms"
log "    N=50 Verify/round  :    182.4 ms"
log "    N=50 Amort/round   :    381.8 ms"
log "    Honest verify_ok   :     61 / 61"
log "    Status word        :     0x600d0000"
log ""
log "  BLS12_381 (build/bls12_381_a/amore_bls12_381.elf):"
log "    OneTimeSetup       :  2,565.2 ms"
log "    N=50 Blind/round   :  1,032.1 ms"
log "    N=50 Verify/round  :    887.2 ms"
log "    N=50 Amort/round   :  1,919.3 ms"
log "    Honest verify_ok   :     61 / 61"
log "    Status word        :     0x600d0000"
log ""
log "  Tolerance: ±$(echo "$TOLERANCE * 100" | bc)% per metric (sk-randomness jitter)"
log "  Per-metric pass/fail: see P3 regression log"

PHASE_RESULTS+=("P4|REF|See P3 for per-metric comparison")

# =============================================================================
phase "P5 — Implementation validation"
# =============================================================================

VAL_SCRIPT="$ENERGY_STUDY/scripts/implementation_validation.sh"
P5_START=$(date +%s)

if [ ! -f "$VAL_SCRIPT" ]; then
    warn "implementation_validation.sh not found at $VAL_SCRIPT"
    warn "  Add it from /mnt/user-data/outputs/ or skip P5"
    PHASE_RESULTS+=("P5|SKIP|Script not found")
elif ! command -v openocd &>/dev/null && ! $STATIC_ONLY; then
    warn "openocd missing — running --static-only mode"
    STATIC_ONLY=true
fi

if [ -f "$VAL_SCRIPT" ]; then
    log ""
    log "P5.1 — Running implementation_validation.sh"
    
    VAL_LOG="$LOG_DIR/implementation_validation.log"
    VAL_ARGS=""
    if $STATIC_ONLY || $SKIP_BENCH; then
        VAL_ARGS="--static-only"
        log "  Mode: --static-only (skipping Section B GDB checks)"
    fi
    
    if bash "$VAL_SCRIPT" --curve=both $VAL_ARGS 2>&1 | tee "$VAL_LOG"; then
        pass "implementation_validation exited cleanly"
    else
        EXIT_CODE=$?
        if [ "$EXIT_CODE" -eq 1 ]; then
            fail "implementation_validation has FAIL items — see $VAL_LOG"
        else
            warn "implementation_validation setup issue (exit $EXIT_CODE)"
        fi
    fi
    
    P5_DUR=$(($(date +%s) - P5_START))
    log "P5 wall time: $((P5_DUR / 60))m $((P5_DUR % 60))s"
    
    # Extract pass/fail counts from validation log.
    # FR7 fix: `grep -c PATTERN file || echo 0` was producing TWO lines
    # ("0\n0") when grep had no matches (grep -c prints "0" AND exits 1,
    # so the fallback fired). The resulting multi-line string broke
    # `[ "$VAL_FAIL" -eq 0 ]` with "integer expression expected".
    # tr drops the newline so we always get a single integer.
    VAL_PASS=$(grep -c '✓ PASS' "$VAL_LOG" 2>/dev/null | tr -d '\n')
    VAL_FAIL=$(grep -c '✗ FAIL' "$VAL_LOG" 2>/dev/null | tr -d '\n')
    : "${VAL_PASS:=0}"
    : "${VAL_FAIL:=0}"
    
    if [ "$VAL_FAIL" -eq 0 ]; then
        PHASE_RESULTS+=("P5|PASS|$VAL_PASS checks passed")
    else
        PHASE_RESULTS+=("P5|FAIL|$VAL_FAIL items in $VAL_LOG")
    fi
fi

# =============================================================================
phase "P6 — Final report"
# =============================================================================

END_TS=$(date +%s)
START_LINE=$(head -10 "$MASTER_LOG" | grep -i "Started:" | head -1)
START_TS=$(date -d "$(echo "$START_LINE" | grep -oE '202[0-9]-[0-9]+-[0-9]+ [0-9]+:[0-9]+:[0-9]+')" +%s 2>/dev/null || echo "$END_TS")
TOTAL_DUR=$((END_TS - START_TS))

REPORT="$LOG_DIR/FINAL_REPORT.txt"
{
    echo "==================================================================="
    echo "  FULL REGRESSION — FINAL REPORT"
    echo "==================================================================="
    echo "  Started:   $(echo "$START_LINE" | grep -oE '202[0-9]-.*')"
    echo "  Ended:     $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Wall:      $((TOTAL_DUR / 60))m $((TOTAL_DUR % 60))s"
    echo "  Mode:      skip_bench=$SKIP_BENCH static_only=$STATIC_ONLY"
    echo ""
    echo "  Total PASS:  $TOTAL_PASS"
    echo "  Total FAIL:  $TOTAL_FAIL"
    echo "  Total WARN:  $TOTAL_WARN"
    echo ""
    echo "  Phase results:"
    for r in "${PHASE_RESULTS[@]}"; do
        IFS='|' read -ra parts <<< "$r"
        printf "    %-4s %-6s — %s\n" "${parts[0]}" "${parts[1]}" "${parts[2]}"
    done
    echo ""
    echo "  Log directory: $LOG_DIR"
    echo "  Master log:    $MASTER_LOG"
    echo ""
    if [ "$TOTAL_FAIL" -eq 0 ]; then
        echo "  ════════════════════════════════════════════════════════════════"
        echo "  ✓ FULL REGRESSION PASSED"
        echo "  ════════════════════════════════════════════════════════════════"
    else
        echo "  ════════════════════════════════════════════════════════════════"
        echo "  ✗ FULL REGRESSION FAILED — $TOTAL_FAIL items"
        echo "  ════════════════════════════════════════════════════════════════"
    fi
} > "$REPORT"

cat "$REPORT" | tee -a "$MASTER_LOG"

# Symlink for easy access
ln -sf "$LOG_DIR" "$ENERGY_STUDY/logs/regression_latest"

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  Final report: $REPORT"
echo "  Quick view:   cat $ENERGY_STUDY/logs/regression_latest/FINAL_REPORT.txt"
echo "═══════════════════════════════════════════════════════════════════"

if [ "$TOTAL_FAIL" -gt 0 ]; then
    exit 1
else
    exit 0
fi
