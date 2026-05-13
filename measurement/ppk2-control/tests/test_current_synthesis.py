"""Tests for ``current_synthesis``.

Per spec §10.1: 'table-driven test of gpio_byte → current for all 8 input states'.
Also covers stop-mode override and the statistical correctness of the noise.
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from current_synthesis import (  # noqa: E402  — sys.path tweak above
    CurrentModel,
    RESERVED_MASK,
    STOP_MODE_MEAN_UA,
    STOP_MODE_SIGMA_UA,
    WAKEUP_BURST_DURATION_US,
    WAKEUP_BURST_PEAK_UA,
    model_for,
    sample_current,
)


# ---------------------------------------------------------------------------
# Spec §7.3 table — exhaustive 8-state coverage (the §10.1 acceptance test)
# ---------------------------------------------------------------------------

# Each tuple: (gpio_byte, mean_mA, sigma_mA) — copied verbatim from
# docs/MOCK_PPK2_SPEC.md §7.3
SPEC_TABLE: list[tuple[int, float, float]] = [
    (0b000, 50.0, 1.0),    # Idle
    (0b001, 85.0, 1.5),    # Setup (PA0)
    (0b010, 55.0, 1.0),    # ServerWait (PA1)
    (0b011, 85.0, 1.5),    # Setup+SW (illegal)
    (0b100, 88.0, 1.5),    # UART (PA4)
    (0b101, 90.0, 1.5),    # Setup+UART (illegal)
    (0b110, 88.0, 1.5),    # SW+UART (illegal)
    (0b111, 90.0, 1.5),    # All (illegal)
]


@pytest.mark.parametrize("gpio_byte,mean_mA,sigma_mA", SPEC_TABLE)
def test_model_for_matches_spec_table(
    gpio_byte: int, mean_mA: float, sigma_mA: float
) -> None:
    m = model_for(gpio_byte)
    assert m.mean_uA == pytest.approx(mean_mA * 1000.0)
    assert m.sigma_uA == pytest.approx(sigma_mA * 1000.0)


def test_model_for_returns_currentmodel_type() -> None:
    assert isinstance(model_for(0), CurrentModel)


def test_currentmodel_is_frozen() -> None:
    m = model_for(0)
    with pytest.raises(Exception):
        m.mean_uA = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stop mode
# ---------------------------------------------------------------------------

def test_stop_mode_with_idle_gpio_returns_quiescent_current() -> None:
    m = model_for(0, stop_mode=True)
    assert m.mean_uA == STOP_MODE_MEAN_UA
    assert m.sigma_uA == STOP_MODE_SIGMA_UA


def test_stop_mode_with_active_gpio_falls_back_to_phase_table() -> None:
    """A non-zero trigger means CPU is awake; stop-mode override should NOT apply."""
    for gpio_byte, mean_mA, sigma_mA in SPEC_TABLE[1:]:  # skip 0b000
        m = model_for(gpio_byte, stop_mode=True)
        assert m.mean_uA == pytest.approx(mean_mA * 1000.0)
        assert m.sigma_uA == pytest.approx(sigma_mA * 1000.0)


def test_stop_mode_off_with_idle_gpio_uses_phase_table() -> None:
    m = model_for(0, stop_mode=False)
    assert m.mean_uA == 50_000.0
    assert m.sigma_uA == 1_000.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_model_for_rejects_reserved_bits() -> None:
    with pytest.raises(ValueError, match="reserved"):
        model_for(0b00001000)
    with pytest.raises(ValueError, match="reserved"):
        model_for(RESERVED_MASK)


def test_model_for_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        model_for(256)
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        model_for(-1)


# ---------------------------------------------------------------------------
# sample_current — Gaussian-noised draws
# ---------------------------------------------------------------------------

def test_sample_current_returns_float() -> None:
    rng = random.Random(42)
    val = sample_current(0, rng=rng)
    assert isinstance(val, float)


def test_sample_current_is_deterministic_with_seeded_rng() -> None:
    rng_a = random.Random(0xCAFE)
    rng_b = random.Random(0xCAFE)
    seq_a = [sample_current(0b001, rng=rng_a) for _ in range(100)]
    seq_b = [sample_current(0b001, rng=rng_b) for _ in range(100)]
    assert seq_a == seq_b


@pytest.mark.parametrize("gpio_byte,mean_mA,sigma_mA", SPEC_TABLE)
def test_sample_current_empirical_stats_match_table(
    gpio_byte: int, mean_mA: float, sigma_mA: float
) -> None:
    """10k samples should match the table mean/σ within tight tolerance.

    Tolerances:
    - Mean: 4×SEM (= 4σ/√n). At n=10k that's 0.04σ. False-positive rate ~6e-5.
    - Stdev: 5% relative. The 1σ uncertainty of sample stdev for n=10k is ~0.7%,
      so 5% is ~7σ — flake-proof.
    """
    rng = random.Random(0xC0FFEE + gpio_byte)
    n = 10_000
    samples = [sample_current(gpio_byte, rng=rng) for _ in range(n)]
    emp_mean = statistics.fmean(samples)
    emp_sigma = statistics.stdev(samples)
    expected_mean = mean_mA * 1000.0
    expected_sigma = sigma_mA * 1000.0

    sem = expected_sigma / math.sqrt(n)
    assert abs(emp_mean - expected_mean) < 4 * sem, (
        f"gpio={gpio_byte:#05b}: emp_mean={emp_mean:.2f} expected={expected_mean:.2f}"
    )
    rel_err = abs(emp_sigma - expected_sigma) / expected_sigma
    assert rel_err < 0.05, (
        f"gpio={gpio_byte:#05b}: emp_sigma={emp_sigma:.2f} "
        f"expected={expected_sigma:.2f} rel_err={rel_err:.3f}"
    )


def test_sample_current_stop_mode_empirical_stats() -> None:
    rng = random.Random(99)
    n = 10_000
    samples = [sample_current(0, stop_mode=True, rng=rng) for _ in range(n)]
    emp_mean = statistics.fmean(samples)
    emp_sigma = statistics.stdev(samples)
    sem = STOP_MODE_SIGMA_UA / math.sqrt(n)
    assert abs(emp_mean - STOP_MODE_MEAN_UA) < 4 * sem
    assert abs(emp_sigma - STOP_MODE_SIGMA_UA) / STOP_MODE_SIGMA_UA < 0.05


def test_sample_current_validates_gpio_byte() -> None:
    rng = random.Random(0)
    with pytest.raises(ValueError):
        sample_current(0b1000, rng=rng)
    with pytest.raises(ValueError):
        sample_current(-1, rng=rng)


# ---------------------------------------------------------------------------
# Wake-up burst constants (spec §7.3 — temporal logic lives in the server)
# ---------------------------------------------------------------------------

def test_wakeup_burst_peak_matches_spec() -> None:
    # spec §7.3: 80 mA peak during the wake-up latency window
    assert WAKEUP_BURST_PEAK_UA == 80_000.0


def test_wakeup_burst_duration_matches_spec() -> None:
    # spec §7.3: ~13 µs wake-up latency
    assert WAKEUP_BURST_DURATION_US == 13
