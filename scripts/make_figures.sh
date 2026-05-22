#!/usr/bin/env bash
# =============================================================================
# make_figures.sh — regenerate all paper figures from measurement/traces/
#
# Maps each plot_*.py module to the filename main.tex expects in
# report/figures/. Saves PDFs (not PNGs — TeX wants PDF).
#
# Usage:
#   bash scripts/make_figures.sh           # regenerate all 6 figures
#   bash scripts/make_figures.sh fig1      # regenerate one (fig1/fig2/etc)
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
# ─────────────────────────────────────────────────────────────────
declare -A FIGURES=(
    [fig1]="plot_energy_per_round:fig1_energy_vs_n.pdf"
    [fig3]="plot_mode_comparison:fig3_time_vs_n.pdf"
    [fig4]="plot_crossover:fig4_crossover.pdf"
    [fig5]="plot_phase_breakdown:fig5_phase_breakdown.pdf"
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
    for key in fig1 fig3 fig4 fig5; do
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
        info "Valid: fig1 fig2a fig2b fig3 fig4 fig5 all"
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
