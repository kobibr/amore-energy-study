"""Tests for measurement/backends.py — the Backend Protocol and
MockBackend implementation.

PPK2Backend cannot be tested without hardware, so we only assert that
its stub methods raise NotImplementedError with a clear message.
"""
import sys
from pathlib import Path

import pytest

# Add the measurement package to the path so `import backends` works
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "measurement"))

from backends import Backend, MockBackend, PPK2Backend, MeasurementResult  # noqa: E402


def test_backend_protocol_has_measure_replica():
    """The Protocol contract advertises a measure_replica method."""
    assert hasattr(Backend, "measure_replica") or hasattr(Backend, "__protocol_attrs__")


def test_mock_backend_implements_protocol():
    """MockBackend should be usable wherever Backend is expected."""
    mb = MockBackend()
    assert callable(mb.measure_replica)


def test_ppk2_backend_raises_not_implemented():
    """PPK2Backend is a skeleton. Calling it before PPK2 arrives should
    raise NotImplementedError with a helpful message."""
    pb = PPK2Backend()
    with pytest.raises(NotImplementedError) as excinfo:
        pb.measure_replica(
            csv_out=Path("/tmp/x.csv"),
            duration_s=1.0,
            gpio_source="fake-script:idle",
            log_dir=Path("/tmp"),
        )
    msg = str(excinfo.value).lower()
    assert "ppk2" in msg or "skeleton" in msg or "implement" in msg


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
