#!/usr/bin/env bash
# =============================================================================
# sanity_check.sh — pre-commit / post-deploy gate
#
# Refuses to run if:
#   - working tree has uncommitted changes (must `git add` + `git commit`)
#   - local HEAD != remote main (must `git push`)
#   - submodule HEAD has uncommitted changes
#   - submodule HEAD != submodule remote main
#
# When everything is clean, runs:
#   1. scripts/smoke_ppk2.sh    (~20s — PPK2 hardware functional)
#   2. scripts/mini_regression.sh (~10min — analysis stack functional)
#
# On full PASS, appends a line to measurement/sanity_log.txt:
#   2026-05-22 13:51:00 | outer=e40ef8c | fw=fdb30fd | smoke=PASS regr=PASS
#
# This file is THE record of "the codebase was provably-working at this
# commit". Use it to find a safe point to rewind to if things break.
#
# Usage:
#   bash scripts/sanity_check.sh           # full run (10 min)
#   bash scripts/sanity_check.sh --smoke   # smoke_ppk2 only (20s)
#   bash scripts/sanity_check.sh --force   # skip git checks (debug only)
# =============================================================================

set -uo pipefail

ES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ES}"

RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'
BLU='\033[94m'; CYN='\033[96m'; RST='\033[0m'; BOLD='\033[1m'
ok()    { echo -e "${GRN}✓${RST} $*"; }
fail()  { echo -e "${RED}✗${RST} $*"; }
warn()  { echo -e "${YLW}⚠${RST} $*"; }
info()  { echo -e "${CYN}  $*${RST}"; }
head_() { echo -e "\n${BOLD}${BLU}$*${RST}"; }

# ── Parse flags ──────────────────────────────────────────────────────
SMOKE_ONLY=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --smoke) SMOKE_ONLY=1 ;;
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '3,30p' "$0"
            exit 0
            ;;
    esac
done

LOG_FILE="${ES}/measurement/sanity_log.txt"
mkdir -p "$(dirname "${LOG_FILE}")"

head_ "═════════════════════════════════════════════════════════════"
head_ "  AmorE sanity check"
head_ "═════════════════════════════════════════════════════════════"
echo "  Start:    $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Log file: ${LOG_FILE}"
echo

# ─────────────────────────────────────────────────────────────────────
# Gate 1: outer repo clean
# ─────────────────────────────────────────────────────────────────────
head_ "━━━ Gate 1/4: outer repo clean ━━━"

OUTER_DIRTY=$(git status --porcelain | grep -v '^?? ' | wc -l)
if [ "${OUTER_DIRTY}" -gt 0 ] && [ "${FORCE}" -eq 0 ]; then
    fail "Outer repo has ${OUTER_DIRTY} uncommitted changes"
    git status -s | head -10 | sed 's/^/    /'
    echo
    fail "Commit or stash before running sanity_check"
    fail "  (use --force to skip git gates, e.g. during initial setup)"
    exit 1
fi
[ "${FORCE}" -eq 1 ] && warn "--force: skipping outer clean check" || ok "Outer repo clean"

# ─────────────────────────────────────────────────────────────────────
# Gate 2: outer pushed
# ─────────────────────────────────────────────────────────────────────
head_ "━━━ Gate 2/4: outer pushed to remote ━━━"

git fetch --quiet origin main 2>/dev/null || warn "fetch failed (offline?)"
OUTER_LOCAL=$(git rev-parse HEAD)
OUTER_REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "unknown")

if [ "${OUTER_LOCAL}" != "${OUTER_REMOTE}" ] && [ "${FORCE}" -eq 0 ]; then
    fail "Local outer HEAD does not match origin/main"
    info "  local:  ${OUTER_LOCAL:0:7}"
    info "  remote: ${OUTER_REMOTE:0:7}"
    fail "Run: git push origin main"
    exit 1
fi
[ "${FORCE}" -eq 1 ] && warn "--force: skipping outer push check" || ok "Outer pushed (${OUTER_LOCAL:0:7})"

# ─────────────────────────────────────────────────────────────────────
# Gate 3: submodule clean
# ─────────────────────────────────────────────────────────────────────
head_ "━━━ Gate 3/4: firmware submodule clean ━━━"

pushd firmware/amore-fw >/dev/null
SUB_DIRTY=$(git status --porcelain | grep -v '^?? ' | wc -l)
SUB_LOCAL=$(git rev-parse HEAD)

if [ "${SUB_DIRTY}" -gt 0 ] && [ "${FORCE}" -eq 0 ]; then
    fail "Submodule has ${SUB_DIRTY} uncommitted changes"
    git status -s | head -10 | sed 's/^/    /'
    popd >/dev/null
    exit 1
fi
[ "${FORCE}" -eq 1 ] && warn "--force: skipping submodule clean check" || ok "Submodule clean"

# ─────────────────────────────────────────────────────────────────────
# Gate 4: submodule pushed
# ─────────────────────────────────────────────────────────────────────
head_ "━━━ Gate 4/4: firmware submodule pushed ━━━"

