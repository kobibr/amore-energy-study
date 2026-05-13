#!/usr/bin/env bash
# mini_regression.sh — 10-minute wide smoke test of the analysis stack.
#
# Goal: exercise every code path once, with sanity checks that the outputs
# are PLAUSIBLE (not zeros, not NaN, not identical across replicas). NOT
# statistically meaningful — just "does the wheel turn end-to-end".
#
# 8 layers, each = one minimal step + sanity check:
#   1. venv + deps
#   2. 188 unit tests
#   3. firmware build matrix (Mode A only, BLS12_381 + BN254)
#   4. run_cell.py smoke (5s)
#   5. run_cell.py with --duration 10s, idempotency check
#   6. synthetic data regeneration (10 cells)
#   7. all 4 figures + size sanity
#   8. analysis output plausibility (BatchModel + crossover)
#
# Each layer: exits non-zero on failure with a clear message, so you can
# spot exactly where the chain broke.

set -uo pipefail   # NOT -e — we want to see all failures, not just the first
ES=~/amore-energy-study
cd "${ES}"

# colours for output
R=$'\033[91m'; G=$'\033[92m'; Y=$'\033[93m'; B=$'\033[94m'; C=$'\033[96m'; RST=$'\033[0m'; BOLD=$'\033[1m'

LOG_DIR=/tmp/mini_regression_$(date +%Y%m%d_%H%M%S)
mkdir -p "${LOG_DIR}"

n_pass=0
n_fail=0
n_warn=0
declare -a failures=()
declare -a warnings=()

layer() { printf "\n${BOLD}${B}═══ Layer %s ═══${RST}  %s\n" "$1" "$2"; }
ok()    { printf "  ${G}✓${RST} %s\n" "$*"; n_pass=$((n_pass+1)); }
fail()  { printf "  ${R}✗${RST} %s\n" "$*"; n_fail=$((n_fail+1)); failures+=("$*"); }
warn()  { printf "  ${Y}⚠${RST} %s\n" "$*"; n_warn=$((n_warn+1)); warnings+=("$*"); }
info()  { printf "  ${C}→${RST} %s\n" "$*"; }

t_global=$(date +%s)

# =============================================================================
# Layer 1 — venv + deps
# =============================================================================
layer 1 "venv + python deps"

if [ ! -d .venv ]; then
    fail ".venv missing; create with 'python3 -m venv .venv'"
else
    ok ".venv exists"
fi

source .venv/bin/activate

if python3 -c 'import numpy, scipy, matplotlib, pandas, pytest' 2>/dev/null; then
    ok "numpy, scipy, matplotlib, pandas, pytest all importable"
else
    fail "one of numpy/scipy/matplotlib/pandas/pytest missing in venv"
fi

if python3 -c 'from analysis.parse_traces import parse_trace; from analysis.sleep_model import BatchModel' 2>/dev/null; then
    ok "analysis package importable"
else
    fail "analysis package not importable (run 'pip install -e .')"
fi

# =============================================================================
# Layer 2 — unit tests (188 total)
# =============================================================================
layer 2 "188 unit tests"

t0=$(date +%s)
python3 -m pytest analysis/tests/ measurement/ppk2-control/tests/ -q \
    > "${LOG_DIR}/02_pytest.log" 2>&1
rc=$?
elapsed=$(( $(date +%s) - t0 ))

if [ $rc -eq 0 ]; then
    n_tests=$(grep -oE '[0-9]+ passed' "${LOG_DIR}/02_pytest.log" | head -1 | grep -oE '[0-9]+')
    ok "all ${n_tests} tests passed in ${elapsed}s"
    if [ "${n_tests:-0}" -lt 180 ]; then
        warn "only ${n_tests} tests collected — expected ~188"
    fi
else
    fail "pytest failed; see ${LOG_DIR}/02_pytest.log"
    tail -20 "${LOG_DIR}/02_pytest.log" | sed 's/^/    /'
fi

# =============================================================================
# Layer 3 — firmware build matrix
# =============================================================================
layer 3 "firmware build matrix (Mode A only — fast path)"

FW=~/amore-energy-study/firmware/amore-fw
if [ ! -d "${FW}" ]; then
    fail "firmware submodule missing at ${FW}"
