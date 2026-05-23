"""Measured baseline data from doc/AmorE_*_Results.txt.

Single source of truth for:
  - Time per round (Blind, Verify) at N = 1, 10, 50
  - OneTimeSetup time per curve
  - Single direct-pairing time (RELIC pp_map_oatep_k12)
  - Memory footprint (Flash, SRAM) for AmorE and RELIC

All values cite their source document. The constants in
``analysis/fixtures/synthetic_cells.py`` use the N=50 amortized
numbers; this module exposes ALL N points so plot scripts can
display the actual amortization curve.

References
----------
- doc/AmorE_BN128_Results.txt §4.2 (BN254 amort), §11 (BN254 pairing,
  memory)
- doc/AmorE_BLS12_381_Results.txt §4.2 (BLS pre-O3), §5 (BLS pre-O3
  pairing), §2 (BLS memory), §8 (BLS post-O3, authoritative for AmorE)

Provenance summary (2026-05-12)
-------------------------------
BLS12-381 AmorE numbers below are post-O3 (measurement-O3-2026-05-12,
commit 0ecc6e8, binary SHA prefix 4e2df263). The -O3 (Release) build
unrolls the inner CIOS Montgomery loops in fp_mul (2.14x speedup over
the prior -O2 build).

BLS12-381 DIRECT_PAIRING (RELIC) remains pre-O3 (523.4 ms, from the
2026-05-07 measurement session). RELIC re-measurement at -O3 is
tracked in docs/future_work.md; the headline AmorE/(3xdirect) ratio
is flagged "pending RELIC re-measurement" until that lands.

BN254 numbers are unchanged from 2026-04-01 (pre-port-bn254-working).
BN254 re-measurement on the unified-curves branch is blocked by an
fp12_mul curve-specificity issue (see docs/future_work.md).

**Silent-bias fixes applied 2026-05-23**
----------------------------------------
CRITICAL-1: ``SERVER_RTT_MS`` alias removed. The public API now requires
            an explicit ``server="real"`` or ``server="mock"`` keyword
            argument so callers can no longer silently get MOCK-compressed
            values that are 68-80× off from real-world ServerWait. This
            is a breaking change to ``amore_serverwait_ms``,
            ``amore_round_energy_mJ``, ``amore_with_ots_per_round_mJ``,
            and ``session_time_s``; downstream callers must be updated.

HIGH-2:     ``amore_round_time_ms`` now raises ``ValueError`` on unknown
            curve names instead of silently falling back to BLS12_381.

MEDIUM-3:   tie-breaking in ``amore_round_time_ms`` now goes to the
            larger ``amort_ms`` (truly conservative for AmorE) rather
            than the larger ``N`` — which was only conservative under
            the now-falsified assumption that amort is monotonic in N.

MEDIUM-4:   warning block added above ``OTS_MS`` mirroring the one above
            ``DIRECT_PAIRING_MS``; the two values are NOT like-for-like.

MEDIUM-5:   ``SERVER_RTT_MS_REAL["BLS12_381"]`` updated to 87,500 ms
            (telemetry central value, was 87,020.4 — a 0.55% silent
            negative bias on every BLS ServerWait calculation).

MEDIUM-6:   docstrings for ``session_time_s`` and the duty-cycle block
            corrected: it's "wall-clock", not "active" time. The phase
            during ServerWait is exactly where active vs Stop matters.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AmortPoint:
    """Per-N amortized timing point from the result documents."""
    n: int
    blind_ms: float
    verify_ms: float

    @property
    def amort_ms(self) -> float:
        return self.blind_ms + self.verify_ms


# AmorE Mode A amortized per-round timing.
# BN254: doc/AmorE_BN128_Results.txt §4.2 / §4.3 (2026-04-01, pre-O3).
# BLS:   doc/AmorE_BLS12_381_Results.txt §8 (2026-05-12, post-O3).
AMORE_AMORT_BN254 = [
    AmortPoint(n=1,  blind_ms=197.3, verify_ms=175.2),  # amort 372.5 ms
    AmortPoint(n=10, blind_ms=198.3, verify_ms=180.5),  # amort 378.8 ms
    AmortPoint(n=50, blind_ms=199.4, verify_ms=182.4),  # amort 381.8 ms
]

# Post-O3 (Variant B, CMAKE_BUILD_TYPE=Release):
#   binary SHA 4e2df263, commit 0ecc6e8, tag measurement-O3-2026-05-12,
#   logs/combined_report_20260512_090923.txt (61/61 honest + 1/1
#   malicious, status 0x600D0000).
# Per-round breakdown for N=50:
#   blind_total  = 4,101,567,538 cyc / 50 = ~488.28 ms/round
#   verify_total = 3,441,461,050 cyc / 50 = ~409.70 ms/round
#   amort/round  = 898.0 ms
AMORE_AMORT_BLS12_381 = [
    AmortPoint(n=1,  blind_ms=472.97, verify_ms=397.31),  # amort 870.3 ms
    AmortPoint(n=10, blind_ms=483.66, verify_ms=414.54),  # amort 898.2 ms
    AmortPoint(n=50, blind_ms=488.28, verify_ms=409.70),  # amort 898.0 ms
]


# OneTimeSetup — paid once per session, not per round.
# BN254: doc/AmorE_BN128_Results.txt §4.2 (pre-O3, 2026-04-01).
# BLS:   doc/AmorE_BLS12_381_Results.txt §8 (post-O3, 2026-05-12).
#
# WARNING (MEDIUM-4): BN254 OTS is pre-O3 (503.9 ms, 2026-04-01).
# BLS12_381 OTS is post-O3 (1151.2 ms, 2026-05-12). Cross-curve
# comparisons that use OTS are NOT like-for-like — the -O3 (Release)
# build is up to ~2.14× faster than -O2 on fp_mul, so the BN254 value
# could be roughly half of its current 503.9 ms once re-measured.
# BN254 re-measurement on the unified-curves branch is blocked by an
# fp12_mul curve-specificity issue (see docs/future_work.md).
OTS_MS = {
    "BN254":     503.9,
    "BLS12_381": 1151.2,
}


# Direct pairing: single pp_map_oatep_k12 via RELIC, measured on same MCU.
# BN254: doc/AmorE_BN128_Results.txt §11 (pre-O3, 2026-04-01).
# BLS:   doc/AmorE_BLS12_381_Results.txt §5 (pre-O3, 2026-05-07,
#        Diego's measurement session).
#
# WARNING: BLS12_381 entry below is pre-O3. AmorE BLS amort above is
# post-O3. Comparisons mixing the two (e.g. amore_amort / direct ratio)
# are mechanically correct but NOT like-for-like. See docs/future_work.md
# "RELIC re-measurement at -O3" (HIGH priority). Expected post-O3 range:
# ~220-300 ms per pairing.
DIRECT_PAIRING_MS = {
    "BN254":     252.3,
    "BLS12_381": 523.4,   # pre-O3, RELIC rebuild pending
}


# Memory footprint, Flash & SRAM in KB.
# Source: doc/AmorE_BN128_Results.txt §11.2 (BN254 measured),
#         doc/AmorE_BLS12_381_Results.txt §2 (BLS AmorE only)
FLASH_KB = {
    "AmorE_BN254":     18.5,
    "AmorE_BLS12_381": 15.5,
    "RELIC_BN254":     55.1,
    "RELIC_BLS12_381": None,   # not measured — RELIC not built for BLS on this hardware
}

SRAM_KB = {
    "AmorE_BN254":       3.1,
    "AmorE_BLS12_381":  35.0,
    "RELIC_BN254":     101.4,
    "RELIC_BLS12_381":  None,
}

# Estimated upper bound for RELIC_BLS — used only for annotation, never plotted.
# Scaling factor: 12-limb / 8-limb arithmetic ≈ 1.5×, plus buffer growth.
RELIC_BLS_FLASH_ESTIMATE_RANGE = (75.0, 150.0)
RELIC_BLS_SRAM_ESTIMATE_RANGE  = (140.0, 160.0)


# Energy model: simple I × V × t.
# I_ACTIVE is the average active-mode current at 168 MHz running compute code.
# Distilled from current_synthesis.py spec table — Blind/Verify both fall under
# I_SETUP = 85 mA. This is consistent with what synthetic_cells.py uses.
I_ACTIVE_MA = 85.0
V_NOMINAL   = 3.300

# Stop-mode quiescent current for the proposed optimization scenario.
# Source: STM32F407 datasheet typical (0.4-0.6 µA); we use 0.5 µA as midpoint.
# Will be replaced with measured value once PPK2 is connected and Stop firmware
# debug is complete.
I_STOP_UA = 0.5

# Server-side compute time (approximate; UART RTT + Pi pairing).
# Used to estimate the ServerWait phase duration per round.
#
# CAVEAT M3 (resolves OQ-1): the SERVER_RTT_MS constant historically
# applied a /100 factor inherited from MOCK_SERVER_COMPRESS in
# fixtures/synthetic_cells.py. That meant `amore_serverwait_ms()` and
# everything downstream (figure energy values, crossover analysis) was
# 68x off from real-world numbers.
#
# Resolution: we now expose THREE constants:
#   SERVER_RTT_MS_REAL — what a real Pi running py_ecc actually takes.
#                        Telemetry from 2026-05-22 sweep_n10 confirmed
#                        BLS12_381 server compute = 87,500 ± 720 ms/round
#                        (CV = 0.82% across 10 rounds). Add 406 ms UART.
#   SERVER_RTT_MS_MOCK — the 1/100 compressed value used by the mock
#                        server during dev testing. NOT representative
#                        of reality.
#   SERVER_RTT_MS      — alias retained for backward compatibility;
#                        currently points to MOCK. To switch a figure
#                        to real-world projection, import _REAL directly.
#
# Figures used for the paper should target SERVER_RTT_MS_REAL — at
# 87 seconds of ServerWait per round, energy is dominated by what the
# MCU does during that wait (busy-wait vs Stop mode), which is exactly
# the point the paper makes.
SERVER_RTT_MS_MOCK = {
    "BN254":     175.0 + 73452.8 / 100,    # 909.5 ms — mock-server compressed
    "BLS12_381": 406.0 + 87020.4 / 100,    # 1276.2 ms — mock-server compressed
}

SERVER_RTT_MS_REAL = {
    "BN254":     175.0 + 73452.8,          # 73,627.8 ms — real Pi (BN254 awaiting telemetry confirmation)
    "BLS12_381": 406.0 + 87500.0,          # 87,906.0 ms — real Pi (MEDIUM-5 fix:
                                            # updated to telemetry central value
                                            # 87,500 ± 720 ms/round from
                                            # 2026-05-22 sweep_n10. The previous
                                            # 87,020.4 was Diego's 2026-05-07
                                            # baseline — within the telemetry CI
                                            # but 479.6 ms below the central
                                            # value, a 0.55% silent negative bias
                                            # on every BLS ServerWait energy
                                            # calculation.)
}

# CRITICAL-1 fix: the previous module exported `SERVER_RTT_MS` as a
# silent alias for `SERVER_RTT_MS_MOCK`. Every caller of
# amore_serverwait_ms() therefore got the MOCK value (compressed by
# /100), which is 68-80x smaller than the real Pi ServerWait. The
# resulting energy figures were silently 68-80x understated wherever
# busy-wait dominated the round, and silently understated Stop-mode's
# advantage by the same factor.
#
# The fix removes the silent alias entirely. The public API
# (amore_serverwait_ms, amore_round_energy_mJ,
# amore_with_ots_per_round_mJ, session_time_s) now takes an explicit
# ``server`` keyword argument with no default — callers must choose
# "real" or "mock" at the call site. Code that was relying on the old
# alias will fail loudly with a TypeError, not silently with wrong
# numbers.
SERVER_KIND_REAL = "real"
SERVER_KIND_MOCK = "mock"


def _server_rtt_ms(curve: str, server: str) -> float:
    """Look up ServerWait time per round for the chosen server backend.

    Raises ValueError on unknown ``server`` (no silent default) or
    unknown ``curve``.
    """
    if server == SERVER_KIND_REAL:
        table = SERVER_RTT_MS_REAL
    elif server == SERVER_KIND_MOCK:
        table = SERVER_RTT_MS_MOCK
    else:
        raise ValueError(
            f"server must be {SERVER_KIND_REAL!r} or {SERVER_KIND_MOCK!r}, "
            f"got {server!r}"
        )
    if curve not in table:
        raise ValueError(f"unknown curve: {curve!r}")
    return table[curve]


def energy_from_time_ms(time_ms: float, current_mA: float = I_ACTIVE_MA,
                         voltage_V: float = V_NOMINAL) -> float:
    """Energy in mJ from time in ms at given current (mA) and voltage (V)."""
    return time_ms * current_mA * voltage_V / 1000.0


def amore_round_time_ms(curve: str, n: int) -> float:
    """Amortized per-round time, snapped to the nearest measured N.

    We have three measured anchors: N=1, 10, 50. For an arbitrary
    requested N we return the amort_ms of the closest one (by absolute
    distance).

    HIGH-2 fix: unknown curve names now raise ValueError instead of
    silently falling back to BLS12_381 data.

    Bug H1 fix (original): the previous implementation always returned
    N=50 for any 10 < n < 50. For n=15 (closer to 10) the old code
    returned the N=50 number, producing a small systematic bias.

    MEDIUM-3 fix: tie-breaking now goes to the anchor with the LARGER
    amort_ms (truly conservative for AmorE — worse number = more
    cautious claim). The previous "tie-break by larger N" was only
    conservative under the assumption that amort grows monotonically
    with N, which holds for BN254 (372.5 → 378.8 → 381.8) but breaks
    for BLS12_381 (870.3 → 898.2 → 898.0 — N=50 has a LOWER amort
    than N=10). At a tie distance, picking N=50 there was the OPTIMISTIC
    choice, not the conservative one.
    """
    if curve == "BN254":
        data = AMORE_AMORT_BN254
    elif curve == "BLS12_381":
        data = AMORE_AMORT_BLS12_381
    else:
        raise ValueError(f"unknown curve: {curve!r}")

    if n <= 1:
        return data[0].amort_ms
    if n >= 50:
        return data[2].amort_ms
    # Distance to each anchor; ties go to the LARGER amort_ms (conservative).
    # Sort key: (distance ascending, -amort_ms ascending). Smaller distance
    # wins; on ties, smaller -amort_ms wins, i.e. larger amort_ms wins.
    candidates = sorted(
        ((abs(n - p.n), -p.amort_ms, p.amort_ms) for p in data)
    )
    return candidates[0][2]


def amore_serverwait_ms(curve: str, *, server: str) -> float:
    """ServerWait time per round — dominated by Pi server compute + UART RTT.

    CRITICAL-1 fix: ``server`` is now mandatory (no default) so callers
    cannot silently pick up the /100 mock value. Pass ``server="real"``
    for paper figures and projections; ``server="mock"`` only when
    explicitly reasoning about mock-server behaviour.
    """
    return _server_rtt_ms(curve, server)


def amore_round_energy_mJ(curve: str, n: int, *, stop_mode: bool,
                           server: str) -> float:
    """Energy per amortized round of AmorE Mode A.

    Composed of:
      Compute (Blind + Verify) at I_ACTIVE
    + ServerWait energy:
        if baseline:    full SERVER_RTT_MS at I_ACTIVE (busy-wait)
        if stop_mode:   full SERVER_RTT_MS at I_STOP (Stop mode quiescent)

    CRITICAL-1 fix: ``server`` is mandatory (no default). Pass
    ``server="real"`` for paper figures.
    """
    compute_ms = amore_round_time_ms(curve, n)
    wait_ms = amore_serverwait_ms(curve, server=server)

    e_compute = energy_from_time_ms(compute_ms)
    if stop_mode:
        # Stop mode current is in µA; conversion: µA → mA = ÷ 1000
        e_wait = energy_from_time_ms(wait_ms, current_mA=I_STOP_UA / 1000.0)
    else:
        e_wait = energy_from_time_ms(wait_ms)
    return e_compute + e_wait


def amore_with_ots_per_round_mJ(curve: str, n: int, *, stop_mode: bool,
                                  server: str) -> float:
    """Per-round energy INCLUDING amortized OTS overhead.

    CRITICAL-1 fix: ``server`` mandatory.

    Bug M1 fix: previously n=0 produced a ZeroDivisionError (`ots_e/n`).
    Clamp to n=1 — semantically: a session of 0 rounds isn't a session
    at all, but the safer answer is "OTS-only cost is paid in full" rather
    than crash mid-pipeline.
    """
    if n < 1:
        n = 1
    per_round = amore_round_energy_mJ(curve, n, stop_mode=stop_mode,
                                       server=server)
    ots_ms = OTS_MS[curve]
    ots_e = energy_from_time_ms(ots_ms)
    return per_round + ots_e / n


def direct_pairing_energy_mJ(curve: str) -> float:
    """Energy of one direct pairing (= one RELIC pp_map_oatep_k12 invocation)."""
    return energy_from_time_ms(DIRECT_PAIRING_MS[curve])

# ─────────────────────────────────────────────────────────────────────────────
#  Duty-cycle / Stop-mode parameters for Figure 4 (PLACEHOLDER)
# ─────────────────────────────────────────────────────────────────────────────
# Source: STM32F407 datasheet typical values. Will be replaced with PPK2
# measurements when hardware arrives. The (low, high) ranges below define
# the "uncertainty band" plotted in Figure 4.

IDD_STOP_RANGE_UA   = (0.4, 0.6)    # µA — Stop-mode quiescent current
E_WAKEUP_RANGE_UJ   = (10.0, 30.0)  # µJ — energy per wake-up event

# Duty-cycle convention: a device "wakes, runs one session, sleeps".
# T_session = wall-clock duration of one session
#           = OTS + N × (compute + ServerWait)
#           (the MCU may be in Stop during the ServerWait portion of
#            each round, not necessarily active — see MEDIUM-6 fix)
# T_sleep   = idle time between sessions, parameterised by duty cycle
#             duty_cycle = T_session / (T_session + T_sleep)
#
# E_session_total = E_active(N) + T_sleep × IDD_STOP × V + E_wakeup
#
# Comparison vs direct pairing:
# E_direct_total  = N × E_direct_pairing + T_sleep × IDD_STOP × V + E_wakeup
#
# Crossover region: where E_session_AmorE < E_direct.

def session_time_s(curve: str, n: int, *, server: str) -> float:
    """Wall-clock session duration in seconds.

    MEDIUM-6 fix: this is wall-clock time, NOT active time. It includes
    ServerWait, during which the MCU may be in Stop mode (drawing
    ~0.5 µA) rather than active (~85 mA). Multiplying this return value
    by I_ACTIVE for an energy estimate would be wrong by construction —
    use ``amore_round_energy_mJ`` / ``amore_with_ots_per_round_mJ`` for
    energy, which handles the active-vs-Stop split correctly.

    The wall-clock duration is:
        OTS + N × (compute + ServerWait)

    CRITICAL-1 fix: ``server`` is mandatory (no default).

    Bug M1 fix: clamp n>=1 for consistency with amore_with_ots_per_round_mJ.
    Previously n=0 returned just OTS_MS/1000, which silently treats "zero
    rounds" as a valid session — better to normalize at the boundary.
    """
    if n < 1:
        n = 1
    t_ms = OTS_MS[curve] + n * (
        amore_round_time_ms(curve, n)
        + amore_serverwait_ms(curve, server=server)
    )
    return t_ms / 1000.0
