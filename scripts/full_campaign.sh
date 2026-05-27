#!/usr/bin/env bash
# =============================================================================
#  full_campaign.sh — Run AmorE Energy Study campaign with PPK2 unplug-once
#  per firmware (the only way to recover from PPK2 D-channel firmware bug).
#
#  Architecture:
#    For each (curve, mode):
#      1. Flash firmware (one time)
#      2. Big prompt → user unplugs+replugs PPK2 once
#      3. Run N replicas using NRST between (no SWD, PPK2 stays open)
#
#  Wall time: ~30h for 40 cells. Manual unplugs: 4 total (one per firmware).
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ES="$(cd "$SCRIPT_DIR/.." && pwd)"
FW="$ES/firmware/amore-fw"

REPLICAS="${REPLICAS:-10}"
DURATION_S="${DURATION_S:-220}"
CURVES="${CURVES:-BN254 BLS12_381}"
MODES="${MODES:-A B}"
SMOKE=false
if [[ "${1:-}" == "--smoke" ]]; then
    SMOKE=true
    REPLICAS=1
    DURATION_S=30
fi

TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$ES/logs/full_campaign_${TS}"
mkdir -p "$LOG_DIR"
MASTER_LOG="$LOG_DIR/MASTER.log"

log()  { echo "$(date '+%H:%M:%S') $*" | tee -a "$MASTER_LOG"; }
head_(){ log ""; log "═══════════════════════════════════════════════════════════════"
         log "  $*"; log "═══════════════════════════════════════════════════════════════"; }

head_ "AmorE Energy Study — Full Campaign (PPK2-stable architecture)"
log "  Smoke      : $SMOKE"
log "  Replicas   : $REPLICAS per firmware × $DURATION_S s"
log "  Curves     : $CURVES"
log "  Modes      : $MODES   (A=AmorE Mode A, B=RELIC direct Mode B)"
log "  Log dir    : $LOG_DIR"
log ""

# Build all 4 ELFs first
head_ "Phase 1 — Build all firmwares"
cd "$FW"
ALL_OK=true
for curve in BN254 BLS12_381; do
    for mode in A B; do
        curve_lc="${curve,,}"
        mode_lc="${mode,,}"
        build_dir="build/${curve_lc}_${mode_lc}"
        if [[ "$mode" == "A" ]]; then
            elf="amore_${curve_lc}.elf"
        else
            elf="relic_bench_${curve_lc}.elf"
        fi
        if [[ ! -f "$build_dir/$elf" ]]; then
            log "  ✗ $build_dir/$elf MISSING — please run build_all first"
            ALL_OK=false
        else
            size=$(arm-none-eabi-size "$build_dir/$elf" | tail -1)
            log "  ✓ $build_dir/$elf ($size)"
        fi
    done
done
if ! $ALL_OK; then
    log "✗ Missing ELFs — abort"
    exit 1
fi

# Run each firmware
head_ "Phase 2 — Run all firmwares"
CELL_IDX=0
CELLS_OK=0
CELLS_FAIL=0
for curve in $CURVES; do
    for mode in $MODES; do
        CELL_IDX=$((CELL_IDX + 1))
        curve_lc="${curve,,}"
        mode_lc="${mode,,}"
        build_dir="$FW/build/${curve_lc}_${mode_lc}"
        if [[ "$mode" == "A" ]]; then
            elf_path="$build_dir/amore_${curve_lc}.elf"
        else
            elf_path="$build_dir/relic_bench_${curve_lc}.elf"
        fi
        cell_dir="$LOG_DIR/${curve_lc}__${mode}"
        cell_log="$LOG_DIR/${curve_lc}__${mode}.log"

        head_ "Firmware $CELL_IDX/4: $curve Mode $mode → $REPLICAS replicas"
        log "  ELF: $elf_path"
        log "  Out: $cell_dir"

        if python3 "$SCRIPT_DIR/flash_once_then_replicas.py" \
                --curve "$curve" --mode "$mode" \
                --elf "$elf_path" \
                --out-dir "$cell_dir" \
                --replicas "$REPLICAS" \
                --duration-s "$DURATION_S" \
                2>&1 | tee -a "$cell_log" | tee -a "$MASTER_LOG"; then
            log "  ✓ $curve $mode completed"
            CELLS_OK=$((CELLS_OK + 1))
        else
            log "  ✗ $curve $mode FAILED"
            CELLS_FAIL=$((CELLS_FAIL + 1))
        fi
    done
done

head_ "Campaign complete"
log "  Cells OK   : $CELLS_OK"
log "  Cells FAIL : $CELLS_FAIL"
log "  Logs       : $LOG_DIR"

# Restart MM
sudo -n systemctl start ModemManager 2>/dev/null || true

[[ $CELLS_FAIL -gt 0 ]] && exit 1
exit 0