else
    t0=$(date +%s)
    for curve in BLS12_381 BN254; do
        BUILD_DIR="${LOG_DIR}/build_${curve,,}"
        rm -rf "${BUILD_DIR}"
        info "building ${curve} Mode A with triggers..."
        if cmake -S "${FW}" -B "${BUILD_DIR}" \
                 -DCURVE="${curve}" -DMEASUREMENT_MODE=A -DAMORE_TRIGGERS_ENABLED=1 \
                 > "${LOG_DIR}/03_${curve,,}_cmake.log" 2>&1 && \
           cmake --build "${BUILD_DIR}" --target "amore_${curve,,}.elf" --parallel "$(nproc)" \
                 > "${LOG_DIR}/03_${curve,,}_build.log" 2>&1; then
            # sanity: ELF has nonzero text section
            text_size=$(arm-none-eabi-size "${BUILD_DIR}/amore_${curve,,}.elf" 2>/dev/null | \
                        awk 'NR==2 {print $1}')
            if [ -n "${text_size}" ] && [ "${text_size}" -gt 5000 ]; then
                ok "${curve} amore.elf built  (text=${text_size} bytes)"
            else
                fail "${curve} ELF text section suspicious: ${text_size}"
            fi
        else
            fail "${curve} build failed; see ${LOG_DIR}/03_${curve,,}_*.log"
        fi
    done
    elapsed=$(( $(date +%s) - t0 ))
    info "firmware build matrix done in ${elapsed}s"
fi

# Also try Stop-mode variants (they're tiny, fast)
info "building stop_test + wakeup_burst variants..."
BUILD_DIR="${LOG_DIR}/build_stop"
rm -rf "${BUILD_DIR}"
if cmake -S "${FW}" -B "${BUILD_DIR}" -DCURVE=BLS12_381 -DAMORE_TRIGGERS_ENABLED=1 \
         > "${LOG_DIR}/03_stop_cmake.log" 2>&1 && \
   cmake --build "${BUILD_DIR}" --target stop_test.elf --target wakeup_burst.elf --target stop_test_nostop.elf --parallel "$(nproc)" \
         > "${LOG_DIR}/03_stop_build.log" 2>&1; then
    for tgt in stop_test wakeup_burst stop_test_nostop; do
        if [ -f "${BUILD_DIR}/${tgt}.elf" ]; then
            text=$(arm-none-eabi-size "${BUILD_DIR}/${tgt}.elf" | awk 'NR==2 {print $1}')
            ok "${tgt}.elf built  (text=${text} bytes)"
        else
            fail "${tgt}.elf missing after build"
        fi
    done
else
    fail "stop-mode variants build failed; see ${LOG_DIR}/03_stop_*.log"
fi

# =============================================================================
# Layer 4 — run_cell.py smoke (5s, fake-script:idle)
# =============================================================================
layer 4 "run_cell.py smoke (5s, idle scenario)"

SMOKE_DIR="${LOG_DIR}/smoke_layer4"
rm -rf "${SMOKE_DIR}"
t0=$(date +%s)
python3 -u scripts/run_cell.py --smoke --out "${SMOKE_DIR}" \
    > "${LOG_DIR}/04_smoke.log" 2>&1
rc=$?
elapsed=$(( $(date +%s) - t0 ))

if [ $rc -ne 0 ]; then
    fail "run_cell.py --smoke failed (rc=${rc})"
    tail -20 "${LOG_DIR}/04_smoke.log" | sed 's/^/    /'
