#!/usr/bin/env bash
# =============================================================================
#  full_regression.sh — Energy + cycles regression (clean architecture v3)
#
#  Design:
#    - Each cell is run by measure_one_cell.py, the SOLE owner of the PPK2
#      for that cell's lifetime. No background hold, no shared driver.
#    - Per cell: PPK2 ON → flash STM32 → server.py (Mode A) → sample to CSV
#                → GDB telemetry → PPK2 OFF.
#    - Flash happens inside each cell while PPK2 is supplying power, so
#      flash never fails for lack of DUT power.
#    - Fail-fast: if a cell fails its retry budget, the whole run aborts.
#    - TRAP cleanup on Ctrl+C / kill: forces PPK2 OFF, kills RPi processes.
#    - Auto-cleanup on start: kills any leftover PPK2 / openocd / server.py
#      processes before pre-flight.
#    - Checkpoints: per-cell state in state.json; --resume continues.
#
#  Hardware wiring (must be in place before running):
#    PPK2 VOUT → STM32 3V3 (IDD jumper REMOVED on Nucleo)
#    PPK2 GND  → STM32 GND
#    PPK2 D0/D1/D2 → STM32 PA0/PA1/PA4
#    RPi GPIO 25/24/18 → STM32 SWCLK/SWDIO/NRST
#    PPK2 USB → host
#    No ST-LINK USB
#
#  Wall time at defaults (10 replicas × 2 curves × 2 modes = 40 cells):
#    BN254 AmorE  : 10 × ~74min = 12.3h
#    BLS   AmorE  : 10 × ~90min = 15.0h
#    BN254 direct : 10 × ~3min  =  0.5h
#    BLS   direct : 10 × ~95min = 15.8h
#    + build + analysis ≈ 30 min
#    TOTAL ≈ 44h
#
#  Smoke run (--smoke): 4 cells × 1 replica × 1 honest_round = ~15 min total
#
#  Usage:
#    bash scripts/full_regression.sh                  # full default
#    bash scripts/full_regression.sh --smoke          # 4-cell smoke (~15min)
#    bash scripts/full_regression.sh --replicas=3     # quick (~12h)
#    bash scripts/full_regression.sh --curves=BN254   # one curve
#    bash scripts/full_regression.sh --modes=A        # AmorE only
#    bash scripts/full_regression.sh --resume         # continue
#    bash scripts/full_regression.sh --dry-run        # plan only
#    bash scripts/full_regression.sh --skip-analysis  # measurements only
#
#  Exit codes:
#    0 = all cells done; report written
#    1 = one or more cells failed; partial report written
#    2 = pre-flight failed
#    3 = aborted (Ctrl+C, kill, fatal error)
# =============================================================================

set -uo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_REPLICAS=10
DEFAULT_CURVES="BN254 BLS12_381"
DEFAULT_MODES="A B"
DEFAULT_HONEST_ROUNDS=61

# Per-cell retry budget: how many times to invoke measure_one_cell.py
# before declaring the cell failed.
CELL_RETRIES=3
RETRY_BACKOFF_S=30
INTER_CELL_SETTLE_S=5

# RPi auto-discovery: env > mdns > fallback IP
if [[ -z "${RPI_HOST:-}" ]]; then
    _mdns=$(getent hosts raspberrypi.local 2>/dev/null | awk '{print $1}' | head -1)
    if [[ -n "$_mdns" ]]; then
        RPI_HOST="$_mdns"
    else
        RPI_HOST="10.164.56.169"
    fi
fi
RPI_USER="${RPI_USER:-pi}"
PPK2_PORT="${PPK2_PORT:-/dev/ttyACM0}"
PPK2_VOLTAGE_MV="${PPK2_VOLTAGE_MV:-3300}"

# ── Self-locate ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ES="$(cd "$SCRIPT_DIR/.." && pwd)"
FW="$ES/firmware/amore-fw"
MEASURE_ONE="$SCRIPT_DIR/measure_one_cell.py"

# ── Argument parsing ───────────────────────────────────────────────────────
REPLICAS="$DEFAULT_REPLICAS"
CURVES="$DEFAULT_CURVES"
MODES="$DEFAULT_MODES"
HONEST_ROUNDS="$DEFAULT_HONEST_ROUNDS"
DRY_RUN=false
RESUME=false
SKIP_ANALYSIS=false
SMOKE=false

