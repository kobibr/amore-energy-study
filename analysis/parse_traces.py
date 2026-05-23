"""Parse a 4-column PPK2 CSV into a list of Phase records.

A Phase is a contiguous run of samples sharing the same gpio_byte. The
list of Phases is the basic unit consumed by compute_energy.py.

CSV format (per measurement/ppk2-control/csv_format.py)::
    timestamp_us, current_uA, voltage_V, gpio_byte

Phase identification
--------------------
A new Phase begins at every gpio_byte transition. The Phase's
``mean_current_uA`` is the arithmetic mean of all samples inside it.
We use mean (not median) because PPK2's noise is Gaussian and the
analysis integrates to compute energy (E = mean × dt), where mean is
the optimal estimator.

Edge handling
-------------
- The first sample's timestamp anchors t=0 for the trace.
- A Phase's ``end_us`` is the timestamp of the FIRST sample of the
  next Phase (i.e. exclusive). The final Phase ends at the timestamp
  of the last sample plus the sample period (assumed uniform).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List


@dataclass(frozen=True)
class Phase:
    """One contiguous segment of samples sharing the same gpio_byte."""
    gpio_byte: int
    start_us: int
    end_us: int
    samples: int
    mean_current_uA: float
    mean_voltage_V: float

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us


def _iter_csv_rows(path: Path) -> Iterator[tuple[int, float, float, int]]:
    """Yield (timestamp_us, current_uA, voltage_V, gpio_byte) from CSV."""
    with path.open() as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None or header[0].lower() != "timestamp_us":
            raise ValueError(f"unexpected header in {path}: {header}")
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            try:
                ts = int(row[0])
                cur = float(row[1])
                vol = float(row[2])
                gb = int(row[3])
            except (ValueError, IndexError) as e:
                raise ValueError(f"bad row in {path}: {row} ({e})")
            yield (ts, cur, vol, gb)


def parse_trace(csv_path: Path) -> List[Phase]:
    """Read CSV and return a list of Phase records in time order.

    Empty CSV (header only) returns an empty list — not an error.
    """
    csv_path = Path(csv_path)
    rows = list(_iter_csv_rows(csv_path))
    if not rows:
        return []

    # Determine sample period from the first two rows (uniform assumed).
    # A single-row CSV cannot define a sample period; treat as empty
    # rather than guess a default that may be wrong by orders of magnitude.
    if len(rows) < 2:
        return []
    sample_period_us = rows[1][0] - rows[0][0]
    if sample_period_us <= 0:
        # Same defensive case: non-positive period means malformed
        # CSV (out-of-order timestamps). Return empty rather than guess.
        return []

    phases: List[Phase] = []
    # Accumulator for the current Phase
    cur_gb = rows[0][3]
    cur_start = rows[0][0]
    cur_sum_i = 0.0
    cur_sum_v = 0.0
    cur_count = 0
    cur_last_ts = rows[0][0]

    def flush(end_ts: int) -> None:
        if cur_count == 0:
            return
        phases.append(Phase(
            gpio_byte=cur_gb,
            start_us=cur_start,
            end_us=end_ts,
            samples=cur_count,
            mean_current_uA=cur_sum_i / cur_count,
            mean_voltage_V=cur_sum_v / cur_count,
        ))

    for ts, cur, vol, gb in rows:
        if gb != cur_gb:
            flush(ts)
            cur_gb = gb
            cur_start = ts
            cur_sum_i = 0.0
            cur_sum_v = 0.0
            cur_count = 0
        cur_sum_i += cur
        cur_sum_v += vol
        cur_count += 1
        cur_last_ts = ts

    # Final phase: end at last_ts + period (sample-inclusive end)
    flush(cur_last_ts + sample_period_us)
    return phases