else
    CSV=$(ls "${SMOKE_DIR}"/*/run_001.csv 2>/dev/null | head -1)
    if [ -z "${CSV}" ]; then
        fail "no CSV produced"
    else
        n_samples=$(( $(wc -l < "${CSV}") - 1 ))
        # expect ~125000 at 25 kHz × 5s
        if [ "${n_samples}" -ge 100000 ] && [ "${n_samples}" -le 150000 ]; then
            ok "smoke CSV has ${n_samples} samples (expected ~125000)  [${elapsed}s wall]"
        else
            warn "smoke CSV sample count off: ${n_samples} (expected ~125000)"
        fi

        # Sanity: not all zeros, not all identical
        n_distinct=$(awk -F, 'NR>1 {print $2}' "${CSV}" | sort -u | head -10 | wc -l)
        if [ "${n_distinct}" -ge 5 ]; then
            ok "current samples have ${n_distinct}+ distinct values (not constant)"
        else
            fail "current samples too repetitive (only ${n_distinct} distinct)"
        fi

        # Sanity: voltage column is 3.3V everywhere
        n_bad_v=$(awk -F, 'NR>1 && $3 != "3.300" {print}' "${CSV}" | wc -l)
        if [ "${n_bad_v}" -eq 0 ]; then
            ok "voltage is 3.300V on all samples"
        else
            warn "${n_bad_v} samples have voltage ≠ 3.300"
        fi

        # Sanity: mean current ~ 50 mA (idle scenario)
        mean_uA=$(awk -F, 'NR>1 {s+=$2; n++} END {if (n>0) printf "%.0f", s/n}' "${CSV}")
        if [ -n "${mean_uA}" ] && [ "${mean_uA}" -gt 45000 ] && [ "${mean_uA}" -lt 55000 ]; then
            ok "mean current = ${mean_uA} µA (expected ~50000 for idle)"
        else
            fail "mean current = ${mean_uA} µA — out of plausible idle range"
        fi
    fi
fi

# =============================================================================
# Layer 5 — run_cell.py idempotency check
# =============================================================================
layer 5 "run_cell.py idempotency (re-run, expect skip)"

t0=$(date +%s)
python3 -u scripts/run_cell.py --smoke --out "${SMOKE_DIR}" \
    > "${LOG_DIR}/05_idempotent.log" 2>&1
rc=$?
elapsed=$(( $(date +%s) - t0 ))

if [ $rc -ne 0 ]; then
    fail "idempotent re-run failed (rc=${rc})"
elif grep -q '\[skip\]' "${LOG_DIR}/05_idempotent.log"; then
    ok "idempotent re-run skipped existing CSV in ${elapsed}s"
else
    warn "re-run did not skip (no [skip] marker in log)"
fi

# =============================================================================
# Layer 6 — regenerate synthetic data
# =============================================================================
layer 6 "synthetic data regeneration (10 cells)"

# Move existing traces aside, regenerate, compare
BACKUP_TRACES="${LOG_DIR}/traces_before"
mkdir -p "${BACKUP_TRACES}"
if [ -d measurement/traces ] && [ -n "$(ls -A measurement/traces 2>/dev/null)" ]; then
    cp -r measurement/traces/* "${BACKUP_TRACES}/" 2>/dev/null || true
    info "backed up existing traces to ${BACKUP_TRACES}"
fi

t0=$(date +%s)
python3 << 'PY' > "${LOG_DIR}/06_synth.log" 2>&1
from pathlib import Path
from analysis.fixtures.synthetic_cells import CellSpec, write_synthetic_cell

out = Path("measurement/traces")
out.mkdir(parents=True, exist_ok=True)
specs = [
    CellSpec("BN254",     "A", n=1,  replicas=3),
    CellSpec("BN254",     "A", n=3,  replicas=3),
    CellSpec("BN254",     "A", n=10, replicas=3),
    CellSpec("BN254",     "A", n=30, replicas=3),
    CellSpec("BLS12_381", "A", n=1,  replicas=3),
    CellSpec("BLS12_381", "A", n=3,  replicas=3),
    CellSpec("BLS12_381", "A", n=10, replicas=3),
    CellSpec("BLS12_381", "A", n=30, replicas=3),
    CellSpec("BN254",     "B", n=10, replicas=3),
    CellSpec("BLS12_381", "B", n=10, replicas=3),
]
for s in specs:
    cd = write_synthetic_cell(s, out)
    print(f"  ✓ {cd.name}")
PY
rc=$?
elapsed=$(( $(date +%s) - t0 ))

if [ $rc -eq 0 ]; then
    n_cells=$(ls -d measurement/traces/*__r3 2>/dev/null | wc -l)
    if [ "${n_cells}" -eq 10 ]; then
        ok "10 cells regenerated in ${elapsed}s"
    else
        fail "expected 10 cells, got ${n_cells}"
    fi

    # Sanity: check CSV size — different N must yield different sizes
    s_n1=$(stat -c%s measurement/traces/bls12_381__a__N1__r3/run_001.csv 2>/dev/null)
    s_n30=$(stat -c%s measurement/traces/bls12_381__a__N30__r3/run_001.csv 2>/dev/null)
    if [ -n "${s_n1}" ] && [ -n "${s_n30}" ] && [ "${s_n30}" -gt $(( s_n1 * 5 )) ]; then
        ok "N=30 CSV (${s_n30} B) much larger than N=1 (${s_n1} B) — expected"
    else
        warn "N=1 vs N=30 CSV size ratio looks off (${s_n1} vs ${s_n30})"
    fi

    # Sanity: different replicas (seed) yield different content
    h1=$(sha256sum measurement/traces/bls12_381__a__N10__r3/run_001.csv | cut -c1-16)
    h2=$(sha256sum measurement/traces/bls12_381__a__N10__r3/run_002.csv | cut -c1-16)
    if [ "${h1}" != "${h2}" ]; then
        ok "replicas have distinct content (rng seed working)"
    else
        fail "replicas have identical content — rng seed broken"
    fi
else
    fail "synthetic data regeneration failed; see ${LOG_DIR}/06_synth.log"
fi

# =============================================================================
# Layer 7 — all 4 figures
# =============================================================================
layer 7 "generate all 4 figures + size sanity"

rm -f figures/fig*.png
t0=$(date +%s)
for plot in plot_energy_per_round plot_phase_breakdown plot_mode_comparison plot_crossover; do
    python3 -m "analysis.${plot}" \
        > "${LOG_DIR}/07_${plot}.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
        fail "${plot} failed (rc=${rc})"
        tail -10 "${LOG_DIR}/07_${plot}.log" | sed 's/^/    /'
    fi
done
elapsed=$(( $(date +%s) - t0 ))

n_figs=$(ls figures/fig*.png 2>/dev/null | wc -l)
if [ "${n_figs}" -eq 4 ]; then
    ok "all 4 figures generated in ${elapsed}s"
    # sanity: each PNG has nonzero size and >5KB (not blank/error)
    for f in figures/fig*.png; do
        size=$(stat -c%s "${f}")
        if [ "${size}" -gt 5000 ]; then
            ok "  $(basename ${f}): ${size} bytes"
        else
            fail "  $(basename ${f}) suspiciously small: ${size} bytes"
        fi
    done
else
    fail "expected 4 figures, got ${n_figs}"
fi

# =============================================================================
# Layer 8 — analysis output plausibility
# =============================================================================
layer 8 "analysis output plausibility (numbers make sense)"

python3 << 'PY' > "${LOG_DIR}/08_analysis.log" 2>&1
"""Sanity check: parse a synthetic CSV, compute energy, verify the numbers
look right against the spec table."""
from pathlib import Path
from analysis.parse_traces import parse_trace
from analysis.compute_energy import compute_trace
from analysis.variance_summary import summarize_replicas
from analysis.sleep_model import BatchModel, analyze, find_crossover

results = []

# Sanity 1: parse BLS12_381 N=10 trace, check phase counts
cell_dir = Path("measurement/traces/bls12_381__a__N10__r3")
csvs = sorted(cell_dir.glob("run_*.csv"))
traces = []
for c in csvs:
    phases = parse_trace(c)
    traces.append(compute_trace(phases))
    # Expected: 1 OTS phase + 10 × (Blind + Wait + Verify + Idle) + framing idles
    # = 1 + 40 + 3 = ~44 phases. Tolerance ±5.
    n_phases = len(phases)
    if 35 <= n_phases <= 55:
        results.append(("PASS", f"N=10 trace has {n_phases} phases (expected ~44)"))
    else:
        results.append(("FAIL", f"N=10 trace has {n_phases} phases — out of plausible range"))

# Sanity 2: idle current should be ~50 mA, setup ~85 mA, wait ~55 mA
te = traces[0]
for gb, expected, label in [(0, 50_000, "idle"), (1, 85_000, "setup/verify"), (2, 55_000, "serverwait")]:
    agg = te.by_gpio_byte.get(gb)
    if agg is None:
        results.append(("WARN", f"gpio_byte={gb} ({label}) not in trace"))
        continue
    err_pct = abs(agg.mean_current_uA - expected) / expected * 100
    if err_pct < 5:
        results.append(("PASS", f"gpio_byte={gb} ({label}): {agg.mean_current_uA:.0f} µA (expected {expected}, err {err_pct:.1f}%)"))
    else:
        results.append(("FAIL", f"gpio_byte={gb} ({label}): {agg.mean_current_uA:.0f} µA — off by {err_pct:.1f}%"))

# Sanity 3: total energy should be a few hundred mJ (not 0, not GJ)
if 0.05 < te.total_energy_J < 15.0:
    results.append(("PASS", f"total energy {te.total_energy_J*1000:.1f} mJ (plausible range)"))
else:
    results.append(("FAIL", f"total energy {te.total_energy_J*1000:.1f} mJ — out of plausible range"))

# Sanity 4: variance summary — across 3 replicas, stderr is positive and CV < 5%
summary = summarize_replicas(traces)
for gb, stats in summary.by_gpio_byte_energy_J.items():
    if stats.cv == 0 and stats.mean > 0:
        results.append(("FAIL", f"gpio_byte={gb}: zero variance — replicas identical (seed broken?)"))
    elif stats.cv > 0.10:
        results.append(("WARN", f"gpio_byte={gb}: CV={stats.cv*100:.1f}% high (replicas too different)"))
    elif stats.mean > 0:
        results.append(("PASS", f"gpio_byte={gb}: mean={stats.mean*1000:.2f} mJ ±{stats.stderr*1000:.3f} (CV={stats.cv*100:.2f}%)"))

# Sanity 5: BatchModel crossover with realistic numbers
# Approx from synthetic: per-round = compute + wait ≈ 5+30 mJ, OTS ≈ 200 mJ
m = BatchModel(
    e_setup_per_round_J=2.5e-3,
    e_verify_per_round_J=2.5e-3,
    e_serverwait_per_round_J=30e-3,
    e_one_time_setup_J=210e-3,
)
out = analyze(m, e_direct_pairing_J=42e-3)
if 30 <= out.asymptote_J * 1000 <= 50 and out.crossover_n_for_k1 is not None and out.crossover_n_for_k1 < 100:
    results.append(("PASS", f"BatchModel: asymptote={out.asymptote_J*1000:.1f} mJ, crossover N(k=1)={out.crossover_n_for_k1}"))
else:
    results.append(("FAIL", f"BatchModel numbers off: asymptote={out.asymptote_J*1000:.1f} mJ, crossover={out.crossover_n_for_k1}"))

# Print results
n_p = sum(1 for r in results if r[0] == "PASS")
n_f = sum(1 for r in results if r[0] == "FAIL")
n_w = sum(1 for r in results if r[0] == "WARN")
for status, msg in results:
    print(f"  {status}: {msg}")
print(f"\nLayer 8 summary: {n_p} pass, {n_f} fail, {n_w} warn")

import sys
sys.exit(0 if n_f == 0 else 1)
PY

rc=$?
cat "${LOG_DIR}/08_analysis.log"
if [ $rc -eq 0 ]; then
    ok "all analysis sanity checks passed"
else
    fail "some analysis sanity checks failed; see ${LOG_DIR}/08_analysis.log"
fi

# =============================================================================
# FINAL SUMMARY
# =============================================================================
elapsed_total=$(( $(date +%s) - t_global ))
mm=$(( elapsed_total / 60 ))
ss=$(( elapsed_total % 60 ))

printf "\n${BOLD}═══════════════════════════════════════════════════════════════${RST}\n"
printf "${BOLD}  MINI REGRESSION SUMMARY${RST}\n"
printf "${BOLD}═══════════════════════════════════════════════════════════════${RST}\n"
printf "  Total time:    ${mm}m ${ss}s\n"
printf "  Logs:          ${LOG_DIR}/\n"
printf "  Passed:        ${G}${n_pass}${RST}\n"
printf "  Failed:        ${R}${n_fail}${RST}\n"
printf "  Warnings:      ${Y}${n_warn}${RST}\n"

if [ "${n_fail}" -gt 0 ]; then
    printf "\n${R}Failures:${RST}\n"
    for f in "${failures[@]}"; do printf "  ✗ %s\n" "$f"; done
fi
if [ "${n_warn}" -gt 0 ]; then
    printf "\n${Y}Warnings:${RST}\n"
    for w in "${warnings[@]}"; do printf "  ⚠ %s\n" "$w"; done
fi

printf "\n"
if [ "${n_fail}" -eq 0 ]; then
    printf "${G}${BOLD}  ✓ MINI REGRESSION PASSED — safe to launch overnight regression${RST}\n\n"
    exit 0
else
    printf "${R}${BOLD}  ✗ MINI REGRESSION FAILED — fix issues before overnight run${RST}\n\n"
    exit 1
fi
