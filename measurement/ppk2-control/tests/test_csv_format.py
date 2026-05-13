"""Round-trip and edge-case tests for ``csv_format``.

Per spec §10.1: 'round-trip a CSV through writer + reader; assert byte-equal.'
This module also covers the rejection of malformed rows / illegal bits and
the exact byte form of the spec's example row (§5.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the parent directory importable when pytest is invoked from the
# project root. Avoids needing an installed package for early iterations.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csv_format import (  # noqa: E402  — sys.path tweak above
    CSV_HEADER,
    PA0_BIT,
    PA1_BIT,
    PA4_BIT,
    Sample,
    format_row,
    parse_row,
    read_samples,
    write_samples,
)


# ---------------------------------------------------------------------------
# Header / bit constants
# ---------------------------------------------------------------------------

def test_header_constant_matches_spec_section_5_1() -> None:
    assert CSV_HEADER == "timestamp_us,current_uA,voltage_V,gpio_byte"


def test_gpio_bit_positions_match_spec_section_5_1() -> None:
    assert PA0_BIT == 0b001
    assert PA1_BIT == 0b010
    assert PA4_BIT == 0b100


# ---------------------------------------------------------------------------
# Row formatting
# ---------------------------------------------------------------------------

def test_format_row_matches_spec_section_5_2_example() -> None:
    # spec §5.2: at t = 12.345 ms, I = 52.341 mA, V = 3.300 V, PA0 high
    row = format_row(
        timestamp_us=12345,
        current_uA=52341.230,
        voltage_V=3.300,
        gpio_byte=PA0_BIT,
    )
    assert row == "12345,52341.230,3.300,1"


def test_format_row_three_decimal_places_for_integer_values() -> None:
    assert format_row(0, 50.0, 3.3, 0) == "0,50.000,3.300,0"
    assert format_row(0, 0.0, 3.3, 0) == "0,0.000,3.300,0"


def test_format_row_rounds_to_three_decimals() -> None:
    assert format_row(0, 1.23456, 3.3, 0) == "0,1.235,3.300,0"
    # Banker's rounding: 0.0005 → 0.000 in IEEE round-half-to-even
    # (we don't depend on this; just don't crash and stay 3 dp)
    out = format_row(0, 0.0005, 3.3, 0)
    assert out.startswith("0,0.0") and out.count(".") == 2


# ---------------------------------------------------------------------------
# parse_row inverts to_csv_row
# ---------------------------------------------------------------------------

def test_parse_row_inverts_to_csv_row() -> None:
    s = Sample(
        timestamp_us=999,
        current_uA=85.123,
        voltage_V=3.300,
        gpio_byte=PA0_BIT,
    )
    assert parse_row(s.to_csv_row()) == s


def test_parse_row_rejects_wrong_field_count() -> None:
    with pytest.raises(ValueError, match="expected 4 fields"):
        parse_row("1,2,3")
    with pytest.raises(ValueError, match="expected 4 fields"):
        parse_row("1,2,3,4,5")


# ---------------------------------------------------------------------------
# Sample validation
# ---------------------------------------------------------------------------

def test_sample_rejects_reserved_bits() -> None:
    # spec §5.1: bits 3..7 must be 0
    with pytest.raises(ValueError, match="reserved"):
        Sample(timestamp_us=0, current_uA=0.0, voltage_V=3.3, gpio_byte=0b00001000)
    with pytest.raises(ValueError, match="reserved"):
        Sample(timestamp_us=0, current_uA=0.0, voltage_V=3.3, gpio_byte=0xFF)


def test_sample_accepts_all_valid_phase_combinations() -> None:
    # spec §7.3 enumerates 8 valid gpio_byte values (0..7)
    for b in range(8):
        Sample(timestamp_us=0, current_uA=50.0, voltage_V=3.3, gpio_byte=b)


def test_sample_rejects_out_of_range_gpio() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        Sample(timestamp_us=0, current_uA=0.0, voltage_V=3.3, gpio_byte=256)
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        Sample(timestamp_us=0, current_uA=0.0, voltage_V=3.3, gpio_byte=-1)


def test_sample_rejects_negative_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp_us"):
        Sample(timestamp_us=-1, current_uA=0.0, voltage_V=3.3, gpio_byte=0)


# ---------------------------------------------------------------------------
# File round-trip — the §10.1 acceptance test
# ---------------------------------------------------------------------------

def test_roundtrip_byte_equal(tmp_path: Path) -> None:
    """Spec §10.1: write-then-read must be byte-equal."""
    samples = [
        Sample(0, 50.000, 3.300, 0),                 # idle
        Sample(10, 85.000, 3.300, PA0_BIT),          # Setup active
        Sample(20, 88.000, 3.300, PA4_BIT),          # UART
        Sample(30, 55.000, 3.300, PA1_BIT),          # ServerWait
        Sample(40, 50.000, 3.300, 0),                # back to idle
    ]
    path = tmp_path / "trace.csv"
    n = write_samples(path, samples)
    assert n == 5

    decoded = list(read_samples(path))
    assert decoded == samples

    expected = (
        "timestamp_us,current_uA,voltage_V,gpio_byte\n"
        "0,50.000,3.300,0\n"
        "10,85.000,3.300,1\n"
        "20,88.000,3.300,4\n"
        "30,55.000,3.300,2\n"
        "40,50.000,3.300,0\n"
    )
    assert path.read_text(encoding="utf-8") == expected


def test_write_creates_missing_parents(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "trace.csv"
    write_samples(deep, [Sample(0, 50.0, 3.3, 0)])
    assert deep.exists()


def test_read_rejects_wrong_header(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("wrong,header,here,now\n0,1.0,3.3,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad header"):
        list(read_samples(p))


def test_read_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "trace.csv"
    p.write_text(
        CSV_HEADER + "\n\n0,50.000,3.300,0\n\n5,85.000,3.300,1\n",
        encoding="utf-8",
    )
    rows = list(read_samples(p))
    assert len(rows) == 2
    assert rows[0].gpio_byte == 0
    assert rows[1].gpio_byte == 1


def test_read_reports_line_number_on_bad_row(tmp_path: Path) -> None:
    p = tmp_path / "trace.csv"
    p.write_text(
        CSV_HEADER + "\n"
        "0,50.000,3.300,0\n"
        "10,85.000,3.300,8\n",   # gpio=8 → reserved bit set, line 3
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 3"):
        list(read_samples(p))


def test_empty_trace_writes_header_only(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    n = write_samples(p, [])
    assert n == 0
    assert p.read_text(encoding="utf-8") == CSV_HEADER + "\n"
    assert list(read_samples(p)) == []
