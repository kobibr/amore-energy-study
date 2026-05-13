"""Assemble full Sample stream from GPIO samples + current synthesis.

This is the layer that joins the GPIO half of the pipeline (events →
interpolated dense samples) with the current half (gpio_byte → current
draw with Gaussian noise + voltage from supply setting). The output is
a stream of ``csv_format.Sample`` objects ready to be written to the
canonical CSV or streamed over the wire protocol.

Pipeline location::

    GPIOSource.events()
        ↓ interpolate_to_fixed_rate()
    (timestamp_us, gpio_byte) at 100 ksps
        ↓ assemble_samples()           ← THIS MODULE
    Sample(timestamp_us, current_uA, voltage_V, gpio_byte) at 100 ksps
        ↓ write_samples() / wire-protocol streamer
    CSV / TCP chunks

The assembler is **stateless modulo the RNG**: each output ``Sample``
depends only on the input ``(timestamp_us, gpio_byte)`` and the
``voltage_mV`` / ``stop_mode`` parameters. The current value is drawn
afresh from the Gaussian for every sample (independent noise per
sample, matching how a real PPK2 produces its 100 ksps stream).

Wake-up burst overlay
---------------------

Spec §7.3 calls for a wake-up burst overlay (80 mA for 13 µs after
each PA0 rising edge during the burst-measurement firmware). That
overlay is intentionally NOT implemented here — it is stateful (needs
to remember when the last PA0 rising edge fired) and will live as a
separate composable layer in a later iter. ``WAKEUP_BURST_PEAK_UA`` /
``WAKEUP_BURST_DURATION_US`` are exposed by ``current_synthesis`` for
that future layer to consume.
"""
from __future__ import annotations

import random
from typing import Iterable, Iterator, Tuple

from csv_format import Sample
from current_synthesis import sample_current

GPIOSampleAtRate = Tuple[int, int]  # (timestamp_us, gpio_byte)


def assemble_samples(
    gpio_samples: Iterable[GPIOSampleAtRate],
    voltage_mV: int,
    stop_mode: bool = False,
    rng: random.Random | None = None,
) -> Iterator[Sample]:
    """Yield full ``Sample`` objects from a stream of ``(ts_us, gpio_byte)``.

    Args:
        gpio_samples: typically the output of
            ``interpolator.interpolate_to_fixed_rate``. Any iterable
            of ``(timestamp_us, gpio_byte)`` will do.
        voltage_mV: the supply voltage in millivolts, set by the client
            via ``set_source_voltage``. Constant within a session, per
            spec §5.1. Stored as the ``voltage_V`` field of every
            emitted sample.
        stop_mode: if True and a sample's ``gpio_byte == 0``, draw the
            current from the Stop-mode model instead of the active-idle
            model (~0.5 µA instead of ~50 mA). Mode is set per-session
            via the wire protocol's ``set_stop_mode`` command.
        rng: optional ``random.Random`` for reproducibility. None →
            module-level random state.

    Yields:
        ``Sample(timestamp_us, current_uA, voltage_V, gpio_byte)``,
        one per input gpio_sample.

    Raises:
        ValueError: if ``voltage_mV < 0`` or any gpio_byte is invalid
            (range is enforced by ``current_synthesis`` and
            ``csv_format`` in the same way).
    """
    if voltage_mV < 0:
        raise ValueError(f"voltage_mV must be non-negative, got {voltage_mV}")
    voltage_V = voltage_mV / 1000.0

    for ts_us, gpio_byte in gpio_samples:
        current_uA = sample_current(
            gpio_byte, stop_mode=stop_mode, rng=rng
        )
        # current_synthesis.sample_current can return negative values
        # (Gaussian noise tail). Real currents can't be negative, so
        # clamp at 0 — the canonical CSV format permits any non-negative
        # float, and Sample's __post_init__ doesn't validate sign of
        # current. But pipeline analysis code expects non-negative.
        if current_uA < 0.0:
            current_uA = 0.0
        yield Sample(
            timestamp_us=ts_us,
            current_uA=current_uA,
            voltage_V=voltage_V,
            gpio_byte=gpio_byte,
        )
