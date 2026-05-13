"""Tests for scenario_format.parse_scenario."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenario_format import parse_scenario  # noqa: E402


def _parse(text: str) -> list[tuple[int, int]]:
    return list(parse_scenario(io.StringIO(text)))


def test_empty_input_yields_nothing() -> None:
    assert _parse("") == []


def test_single_event() -> None:
    assert _parse("100 1\n") == [(100_000, 1)]


def test_cumulative_timestamps() -> None:
    """Each delay adds to the running total — that's the contract."""
    text = "100  1\n380  0\n50  2\n100  0\n"
    assert _parse(text) == [
        (100_000, 1),
        (480_000, 0),
        (530_000, 2),
        (630_000, 0),
    ]


def test_sub_millisecond_delays_round_to_microseconds() -> None:
    """0.7 ms → 700 µs."""
    assert _parse("0.7  4\n0.7  0\n") == [(700, 4), (1400, 0)]


def test_zero_delay_event() -> None:
    """Delay of 0 → simultaneous transition; valid (e.g. illegal-state probes)."""
    assert _parse("100 1\n0 3\n") == [(100_000, 1), (100_000, 3)]


def test_blank_lines_skipped() -> None:
    text = "\n\n100 1\n\n100 0\n\n"
    assert _parse(text) == [(100_000, 1), (200_000, 0)]


def test_full_line_comments_skipped() -> None:
    text = "# header\n100 1\n# inline note\n100 0\n"
    assert _parse(text) == [(100_000, 1), (200_000, 0)]


def test_indented_comments_skipped() -> None:
    text = "  # indented\n100 1\n"
    assert _parse(text) == [(100_000, 1)]


def test_trailing_inline_comments_stripped() -> None:
    text = "100 5  # comment with extra # hashes\n"
    assert _parse(text) == [(100_000, 5)]


def test_bad_token_count_warned_and_skipped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = "100 1\nONLY_ONE\n1 2 3 4\n100 0\n"
    assert _parse(text) == [(100_000, 1), (200_000, 0)]
    err = capsys.readouterr().err
    assert err.count("WARN:") == 2
    assert "ONLY_ONE" in err
    assert "1 2 3 4" in err


def test_parse_error_warned_and_skipped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = "abc xyz\n100 1\n"
    assert _parse(text) == [(100_000, 1)]
    assert "WARN:" in capsys.readouterr().err


def test_gpio_byte_out_of_range_warned_and_skipped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rejected lines drop their delay too — consistent with gpio_logger.py.

    A rejected line is treated as if it weren't there, so its delay does
    NOT accumulate into ``t_us``. This keeps the parser's behavior simple
    and matches how ``gpio_logger.py --mode fake-stdin`` works.
    """
    text = "100 1\n100 300\n100 -1\n100 0\n"
    assert _parse(text) == [(100_000, 1), (200_000, 0)]
    err = capsys.readouterr().err
    assert err.count("WARN:") == 2
    assert "300" in err
    assert "-1" in err


def test_all_eight_valid_gpio_bytes() -> None:
    """Spec §7.3 — all 0..7 must be accepted."""
    text = "".join(f"1 {b}\n" for b in range(8))
    expected = [(1000 * (i + 1), b) for i, b in enumerate(range(8))]
    assert _parse(text) == expected
