"""Canonical scenario file format for the AmorE Mock PPK2.

A 'scenario' is a sequence of timed GPIO transitions, written as
plaintext lines::

    delay_ms  gpio_byte    [# comment]

* ``delay_ms`` — float milliseconds since the *previous* transition
  (or since t=0 for the first one).
* ``gpio_byte`` — integer in [0, 255]. Bit 0 = PA0, bit 1 = PA1,
  bit 2 = PA4 (matches ``csv_format.py``'s bit assignment).
* Lines starting with ``#`` are comments. Inline ``#`` starts a comment.
* Blank lines are ignored.

Example (Mode-A round)::

    100  1   # PA0 high — Setup begins (gpio_byte=0b001)
    380  0   # all low — Setup ends
    50   2   # PA1 high — ServerWait
    100  0   # all low

This format is used by:

* ``gpio_logger.py --mode fake-stdin``     (iter 4 — keeps its own copy
  of the parser for self-containment; future iter will dedupe)
* ``gpio_source.FileGPIOSource``           (iter 6+)
* ``mock_ppk2_server.py``'s ``--gpio-source=fake-script`` (iter 9+)

The parser is intentionally permissive: malformed lines emit ``WARN:``
to stderr and are skipped, not fatal — keeps long scenario files robust
against typos.
"""
from __future__ import annotations

import sys
from typing import IO, Iterator, Tuple


# (timestamp_us, gpio_byte) — exported so callers don't have to retype.
GPIOEvent = Tuple[int, int]


def parse_scenario(source: IO[str]) -> Iterator[GPIOEvent]:
    """Parse a scenario stream into ``(cumulative_timestamp_us, gpio_byte)`` events.

    Args:
        source: a text-mode file-like object.

    Yields:
        Tuples ``(timestamp_us, gpio_byte)``, monotonically non-decreasing
        in ``timestamp_us``. The first event's timestamp is the first
        ``delay_ms`` (converted to µs); each subsequent event's timestamp
        is the cumulative sum.

    Notes:
        * Bad lines (wrong token count, parse error) write ``WARN: ...``
          to stderr and are skipped.
        * ``gpio_byte`` outside [0, 255] is also warned and skipped.
    """
    t_us = 0
    for raw in source:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) != 2:
            print(f"WARN: bad line: {raw!r}", file=sys.stderr)
            continue
        try:
            delay_ms = float(parts[0])
            gb = int(parts[1])
        except ValueError:
            print(f"WARN: parse error: {raw!r}", file=sys.stderr)
            continue
        # Bug #7 fix: a negative delay would silently move t_us backwards
        # and produce a non-monotonic stream that only blows up later in
        # interpolate_to_fixed_rate with an index-based error message
        # that's hard to map back to the scenario file. Catch at source.
        if delay_ms < 0:
            print(
                f"WARN: negative delay_ms ignored: {raw!r}",
                file=sys.stderr,
            )
            continue
        if not 0 <= gb <= 0xFF:
            print(
                f"WARN: gpio_byte out of range [0, 255]: {gb}",
                file=sys.stderr,
            )
            continue
        t_us += int(round(delay_ms * 1000))
        yield (t_us, gb)