for arg in "$@"; do
    case "$arg" in
        --replicas=*)       REPLICAS="${arg#*=}" ;;
        --curves=*)         CURVES="${arg#*=}"; CURVES="${CURVES//,/ }" ;;
        --modes=*)          MODES="${arg#*=}";  MODES="${MODES//,/ }" ;;
        --honest-rounds=*)  HONEST_ROUNDS="${arg#*=}" ;;
        --dry-run)          DRY_RUN=true ;;
        --resume)           RESUME=true ;;
        --skip-analysis)    SKIP_ANALYSIS=true ;;
        --smoke)            SMOKE=true; REPLICAS=1; HONEST_ROUNDS=1 ;;
        -h|--help)
            sed -n '2,55p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

# ── Colours + helpers ──────────────────────────────────────────────────────
R=$'\033[91m'; G=$'\033[92m'; Y=$'\033[93m'
B=$'\033[94m'; C=$'\033[96m'; RST=$'\033[0m'; BOLD=$'\033[1m'

TS="$(date +%Y%m%d_%H%M%S)"
if $RESUME; then
    LATEST="$(ls -td "$ES"/logs/full_regression_* 2>/dev/null | head -1)"
    if [[ -n "$LATEST" ]]; then
        LOG_DIR="$LATEST"
        echo "${C}[resume]${RST} $LOG_DIR"
    else
        echo "${R}--resume: no prior dir${RST}" >&2
        exit 2
    fi
else
    LOG_DIR="$ES/logs/full_regression_${TS}"
    mkdir -p "$LOG_DIR"
fi

CSV_DIR="$LOG_DIR/measurements"
TELEMETRY_DIR="$LOG_DIR/telemetry"
ANALYSIS_DIR="$LOG_DIR/analysis"
STATE_FILE="$LOG_DIR/state.json"
MASTER_LOG="$LOG_DIR/MASTER.log"
mkdir -p "$CSV_DIR" "$TELEMETRY_DIR" "$ANALYSIS_DIR"

log()    { echo "$(date '+%H:%M:%S') $*" | tee -a "$MASTER_LOG"; }
ok()     { log "${G}  ✓${RST} $*"; }
fail()   { log "${R}  ✗${RST} $*"; }
warn()   { log "${Y}  ⚠${RST} $*"; }
info()   { log "${C}  →${RST} $*"; }
head_()  {
    log ""
    log "${BOLD}${B}═════════════════════════════════════════════════════════════${RST}"
    log "${BOLD}${B}  $*${RST}"
    log "${BOLD}${B}═════════════════════════════════════════════════════════════${RST}"
}
led_red()   { log "${R}  ● LED expected: RED${RST}    (PPK2 powering DUT during cell)"; }
led_green() { log "${G}  ● LED expected: GREEN${RST}  (between cells, PPK2 OFF)"; }

