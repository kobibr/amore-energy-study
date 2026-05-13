"""Tests for gpio_source: ScriptedGPIOSource and FileGPIOSource."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpio_source import (  # noqa: E402
    FileGPIOSource,
    GPIOSource,
    ScriptedGPIOSource,
)


# ---------------------------------------------------------------------------
# ScriptedGPIOSource
# ---------------------------------------------------------------------------

def test_scripted_empty() -> None:
    src = ScriptedGPIOSource([])
    assert list(src.events()) == []
    assert len(src) == 0


def test_scripted_single_event() -> None:
    src = ScriptedGPIOSource([(100, 1)])
    assert list(src.events()) == [(100, 1)]


def test_scripted_multiple_events_in_order() -> None:
    events = [(0, 0), (100, 1), (480, 0), (530, 2), (630, 0)]
    src = ScriptedGPIOSource(events)
    assert list(src.events()) == events
    assert len(src) == 5


def test_scripted_iterator_can_be_replayed() -> None:
    """events() returns a fresh iterator on each call."""
    events = [(0, 0), (100, 1)]
    src = ScriptedGPIOSource(events)
    assert list(src.events()) == events
    assert list(src.events()) == events  # second iteration also works


def test_scripted_rejects_out_of_order() -> None:
    with pytest.raises(ValueError, match="out of order"):
        ScriptedGPIOSource([(100, 1), (50, 0)])


def test_scripted_allows_simultaneous_events() -> None:
    """Equal timestamps OK — last one wins downstream."""
    events = [(100, 1), (100, 3)]
    src = ScriptedGPIOSource(events)
    assert list(src.events()) == events


def test_scripted_rejects_negative_gpio_byte() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        ScriptedGPIOSource([(0, -1)])


def test_scripted_rejects_overflow_gpio_byte() -> None:
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        ScriptedGPIOSource([(0, 256)])


def test_scripted_satisfies_protocol() -> None:
    """Static + dynamic conformance to the GPIOSource Protocol."""
    src: GPIOSource = ScriptedGPIOSource([(0, 0)])
    assert hasattr(src, "events")
    assert callable(src.events)


# ---------------------------------------------------------------------------
# FileGPIOSource
# ---------------------------------------------------------------------------

def test_file_source_basic(tmp_path: Path) -> None:
    p = tmp_path / "scenario.txt"
    p.write_text("100  1\n380  0\n50  2\n100  0\n", encoding="utf-8")
    src = FileGPIOSource(p)
    assert list(src.events()) == [
        (100_000, 1),
        (480_000, 0),
        (530_000, 2),
        (630_000, 0),
    ]


def test_file_source_with_comments(tmp_path: Path) -> None:
    p = tmp_path / "scenario.txt"
    p.write_text(
        "# Mode A round\n"
        "100  1   # PA0 high — Setup\n"
        "380  0   # all low\n",
        encoding="utf-8",
    )
    assert list(FileGPIOSource(p).events()) == [(100_000, 1), (480_000, 0)]


def test_file_source_replayable(tmp_path: Path) -> None:
    p = tmp_path / "scenario.txt"
    p.write_text("100 1\n", encoding="utf-8")
    src = FileGPIOSource(p)
    assert list(src.events()) == [(100_000, 1)]
    assert list(src.events()) == [(100_000, 1)]  # second call re-reads file


def test_file_source_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="scenario file not found"):
        FileGPIOSource(tmp_path / "does_not_exist.txt")
