"""Synthetic baseline data derived from doc/AmorE_*_Results.txt.

Generates two parallel scenarios per cell:

  - BASELINE  (stop_mode=False):  firmware busy-waits on UART recv during
                                   ServerWait; MCU at ~55 mA continuously.
                                   Reflects the as-built firmware (amore.c
                                   line 320: uart_recv_packet busy-loop).

  - WITH_STOP (stop_mode=True):   proposed optimization. MCU enters Stop mode
                                   during ServerWait; quiescent current drops
                                   to 0.5 µA (per current_synthesis.py spec
                                   table sub-section "Stop-mode special case").
                                   Requires firmware changes.

Phase durations come from doc/AmorE_*_Results.txt cycle counts; phase
currents come from current_synthesis.py spec table (mirrored here as
module-level constants).

When real PPK2 traces arrive, plot scripts work unchanged.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

CURVES = {
    "BN254": {
        "ots_ms": 503.9,
        "blind_per_round_ms": 199.4,
        "verify_per_round_ms": 182.4,
        "server_compute_ms": 73452.8,
        "uart_rtt_ms": 175.0,
        "direct_pairing_ms": 252.3,
    },
        # BLS12_381 amort numbers below are post-O3 (-DCMAKE_BUILD_TYPE=Release)
        # Source: doc/AmorE_BLS12_381_Results.txt Section 8 (2026-05-12).
        # Binary SHA prefix 4e2df263, tag measurement-O3-2026-05-12.
        # direct_pairing_ms below remains pre-O3 (RELIC rebuild pending —
        # see docs/future_work.md "RELIC re-measurement at -O3").
        # server_compute_ms updated 2026-05-23 from 87020.4 → 87500.0 to
        # match baseline_data MEDIUM-5 fix (telemetry central value from
        # 2026-05-22 sweep_n10: 87,500 ± 720 ms/round).
    "BLS12_381": {
        "ots_ms": 1151.2,
        "blind_per_round_ms": 488.28,
        "verify_per_round_ms": 409.70,
        "server_compute_ms": 87500.0,
        "uart_rtt_ms": 406.0,
        "direct_pairing_ms": 523.4,  # MEASURED on STM32F407 via relic_bench.elf
                                    # Source: doc/AmorE_BLS12_381_Results.txt §5
                                    # 10× pp_map_oatep_k12, CV<0.001%, dated 2026-05-07
                                    # PREVIOUSLY (incorrect): 1290.0 (estimate from BN254×5)
    },
}

# Current models (mA → µA when used). Mirror of current_synthesis.py spec table.
I_IDLE_uA      = 50_000.0
I_SETUP_uA     = 85_000.0   # Setup / Blind / Verify (PA0 high)
I_WAIT_uA      = 55_000.0   # ServerWait, baseline firmware (busy-wait UART)
I_WAIT_STOP_uA = 0.5        # ServerWait, with Stop mode (quiescent µA, not mA)

V_NOMINAL          = 3.300
SAMPLE_PERIOD_us   = 40        # 25 kHz mock rate

# Bug #1 fix (silent-bias re-review 2026-05-23): synthetic gpio_byte
# for Stop-mode ServerWait. Real firmware in Stop CANNOT keep PA1
# high (peripherals disabled in Stop), so a real PPK2 capture would
# see gpio_byte=0 for Stop — indistinguishable from Idle. The PREVIOUS
# synthetic code mirrored this and used gpio_byte=0 for Stop, but
# that produced a SILENT classification bug downstream:
#
# In compute_energy.compute_trace, Idle phases (50 ms initial,
# 5 ms inter-round gaps, 50 ms final) all use gpio_byte=0 with
# I=50 mA. Stop ServerWait phases used gpio_byte=0 with I=0.5 µA.
# Both got aggregated under by_gpio_byte[0], producing:
#   - duration-weighted mean_current_uA ≈ 598 µA — a nonsense
#     intermediate value (~6× higher than Stop, ~80× lower than Idle)
#   - by_gpio_byte breakdown made Stop ServerWait *invisible*:
#     the bar showed "Idle = 25 mJ" with no separate Stop entry.
#   - total_energy_J was still correct (energy doesn't disappear,
#     just gets mis-classified), so simple sanity tests passed.
#
# Fix: synthetic Stop now emits gpio_byte=8, deliberately outside
# the firmware's 0..7 GPIO encoding (PA0|PA1<<1|PA4<<2). This is
# a SYNTHETIC-DATA-ONLY label; real PPK2 captures will need a
# current-range classifier in compute_energy or downstream to
# perform the same separation. See compute_energy's runtime warning
# (added in the same review) that flags real-data cases where one
# gpio_byte contains phases varying >100× in current.
GPIO_BYTE_STOP_SYNTHETIC = 8

# Bug #3 fix: wake-up bursts in Stop ServerWait are now emitted.
# Real PPK2 captures see a brief current spike (~80 mA for ~13 µs)
# at Stop entry and exit, contributing ~6.9 µJ per round. The
# previous synthetic emitted a flat 0.5 µA floor and silently
# under-reported this energy. Each Stop phase now emits 2 wake
# samples (one at entry, one before exit). Each sample represents
# the burst spread across one SAMPLE_PERIOD_us window, with a
# current value chosen so the total energy contribution matches
# the real 80 mA × 13 µs:
#   E_per_burst = 80 mA × 13 µs × V = 80e-3 × 13e-6 × 3.3 = 3.43 µJ
#   I_smeared   = 3.43 µJ / (V × SAMPLE_PERIOD_us)
#                = 3.43e-6 / (3.3 × 40e-6) ≈ 26 mA = 26,000 µA
# So 2 × 40 µs × 26 mA × 3.3 V = 6.86 µJ per round, matching the
# 6.9 µJ figure from the spec.
WAKEUP_BURST_DURATION_REAL_us = 13       # physical, for reference
WAKEUP_BURST_PEAK_REAL_uA     = 80_000.0  # physical, for reference
# Smeared per-sample equivalent (so 1 sample carries 1 burst's energy):
WAKEUP_BURST_SAMPLE_uA = (
    WAKEUP_BURST_PEAK_REAL_uA *
    WAKEUP_BURST_DURATION_REAL_us / SAMPLE_PERIOD_us
)  # = 26,000 µA

# Mock-time compression factor — server_compute_ms is shrunk by this to keep
# trace files small. Only server_compute_ms is divided; uart_rtt_ms stays in
# its real-world magnitude. The effective compression ratio is therefore
# curve-dependent, NOT a flat 100×.
#
# Bug #4 fix (silent-bias review): the previous comment claimed
# "ServerWait energy in synthetic figures is ~100× smaller than reality",
# which is materially wrong because only server_compute is compressed:
#
#   real_ms = uart_rtt_ms + server_compute_ms
#   mock_ms = uart_rtt_ms + server_compute_ms / MOCK_SERVER_COMPRESS
#   ratio  = real_ms / mock_ms
#
# Computed from the values in CURVES above (verified by
# server_compress_ratio() at runtime — call that function in new
# code rather than hardcoding these numbers, so the next CURVES
# update doesn't strand stale comments):
#
#   BN254:     real ≈ 73,628 / mock ≈   910  → ratio ≈ 80.9×
#   BLS12_381: real ≈ 87,906 / mock ≈ 1,281  → ratio ≈ 68.6×
#       (updated 2026-05-23 to track BLS12_381 server_compute_ms
#        bump from 87,020.4 → 87,500.0; previous comment showed
#        87,426.4 / 1,276.2 → 68.5×, all stale.)
#
# Any downstream code that scaled synthetic ServerWait energy back up
# by 100× was off by 20-30% systematically. Use server_compress_ratio()
# below to get the true per-curve ratio if you need to scale back to
# real-world numbers — but PREFER targeting SERVER_RTT_MS_REAL in
# baseline_data.py instead of scaling synthetic cells.
MOCK_SERVER_COMPRESS = 100


def server_compress_ratio(curve: str) -> float:
    """True real-vs-mock ServerWait duration ratio for the given curve.

    Use this if you have a synthetic ServerWait duration/energy and
    need the real-world equivalent. Returns real_ms / mock_ms with
    the same arithmetic used by synthesize_mode_a_trace below.
    """
    p = CURVES[curve]
    real_ms = p["uart_rtt_ms"] + p["server_compute_ms"]
    mock_ms = p["uart_rtt_ms"] + p["server_compute_ms"] / MOCK_SERVER_COMPRESS
    return real_ms / mock_ms


@dataclass
class CellSpec:
    curve: str
    mode: str
    n: int
    replicas: int
    noise_sigma_pct: float = 1.8     # Bug #2 fix: now actually used (was dead).
                                      # Default 1.8 matches the previously
                                      # hardcoded compute-phase sigma so existing
                                      # synthetic data is reproducible.
    stop_mode: bool = False           # selects scenario (BASELINE vs WITH_STOP)

    def __post_init__(self) -> None:
        # Bug #5 fix (silent-bias review): Mode B has no ServerWait
        # phase, so the Stop-mode optimization has nothing to optimize.
        # The previous behaviour silently dropped stop_mode for Mode B
        # AND silently dropped the "__stop" suffix from the cell_id,
        # producing two indistinguishable files for baseline and "stop"
        # — downstream comparison would conclude "Stop saves 0% in
        # Mode B", which is true by construction but masks the bug.
        # Fail loud instead.
        if self.mode == "B" and self.stop_mode:
            raise ValueError(
                "CellSpec: mode='B' has no ServerWait phase, so "
                "stop_mode=True is meaningless. Either set mode='A' "
                "(if you wanted to test the Stop optimization) or "
                "stop_mode=False (if you wanted Mode B baseline). "
                "Refusing to generate an indistinguishable duplicate "
                "of the baseline."
            )
        if self.mode not in ("A", "B"):
            raise ValueError(
                f"CellSpec.mode must be 'A' or 'B', got {self.mode!r}"
            )
        if self.curve not in CURVES:
            raise ValueError(
                f"CellSpec.curve must be one of {sorted(CURVES)}, "
                f"got {self.curve!r}"
            )


def _emit_phase(rows, t_start_us, duration_us, gpio_byte,
                mean_current_uA, rng, sigma_pct):
    """Emit Gaussian-noised current samples for a single phase.

    Note: noise is symmetric around mean. For very small currents (Stop mode
    at 0.5 µA), we clip at 0 to avoid negative current readings.
    """
    sigma = mean_current_uA * sigma_pct / 100.0
    t = t_start_us
    end = t_start_us + duration_us
    while t < end:
        i = max(0.0, rng.gauss(mean_current_uA, sigma))
        rows.append((t, i, V_NOMINAL, gpio_byte))
        t += SAMPLE_PERIOD_us
    return end


def synthesize_mode_a_trace(curve: str, n: int, seed: int = 0,
                             stop_mode: bool = False,
                             compute_sigma_pct: float = 1.8) -> List[Tuple]:
    """Generate one AmorE Mode A trace (OTS + N rounds).

    Parameters
    ----------
    curve : "BN254" or "BLS12_381"
    n     : number of rounds in the batch
    seed  : RNG seed for replica reproducibility
    stop_mode : if True, ServerWait phase uses Stop-mode quiescent current
                (0.5 µA, gpio_byte=0) instead of busy-wait current
                (55 mA, gpio_byte=2). Models the proposed firmware
                optimization.
    compute_sigma_pct : Gaussian sigma as % of mean for compute-bound
                phases (OTS, Blind, Verify, baseline ServerWait). Bug #2
                fix: this is the ONLY tunable in CellSpec.noise_sigma_pct.
                Idle and Stop phases use fixed sigmas (2.0 % and 0.1 µA
                respectively) because they represent different physical
                noise sources (idle current ripple vs Stop-mode quiescent
                resolution floor).

    Notes on gpio_byte coding when stop_mode=True
    ---------------------------------------------
    Bug #1 fix (silent-bias re-review 2026-05-23): synthetic Stop
    ServerWait phases now use gpio_byte = GPIO_BYTE_STOP_SYNTHETIC (=8),
    NOT 0. The previous behaviour (gpio_byte=0, mirroring real-firmware
    constraints) caused Stop ServerWait to alias with Idle phases in
    compute_energy's by_gpio_byte aggregation: both Idle (50 mA) and
    Stop (0.5 µA) collapsed to the same key, producing a meaningless
    duration-weighted mean current and hiding Stop ServerWait from
    the phase breakdown entirely.

    Note that REAL PPK2 captures will still see gpio_byte=0 for Stop
    because the physical PA1 cannot stay high in Stop mode. The
    separation for real data must happen via a current-range
    classifier (added as a runtime warning in compute_energy in the
    same review — it flags any gpio_byte whose phases vary >100× in
    current). Synthetic and real data therefore behave DIFFERENTLY
    on this point; downstream tools that compare them should be
    aware. The synthetic divergence is intentional: it keeps the
    pipeline test data unambiguous.

    Wake-up bursts (Bug #3 fix)
    ---------------------------
    Each Stop ServerWait now emits two wake-up burst samples — one
    at the start of the phase, one before the end. Each sample
    represents one physical ~13 µs / ~80 mA burst, smeared across
    one synthetic SAMPLE_PERIOD_us (40 µs) window. The smeared
    sample current (~26 mA) is chosen so the integrated energy
    per burst matches the physical 80 mA × 13 µs ≈ 3.43 µJ — and
    the per-round burst energy ≈ 6.86 µJ matches the spec figure
    of 6.9 µJ. The bursts retain gpio_byte = GPIO_BYTE_STOP_SYNTHETIC
    so they aggregate with the Stop quiescent floor, not with the
    Compute phase that surrounds them. Any analysis that previously
    consumed synthetic cells to estimate wakeup energy will now get
    the correct contribution; previously it under-reported by the
    full 6.9 µJ per round (becoming material at N≥1000).
    """
    rng = random.Random(seed)
    p = CURVES[curve]
    rows: List[Tuple] = []
    t = 0

    # Initial idle (50 ms). Idle uses its own sigma (2.0 %); not the
    # compute-phase knob.
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)

    # OTS phase — compute, never optimized to Stop mode (CPU is actively computing xi)
    t = _emit_phase(rows, t, int(p["ots_ms"] * 1000), 1, I_SETUP_uA, rng, compute_sigma_pct)

    # Idle (5 ms gap)
    t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)

    for _ in range(n):
        # Blind (compute)
        t = _emit_phase(rows, t, int(p["blind_per_round_ms"] * 1000), 1, I_SETUP_uA, rng, compute_sigma_pct)

        # ServerWait — the optimization point
        wait_ms = p["uart_rtt_ms"] + p["server_compute_ms"] / MOCK_SERVER_COMPRESS
        if stop_mode:
            # Stop mode (Bug #1 fix): gpio_byte = GPIO_BYTE_STOP_SYNTHETIC,
            # not 0. See class docstring above for the rationale.
            #
            # Use a tiny ABSOLUTE sigma (0.1 µA) for the quiescent floor:
            # 1.8% of 0.5 µA would be ~0.009 µA — well below sensor noise
            # floor. This sigma is NOT a percent of mean; it stays
            # hardcoded regardless of compute_sigma_pct.
            t_stop_start = t
            end_stop = t + int(wait_ms * 1000)

            # Bug #3 fix: emit the entry wake-up burst. One synthetic
            # sample carrying the energy of the physical ~80 mA × ~13 µs
            # event.
            rows.append((
                t_stop_start,
                rng.gauss(WAKEUP_BURST_SAMPLE_uA, WAKEUP_BURST_SAMPLE_uA * 0.02),
                V_NOMINAL,
                GPIO_BYTE_STOP_SYNTHETIC,
            ))
            t_stop = t_stop_start + SAMPLE_PERIOD_us

            # Quiescent floor. Reserve the final sample slot for the
            # exit burst (Bug #3 fix), so we stop the loop one sample
            # period before end_stop.
            exit_burst_ts = end_stop - SAMPLE_PERIOD_us
            while t_stop < exit_burst_ts:
                i = max(0.0, rng.gauss(I_WAIT_STOP_uA, 0.1))
                rows.append((t_stop, i, V_NOMINAL, GPIO_BYTE_STOP_SYNTHETIC))
                t_stop += SAMPLE_PERIOD_us

            # Bug #3 fix: emit the exit wake-up burst.
            if exit_burst_ts >= t_stop_start + SAMPLE_PERIOD_us:
                rows.append((
                    exit_burst_ts,
                    rng.gauss(WAKEUP_BURST_SAMPLE_uA, WAKEUP_BURST_SAMPLE_uA * 0.02),
                    V_NOMINAL,
                    GPIO_BYTE_STOP_SYNTHETIC,
                ))
            t = end_stop
        else:
            # Baseline busy-wait: gpio_byte=2, current = 55 mA. Compute-class
            # noise (the firmware is running a tight UART recv loop).
            t = _emit_phase(rows, t, int(wait_ms * 1000), 2, I_WAIT_uA, rng, compute_sigma_pct)

        # Verify (compute)
        t = _emit_phase(rows, t, int(p["verify_per_round_ms"] * 1000), 1, I_SETUP_uA, rng, compute_sigma_pct)

        # Idle gap (5 ms)
        t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)

    # Final idle (50 ms)
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    return rows


def synthesize_mode_b_trace(curve: str, n: int, seed: int = 0,
                             compute_sigma_pct: float = 1.8) -> List[Tuple]:
    """Mode B: N consecutive direct pairings, no protocol overhead.

    Mode B has no ServerWait phase by definition, so stop_mode is
    irrelevant and CellSpec.__post_init__ rejects mode='B' + stop_mode=True
    (Bug #5 fix).

    ``compute_sigma_pct`` (Bug #2 fix): tunable noise level for the
    direct-pairing compute phases, mirroring synthesize_mode_a_trace.
    """
    rng = random.Random(seed)
    p = CURVES[curve]
    rows: List[Tuple] = []
    t = 0
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    for _ in range(n):
        t = _emit_phase(rows, t, int(p["direct_pairing_ms"] * 1000), 1, I_SETUP_uA, rng, compute_sigma_pct)
        t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    return rows


def write_synthetic_cell(spec: CellSpec, out_dir: Path) -> Path:
    """Write replicas to disk. Stop-mode cells get a __stop suffix."""
    base_id = f"{spec.curve.lower()}__{spec.mode.lower()}__N{spec.n}__r{spec.replicas}"
    # Bug #5 fix is in CellSpec.__post_init__ now: mode='B' + stop_mode=True
    # raises ValueError, so this code path is unreachable for that case.
    # The conditional below is still correct: only Mode A cells get the
    # __stop suffix because Mode B never has stop_mode=True.
    if spec.stop_mode and spec.mode == "A":
        cell_id = f"{base_id}__stop"
    else:
        cell_id = base_id

    cell_dir = out_dir / cell_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, spec.replicas + 1):
        if spec.mode == "A":
            # Bug #2 fix: pass spec.noise_sigma_pct through.
            rows = synthesize_mode_a_trace(
                spec.curve, spec.n, seed=k, stop_mode=spec.stop_mode,
                compute_sigma_pct=spec.noise_sigma_pct,
            )
        else:
            rows = synthesize_mode_b_trace(
                spec.curve, spec.n, seed=k,
                compute_sigma_pct=spec.noise_sigma_pct,
            )
        csv_path = cell_dir / f"run_{k:03d}.csv"
        with csv_path.open("w") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])
            for r in rows:
                w.writerow([r[0], f"{r[1]:.3f}", f"{r[2]:.3f}", r[3]])
    return cell_dir