git fetch --quiet origin main 2>/dev/null || warn "fetch failed (offline?)"
SUB_REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "unknown")

if [ "${SUB_LOCAL}" != "${SUB_REMOTE}" ] && [ "${FORCE}" -eq 0 ]; then
    fail "Submodule local HEAD does not match origin/main"
    info "  local:  ${SUB_LOCAL:0:7}"
    info "  remote: ${SUB_REMOTE:0:7}"
    fail "Run: cd firmware/amore-fw && git push origin main"
    popd >/dev/null
    exit 1
fi
[ "${FORCE}" -eq 1 ] && warn "--force: skipping submodule push check" || ok "Submodule pushed (${SUB_LOCAL:0:7})"
popd >/dev/null

# ─────────────────────────────────────────────────────────────────────
# Test 1: smoke_ppk2.sh (~20s)
# ─────────────────────────────────────────────────────────────────────
head_ "════════════════════════════════════════════════════════════════"
head_ "  All gates passed — running smoke_ppk2 (~20s)"
head_ "════════════════════════════════════════════════════════════════"

SMOKE_LOG=/tmp/sanity_smoke_$(date +%Y%m%d_%H%M%S).log
SMOKE_START=$(date +%s)
if bash scripts/smoke_ppk2.sh > "${SMOKE_LOG}" 2>&1; then
    SMOKE_RESULT="PASS"
    ok "smoke_ppk2: PASS"
else
    SMOKE_RESULT="FAIL"
    fail "smoke_ppk2: FAIL"
    tail -20 "${SMOKE_LOG}" | sed 's/^/    /'
fi
SMOKE_DUR=$(($(date +%s) - SMOKE_START))
info "duration: ${SMOKE_DUR}s, log: ${SMOKE_LOG}"

if [ "${SMOKE_ONLY}" -eq 1 ]; then
    REGR_RESULT="SKIPPED"
    REGR_DUR=0
else
    # ─────────────────────────────────────────────────────────────────
    # Test 2: mini_regression.sh (~10min)
    # ─────────────────────────────────────────────────────────────────
    head_ "════════════════════════════════════════════════════════════════"
    head_ "  Running mini_regression (~10 min, 8 layers)"
    head_ "════════════════════════════════════════════════════════════════"
    
    REGR_LOG=/tmp/sanity_regr_$(date +%Y%m%d_%H%M%S).log
    REGR_START=$(date +%s)
    if bash scripts/mini_regression.sh > "${REGR_LOG}" 2>&1; then
        REGR_RESULT="PASS"
        ok "mini_regression: PASS"
    else
        REGR_RESULT="FAIL"
        fail "mini_regression: FAIL"
        tail -30 "${REGR_LOG}" | sed 's/^/    /'
    fi
    REGR_DUR=$(($(date +%s) - REGR_START))
    info "duration: ${REGR_DUR}s, log: ${REGR_LOG}"
fi

# ─────────────────────────────────────────────────────────────────────
# Append to sanity_log.txt only on full pass
# ─────────────────────────────────────────────────────────────────────
head_ "════════════════════════════════════════════════════════════════"
head_ "  Summary"
head_ "════════════════════════════════════════════════════════════════"

if [ "${SMOKE_RESULT}" = "PASS" ] && [ "${REGR_RESULT}" != "FAIL" ]; then
    STAMP_NOW=$(date '+%Y-%m-%d %H:%M:%S')
    LINE="${STAMP_NOW} | outer=${OUTER_LOCAL:0:7} | fw=${SUB_LOCAL:0:7} | smoke=${SMOKE_RESULT}(${SMOKE_DUR}s) regr=${REGR_RESULT}(${REGR_DUR}s)"
    
    # Initialize log file if missing
    if [ ! -f "${LOG_FILE}" ]; then
        cat > "${LOG_FILE}" << 'INIT_EOF'
# AmorE sanity check log — append-only record of provably-working commits.
# Each line documents a sanity check that PASSED.
# Format: YYYY-MM-DD HH:MM:SS | outer=<sha7> | fw=<sha7> | smoke=PASS(<s>) regr=PASS(<s>)
#
# Use this file to find a safe point to rewind to if things break.
# Never edit existing lines — only append new ones.
INIT_EOF
    fi
    
    echo "${LINE}" >> "${LOG_FILE}"
    ok "Sanity PASS recorded:"
    info "  ${LINE}"
    
    echo
    head_ "${GRN}${BOLD}  ✓ SANITY CHECK PASSED${RST}"
    info "  This commit is provably-working."
    info "  Logged to: ${LOG_FILE}"
    exit 0
else
    echo
    head_ "${RED}${BOLD}  ✗ SANITY CHECK FAILED${RST}"
    info "  smoke_ppk2:      ${SMOKE_RESULT}"
    info "  mini_regression: ${REGR_RESULT}"
    info "  NOT recorded to ${LOG_FILE} (only PASSes are logged)"
    exit 1
fi
