"""Integration tests for the full host-side pipeline.

Per spec §10.2, these tests drive the full pipeline (events →
interpolated → assembled → CSV-format Samples) using a scripted source,
without GPIO hardware. They verify the **statistical** correctness of
the output for canonical AmorE scenarios.

Pipeline composed:

    ScriptedGPIOSource.events()
        → interpolate_to_fixed_rate()      # 100 ksps
            → assemble_samples()           # add current+voltage
                → list  (consumed by these tests for stats)

The fifth integration test in the spec (§10.2 ``test_wakeup_burst.py``)
is deferred until the wake-up burst overlay layer lands in a later
iter — that layer is stateful and not part of the base assembler.
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csv_format import Sample  # noqa: E402
from gpio_source import ScriptedGPIOSource  # noqa: E402
from interpolator import interpolate_to_fixed_rate  # noqa: E402
from sample_assembler import assemble_samples  # noqa: E402

# A consistent seed gives reproducible statistics. Each test gets its own
# seed offset so a regression in one test doesn't mask another.
BASE_SEED = 0xA1A2A3


def _run_pipeline(
    events: list[tuple[int, int]],
    end_time_us: int,
    voltage_mV: int = 3300,
    stop_mode: bool = False,
    seed: int = 0,
) -> List[Sample]:
    """Drive the full pipeline and materialize samples."""
    src = ScriptedGPIOSource(events)
    gpio_samples = interpolate_to_fixed_rate(
        src.events(), end_time_us=end_time_us
    )
    return list(
        assemble_samples(
            gpio_samples,
            voltage_mV=voltage_mV,
            stop_mode=stop_mode,
            rng=random.Random(BASE_SEED + seed),
        )
    )


# ---------------------------------------------------------------------------
# Spec §10.2 — test_idle_baseline
# ---------------------------------------------------------------------------

def test_idle_baseline_1_second() -> None:
    """1 s of idle (no GPIO transitions); mean ~50 mA, σ < 1.5 mA.

    Spec calls for 30 seconds, but the statistical conclusion converges
    long before that — at 100 ksps × 1 s = 100 000 samples the SE on
    the mean is ≈ 3 µA. We use 1 s to keep the test fast.
    """
    samples = _run_pipeline(
        events=[],  # no transitions — pure idle
        end_time_us=1_000_000,
        seed=1,
    )
    assert len(samples) == 100_000

    currents = [s.current_uA for s in samples]
    mean_mA = statistics.fmean(currents) / 1000.0
    sigma_mA = statistics.stdev(currents) / 1000.0

    # Spec acceptance: mean = 50.0 ± 1.0 mA, σ < 1.5 mA
    assert abs(mean_mA - 50.0) < 1.0, f"idle mean={mean_mA:.3f} mA"
    assert sigma_mA < 1.5, f"idle σ={sigma_mA:.3f} mA"

    # All gpio_bytes should be 0 (initial state, no events)
    assert all(s.gpio_byte == 0 for s in samples)


# ---------------------------------------------------------------------------
# Spec §10.2 — test_setup_active
# ---------------------------------------------------------------------------

def test_setup_active_pa0_high_for_380ms() -> None:
    """PA0 high for 380 ms (BN254-like Setup phase) → window mean ~85 mA.

    Total recording: 1 s. PA0 goes high at t=100 ms, low at t=480 ms.
    """
    samples = _run_pipeline(
        events=[(100_000, 1), (480_000, 0)],
        end_time_us=1_000_000,
        seed=2,
    )

    # Slice out the Setup-phase window (PA0 high). At 100 ksps, t=100ms
    # is index 10000, t=480ms is index 48000.
    setup_window = samples[10_000:48_000]
    assert len(setup_window) == 38_000
    assert all(s.gpio_byte == 1 for s in setup_window)

    setup_mean_mA = (
        statistics.fmean(s.current_uA for s in setup_window) / 1000.0
    )
    # Spec: mean = 85.0 ± 1.5 mA
    assert abs(setup_mean_mA - 85.0) < 1.5, (
        f"setup window mean={setup_mean_mA:.3f} mA"
    )

    # Verify the surrounding idle windows are still ~50 mA
    pre_idle = samples[:10_000]
    post_idle = samples[48_000:]
    pre_mean_mA = statistics.fmean(s.current_uA for s in pre_idle) / 1000.0
    post_mean_mA = statistics.fmean(s.current_uA for s in post_idle) / 1000.0
    assert abs(pre_mean_mA - 50.0) < 1.0
    assert abs(post_mean_mA - 50.0) < 1.0


# ---------------------------------------------------------------------------
# Spec §10.2 — test_uart_window
# ---------------------------------------------------------------------------

def test_uart_window_700us_pulse() -> None:
    """PA4 high for 700 µs (~64 bytes at 921600 baud) → 70 high samples.

    At 100 ksps the 700 µs window contains 70 samples. Each carries
    gpio_byte=4 (PA4 only). Their mean current is ~88 mA (spec §7.3
    UART state).
    """
    samples = _run_pipeline(
        events=[(0, 4), (700, 0)],  # PA4 high from t=0 to t=700µs
        end_time_us=2000,           # capture 2 ms total
        seed=3,
    )

    high_samples = [s for s in samples if s.gpio_byte == 4]
    low_samples  = [s for s in samples if s.gpio_byte == 0]
    assert len(high_samples) == 70   # 0..690 inclusive
    assert len(low_samples) == 130   # 700..1990 inclusive

    # Mean current in the UART window — spec says 88 mA ± 1.5 mA.
    # n=70 → SE ≈ 1500/√70 ≈ 180 µA → 4·SE ≈ 720 µA = 0.72 mA. Tight
    # but well within spec's ±1.5 mA tolerance.
    uart_mean_mA = (
        statistics.fmean(s.current_uA for s in high_samples) / 1000.0
    )
    assert abs(uart_mean_mA - 88.0) < 1.5, (
        f"UART window mean={uart_mean_mA:.3f} mA"
    )


# ---------------------------------------------------------------------------
# Spec §10.2 — test_stop_mode (1 s instead of 1 min — see docstring)
# ---------------------------------------------------------------------------

def test_stop_mode_quiescent_for_1_second() -> None:
    """Stop-mode start_measuring with no GPIO activity → mean ~0.5 µA.

    Spec calls for 1 minute (6 000 000 samples = 300 MB CSV) which is
    impractical for unit tests. Statistical convergence is excellent
    at 1 second (100 000 samples; SE on the mean ≈ 0.0003 µA), so we
    take that as the unit-test sample size. The full-minute version
    would live as a longer-running acceptance test.
    """
    samples = _run_pipeline(
        events=[],
        end_time_us=1_000_000,
        stop_mode=True,
        seed=4,
    )
    assert len(samples) == 100_000

    currents_uA = [s.current_uA for s in samples]
    mean_uA = statistics.fmean(currents_uA)
    # Spec: mean ~0.5 µA. Tolerate 0.1 µA spread (well above SE).
    assert abs(mean_uA - 0.5) < 0.1, f"stop-mode mean={mean_uA:.4f} µA"
    # All currents should be tiny (under 1 µA each)
    assert max(currents_uA) < 2.0, (
        f"stop-mode max current = {max(currents_uA)} µA — too high"
    )


# ---------------------------------------------------------------------------
# Bonus: pipeline lazy / streaming property
# ---------------------------------------------------------------------------

def test_pipeline_streams_lazily() -> None:
    """The full pipeline is a chain of generators — no full materialization.

    Take 1000 samples from a 'long' (1 s = 100 000 samples) pipeline
    and verify we don't have to consume the whole thing.
    """
    src = ScriptedGPIOSource([(100_000, 1)])
    gpio_samples = interpolate_to_fixed_rate(
        src.events(), end_time_us=1_000_000
    )
    samples_iter = assemble_samples(
        gpio_samples, voltage_mV=3300, rng=random.Random(0)
    )

    first_1000 = []
    for _ in range(1000):
        first_1000.append(next(samples_iter))

    assert len(first_1000) == 1000
    # First 1000 samples cover t=0..9990 µs — all gpio_byte=0 (event at 100ms)
    assert all(s.gpio_byte == 0 for s in first_1000)


# ---------------------------------------------------------------------------
# CSV round-trip — proves the full pipeline writes a valid canonical CSV
# ---------------------------------------------------------------------------

def test_full_pipeline_to_csv_round_trip(tmp_path: Path) -> None:
    """Drive a tiny scenario all the way through to a CSV and read it back."""
    from csv_format import read_samples, write_samples

    samples = _run_pipeline(
        events=[(0, 1), (100, 0)],
        end_time_us=200,
        seed=5,
    )
    assert len(samples) == 20  # 200 µs / 10 µs

    out = tmp_path / "trace.csv"
    n = write_samples(out, samples)
    assert n == 20

    decoded = list(read_samples(out))
    assert len(decoded) == 20
    # gpio_byte profile: 10 samples at 1, then 10 at 0
    assert [s.gpio_byte for s in decoded] == [1] * 10 + [0] * 10
    # Voltage stable across all samples
    assert all(s.voltage_V == 3.3 for s in decoded)
