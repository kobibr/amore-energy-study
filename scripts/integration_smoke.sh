#!/usr/bin/env bash
# integration_smoke.sh — end-to-end test, ~30s total.
# Validates: venv → run_cell → parse → energy → all 4 plots.
# Final integration gate before hardware-in-the-loop testing.

set -euo pipefail
ES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ES}"

source .venv/bin/activate

t0=$(date +%s)
echo "── 1/5 unit tests ─────────────────────────────────────"
python3 -m pytest analysis/tests/ measurement/ppk2-control/tests/ -q 2>&1 | tail -3
echo

echo "── 2/5 run_cell smoke ─────────────────────────────────"
SMOKE_OUT=$(mktemp -d)
python3 -u scripts/run_cell.py --smoke --out "${SMOKE_OUT}" 2>&1 | tail -3
[ -s "${SMOKE_OUT}"/*/run_001.csv ] && echo "  ✓ smoke CSV non-empty" || { echo "✗"; exit 1; }
rm -rf "${SMOKE_OUT}"
echo

echo "── 3/5 synthetic cells exist ──────────────────────────"
N_CELLS=$(ls -d measurement/traces/*__r3 2>/dev/null | wc -l)
echo "  ${N_CELLS} cells found"
[ "${N_CELLS}" -ge 10 ] || { echo "✗ expected ≥10 cells"; exit 1; }
echo

echo "── 4/5 regenerate all 4 figures ───────────────────────"
for plot in plot_energy_per_round plot_phase_breakdown plot_mode_comparison plot_crossover; do
    python3 -m "analysis.${plot}" 2>&1 | tail -1
done
N_FIGS=$(ls figures/fig*.png 2>/dev/null | wc -l)
[ "${N_FIGS}" -eq 4 ] && echo "  ✓ 4 figures generated" || { echo "✗ expected 4 PNGs got ${N_FIGS}"; exit 1; }
echo

echo "── 5/5 sleep_model end-to-end ─────────────────────────"
python3 -c '
from analysis.sleep_model import BatchModel, analyze
m = BatchModel(0.85e-3, 11.0e-3, 0.73e-3, 213e-3)
out = analyze(m, e_direct_pairing_J=42e-3)
print(f"  asymptote = {out.asymptote_J*1000:.2f} mJ/round")
print(f"  crossover N (k=1) = {out.crossover_n_for_k1}")
print(f"  crossover N (k=3) = {out.crossover_n_for_k3}")
'

elapsed=$(( $(date +%s) - t0 ))
echo
echo "════════════════════════════════════════════════════════"
echo "  ✓ integration smoke PASSED in ${elapsed}s"
echo "════════════════════════════════════════════════════════"