# ── Auto-cleanup at startup (BETON-BARZEL) ─────────────────────────────────
# Wait+verify at every step. Never proceeds on "fire and pray".
#
# Steps:
#   1. Stop ModemManager (else it grabs /dev/ttyACM0 randomly → PPK2 unstable)
#   2. Verify MM stopped by polling systemctl is-active (up to 6s)
#   3. Kill local stale processes (PPK2 holds, measure_one_cell)
#   4. Kill RPi-side stale processes (server.py, openocd)
#   5. Force PPK2 DUT power OFF (clean slate)
#   6. Wait for PPK2 to be present AND openable 3× in a row (up to 20s)
#
# Fatal if any verify step fails — we will NOT run an unstable session.
startup_cleanup() {
    info "auto-cleanup (BETON-BARZEL): MM stop, kill stale, force PPK2 OFF, verify"

    # ── Step 1+2: stop ModemManager + verify ──
    info "  [mm] stopping ModemManager"
    sudo -n systemctl stop ModemManager 2>/dev/null || true
    local mm_ok=0 i
    for i in $(seq 1 20); do
        local state
        # is-active returns exit 3 for inactive — that's expected, not error.
        # Capture stdout only; ignore exit code.
        state=$(sudo -n systemctl is-active ModemManager 2>/dev/null)
        state="${state:-unknown}"
        if [[ "$state" == "inactive" || "$state" == "failed" ]]; then
            info "  [mm] confirmed stopped (state=$state)"
            mm_ok=1
            break
        fi
        sleep 0.3
    done
    if [[ $mm_ok -eq 0 ]]; then
        fail "ModemManager did not stop within 6s — refusing to proceed"
        exit 2
    fi

    # ── Step 3: local stale processes ──
    info "  [local] killing stale PPK2 holds, measure_one_cell"
    pkill -f "ppk2_hold\|ppk2_keep_alive\|measure_one_cell" 2>/dev/null || true

    # ── Step 4: RPi-side stale processes ──
    info "  [rpi] killing stale server.py, openocd"
    ssh -o ConnectTimeout=3 -o BatchMode=yes "$RPI_USER@$RPI_HOST" \
        'sudo pkill -f "server.py" 2>/dev/null; sudo pkill -f "openocd" 2>/dev/null' \
        2>/dev/null || true
    sleep 1

    # ── Step 5: Force PPK2 OFF ──
    info "  [ppk2] forcing DUT power OFF (clean slate)"
    python3 /tmp/ppk2_force_off.py 2>&1 | sed 's/^/      /' | tee -a "$MASTER_LOG" || true
    sleep 2

    # ── Step 6: Verify PPK2 present + openable (3× consecutive) ──
    info "  [ppk2] verifying PPK2 stably present + openable (3× consecutive)"
    python3 /tmp/ppk2_verify_stable.py 2>&1 | sed 's/^/      /' | tee -a "$MASTER_LOG"
    local verify_rc=${PIPESTATUS[0]}
    if [[ $verify_rc -ne 0 ]]; then
        fail "PPK2 verify-stable failed — refusing to start measurement"
        exit 2
    fi

    # ── Step 7: BETON-BARZEL PPK2 D-channel health check ──
    # Known PPK2 firmware bug: after heavy SWD + toggle_DUT_power activity,
    # digital channels silently degrade to "always 0". Only USB unplug+replug
    # recovers. We MUST detect this here, before measurement, or all CSVs
    # will be useless for phase-resolved analysis.
    info "  [ppk2-health] verifying D-channels respond (gpio_byte diversity)"
    if python3 "$ES/scripts/lib/ppk2_digital_health_check.py" 2>&1 | sed 's/^/      /' | tee -a "$MASTER_LOG"; then
        ok "PPK2 D-channels healthy"
    else
        fail "PPK2 D-channels stuck — physical USB unplug+replug required"
        exit 2
    fi

    ok "startup cleanup complete — PPK2 ready"
    return 0
}

