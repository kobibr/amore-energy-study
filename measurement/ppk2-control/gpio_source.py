"""GPIO-event source abstraction for the Mock PPK2.

A ``GPIOSource`` produces a sequence of ``(timestamp_us, gpio_byte)``
events that downstream pipeline stages (interpolator, current
synthesizer, CSV writer) consume.

The abstraction has two purposes:

1. **Decouple** the rest of the server from how events arrive (real
   lgpio callbacks vs. scripted file vs. test harness).
2. **Enable testing** the full pipeline without GPIO hardware — the
   integration tests in spec §10.2 use the scripted source.

Two concrete implementations live here:

* ``ScriptedGPIOSource`` — events come from an in-memory iterable.
  Used by unit tests of the interpolator and downstream stages.

* ``FileGPIOSource`` — events come from a scenario file in the
  canonical format (see ``scenario_format.py``). Used by integration
  tests that drive the full pipeline from a script.

A third implementation ``RealGPIOSource`` (lgpio callbacks) is deferred
to iter 11 once the rest of the pipeline is wired up — that's when
hardware loopback (3 jumpers Pi → Pi) becomes the validation gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Protocol

from scenario_format import GPIOEvent, parse_scenario


class GPIOSource(Protocol):
    """Yields a stream of ``(timestamp_us, gpio_byte)`` events.

    Events must be monotonically non-decreasing in ``timestamp_us``.
    The contract is one-shot per call: ``events()`` returns a fresh
    iterator. Implementations holding expensive resources (lgpio
    handles, open files) must release them when their iterator is
    exhausted or the object is garbage-collected.
    """

    def events(self) -> Iterator[GPIOEvent]: ...


class ScriptedGPIOSource:
    """A ``GPIOSource`` backed by an in-memory list of events.

    Used for unit tests where deterministic input is desired. Validates
    monotonicity and gpio_byte range at construction time so failures
    show up at the test setup, not deep in the pipeline.
    """

    def __init__(self, events: Iterable[GPIOEvent]) -> None:
        # Materialize so callers can re-iterate via repeated events() calls.
        self._events: list[GPIOEvent] = list(events)
        prev_ts = -1
        for i, (ts, gb) in enumerate(self._events):
            if ts < prev_ts:
                raise ValueError(
                    f"events out of order at index {i}: "
                    f"ts={ts} < previous ts={prev_ts}"
                )
            if not 0 <= gb <= 0xFF:
                raise ValueError(
                    f"event[{i}].gpio_byte out of range [0, 255]: {gb}"
                )
            prev_ts = ts

    def events(self) -> Iterator[GPIOEvent]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)


class FileGPIOSource:
    """A ``GPIOSource`` that parses a scenario file.

    Events are read lazily on each call to ``events()``, so a single
    instance can be replayed if the file is still present.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"scenario file not found: {self.path}")

    def events(self) -> Iterator[GPIOEvent]:
        with self.path.open("r", encoding="utf-8") as fp:
            yield from parse_scenario(fp)
