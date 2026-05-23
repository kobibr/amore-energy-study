"""Unit tests for parse_traces.py."""
import csv
import tempfile
from pathlib import Path

import pytest

from analysis.parse_traces import parse_trace, Phase


def _write_csv(rows):
    """rows: list of (ts_us, current_uA, voltage_V, gpio_byte) tuples"""
    fp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    w = csv.writer(fp)
    w.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])
    for r in rows:
        w.writerow(r)
    fp.close()
    return Path(fp.name)


def test_empty_returns_empty_list():
    p = _write_csv([])
    assert parse_trace(p) == []


def test_single_phase():
    rows = [
        (0,    50000.0, 3.3, 0),
        (40,   51000.0, 3.3, 0),
        (80,   49500.0, 3.3, 0),
    ]
    phases = parse_trace(_write_csv(rows))
    assert len(phases) == 1
    p = phases[0]
    assert p.gpio_byte == 0
    assert p.samples == 3
    assert p.start_us == 0
    assert p.end_us == 120         # last_ts + period
    assert p.duration_us == 120
    assert p.mean_current_uA == pytest.approx((50000+51000+49500)/3)
    assert p.mean_voltage_V == pytest.approx(3.3)


def test_two_transitions_create_three_phases():
    """5 samples with pattern 0,0,1,1,0 → 3 segments.

    Bug #6 fix: previously named `test_transition_creates_two_phases`
    which misleads — the input has TWO transitions, producing THREE
    phases. Renamed to match what the assertion actually verifies.
    """
    rows = [
        (0,   50000.0, 3.3, 0),    # idle
        (40,  50000.0, 3.3, 0),
        (80,  85000.0, 3.3, 1),    # Setup
        (120, 85000.0, 3.3, 1),
        (160, 50000.0, 3.3, 0),    # idle again
    ]
    phases = parse_trace(_write_csv(rows))
    assert len(phases) == 3
    assert phases[0].gpio_byte == 0 and phases[0].samples == 2
    assert phases[1].gpio_byte == 1 and phases[1].samples == 2
    assert phases[2].gpio_byte == 0 and phases[2].samples == 1
    assert phases[1].start_us == 80
    assert phases[1].end_us == 160  # = phases[2].start_us
    assert phases[1].duration_us == 80


def test_phase_durations_contiguous():
    """Sum of phase durations should equal total trace span (last_ts - first_ts + period)."""
    rows = [
        (0,   50000.0, 3.3, 0),
        (40,  85000.0, 3.3, 1),
        (80,  55000.0, 3.3, 2),
        (120, 50000.0, 3.3, 0),
    ]
    phases = parse_trace(_write_csv(rows))
    total = sum(p.duration_us for p in phases)
    expected = (120 - 0) + 40  # period
    assert total == expected


def test_mean_current_per_phase():
    rows = [
        (0,    50000.0, 3.3, 0),
        (40,   60000.0, 3.3, 0),
        (80,   85000.0, 3.3, 1),
        (120,  87000.0, 3.3, 1),
    ]
    phases = parse_trace(_write_csv(rows))
    assert phases[0].mean_current_uA == pytest.approx(55000.0)
    assert phases[1].mean_current_uA == pytest.approx(86000.0)
