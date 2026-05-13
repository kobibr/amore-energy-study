"""Unit tests for sample_assembler.assemble_samples."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csv_format import Sample  # noqa: E402
from sample_assembler import assemble_samples  # noqa: E402


def test_empty_input_yields_nothing() -> None:
    out = list(assemble_samples([], voltage_mV=3300))
    assert out == []


def test_voltage_conversion_mv_to_V() -> None:
    """voltage_mV=3300 → voltage_V=3.3 in every emitted sample."""
    rng = random.Random(0)
    out = list(
        assemble_samples([(0, 0), (10, 0)], voltage_mV=3300, rng=rng)
    )
    assert all(s.voltage_V == 3.3 for s in out)


def test_voltage_3000_mv_for_subsweep() -> None:
    """PRD §5.4.2 — the v3.0 voltage sub-sweep at 3.0 V."""
    rng = random.Random(0)
    out = list(assemble_samples([(0, 0)], voltage_mV=3000, rng=rng))
    assert out[0].voltage_V == 3.0


def test_yields_sample_objects() -> None:
    rng = random.Random(0)
    out = list(assemble_samples([(0, 0)], voltage_mV=3300, rng=rng))
    assert len(out) == 1
    assert isinstance(out[0], Sample)


def test_timestamp_passes_through() -> None:
    rng = random.Random(0)
    inputs = [(0, 0), (10, 1), (20, 0), (1_234_567, 4)]
    out = list(assemble_samples(inputs, voltage_mV=3300, rng=rng))
    assert [s.timestamp_us for s in out] == [t for t, _ in inputs]


def test_gpio_byte_passes_through() -> None:
    rng = random.Random(0)
    inputs = [(0, 0), (10, 1), (20, 2), (30, 4), (40, 7)]
    out = list(assemble_samples(inputs, voltage_mV=3300, rng=rng))
    assert [s.gpio_byte for s in out] == [b for _, b in inputs]


def test_current_in_idle_range() -> None:
    """gpio_byte=0 → ~50 mA (50_000 µA) per spec §7.3."""
    rng = random.Random(0)
    out = list(
        assemble_samples([(t, 0) for t in range(0, 10000, 10)],
                          voltage_mV=3300, rng=rng)
    )
    mean = sum(s.current_uA for s in out) / len(out)
    # 1000 samples, σ=1000 µA → SE ≈ 32 µA. 4·SE = 130 µA.
    assert abs(mean - 50_000.0) < 200


def test_current_in_active_range() -> None:
    """gpio_byte=1 → ~85 mA (85_000 µA) per spec §7.3."""
    rng = random.Random(0)
    out = list(
        assemble_samples([(t, 1) for t in range(0, 10000, 10)],
                          voltage_mV=3300, rng=rng)
    )
    mean = sum(s.current_uA for s in out) / len(out)
    assert abs(mean - 85_000.0) < 300


def test_stop_mode_quiescent_at_idle() -> None:
    """stop_mode=True + gpio_byte=0 → ~0.5 µA, not 50 mA."""
    rng = random.Random(0)
    out = list(
        assemble_samples(
            [(t, 0) for t in range(0, 10000, 10)],
            voltage_mV=3300, stop_mode=True, rng=rng,
        )
    )
    mean = sum(s.current_uA for s in out) / len(out)
    # Stop mode mean is 0.5 µA; sample mean within a few σ of that
    assert mean < 1.0


def test_stop_mode_ignored_when_active() -> None:
    """stop_mode=True with gpio_byte!=0 falls back to active synthesis."""
    rng = random.Random(0)
    out = list(
        assemble_samples([(0, 1)], voltage_mV=3300, stop_mode=True, rng=rng)
    )
    # Should be ~85 mA, NOT ~0.5 µA
    assert out[0].current_uA > 50_000.0


def test_current_clamped_at_zero() -> None:
    """Gaussian samples clipped to >=0 — no negative currents in output."""
    rng = random.Random(0)
    # Stop mode is mean=0.5 sigma=0.1 → 5σ from 0; rarely produces negatives,
    # but we verify the clamp anyway across many samples.
    out = list(
        assemble_samples(
            [(t, 0) for t in range(0, 100000, 10)],
            voltage_mV=3300, stop_mode=True, rng=rng,
        )
    )
    assert all(s.current_uA >= 0.0 for s in out)


def test_rejects_negative_voltage() -> None:
    with pytest.raises(ValueError, match="voltage_mV"):
        list(assemble_samples([(0, 0)], voltage_mV=-1))


def test_invalid_gpio_byte_propagates() -> None:
    """An out-of-range gpio_byte must fail — propagated from current_synthesis."""
    rng = random.Random(0)
    with pytest.raises(ValueError):
        # gpio_byte=8 → reserved bit set
        list(assemble_samples([(0, 8)], voltage_mV=3300, rng=rng))


def test_deterministic_with_seeded_rng() -> None:
    """Same seed → byte-equal output."""
    inputs = [(t, 1) for t in range(0, 1000, 10)]
    a = list(assemble_samples(inputs, voltage_mV=3300, rng=random.Random(42)))
    b = list(assemble_samples(inputs, voltage_mV=3300, rng=random.Random(42)))
    assert a == b


def test_lazy_evaluation() -> None:
    """assemble_samples must be a generator — never materializes all input."""
    def infinite_idle():
        t = 0
        while True:
            yield (t, 0)
            t += 10

    out_iter = assemble_samples(infinite_idle(), voltage_mV=3300,
                                  rng=random.Random(0))
    # Take 100 samples without exhausting the infinite source
    first_100 = []
    for _ in range(100):
        first_100.append(next(out_iter))
    assert len(first_100) == 100
