"""Canonical CSV format for AmorE energy traces.

Used by both ``mock_ppk2_server.py`` (writer) and the analysis pipeline
(reader). The format must match what the real PPK2 produces so downstream
code is agnostic to the data source.

See ``docs/MOCK_PPK2_SPEC.md`` §5 for the full canonical-format contract.

Header
------
``timestamp_us,current_uA,voltage_V,gpio_byte``

Per-column semantics
--------------------
- ``timestamp_us`` — int, monotonic microseconds since ``start_measuring``.
- ``current_uA``  — float, three decimal places, microamps.
- ``voltage_V``   — float, three decimal places, volts.
- ``gpio_byte``   — uint8 (0..255). bit 0 = PA0, bit 1 = PA1, bit 2 = PA4.
                    bits 3..7 reserved (must be 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

CSV_HEADER: str = "timestamp_us,current_uA,voltage_V,gpio_byte"
"""The exact header line, without trailing newline."""

# GPIO bit positions inside ``gpio_byte``. These map STM32 pins to bits.
# (See spec §2.2 for the STM32→Pi pin assignment.)
PA0_BIT: int = 0x01  # bit 0 — Setup / Blind / Verify trigger
PA1_BIT: int = 0x02  # bit 1 — ServerWait trigger
PA4_BIT: int = 0x04  # bit 2 — Mode C UART-isolation trigger

GPIO_BYTE_MAX: int = 0xFF
RESERVED_MASK: int = 0xF8  # bits 3..7 must be 0


# ---------------------------------------------------------------------------
# Sample type
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Sample:
    """One row of the canonical CSV.

    Frozen so the writer can rely on rows being immutable while a chunk is
    in flight on the wire (the JSON streamer in ``mock_ppk2_server.py``
    builds chunks of these from the GPIO ring buffer).
    """

    timestamp_us: int
    current_uA: float
    voltage_V: float
    gpio_byte: int

    def __post_init__(self) -> None:
        if self.timestamp_us < 0:
            raise ValueError(
                f"timestamp_us must be >=0, got {self.timestamp_us}"
            )
        if not 0 <= self.gpio_byte <= GPIO_BYTE_MAX:
            raise ValueError(
                f"gpio_byte must be in [0, 255], got {self.gpio_byte}"
            )
        if self.gpio_byte & RESERVED_MASK:
            raise ValueError(
                "reserved bits 3..7 must be 0, got "
                f"gpio_byte=0x{self.gpio_byte:02x}"
            )

    def to_csv_row(self) -> str:
        """Format as a single CSV row (no trailing newline)."""
        return (
            f"{self.timestamp_us},"
            f"{self.current_uA:.3f},"
            f"{self.voltage_V:.3f},"
            f"{self.gpio_byte}"
        )


# ---------------------------------------------------------------------------
# Row-level helpers
# ---------------------------------------------------------------------------

def format_row(
    timestamp_us: int,
    current_uA: float,
    voltage_V: float,
    gpio_byte: int,
) -> str:
    """Format a row without going through Sample construction.

    Convenience for hot-path writers; performs the same validation.
    """
    return Sample(timestamp_us, current_uA, voltage_V, gpio_byte).to_csv_row()


def parse_row(line: str) -> Sample:
    """Parse one CSV row (no trailing newline) into a Sample.

    Raises ValueError on any parse or range error.
    """
    parts = line.split(",")
    if len(parts) != 4:
        raise ValueError(
            f"expected 4 fields, got {len(parts)}: {line!r}"
        )
    return Sample(
        timestamp_us=int(parts[0]),
        current_uA=float(parts[1]),
        voltage_V=float(parts[2]),
        gpio_byte=int(parts[3]),
    )


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------

def write_samples(path: str | Path, samples: Iterable[Sample]) -> int:
    """Write a header + every sample to ``path``. Returns row count.

    Creates parent directories as needed. Overwrites any existing file
    (the orchestration layer owns naming; here we just write what we
    are told).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("w", encoding="utf-8") as f:
        f.write(CSV_HEADER + "\n")
        for s in samples:
            f.write(s.to_csv_row() + "\n")
            count += 1
    return count


def read_samples(path: str | Path) -> Iterator[Sample]:
    """Stream Samples from ``path``. Validates header on entry.

    Streams (yields) rather than returning a list — analysis-pipeline
    traces can be tens of millions of rows.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        header = f.readline().rstrip("\n")
        if header != CSV_HEADER:
            raise ValueError(
                f"bad header: expected {CSV_HEADER!r}, got {header!r}"
            )
        for lineno, line in enumerate(f, start=2):
            line = line.rstrip("\n")
            if not line:
                # Blank lines are tolerated but don't yield a sample.
                continue
            try:
                yield parse_row(line)
            except ValueError as e:
                raise ValueError(f"line {lineno}: {e}") from e
