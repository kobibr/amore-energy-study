"""Convert sparse GPIO transitions to dense fixed-rate samples.

The mock samples real GPIO at the rate of arriving edges (~10 kHz from
lgpio callbacks under typical AmorE traffic), but advertises a constant
100 ksps to clients. This module bridges the gap by interpolating the
``gpio_byte`` forward in time until the next transition.

Per spec §5.3::

    Claimed sample rate is 100,000 samples/second (10 µs intervals).
    The mock samples real GPIO at ~10 kHz via pigpio callbacks and
    interpolates to 100 ksps with the synthetic current value held
    constant within each phase.

Boundary semantics
------------------

An event at timestamp T fires *at or before* the sample at timestamp T.
So a transition at t=100 µs is reflected in the sample emitted at
t=100 µs (with the new gpio_byte), not in the sample at t=110 µs. This
matches lgpio's callback semantics (callback fires "at" the edge time,
after which the new level is observed).

Aliasing
--------

Pulses shorter than the sample period (10 µs by default) may be missed
if both edges fall between sample boundaries. This matches the real
PPK2's behavior and is acceptable for the AmorE phase durations
(smallest is the wake-up burst at ~13 µs; longest are hundreds of ms).
"""
from __future__ import annotations

from typing import Iterable, Iterator, Tuple

DEFAULT_SAMPLE_PERIOD_US = 10  # 100 ksps

GPIOEvent = Tuple[int, int]
Sample = Tuple[int, int]  # (timestamp_us, gpio_byte) at fixed rate


def interpolate_to_fixed_rate(
    events: Iterable[GPIOEvent],
    end_time_us: int,
    sample_period_us: int = DEFAULT_SAMPLE_PERIOD_US,
    initial_gpio_byte: int = 0,
) -> Iterator[Sample]:
    """Convert sparse events to dense samples at fixed rate.

    Args:
        events: iterable of ``(timestamp_us, gpio_byte)``. Must be
            monotonically non-decreasing in timestamp.
        end_time_us: emit samples for ``sample_ts`` in
            ``range(0, end_time_us, sample_period_us)``. Half-open
            interval — the sample exactly at ``end_time_us`` is NOT
            emitted.
        sample_period_us: spacing between samples (default 10 = 100 ksps).
        initial_gpio_byte: ``gpio_byte`` before any event has fired
            (default 0 = idle, all triggers low).

    Yields:
        ``(sample_ts_us, gpio_byte)`` tuples. Stream length is exactly
        ``ceil(end_time_us / sample_period_us)`` if ``end_time_us > 0``,
        else 0.

    Raises:
        ValueError: if ``sample_period_us <= 0``, ``end_time_us < 0``,
            events are out of order, or any event has invalid gpio_byte.
    """
    if sample_period_us <= 0:
        raise ValueError(
            f"sample_period_us must be positive, got {sample_period_us}"
        )
    if end_time_us < 0:
        raise ValueError(
            f"end_time_us must be non-negative, got {end_time_us}"
        )
    if not 0 <= initial_gpio_byte <= 0xFF:
        raise ValueError(
            f"initial_gpio_byte out of range [0, 255]: {initial_gpio_byte}"
        )

    current_byte = initial_gpio_byte
    sample_ts = 0
    last_event_ts = -1

    events_iter = iter(events)
    next_event = next(events_iter, None)

    while sample_ts < end_time_us:
        # Apply all events with ts <= sample_ts. Multiple events at the
        # same ts → the last one wins (consistent with "an event at T
        # fires at or before the sample at T").
        while next_event is not None and next_event[0] <= sample_ts:
            ev_ts, ev_byte = next_event
            if ev_ts < last_event_ts:
                raise ValueError(
                    f"events out of order: {ev_ts} < {last_event_ts}"
                )
            if not 0 <= ev_byte <= 0xFF:
                raise ValueError(
                    f"event gpio_byte out of range [0, 255]: {ev_byte}"
                )
            current_byte = ev_byte
            last_event_ts = ev_ts
            next_event = next(events_iter, None)

        yield (sample_ts, current_byte)
        sample_ts += sample_period_us
