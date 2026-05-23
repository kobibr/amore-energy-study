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
  of the last sample plus the sample period (the period is derived
  as the *median* of all inter-sample deltas; see Bug #2 fix below).

Silent-bias fixes (2026-05-23 review)
-------------------------------------
Bug #1: Phase now exposes ``mean_power_uW`` = sum(V_i · I_i) / N, the
        sample-level mean of instantaneous power. Downstream energy
        integration should prefer it over ``mean_I · mean_V`` because
        that decomposition is exact only when Cov(V,I) = 0 within the
        phase. Today's PPK2 source-meter setup has V ≈ 3.3 V constant
        so the difference is <0.01%, but the API now provides the
        correct quantity directly.
Bug #2: sample period is the median of all inter-sample deltas, not
        just rows[1] - rows[0] (which is jitter-sensitive). Each Phase
        additionally exposes ``measured_us`` = samples × sample_period,
        i.e. the actual time the PPK2 captured during the phase. If
        ``duration_us > measured_us`` by more than a few percent, the
        PPK2 dropped samples in that phase — consumers can detect this
        and decide whether to trust the energy estimate.
Bug #3: a single-row CSV or a CSV with non-monotonic first two
        timestamps now raises ValueError, distinguishing those
        corruption cases from a legitimately empty (header-only) CSV.
Bug #4: the header is validated against ALL four expected column names,
        not just the first one. Silent column reordering used to slip
        through.
Bug #5: timestamps are validated monotonically along the entire trace.
        Out-of-order timestamps used to produce zero- or negative-
        duration phases silently; now they raise ValueError.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List


# Header column names, in lowercase for case-insensitive matching.
# (The canonical CSV format uses mixed-case names like "current_uA"
# but parse_trace accepts any case as long as the words match.)
EXPECTED_HEADER = ("timestamp_us", "current_ua", "voltage_v", "gpio_byte")


@dataclass(frozen=True)
class Phase:
    """One contiguous segment of samples sharing the same gpio_byte."""
    gpio_byte: int
    start_us: int
    end_us: int
    samples: int
    mean_current_uA: float
    mean_voltage_V: float
    # New fields (defaults preserve backward compatibility with code that
    # constructs Phase directly with the old 6-arg positional signature,
    # e.g. tests). New parses always set both fields.
    mean_power_uW: float = 0.0     # Bug #1 fix: sample-level mean(V·I)
    measured_us: int = 0           # Bug #2 fix: samples × sample_period

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    @property
    def gap_us(self) -> int:
        """Difference between span and actually-measured time.

        gap_us > 0 means the PPK2 dropped samples inside this phase
        (or the phase straddled an inter-batch hiccup). Energy
        estimates based on duration_us include this gap as if the
        captured mean held during it — flag in audit if gap_us /
        duration_us exceeds a few percent.
        """
        if self.measured_us <= 0:
            return 0  # legacy Phase without measured_us — can't tell
        return max(0, self.duration_us - self.measured_us)


def _iter_csv_rows(path: Path) -> Iterator[tuple[int, float, float, int]]:
    """Yield (timestamp_us, current_uA, voltage_V, gpio_byte) from CSV.

    Bug #4 fix: validates the FULL header against EXPECTED_HEADER, not
    just the first column. A silent column reordering in csv_format.py
    used to produce energy values from the wrong column.
    """
    with path.open() as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"empty CSV (no header) in {path}")
        # Case-insensitive comparison, tolerate whitespace.
        norm = tuple(h.strip().lower() for h in header[:4])
        if norm != EXPECTED_HEADER:
            raise ValueError(
                f"unexpected header in {path}: got {list(norm)}, "
                f"expected {list(EXPECTED_HEADER)} (case-insensitive)"
            )
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

    Returns
    -------
    []  iff the CSV has only a header (legitimately empty).

    Raises
    ------
    ValueError
        If the CSV is malformed in any of the following ways:
          - bad header (Bug #4)
          - single data row only (cannot infer sample period; Bug #3)
          - non-monotonic timestamps in the first two rows (Bug #3)
          - non-monotonic timestamps anywhere in the trace (Bug #5)
    """
    csv_path = Path(csv_path)
    rows = list(_iter_csv_rows(csv_path))
    if not rows:
        return []

    # Bug #3 fix: a single data row is corruption, not empty data.
    if len(rows) < 2:
        raise ValueError(
            f"{csv_path} has only one data row; cannot derive sample "
            f"period. Either the capture failed immediately or the file "
            f"is truncated."
        )

    # Bug #2a fix: derive the sample period from the MEDIAN of all
    # inter-sample deltas, not from just rows[1] - rows[0]. The latter
    # is jitter-sensitive and leaks into the final phase's end_us; the
    # median is robust to outliers like startup transients or USB
    # boundary effects.
    deltas = [rows[i + 1][0] - rows[i][0] for i in range(len(rows) - 1)]
    sample_period_us = int(statistics.median(deltas))
    if sample_period_us <= 0:
        # Median non-positive means more than half of the trace's
        # inter-sample gaps are zero or negative — file is unusable.
        raise ValueError(
            f"{csv_path}: median sample period is {sample_period_us} µs "
            f"(<= 0); timestamps appear non-monotonic or duplicated. "
            f"The file is malformed; refusing to silently return 0 energy."
        )

    # Bug #5 fix: validate monotonicity along the WHOLE trace, not just
    # the first two rows. A backwards jump silently produced zero- or
    # negative-duration phases under the old code.
    prev_ts = rows[0][0]
    for i in range(1, len(rows)):
        if rows[i][0] < prev_ts:
            raise ValueError(
                f"{csv_path}: non-monotonic timestamp at row {i+1}: "
                f"{rows[i][0]} µs follows {prev_ts} µs. "
                f"PPK2 traces should be strictly monotonic; this file "
                f"is corrupted."
            )
        prev_ts = rows[i][0]

    phases: List[Phase] = []

    # Accumulator for the current Phase
    cur_gb = rows[0][3]
    cur_start = rows[0][0]
    cur_sum_i = 0.0
    cur_sum_v = 0.0
    cur_sum_iv = 0.0          # Bug #1 fix: sample-level Σ(V·I)
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
            mean_power_uW=cur_sum_iv / cur_count,  # Bug #1 fix
            measured_us=cur_count * sample_period_us,  # Bug #2b fix
        ))

    for ts, cur, vol, gb in rows:
        if gb != cur_gb:
            flush(ts)
            cur_gb = gb
            cur_start = ts
            cur_sum_i = 0.0
            cur_sum_v = 0.0
            cur_sum_iv = 0.0
            cur_count = 0
        cur_sum_i += cur
        cur_sum_v += vol
        cur_sum_iv += cur * vol   # Bug #1 fix: accumulate per-sample power
        cur_count += 1
        cur_last_ts = ts

    # Final phase: end at last_ts + period (sample-inclusive end).
    # Now uses the robust median-derived period.
    flush(cur_last_ts + sample_period_us)
    return phases
