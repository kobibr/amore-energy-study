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
    "BLS12_381": {
        "ots_ms": 1151.2,
        "blind_per_round_ms": 488.28,
        "verify_per_round_ms": 409.70,
        "server_compute_ms": 87020.4,
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

# Mock-time compression factor — server_compute_ms is shrunk by this to keep
# trace files small. WARNING: this scales ServerWait *duration* by 1/100, so
# ServerWait energy (duration × mean_current × V) in synthetic figures is
# ~100× smaller than reality. Compute phases are unaffected.
# See methodology document.
MOCK_SERVER_COMPRESS = 100


@dataclass
class CellSpec:
    curve: str
    mode: str
    n: int
    replicas: int
    noise_sigma_pct: float = 1.5
    stop_mode: bool = False     # NEW — selects scenario (BASELINE vs WITH_STOP)


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
                             stop_mode: bool = False) -> List[Tuple]:
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

    Notes on gpio_byte coding when stop_mode=True
    ---------------------------------------------
    Real firmware in Stop mode CANNOT keep PA1 high — entering Stop disables
    most peripherals, including GPIO output. So a Stop-mode ServerWait must
    have gpio_byte=0 (the MCU just looks "idle" from a trigger perspective).
    The current value differentiates: 0.5 µA = Stop, 50 mA = ordinary idle.

    This is consistent with what real PPK2 would capture: a ~0.5 µA floor
    during ServerWait, with brief wake-up bursts (~13 µs at 80 mA, modelled
    in current_synthesis.py as WAKEUP_BURST_*) at start and end.
    """
    rng = random.Random(seed)
    p = CURVES[curve]
    rows: List[Tuple] = []
    t = 0

    # Initial idle (50 ms)
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)

    # OTS phase — compute, never optimized to Stop mode (CPU is actively computing xi)
    t = _emit_phase(rows, t, int(p["ots_ms"] * 1000), 1, I_SETUP_uA, rng, 1.8)

    # Idle (5 ms gap)
    t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)

    for _ in range(n):
        # Blind (compute)
        t = _emit_phase(rows, t, int(p["blind_per_round_ms"] * 1000), 1, I_SETUP_uA, rng, 1.8)

        # ServerWait — the optimization point
        wait_ms = p["uart_rtt_ms"] + p["server_compute_ms"] / MOCK_SERVER_COMPRESS
        if stop_mode:
            # Stop mode: gpio_byte=0, current = 0.5 µA (quiescent)
            # Use a tiny sigma (0.1 µA absolute) since we can't use 1.8% of 0.5 µA
            # which would be way below sensor noise floor.
            t_stop = t
            end_stop = t + int(wait_ms * 1000)
            while t_stop < end_stop:
                i = max(0.0, rng.gauss(I_WAIT_STOP_uA, 0.1))
                rows.append((t_stop, i, V_NOMINAL, 0))   # gpio_byte=0 in Stop
                t_stop += SAMPLE_PERIOD_us
            t = end_stop
        else:
            # Baseline busy-wait: gpio_byte=2, current = 55 mA
            t = _emit_phase(rows, t, int(wait_ms * 1000), 2, I_WAIT_uA, rng, 1.8)

        # Verify (compute)
        t = _emit_phase(rows, t, int(p["verify_per_round_ms"] * 1000), 1, I_SETUP_uA, rng, 1.8)

        # Idle gap (5 ms)
        t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)

    # Final idle (50 ms)
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    return rows


def synthesize_mode_b_trace(curve: str, n: int, seed: int = 0) -> List[Tuple]:
    """Mode B: N consecutive direct pairings, no protocol overhead.

    Mode B has no ServerWait phase by definition, so stop_mode is irrelevant.
    """
    rng = random.Random(seed)
    p = CURVES[curve]
    rows: List[Tuple] = []
    t = 0
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    for _ in range(n):
        t = _emit_phase(rows, t, int(p["direct_pairing_ms"] * 1000), 1, I_SETUP_uA, rng, 1.8)
        t = _emit_phase(rows, t, 5_000, 0, I_IDLE_uA, rng, 2.0)
    t = _emit_phase(rows, t, 50_000, 0, I_IDLE_uA, rng, 2.0)
    return rows


def write_synthetic_cell(spec: CellSpec, out_dir: Path) -> Path:
    """Write replicas to disk. Stop-mode cells get a __stop suffix."""
    base_id = f"{spec.curve.lower()}__{spec.mode.lower()}__N{spec.n}__r{spec.replicas}"
    if spec.stop_mode and spec.mode == "A":
        cell_id = f"{base_id}__stop"
    else:
        cell_id = base_id

    cell_dir = out_dir / cell_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, spec.replicas + 1):
        if spec.mode == "A":
            rows = synthesize_mode_a_trace(
                spec.curve, spec.n, seed=k, stop_mode=spec.stop_mode
            )
        else:
            rows = synthesize_mode_b_trace(spec.curve, spec.n, seed=k)
        csv_path = cell_dir / f"run_{k:03d}.csv"
        with csv_path.open("w") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])
            for r in rows:
                w.writerow([r[0], f"{r[1]:.3f}", f"{r[2]:.3f}", r[3]])
    return cell_dir
