#!/usr/bin/env bash
# =============================================================================
# make_figures.sh — regenerate all paper figures from measurement/traces/
#
# Maps each plot_*.py module to the filename main.tex expects in
# report/figures/. Saves PDFs (not PNGs — TeX wants PDF).
#
# Bug #1 fix (silent-bias review 2026-05-23): the previous version
# claimed to generate "all 6 figures" but its FIGURES dict only had 4
# entries (fig1, fig3, fig4, fig5). The unknown-target message
# advertised "fig2a fig2b" which were never defined, so passing them
# to the script produced the misleading "Unknown target" error.
# Worse, plot_phase_breakdown.py self-identifies as Fig 2 (its
# docstring says "Fig 2: Per-batch energy breakdown by phase" and
# its default --out is fig2_phase_breakdown.png) but the script was
# wiring it as fig5_phase_breakdown.pdf. This guaranteed main.tex
# couldn't compile cleanly: \includegraphics{fig2_phase_breakdown}
# would not find a file, and fig5_phase_breakdown.pdf would land
# unused. Aligned to plot_phase_breakdown.py's own numbering: fig2.
#
# Note: there is currently no fig5 / fig6 generator; the header
# previously promising "6 figures" was aspirational. Update this
# count and the FIGURES dict together when new generators arrive.
#
# Usage:
#   bash scripts/make_figures.sh           # regenerate all 4 figures
#   bash scripts/make_figures.sh fig1      # regenerate one (fig1/fig2/fig3/fig4)
# =============================================================================

set -uo pipefail

ES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ES}"

RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'
BLU='\033[94m'; CYN='\033[96m'; RST='\033[0m'; BOLD='\033[1m'
ok()   { echo -e "${GRN}✓${RST} $*"; }
fail() { echo -e "${RED}✗${RST} $*"; }
info() { echo -e "${CYN}  $*${RST}"; }
head_(){ echo -e "\n${BOLD}${BLU}$*${RST}"; }

FIG_DIR="${ES}/report/figures"
TRACES_DIR="${ES}/measurement/traces"
mkdir -p "${FIG_DIR}"

# Activate venv if exists
[ -d .venv ] && source .venv/bin/activate

# Which figure to build (default: all)
TARGET="${1:-all}"

head_ "═══════════════════════════════════════════════════════════════"
head_ "  Regenerate paper figures"
head_ "═══════════════════════════════════════════════════════════════"
info "Target:      ${TARGET}"
info "Traces:      ${TRACES_DIR}"
info "Output:      ${FIG_DIR}"
info "Start:       $(date '+%Y-%m-%d %H:%M:%S')"
echo

# Sanity: traces directory has data
N_CELLS=$(find "${TRACES_DIR}" -maxdepth 1 -type d ! -path "${TRACES_DIR}" 2>/dev/null | wc -l)
if [ "${N_CELLS}" -eq 0 ]; then
    fail "No cells found in ${TRACES_DIR}"
    exit 1
fi
info "Cells available: ${N_CELLS}"
echo

# ─────────────────────────────────────────────────────────────────
# Each figure: (module, output filename per main.tex)
#
# Bug #1 fix: plot_phase_breakdown is Fig 2, not Fig 5. The previous
# script had it as fig5_phase_breakdown.pdf, mismatched against
# plot_phase_breakdown.py's own default --out (fig2_phase_breakdown).
# main.tex's \includegraphics expects the fig2_ name.
# ─────────────────────────────────────────────────────────────────
declare -A FIGURES=(
    [fig1]="plot_energy_per_round:fig1_energy_vs_n.pdf"
    [fig2]="plot_phase_breakdown:fig2_phase_breakdown.pdf"
    [fig3]="plot_mode_comparison:fig3_time_vs_n.pdf"
    [fig4]="plot_crossover:fig4_crossover.pdf"
)

run_plot() {
    local key="$1"
    local spec="${FIGURES[$key]}"
    local module="${spec%%:*}"
    local outfile="${spec##*:}"
    local outpath="${FIG_DIR}/${outfile}"

    head_ "━━━ ${key}: ${module} → ${outfile} ━━━"

    if python3 -m "analysis.${module}" --traces "${TRACES_DIR}" --out "${outpath}" 2>&1 | sed 's/^/    /'; then
        if [ -f "${outpath}" ]; then
            local size=$(stat -c%s "${outpath}")
            ok "${outfile} (${size} bytes)"
            return 0
        else
            fail "${outfile} NOT created"
            return 1
        fi
    else
        fail "${module} crashed"
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────
# Run the requested target(s)
# ─────────────────────────────────────────────────────────────────
N_OK=0
N_FAIL=0

if [ "${TARGET}" = "all" ]; then
    # Bug #1 fix: iterate over the actual FIGURES keys, in numeric
    # order. Previously iterated fig1 fig3 fig4 fig5 which silently
    # skipped fig2 (it didn't exist in the dict, but the gap was
    # invisible because the loop body only got told the present keys).
    for key in fig1 fig2 fig3 fig4; do
        if run_plot "$key"; then
            N_OK=$((N_OK+1))
        else
            N_FAIL=$((N_FAIL+1))
        fi
    done
else
    if [[ -v "FIGURES[${TARGET}]" ]]; then
        if run_plot "${TARGET}"; then N_OK=1; else N_FAIL=1; fi
    else
        fail "Unknown target: ${TARGET}"
        # Bug #1 fix: list ACTUAL valid targets, not the misleading
        # "fig2a fig2b fig5" set the old message advertised but the
        # dict never supported.
        info "Valid: fig1 fig2 fig3 fig4 all"
        exit 1
    fi
fi

echo
head_ "════════════════════════════════════════════════════════════════"
head_ "  Summary"
head_ "════════════════════════════════════════════════════════════════"
info "OK:   ${N_OK}"
info "FAIL: ${N_FAIL}"
echo

if [ "${N_FAIL}" -eq 0 ]; then
    echo -e "${GRN}${BOLD}  ✓ All figures generated${RST}"
    echo
    info "Files in ${FIG_DIR}:"
    ls -la "${FIG_DIR}" | grep '\.pdf$' | sed 's/^/    /'
    exit 0
else
    echo -e "${RED}${BOLD}  ✗ ${N_FAIL} figures failed${RST}"
    exit 1
fi
