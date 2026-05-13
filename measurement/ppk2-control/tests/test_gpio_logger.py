"""Tests for gpio_logger.py — fake-stdin mode only.

Real-GPIO mode tests run on the Pi in iter 5; here we only validate the
data path (parse delays → emit CSV), which doesn't require lgpio.

Coverage:
  * gpio_byte_from_levels packing (matches csv_format.py's bit layout)
  * fake-stdin: golden CSV bytes for a Mode-A-like scenario
  * fake-stdin: blank/comment lines skipped
  * fake-stdin: inline '#' comments stripped
  * fake-stdin: bad lines warned + skipped, valid lines kept
  * fake-stdin: empty input → header-only CSV
  * CLI: subprocess pipe round-trip
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

import pytest

# Make ../gpio_logger importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpio_logger import (  # noqa: E402
    CSV_HEADER,
    gpio_byte_from_levels,
    run_fake_stdin_mode,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal Args namespace and run fake-stdin mode in-process
# ---------------------------------------------------------------------------

def _run_fake(scenario: str, tmp_path: Path) -> str:
    out_path = tmp_path / "trace.csv"
    args = argparse.Namespace(
        mode="fake-stdin",
        out=str(out_path),
        duration=0.0,
        chip=0,
        pa0=17,
        pa1=27,
        pa4=22,
    )
    rc = run_fake_stdin_mode(args, source=io.StringIO(scenario))
    assert rc == 0
    return out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Bit packing
# ---------------------------------------------------------------------------

def test_gpio_byte_from_levels_all_zero() -> None:
    assert gpio_byte_from_levels(0, 0, 0) == 0


def test_gpio_byte_from_levels_individual_bits() -> None:
    assert gpio_byte_from_levels(1, 0, 0) == 0b001
    assert gpio_byte_from_levels(0, 1, 0) == 0b010
    assert gpio_byte_from_levels(0, 0, 1) == 0b100


def test_gpio_byte_from_levels_all_one() -> None:
    assert gpio_byte_from_levels(1, 1, 1) == 0b111


def test_gpio_byte_from_levels_masks_to_one() -> None:
    """Defensive: lgpio could in theory hand us a non-{0,1} value."""
    assert gpio_byte_from_levels(2, 0, 0) == 0  # bit-0 of 2 is 0
    assert gpio_byte_from_levels(0, 3, 0) == 0b010  # bit-0 of 3 is 1


# ---------------------------------------------------------------------------
# Fake-stdin: golden CSV for a Mode-A-like scenario
# ---------------------------------------------------------------------------

def test_fake_stdin_mode_a_round(tmp_path: Path) -> None:
    """Replay a Mode-A-like sequence; assert byte-equal CSV output."""
    scenario = (
        "100  1   # PA0 high — Setup begins\n"
        "380  0   # all low — Setup ends\n"
        "50   2   # PA1 high — ServerWait\n"
        "100  0   # all low — ServerWait ends\n"
    )
    csv = _run_fake(scenario, tmp_path)
    expected = (
        "timestamp_us,gpio_byte\n"
        "100000,1\n"
        "480000,0\n"
        "530000,2\n"
        "630000,0\n"
    )
    assert csv == expected


def test_fake_stdin_pa4_uart_window(tmp_path: Path) -> None:
    """A PA4 (UART) burst — gpio_byte=4."""
    scenario = "0  4\n0.7 0\n"  # 0ms then PA4 high; 700µs later PA4 low
    csv = _run_fake(scenario, tmp_path)
    lines = csv.splitlines()
    assert lines[0] == CSV_HEADER
    assert lines[1] == "0,4"
    assert lines[2] == "700,0"


# ---------------------------------------------------------------------------
# Fake-stdin: comments and blank lines
# ---------------------------------------------------------------------------

def test_fake_stdin_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    scenario = (
        "# header comment\n"
        "\n"
        "100  1\n"
        "  # indented comment\n"
        "100  0\n"
        "\n"
    )
    csv = _run_fake(scenario, tmp_path)
    lines = csv.splitlines()
    assert lines == [CSV_HEADER, "100000,1", "200000,0"]


def test_fake_stdin_strips_inline_comments(tmp_path: Path) -> None:
    scenario = "100  5   # this # has multiple hashes\n"
    csv = _run_fake(scenario, tmp_path)
    assert csv.splitlines()[1] == "100000,5"


# ---------------------------------------------------------------------------
# Fake-stdin: error tolerance
# ---------------------------------------------------------------------------

def test_fake_stdin_warns_and_skips_malformed_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = (
        "100  1\n"
        "ONLY_ONE_TOKEN\n"        # bad: 1 token
        "1 2 3 4\n"                # bad: 4 tokens
        "abc xyz\n"                # bad: not numeric
        "100  300\n"               # bad: gpio_byte > 255
        "100  -1\n"                # bad: gpio_byte < 0
        "100  0\n"                 # good
    )
    csv = _run_fake(scenario, tmp_path)
    lines = csv.splitlines()
    # Only the two valid lines should produce rows
    assert lines == [CSV_HEADER, "100000,1", "200000,0"]
    err = capsys.readouterr().err
    assert err.count("WARN:") == 5


def test_fake_stdin_empty_input_writes_header_only(tmp_path: Path) -> None:
    csv = _run_fake("", tmp_path)
    assert csv == CSV_HEADER + "\n"


def test_fake_stdin_creates_parent_dirs(tmp_path: Path) -> None:
    """Output path with non-existent parent dirs should be created."""
    deep = tmp_path / "a" / "b" / "c" / "trace.csv"
    args = argparse.Namespace(
        mode="fake-stdin", out=str(deep),
        duration=0.0, chip=0, pa0=17, pa1=27, pa4=22,
    )
    rc = run_fake_stdin_mode(args, source=io.StringIO("100 1\n"))
    assert rc == 0
    assert deep.exists()


# ---------------------------------------------------------------------------
# CLI subprocess test — real pipe round-trip
# ---------------------------------------------------------------------------

def test_cli_pipe_end_to_end(tmp_path: Path) -> None:
    """`echo SCENARIO | python3 gpio_logger.py --mode fake-stdin --out ...`"""
    out = tmp_path / "trace.csv"
    scenario = "100  1\n100  0\n"

    script = Path(__file__).resolve().parent.parent / "gpio_logger.py"
    result = subprocess.run(
        [sys.executable, str(script),
         "--mode", "fake-stdin", "--out", str(out)],
        input=scenario, text=True, timeout=5, capture_output=True,
    )
    assert result.returncode == 0, f"stderr was: {result.stderr}"

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == [CSV_HEADER, "100000,1", "200000,0"]
