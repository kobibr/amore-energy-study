"""Tests for interpolator.interpolate_to_fixed_rate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interpolator import (  # noqa: E402
    DEFAULT_SAMPLE_PERIOD_US,
    interpolate_to_fixed_rate,
)


def _interpolate(*args, **kwargs) -> list[tuple[int, int]]:
    return list(interpolate_to_fixed_rate(*args, **kwargs))


# ---------------------------------------------------------------------------
# Sample-rate / count semantics
# ---------------------------------------------------------------------------

def test_default_sample_period_is_100ksps() -> None:
    """100 ksps == 10 µs period (spec §5.3)."""
    assert DEFAULT_SAMPLE_PERIOD_US == 10


def test_end_time_zero_yields_no_samples() -> None:
    assert _interpolate([], end_time_us=0) == []


def test_count_for_round_end_time() -> None:
    """end=1000, period=10 → samples at 0, 10, ..., 990 = 100 samples."""
    samples = _interpolate([], end_time_us=1000)
    assert len(samples) == 100
    assert samples[0] == (0, 0)
    assert samples[-1] == (990, 0)


def test_count_for_unaligned_end_time() -> None:
    """end=1005, period=10 → samples at 0, 10, ..., 1000 = 101 samples."""
    samples = _interpolate([], end_time_us=1005)
    assert len(samples) == 101
    assert samples[-1] == (1000, 0)


# ---------------------------------------------------------------------------
# No events → constant initial state
# ---------------------------------------------------------------------------

def test_no_events_yields_initial_byte() -> None:
    samples = _interpolate([], end_time_us=50)
    assert samples == [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)]


def test_no_events_with_nonzero_initial() -> None:
    samples = _interpolate([], end_time_us=30, initial_gpio_byte=5)
    assert samples == [(0, 5), (10, 5), (20, 5)]


# ---------------------------------------------------------------------------
# Single event boundary semantics
# ---------------------------------------------------------------------------

def test_event_at_t_zero_takes_effect_immediately() -> None:
    samples = _interpolate([(0, 1)], end_time_us=30)
    assert samples == [(0, 1), (10, 1), (20, 1)]


def test_event_at_sample_boundary_applies_to_that_sample() -> None:
    """Event at t=20 is reflected in the sample at t=20 (not 30).

    Per spec §5.3 / interpolator.py boundary contract: 'an event at T
    fires at or before the sample at T'.
    """
    samples = _interpolate([(20, 1)], end_time_us=50)
    assert samples == [(0, 0), (10, 0), (20, 1), (30, 1), (40, 1)]


def test_event_between_samples_takes_effect_at_next_sample() -> None:
    """Event at t=15 is between samples at 10 and 20 → first visible at 20."""
    samples = _interpolate([(15, 1)], end_time_us=40)
    assert samples == [(0, 0), (10, 0), (20, 1), (30, 1)]


# ---------------------------------------------------------------------------
# Multi-event scenarios
# ---------------------------------------------------------------------------

def test_mode_a_round_fragment() -> None:
    """A trimmed Mode-A-like sequence: PA0 high 380ms, then idle.

    With samples at 100ksps and times in µs, we don't generate the
    full trace here (would be 38000 samples) — just the first and
    last few to verify boundary handling.
    """
    events = [(100_000, 1), (480_000, 0)]
    # End at 500 µs → only see the initial idle period, no events fired.
    samples = _interpolate(events, end_time_us=500)
    assert all(gb == 0 for _, gb in samples)
    assert len(samples) == 50  # 500 / 10

    # End at 100_010 → see the transition at sample t=100_000.
    samples = _interpolate(events, end_time_us=100_020)
    assert samples[-2] == (100_000, 1)
    assert samples[-1] == (100_010, 1)


def test_uart_pulse_700us() -> None:
    """A 700 µs PA4 pulse should produce 70 high samples."""
    events = [(0, 4), (700, 0)]
    samples = _interpolate(events, end_time_us=1000)
    high_count = sum(1 for _, gb in samples if gb == 4)
    low_count = sum(1 for _, gb in samples if gb == 0)
    # 0..690 inclusive = 70 samples; 700..990 inclusive = 30 samples
    assert high_count == 70
    assert low_count == 30


def test_simultaneous_events_last_wins() -> None:
    """Multiple events at the same timestamp → last value retained."""
    events = [(50, 1), (50, 2), (50, 4)]
    samples = _interpolate(events, end_time_us=100)
    # Samples after t=50 should all be 4 (the last event at that ts).
    after_50 = [gb for ts, gb in samples if ts >= 50]
    assert all(gb == 4 for gb in after_50)
    assert len(after_50) == 5  # ts 50, 60, 70, 80, 90


def test_short_pulse_gets_aliased() -> None:
    """Pulse from t=5 to t=8 falls between samples; both edges missed.

    Documented behavior — matches real PPK2 aliasing of sub-period pulses.
    """
    events = [(5, 1), (8, 0)]
    samples = _interpolate(events, end_time_us=20)
    # Sample at t=0 sees 0 (no events yet).
    # Sample at t=10 sees 0 (events at 5 and 8 both applied; last is 0).
    assert samples == [(0, 0), (10, 0)]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_rejects_negative_sample_period() -> None:
    with pytest.raises(ValueError, match="sample_period_us"):
        _interpolate([], end_time_us=10, sample_period_us=0)
    with pytest.raises(ValueError, match="sample_period_us"):
        _interpolate([], end_time_us=10, sample_period_us=-1)


def test_rejects_negative_end_time() -> None:
    with pytest.raises(ValueError, match="end_time_us"):
        _interpolate([], end_time_us=-1)


def test_rejects_out_of_range_initial_gpio_byte() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        _interpolate([], end_time_us=10, initial_gpio_byte=256)


def test_rejects_out_of_order_events() -> None:
    """Event sequence with a backwards timestamp must fail loudly."""
    with pytest.raises(ValueError, match="out of order"):
        _interpolate([(100, 1), (50, 0)], end_time_us=200)


def test_rejects_event_with_invalid_gpio_byte() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        _interpolate([(50, 256)], end_time_us=100)


# ---------------------------------------------------------------------------
# Custom sample period
# ---------------------------------------------------------------------------

def test_custom_sample_period_50us() -> None:
    """20 ksps (50 µs period) — verifies period parameter actually works."""
    events = [(100, 1)]
    samples = _interpolate(events, end_time_us=300, sample_period_us=50)
    assert samples == [(0, 0), (50, 0), (100, 1), (150, 1), (200, 1), (250, 1)]
