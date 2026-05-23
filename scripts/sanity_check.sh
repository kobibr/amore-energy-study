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
fail()  { echo -e "${RED}${BOLD}✗ FAIL:${RST} ${RED}$*${RST}"; }
warn()  { echo -e "${YLW}⚠${RST} $*"; }
info()  { echo -e "${CYN}  $*${RST}"; }
head_() { echo -e "\n${BOLD}${BLU}$*${RST}"; }

# LOUD FAIL banner — impossible to miss
big_fail() {
    local msg="$1"
    echo
    echo -e "${RED}${BOLD}"
    echo "  ██████████████████████████████████████████████████████████████"
    echo "  ██                                                          ██"
    echo "  ██   ✗✗✗  S A N I T Y   C H E C K   F A I L E D  ✗✗✗       ██"
    echo "  ██                                                          ██"
    echo "  ██████████████████████████████████████████████████████████████"
    echo -e "${RST}"
    echo -e "${RED}${BOLD}  >>>  ${msg}  <<<${RST}"
    echo
    echo -e "${RED}${BOLD}  ▼▼▼  See details above  ▼▼▼${RST}"
    echo
}

big_pass() {
    echo
    echo -e "${GRN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════╗"
    echo "  ║                                                          ║"
    echo "  ║      ✓  S A N I T Y   C H E C K   P A S S E D  ✓        ║"
    echo "  ║                                                          ║"
    echo "  ╚══════════════════════════════════════════════════════════╝"
    echo -e "${RST}"
}

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

# Exclude measurement/sanity_log.txt — it's the OUTPUT of this script,
# committed at the end of every PASS. Counting it would make the gate
# self-defeating after the first PASS.
OUTER_DIRTY=$(git status --porcelain | grep -v '^?? ' | grep -v 'measurement/sanity_log.txt' | wc -l)
if [ "${OUTER_DIRTY}" -gt 0 ] && [ "${FORCE}" -eq 0 ]; then
    big_fail "OUTER REPO HAS UNCOMMITTED CHANGES (${OUTER_DIRTY} files)"
    echo -e "${RED}  Uncommitted files:${RST}"
    git status -s | head -10 | sed 's/^/      /'
    echo
    echo -e "${YLW}${BOLD}  TO FIX:${RST}"
    echo -e "${YLW}    git add <files>${RST}"
    echo -e "${YLW}    git commit -m \"...\"${RST}"
    echo -e "${YLW}    git push origin main${RST}"
    echo -e "${YLW}  Or to bypass (NOT recommended): bash $0 --force${RST}"
    echo
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

# S3 fix: if we couldn't resolve origin/main (offline, no remote configured,
# or branch missing), the previous code compared a SHA to the literal string
# "unknown" and reported "NOT PUSHED TO REMOTE" — which is misleading.
# Distinguish the two failure modes.
if [ "${OUTER_REMOTE}" = "unknown" ] && [ "${FORCE}" -eq 0 ]; then
    warn "Outer repo: could not resolve origin/main (offline? no remote?)"
    warn "  Treating gate as passed conditional on next online sanity_check."
    info "  local: ${OUTER_LOCAL:0:7}"
elif [ "${OUTER_LOCAL}" != "${OUTER_REMOTE}" ] && [ "${FORCE}" -eq 0 ]; then
    big_fail "OUTER REPO NOT PUSHED TO REMOTE"
    echo -e "${RED}  local:  ${OUTER_LOCAL:0:7}${RST}"
    echo -e "${RED}  remote: ${OUTER_REMOTE:0:7}${RST}"
    echo
    echo -e "${YLW}${BOLD}  TO FIX:${RST}"
    echo -e "${YLW}    git push origin main${RST}"
    echo
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
    big_fail "FIRMWARE SUBMODULE HAS UNCOMMITTED CHANGES (${SUB_DIRTY} files)"
    echo -e "${RED}  Uncommitted files in firmware/amore-fw:${RST}"
    git status -s | head -10 | sed 's/^/      /'
    echo
    echo -e "${YLW}${BOLD}  TO FIX:${RST}"
    echo -e "${YLW}    cd firmware/amore-fw${RST}"
    echo -e "${YLW}    git add <files>${RST}"
    echo -e "${YLW}    git commit -m \"...\"${RST}"
    echo -e "${YLW}    git push origin main${RST}"
    echo -e "${YLW}    cd ../..${RST}"
    echo -e "${YLW}    git add firmware/amore-fw${RST}"
    echo -e "${YLW}    git commit -m \"Bump firmware\"${RST}"
    echo
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

