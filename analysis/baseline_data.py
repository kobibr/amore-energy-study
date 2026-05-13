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
# CAVEAT (under review
# OQ-1): the /100 factor below applies MOCK_SERVER_COMPRESS from
# analysis/fixtures/synthetic_cells.py. Earlier analysis claimed figures were
# immune to this compression, but figures consuming SERVER_RTT_MS via
# amore_serverwait_ms() / amore_round_energy_mJ() do inherit it. Full
# fix (SERVER_RTT_MS_MOCK vs SERVER_RTT_MS_REAL split) is deferred to
# Task 5.0 with real PPK2 data.
SERVER_RTT_MS = {
    "BN254":     175.0  + 73452.8 / 100,   # uart_rtt + server_compute/MOCK_COMPRESS
    "BLS12_381": 406.0  + 87020.4 / 100,
}


def energy_from_time_ms(time_ms: float, current_mA: float = I_ACTIVE_MA,
                         voltage_V: float = V_NOMINAL) -> float:
    """Energy in mJ from time in ms at given current (mA) and voltage (V)."""
    return time_ms * current_mA * voltage_V / 1000.0


def amore_round_time_ms(curve: str, n: int) -> float:
    """Amortized per-round time, interpolated between measured N points."""
    data = AMORE_AMORT_BN254 if curve == "BN254" else AMORE_AMORT_BLS12_381
    # Use the closest measured N to the requested N (we only have N=1, 10, 50).
    # For N values between, return the nearest. For N>50, return N=50 (asymptote).
    if n <= 1:
        return data[0].amort_ms
    if n >= 50:
        return data[2].amort_ms
    if n <= 10:
        return data[1].amort_ms
    return data[2].amort_ms


def amore_serverwait_ms(curve: str) -> float:
    """ServerWait time per round — dominated by Pi server compute + UART RTT."""
    return SERVER_RTT_MS[curve]


def amore_round_energy_mJ(curve: str, n: int, *, stop_mode: bool) -> float:
    """Energy per amortized round of AmorE Mode A.

    Composed of:
      Compute (Blind + Verify) at I_ACTIVE
    + ServerWait energy:
        if baseline:    full SERVER_RTT_MS at I_ACTIVE (busy-wait)
        if stop_mode:   full SERVER_RTT_MS at I_STOP (Stop mode quiescent)
    """
    compute_ms = amore_round_time_ms(curve, n)
    wait_ms = amore_serverwait_ms(curve)

    e_compute = energy_from_time_ms(compute_ms)
    if stop_mode:
        # Stop mode current is in µA; conversion: µA → mA = ÷ 1000
        e_wait = energy_from_time_ms(wait_ms, current_mA=I_STOP_UA / 1000.0)
    else:
        e_wait = energy_from_time_ms(wait_ms)
    return e_compute + e_wait


def amore_with_ots_per_round_mJ(curve: str, n: int, *, stop_mode: bool) -> float:
    """Per-round energy INCLUDING amortized OTS overhead."""
    per_round = amore_round_energy_mJ(curve, n, stop_mode=stop_mode)
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
# T_session = active time per session = OTS + N × (compute + ServerWait)
# T_sleep   = idle time between sessions, parameterised by duty cycle
#             duty_cycle = T_session / (T_session + T_sleep)
#
# E_session_total = E_active(N) + T_sleep × IDD_STOP × V + E_wakeup
#
# Comparison vs direct pairing:
# E_direct_total  = N × E_direct_pairing + T_sleep × IDD_STOP × V + E_wakeup
#
# Crossover region: where E_session_AmorE < E_direct.

def session_time_s(curve: str, n: int) -> float:
    """Active time per session in seconds: OTS + N × (compute + ServerWait)."""
    t_ms = OTS_MS[curve] + n * (amore_round_time_ms(curve, n) + amore_serverwait_ms(curve))
    return t_ms / 1000.0