# ── Cleanup trap ───────────────────────────────────────────────────────────
ABORTED=0
cleanup() {
    local exit_code=$?
    [[ $ABORTED -ne 0 ]] && return
    ABORTED=1

    log ""
    log "${Y}[trap-cleanup]${RST} powering DUT off, killing children"

    # Kill any measure_one_cell.py we might have spawned
    pkill -P $$ -f measure_one_cell 2>/dev/null || true

    # Force PPK2 OFF
    python3 - <<'PYEOF' 2>&1 | sed 's/^/      /' | tee -a "$MASTER_LOG" || true
try:
    import serial.tools.list_ports
    from ppk2_api.ppk2_api import PPK2_API
    port = next((p.device for p in serial.tools.list_ports.comports()
                 if "PPK" in (p.description or "") or "Nordic" in (p.description or "")), None)
    if port:
        ppk = PPK2_API(port, timeout=2, write_timeout=2)
        ppk.get_modifiers()
        ppk.set_source_voltage(3300)
        ppk.use_source_meter()
        ppk.toggle_DUT_power("OFF")
        print("[trap] PPK2 forced OFF")
except Exception as e:
    print(f"[trap] force-off failed: {e}")
PYEOF

    # Kill RPi processes
    ssh -o ConnectTimeout=3 "$RPI_USER@$RPI_HOST" \
        'sudo pkill -f "server.py" 2>/dev/null; sudo pkill -f "openocd" 2>/dev/null' \
        2>/dev/null || true

    [[ $exit_code -ne 0 ]] && log "${R}[trap] exit code: $exit_code${RST}"

    # Restart MM on cleanup so system returns to normal even on Ctrl+C / crash
    sudo -n systemctl start ModemManager 2>/dev/null || true

    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ── State (checkpoint) ─────────────────────────────────────────────────────
state_init() {
    [[ -f "$STATE_FILE" ]] || echo '{"cells":{}}' > "$STATE_FILE"
}
state_get_cell() {
    python3 -c "
import json
with open('$STATE_FILE') as f: s = json.load(f)
print(s.get('cells', {}).get('$1', 'pending'))
"
}
state_set_cell() {
    python3 -c "
import json
with open('$STATE_FILE') as f: s = json.load(f)
s.setdefault('cells', {})['$1'] = '$2'
with open('$STATE_FILE', 'w') as f: json.dump(s, f, indent=2)
"
}

# ── Header ─────────────────────────────────────────────────────────────────
log ""
log "${BOLD}${C}╔══════════════════════════════════════════════════════════════════╗${RST}"
log "${BOLD}${C}║  AmorE Energy Study — Full Regression (clean architecture)       ║${RST}"
log "${BOLD}${C}║  Started: $(date '+%Y-%m-%d %H:%M:%S')                                  ║${RST}"
log "${BOLD}${C}╚══════════════════════════════════════════════════════════════════╝${RST}"
log ""
log "  Repo            : $ES"
log "  Firmware        : $FW"
log "  Log dir         : $LOG_DIR"
log "  Resume          : $RESUME"
log "  Smoke           : $SMOKE"
log "  Curves          : $CURVES"
log "  Modes           : $MODES   (A=AmorE, B=direct)"
log "  Replicas/cell   : $REPLICAS"
log "  Honest rounds   : $HONEST_ROUNDS"
log "  RPi             : $RPI_USER@$RPI_HOST"
log "  PPK2            : $PPK2_PORT @ ${PPK2_VOLTAGE_MV}mV"
log "  Cell retries    : $CELL_RETRIES"

state_init

# =============================================================================
#  Pre-flight
# =============================================================================
head_ "PRE-FLIGHT"

PREFLIGHT_FAIL=0

if [[ -f "$ES/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$ES/.venv/bin/activate"
    ok "venv: $(python --version)"
else
    fail "venv missing"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

if python3 -c "import numpy, scipy, matplotlib, pandas, serial; from ppk2_api.ppk2_api import PPK2_API" 2>/dev/null; then
    ok "python deps importable"
else
    fail "python deps missing"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

if lsusb 2>/dev/null | grep -qi '1915:c00a\|PPK2'; then
    ok "PPK2 enumerated"
else
    fail "PPK2 not enumerated"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi
if [[ -c "$PPK2_PORT" ]]; then
    ok "PPK2 port: $PPK2_PORT"
else
    fail "PPK2 port missing: $PPK2_PORT"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

if command -v arm-none-eabi-gcc &>/dev/null; then
    ok "arm-none-eabi-gcc"
else
    fail "arm-none-eabi-gcc missing"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi
if command -v gdb-multiarch &>/dev/null || command -v arm-none-eabi-gdb &>/dev/null; then
    ok "GDB available"
else
    fail "no GDB"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

if ping -c 1 -W 2 "$RPI_HOST" &>/dev/null; then
    ok "RPi ping OK ($RPI_HOST)"
else
    fail "RPi unreachable: $RPI_HOST"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi
if ssh -o ConnectTimeout=3 -o BatchMode=yes "$RPI_USER@$RPI_HOST" true 2>/dev/null; then
    ok "RPi SSH passwordless"
else
    fail "RPi SSH needs password"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi
if ssh "$RPI_USER@$RPI_HOST" 'which openocd && test -f /home/pi/rpi_swd.cfg && test -f /home/pi/amore-bn254-cortex-m4/rpi/server.py' &>/dev/null; then
    ok "RPi has openocd + rpi_swd.cfg + server.py"
else
    fail "RPi missing openocd / cfg / server.py"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

if [[ -f "$MEASURE_ONE" ]]; then
    ok "measure_one_cell.py present at $MEASURE_ONE"
else
    fail "measure_one_cell.py missing at $MEASURE_ONE"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi
if python3 "$MEASURE_ONE" --help 2>&1 | grep -q "curve.*BN254.*BLS12_381"; then
    ok "measure_one_cell.py --help OK"
else
    fail "measure_one_cell.py --help broken"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL+1))
fi

startup_cleanup

if [[ $PREFLIGHT_FAIL -gt 0 ]]; then
    log ""
    log "${R}${BOLD}══ PRE-FLIGHT FAILED — $PREFLIGHT_FAIL issues ══${RST}"
    trap - EXIT INT TERM   # no PPK2 to OFF since we may not have touched it
    exit 2
fi
ok "All pre-flight passed"

if $DRY_RUN; then
    head_ "DRY RUN — plan only"
    n_cells=0
    for curve in $CURVES; do
        for mode in $MODES; do
            for r in $(seq 1 "$REPLICAS"); do
                key="${curve,,}__${mode}__r${r}"
                s=$(state_get_cell "$key")
                log "  $key → $s"
                n_cells=$((n_cells+1))
            done
        done
    done
    log ""
    log "  Total: $n_cells cells"
    trap - EXIT INT TERM
    exit 0
fi

# =============================================================================
#  Phase 1: Build firmware
# =============================================================================
head_ "PHASE 1 — Build firmware (BN254/BLS × A/B)"

cd "$FW"
P1_START=$(date +%s)
BUILD_FAIL=0

for curve in $CURVES; do
    for mode in $MODES; do
        curve_lc="${curve,,}"
        mode_lc="${mode,,}"
        build_dir="build/${curve_lc}_${mode_lc}"
        # Mode A: AmorE protocol ELF (amore_${curve}.elf, pure C, no RELIC)
        # Mode B: RELIC pairing benchmark ELF (relic_bench_${curve}.elf,
        #         RELIC easy backend = pure C apples-to-apples vs Mode A)
        if [[ "$mode" == "A" ]]; then
            elf_name="amore_${curve_lc}.elf"
        else
            elf_name="relic_bench_${curve_lc}.elf"
        fi
        build_log="$LOG_DIR/build_${curve}_${mode}.log"

        info "Building $curve Mode $mode → $elf_name"
        rm -rf "$build_dir"
        if cmake -B "$build_dir" \
                -DCMAKE_BUILD_TYPE=Release \
                -DCURVE="$curve" \
                -DMEASUREMENT_MODE="$mode" \
                > "$build_log" 2>&1 \
           && cmake --build "$build_dir" --target "$elf_name" --parallel "$(nproc)" >> "$build_log" 2>&1; then
            elf_path="$build_dir/$elf_name"
            if [[ -f "$elf_path" ]]; then
                size_line=$(arm-none-eabi-size "$elf_path" | tail -1)
                ok "$curve $mode  ($size_line)"
            else
                fail "$curve $mode: ELF missing"
                BUILD_FAIL=$((BUILD_FAIL+1))
            fi
        else
            fail "$curve $mode: cmake/build failed (see $build_log)"
            BUILD_FAIL=$((BUILD_FAIL+1))
        fi
    done
done

P1_DUR=$(($(date +%s) - P1_START))
log ""
log "  Phase 1 wall: $((P1_DUR/60))m $((P1_DUR%60))s"
if [[ $BUILD_FAIL -gt 0 ]]; then
    fail "$BUILD_FAIL build(s) failed — aborting"
    exit 1
fi

# =============================================================================
#  Phase 2: Sanity (pytest)
# =============================================================================
head_ "PHASE 2 — Sanity (pytest)"

cd "$ES"
PYTEST_LOG="$LOG_DIR/pytest.log"
if python3 -m pytest -q --tb=short > "$PYTEST_LOG" 2>&1; then
    summary=$(grep -E '^[0-9]+ passed' "$PYTEST_LOG" | tail -1)
    ok "pytest: $summary"
else
    warn "pytest had failures (continuing)"
    tail -10 "$PYTEST_LOG" | sed 's/^/    /' | tee -a "$MASTER_LOG"
fi

# =============================================================================
#  Phase 3: Energy measurement (per-cell ownership)
# =============================================================================
head_ "PHASE 3 — Energy measurement"

P3_START=$(date +%s)
log "  Architecture: measure_one_cell.py owns PPK2 per-cell"
log "  LED pattern: RED during each cell, GREEN between cells"
log ""

TOTAL_CELLS=$(( $(echo $CURVES | wc -w) * $(echo $MODES | wc -w) * REPLICAS ))
CELLS_DONE=0
CELLS_FAIL=0
CELLS_SKIP=0
CELL_IDX=0

for curve in $CURVES; do
    for mode in $MODES; do
        curve_lc="${curve,,}"
        mode_lc="${mode,,}"
        # Mode-aware ELF selection — see Phase 1 comment
        if [[ "$mode" == "A" ]]; then
            elf="$FW/build/${curve_lc}_${mode_lc}/amore_${curve_lc}.elf"
        else
            elf="$FW/build/${curve_lc}_${mode_lc}/relic_bench_${curve_lc}.elf"
        fi

        for replica in $(seq 1 "$REPLICAS"); do
            CELL_IDX=$((CELL_IDX+1))
            cell_key="${curve_lc}__${mode}__r${replica}"
            log ""
            log "${BOLD}${B}─── Cell $CELL_IDX/$TOTAL_CELLS: $cell_key ───${RST}"

            cell_state=$(state_get_cell "$cell_key")
            if [[ "$cell_state" == "done" ]]; then
                info "[skip] resumed (already done)"
                CELLS_SKIP=$((CELLS_SKIP+1))
                continue
            fi
            state_set_cell "$cell_key" "running"

            cell_dir="$CSV_DIR/$cell_key"
            mkdir -p "$cell_dir"
            cell_log="$LOG_DIR/cell_${cell_key}.log"

            if [[ ! -f "$elf" ]]; then
                fail "ELF missing: $elf"
                state_set_cell "$cell_key" "fail-noelf"
                exit 1
            fi

            led_red

            # Build measure_one_cell.py invocation
            mone_args=(
                --curve "$curve"
                --mode "$mode"
                --replica "$replica"
                --elf "$elf"
                --out "$cell_dir"
                --rpi-user "$RPI_USER"
                --rpi-host "$RPI_HOST"
                --ppk2-port "$PPK2_PORT"
                --voltage-mv "$PPK2_VOLTAGE_MV"
                --honest-rounds "$HONEST_ROUNDS"
            )
            if $SMOKE; then
                mone_args+=(--smoke)
            fi

            # Run with retries
            attempt=1
            cell_ok=0
            while [[ $attempt -le $CELL_RETRIES ]]; do
                if [[ $attempt -gt 1 ]]; then
                    info "[retry] attempt $attempt/$CELL_RETRIES (backoff ${RETRY_BACKOFF_S}s)"
                    sleep "$RETRY_BACKOFF_S"
                fi
                t_cell=$(date +%s)
                if python3 "$MEASURE_ONE" "${mone_args[@]}" >> "$cell_log" 2>&1; then
                    cell_dur=$(($(date +%s) - t_cell))
                    csv_path="$cell_dir/run_001.csv"
                    if [[ -s "$csv_path" ]]; then
                        n_samples=$(wc -l < "$csv_path")
                        ok "cell done (${cell_dur}s wall, $n_samples CSV rows)"
                        # Copy telemetry to telemetry_dir
                        if [[ -f "$cell_dir/telemetry.txt" ]]; then
                            cp "$cell_dir/telemetry.txt" "$TELEMETRY_DIR/${cell_key}.txt"
                        fi
                        cell_ok=1
                        break
                    else
                        warn "[cell] CSV empty/missing (attempt $attempt)"
                    fi
                else
                    rc=$?
                    warn "[cell] measure_one_cell.py exited $rc (attempt $attempt). tail:"
                    tail -10 "$cell_log" | sed 's/^/      /' | tee -a "$MASTER_LOG"
                fi
                attempt=$((attempt+1))
            done

            if [[ $cell_ok -eq 0 ]]; then
                fail "[cell] $cell_key failed after $CELL_RETRIES attempts — aborting"
                state_set_cell "$cell_key" "fail"
                exit 1
            fi

            state_set_cell "$cell_key" "done"
            CELLS_DONE=$((CELLS_DONE+1))

            led_green
            sleep "$INTER_CELL_SETTLE_S"

            elapsed=$(($(date +%s) - P3_START))
            remaining=$((TOTAL_CELLS - CELLS_DONE - CELLS_SKIP))
            if [[ $CELLS_DONE -gt 0 && $remaining -gt 0 ]]; then
                eta=$(( elapsed * remaining / CELLS_DONE ))
                info "[progress] done=$CELLS_DONE skip=$CELLS_SKIP fail=$CELLS_FAIL ETA=$((eta/3600))h$((eta%3600/60))m"
            fi
        done
    done
done

P3_DUR=$(($(date +%s) - P3_START))
log ""
log "  Phase 3 wall: $((P3_DUR/3600))h $((P3_DUR%3600/60))m"
log "  Cells: done=$CELLS_DONE skip=$CELLS_SKIP fail=$CELLS_FAIL total=$TOTAL_CELLS"

# =============================================================================
#  Phase 4: Analysis
# =============================================================================
if $SKIP_ANALYSIS; then
    head_ "PHASE 4 — SKIPPED (--skip-analysis)"
else
    head_ "PHASE 4 — Analysis"
    cd "$ES"
    analysis_log="$LOG_DIR/analysis.log"

    info "parse_traces on all CSVs"
    if find "$CSV_DIR" -name "run_*.csv" -print0 | xargs -0 -I{} \
            python3 -m analysis.parse_traces {} >> "$analysis_log" 2>&1; then
        ok "parse_traces done"
    else
        warn "parse_traces errors — see $analysis_log"
    fi

    if python3 -m analysis.compute_energy "$CSV_DIR" --out "$ANALYSIS_DIR/energy.json" \
            >> "$analysis_log" 2>&1; then
        ok "compute_energy → $ANALYSIS_DIR/energy.json"
    else
        warn "compute_energy failed"
    fi

    for fig in fig1_energy_vs_n fig2_memory fig3_time_vs_n fig4_crossover fig5_phase_breakdown; do
        if python3 -m "analysis.figures.${fig}" \
                --out "$ANALYSIS_DIR/${fig}.png" >> "$analysis_log" 2>&1; then
            ok "$fig.png"
        else
            warn "$fig failed"
        fi
    done
fi

# =============================================================================
#  Phase 5: Final report
# =============================================================================
head_ "PHASE 5 — Final report"

REPORT="$LOG_DIR/FINAL_REPORT.txt"
{
    echo "================================================================="
    echo "  AmorE Energy Study — Full Regression Report"
    echo "================================================================="
    echo "  Ended  : $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Logdir : $LOG_DIR"
    echo "  Mode   : $($SMOKE && echo "SMOKE" || echo "FULL")"
    echo ""
    echo "  Plan    : $(echo $CURVES | wc -w) curves × $(echo $MODES | wc -w) modes × $REPLICAS replicas = $TOTAL_CELLS cells"
    echo "  Done    : $CELLS_DONE"
    echo "  Skipped : $CELLS_SKIP"
    echo "  Failed  : $CELLS_FAIL"
    echo ""
    echo "  CSVs   : $CSV_DIR"
    echo "  Telem  : $TELEMETRY_DIR"
    if ! $SKIP_ANALYSIS; then
        echo "  Energy : $ANALYSIS_DIR/energy.json"
        echo "  Figs   : $ANALYSIS_DIR/fig{1,2,3,4,5}_*.png"
    fi
    echo ""
    if [[ $CELLS_FAIL -eq 0 ]]; then
        echo "  ════════════════════════════════════════════════════════════════"
        echo "  ✓ FULL REGRESSION COMPLETED"
        echo "  ════════════════════════════════════════════════════════════════"
    else
        echo "  ════════════════════════════════════════════════════════════════"
        echo "  ✗ COMPLETED WITH $CELLS_FAIL FAILED CELLS"
        echo "  ════════════════════════════════════════════════════════════════"
    fi
} > "$REPORT"

cat "$REPORT" | tee -a "$MASTER_LOG"
ln -sfn "$LOG_DIR" "$ES/logs/full_regression_latest"

echo ""
echo "  Master log:  $MASTER_LOG"
echo "  Final report: $REPORT"
echo ""

# ── BETON-BARZEL final cleanup: restart ModemManager for normal system use ──
info "[final] restarting ModemManager (was stopped for PPK2 stability)"
sudo -n systemctl start ModemManager 2>/dev/null || true
for i in $(seq 1 20); do
    state=$(sudo -n systemctl is-active ModemManager 2>/dev/null)
    state="${state:-unknown}"
    if [[ "$state" == "active" ]]; then
        info "[final] ✓ ModemManager active again (${i}*0.5s)"
        break
    fi
    sleep 0.5
done

[[ $CELLS_FAIL -gt 0 ]] && exit 1
exit 0
