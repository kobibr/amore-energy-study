#!/usr/bin/env bash
# =============================================================================
# sanity_check.sh — gated end-to-end check before any major work
#
# REFUSES to run if the working tree is dirty OR local != remote.
# Reason: every PASS entry in the log must correspond to a real, pushed
# commit. If the tree is dirty, the PASS doesn't map to anything reproducible.
#
# Sequence:
#   1. Verify outer repo clean + pushed
#   2. Verify firmware submodule clean + pushed
#   3. Run scripts/mini_regression.sh (~10 min, analysis stack)
#   4. Run scripts/smoke_ppk2.sh (~20 s, PPK2 hardware)
#   5. On all-PASS: append a line to measurement/sanity_log.txt with
#      timestamp + outer commit + firmware commit + duration
#
# Exit codes:
#   0  = all green, line appended to log
#   1  = git not clean / not pushed (no test run)
#   2  = mini_regression failed
#   3  = smoke_ppk2 failed
#   4  = log append failed (rare)
#
# WIRING for the PPK2 portion (smoke_ppk2.sh requires):
#   - PPK2 VOUT → STM32 P2 pin 3
#   - PPK2 GND  → STM32 P2 pin 1
#   - PPK2 USB  → laptop
#   - IDD jumper IN
# =============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

# Colors
RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'
BLU=$'\033[94m'; CYN=$'\033[96m'; RST=$'\033[0m'; BOLD=$'\033[1m'

ok()   { echo -e "${GRN}✓${RST} $*"; }
err()  { echo -e "${RED}✗${RST} $*"; }
warn() { echo -e "${YLW}⚠${RST} $*"; }
info() { echo -e "${CYN}  $*${RST}"; }
head() { echo -e "\n${BOLD}${BLU}$*${RST}"; }

SANITY_LOG="${REPO_DIR}/measurement/sanity_log.txt"
TIME_START=$(date +%s)
STAMP=$(date '+%Y-%m-%d %H:%M:%S %z')

# ─────────────────────────────────────────────────────────────────────
# GATE 1: outer repo clean + pushed
# ─────────────────────────────────────────────────────────────────────
head "══ GATE 1: Outer repo (amore-energy-study) ══"

OUTER_DIRTY=$(git status --porcelain | grep -v '^?? ' | wc -l)
if [ "$OUTER_DIRTY" -gt 0 ]; then
    err "Outer working tree has uncommitted changes:"
    git status --short | grep -v '^?? ' | sed 's/^/    /'
    err "Commit and push before running sanity_check"
    exit 1
fi
ok "Outer working tree clean"

OUTER_LOCAL=$(git rev-parse HEAD)
OUTER_REMOTE=$(git ls-remote origin main 2>/dev/null | head -1 | cut -f1)
if [ -z "$OUTER_REMOTE" ]; then
    err "Could not reach remote origin (network issue?)"
    exit 1
fi
if [ "$OUTER_LOCAL" != "$OUTER_REMOTE" ]; then
    err "Outer HEAD ($OUTER_LOCAL) != origin/main ($OUTER_REMOTE)"
    err "Push before running sanity_check:  git push origin main"
    exit 1
fi
ok "Outer in sync with origin/main: ${OUTER_LOCAL:0:7}"

# ─────────────────────────────────────────────────────────────────────
# GATE 2: firmware submodule clean + pushed
# ─────────────────────────────────────────────────────────────────────
head "══ GATE 2: Firmware submodule ══"

(
    cd firmware/amore-fw
    SUB_DIRTY=$(git status --porcelain | grep -v '^?? ' | wc -l)
    if [ "$SUB_DIRTY" -gt 0 ]; then
        echo "DIRTY"
        git status --short | grep -v '^?? '
        exit 1
    fi
    SUB_LOCAL=$(git rev-parse HEAD)
    SUB_REMOTE=$(git ls-remote origin main 2>/dev/null | head -1 | cut -f1)
    if [ -z "$SUB_REMOTE" ] || [ "$SUB_LOCAL" != "$SUB_REMOTE" ]; then
        echo "UNSYNCED"
        echo "  local:  $SUB_LOCAL"
        echo "  remote: $SUB_REMOTE"
        exit 1
    fi
    echo "OK $SUB_LOCAL"
) > /tmp/fw_check_$$.txt 2>&1
FW_RC=$?

FW_LINE=$(cat /tmp/fw_check_$$.txt)
rm -f /tmp/fw_check_$$.txt

if [ "$FW_RC" -ne 0 ]; then
    err "Firmware submodule not clean/pushed:"
    echo "$FW_LINE" | sed 's/^/    /'
    err "cd firmware/amore-fw && git status, fix, commit, push"
    exit 1
fi
SUB_LOCAL=$(echo "$FW_LINE" | awk '/^OK/ {print $2}')
ok "Firmware submodule in sync: ${SUB_LOCAL:0:7}"

head "══ All gates passed — running tests ══"
echo "  Outer:    ${OUTER_LOCAL:0:7}"
echo "  Firmware: ${SUB_LOCAL:0:7}"
echo "  Time:     ${STAMP}"
echo

# ─────────────────────────────────────────────────────────────────────
# TEST 1: mini_regression
# ─────────────────────────────────────────────────────────────────────
head "══ TEST 1: mini_regression.sh (~10 min) ══"
MR_LOG="/tmp/sanity_mini_regression_$(date +%Y%m%d_%H%M%S).log"

bash scripts/mini_regression.sh 2>&1 | tee "$MR_LOG"
MR_RC=${PIPESTATUS[0]}

if [ "$MR_RC" -ne 0 ]; then
    err "mini_regression.sh FAILED (exit $MR_RC)"
    err "Log: $MR_LOG"
    exit 2
fi
ok "mini_regression.sh PASSED"

# ─────────────────────────────────────────────────────────────────────
# TEST 2: smoke_ppk2
# ─────────────────────────────────────────────────────────────────────
head "══ TEST 2: smoke_ppk2.sh (~20 s) ══"
SP_LOG="/tmp/sanity_smoke_ppk2_$(date +%Y%m%d_%H%M%S).log"

bash scripts/smoke_ppk2.sh 2>&1 | tee "$SP_LOG"
SP_RC=${PIPESTATUS[0]}

if [ "$SP_RC" -ne 0 ]; then
    err "smoke_ppk2.sh FAILED (exit $SP_RC)"
    err "Log: $SP_LOG"
    exit 3
fi
ok "smoke_ppk2.sh PASSED"

# ─────────────────────────────────────────────────────────────────────
# ALL PASSED — append to sanity log
# ─────────────────────────────────────────────────────────────────────
TIME_END=$(date +%s)
DURATION=$((TIME_END - TIME_START))
MIN=$((DURATION / 60))
SEC=$((DURATION % 60))

mkdir -p "$(dirname "${SANITY_LOG}")"

LINE=$(printf "%s | outer=%s | firmware=%s | mini_regr=PASS | smoke_ppk2=PASS | duration=%dm%ds" \
    "$STAMP" "${OUTER_LOCAL:0:7}" "${SUB_LOCAL:0:7}" "$MIN" "$SEC")

if echo "$LINE" >> "$SANITY_LOG"; then
    head "══ ✓ ALL CHECKS PASSED — recorded ══"
    echo
    echo "  Appended to ${SANITY_LOG}:"
    echo "    ${LINE}"
    echo
    echo "  Last 5 entries in sanity log:"
    tail -5 "$SANITY_LOG" | sed 's/^/    /'
    echo
    exit 0
else
    err "Failed to append to ${SANITY_LOG}"
    exit 4
fi