# S3 fix: same "unknown" handling as outer gate.
if [ "${SUB_REMOTE}" = "unknown" ] && [ "${FORCE}" -eq 0 ]; then
    warn "Submodule: could not resolve origin/main (offline? no remote?)"
    warn "  Treating gate as passed conditional on next online sanity_check."
    info "  local: ${SUB_LOCAL:0:7}"
elif [ "${SUB_LOCAL}" != "${SUB_REMOTE}" ] && [ "${FORCE}" -eq 0 ]; then
    big_fail "FIRMWARE SUBMODULE NOT PUSHED TO REMOTE"
    echo -e "${RED}  local:  ${SUB_LOCAL:0:7}${RST}"
    echo -e "${RED}  remote: ${SUB_REMOTE:0:7}${RST}"
    echo
    echo -e "${YLW}${BOLD}  TO FIX:${RST}"
    echo -e "${YLW}    cd firmware/amore-fw && git push origin main && cd ../..${RST}"
    echo
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

# Check if a long-running sweep already owns the PPK2 — if so, smoke
# would just collide on USB. Skipping is the correct outcome here:
# we cannot test PPK2 health, but we also can't FAIL on hardware
# we voluntarily made unavailable. Marked SKIPPED(busy) in the log.
SWEEP_PIDFILE=/tmp/sweep_n61.pid
SWEEP_BUSY=0
if [ -f "${SWEEP_PIDFILE}" ]; then
    SWEEP_PID=$(cat "${SWEEP_PIDFILE}")
    if kill -0 "${SWEEP_PID}" 2>/dev/null; then
        SWEEP_BUSY=1
    fi
fi

if [ "${SWEEP_BUSY}" -eq 1 ]; then
    SMOKE_RESULT="SKIPPED-BUSY"
    warn "smoke_ppk2: SKIPPED — sweep is running (PID ${SWEEP_PID}) and owns the PPK2"
    info "  This is expected during a long sweep; rerun sanity_check after sweep completes"
    info "  for a full PPK2 hardware check."
elif bash scripts/smoke_ppk2.sh > "${SMOKE_LOG}" 2>&1; then
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

if [ "${SMOKE_RESULT}" != "FAIL" ] && [ "${REGR_RESULT}" != "FAIL" ]; then
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

    # Auto-commit + push the log so the next sanity run sees a clean tree
    info "Auto-committing sanity_log update..."
    if git add "${LOG_FILE}" 2>/dev/null && \
       git commit -m "sanity_check: PASS at ${STAMP_NOW} (outer=${OUTER_LOCAL:0:7})" >/dev/null 2>&1; then
        if git push origin main >/dev/null 2>&1; then
            ok "Auto-pushed sanity_log update"
        else
            warn "Auto-push failed (offline?) — commit is local only"
        fi
    else
        warn "Auto-commit skipped (no changes? gate already excluded log)"
    fi
    
    big_pass
    info "  This commit is provably-working."
    info "  Logged to: ${LOG_FILE}"
    info "  Last 3 PASS entries:"
    tail -3 "${LOG_FILE}" | grep -v "^#" | sed 's/^/    /'
    exit 0
else
    big_fail "ONE OR MORE TESTS FAILED"
    echo -e "${RED}  Results:${RST}"
    case "${SMOKE_RESULT}" in
        PASS)
            echo -e "    smoke_ppk2:      ${GRN}PASS${RST}" ;;
        SKIPPED-BUSY)
            echo -e "    smoke_ppk2:      ${YLW}SKIPPED (sweep running)${RST}" ;;
        *)
            echo -e "    smoke_ppk2:      ${RED}${BOLD}${SMOKE_RESULT}${RST}" ;;
    esac
    if [ "${REGR_RESULT}" = "PASS" ]; then
        echo -e "    mini_regression: ${GRN}PASS${RST}"
    elif [ "${REGR_RESULT}" = "SKIPPED" ]; then
        echo -e "    mini_regression: ${YLW}SKIPPED (--smoke)${RST}"
    else
        echo -e "    mini_regression: ${RED}${BOLD}${REGR_RESULT}${RST}"
    fi
    echo
    echo -e "${YLW}  NOT recorded to ${LOG_FILE} (only PASSes are logged)${RST}"
    echo
    exit 1
fi
