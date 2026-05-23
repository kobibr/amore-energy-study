"""Tests for measurement/backends.py — the Backend Protocol and
MockBackend implementation.

Covers three concerns:
  1. The Backend Protocol contract advertises measure_replica
     (test_backend_protocol_has_measure_replica).
  2. MockBackend constructs and runs an end-to-end 5-second smoke
     capture against the mock server (test_mock_backend_*).
  3. PPK2Backend constructs with the documented defaults and exposes
     measure_replica as a callable attribute
     (test_ppk2_backend_constructs_and_has_real_method).

The PPK2Backend test does NOT invoke measure_replica — that requires
real PPK2 hardware and is exercised by scripts/smoke_ppk2.sh. The
callable-attribute check is intentionally weak: it only proves the
method exists, not that the implementation works. Don't treat a green
result on this file as evidence that hardware integration is OK.
"""
import sys
from pathlib import Path

import pytest

# Add the measurement package to the path so `import backends` works
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "measurement"))

from backends import Backend, MockBackend, PPK2Backend, MeasurementResult  # noqa: E402


def test_backend_protocol_has_measure_replica():
    """The Protocol contract advertises a measure_replica method.

    Bug #4 fix: previously this assertion used `... or hasattr(Backend,
    '__protocol_attrs__')`, which is always true for typing.Protocol
    classes on Python 3.12+ — meaning the test would pass even if
    measure_replica were removed from the Backend protocol. Tightened
    to a direct hasattr check.
    """
    assert hasattr(Backend, "measure_replica"), (
        "Backend protocol must advertise measure_replica"
    )


def test_mock_backend_implements_protocol():
    """MockBackend should be usable wherever Backend is expected."""
    mb = MockBackend()
    assert callable(mb.measure_replica)


def test_ppk2_backend_constructs_and_has_real_method():
    """PPK2Backend constructs with documented defaults, and
    measure_replica exists as a callable attribute on the instance.

    Bug #5 fix: docstring no longer claims this verifies measure_replica
    "is a real method (not raising NotImplementedError)" — `callable()`
    only inspects the attribute, not its behaviour. To actually verify
    the method runs end-to-end, see scripts/smoke_ppk2.sh (real hardware).

    Bug #1 fix: the local `from measurement.backends import PPK2Backend`
    that was previously here was redundant (PPK2Backend is already imported
    at module top via the sys.path injection) and fragile (the qualified
    name requires ROOT on sys.path, which is not what the file sets up).
    Removed.
    """
    backend = PPK2Backend()
    assert backend.serial_port == "/dev/ttyACM1"
    assert backend.voltage_mV == 3300
    assert backend.settle_s == 0.5

    backend2 = PPK2Backend(serial_port="/dev/ttyACM2", voltage_mV=3000, settle_s=1.0)
    assert backend2.serial_port == "/dev/ttyACM2"
    assert backend2.voltage_mV == 3000
    assert backend2.settle_s == 1.0

    # Attribute lookup only — this does NOT prove the method runs.
    assert callable(backend.measure_replica)

def test_measurement_result_is_a_dataclass():
    """MeasurementResult is structured data, not a tuple."""
    r = MeasurementResult(
        ok=True,
        csv_path=Path("/tmp/x.csv"),
        sample_count=125_000,
        duration_actual_s=5.2,
    )
    assert r.ok is True
    assert r.sample_count == 125_000
    assert r.error_message == ""  # default


def test_mock_backend_smoke_5s(tmp_path):
    """Real end-to-end: spin up the mock server, capture 5s of idle, verify."""
    mb = MockBackend()
    csv_out = tmp_path / "smoke.csv"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    result = mb.measure_replica(
        csv_out=csv_out,
        duration_s=5.0,
        gpio_source="fake-script:idle",
        log_dir=log_dir,
    )

    assert result.ok, f"smoke failed: {result.error_message}"
    assert result.csv_path == csv_out
    assert result.csv_path.is_file()
    # 25 kHz × 5 s = 125000 samples, allow some jitter
    assert 100_000 <= result.sample_count <= 150_000
    assert 4.5 <= result.duration_actual_s <= 8.0
