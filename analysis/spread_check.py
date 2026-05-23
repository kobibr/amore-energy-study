"""spread_check.py — within-replica variability across cells / rounds.

Whereas variance_study.py answers "do replicates produce the same
result?", this module answers "within ONE trace, is each gpio_byte
cell consistent across all its appearances (rounds)?"

Why this matters:
  AmorE's amortization claim depends on each phase having stable
  per-cell energy. If gpio_byte=2 (e.g., pairing-compute phase)
  costs 100 mJ on round 1 but 130 mJ on round 50, that's a
  thermal/cache/resource-contention drift the paper must disclose.
  spread_check.py surfaces that drift if it exists.

Input:
  A 4-column CSV trace produced by PPK2Backend or MockBackend:
    timestamp_us, current_uA, voltage_V, gpio_byte

Algorithm:
  1. Read CSV.
  2. Walk samples, detect transitions in gpio_byte value.
  3. Group consecutive same-byte samples into "cell instances".
     A cell instance = one continuous segment where gpio_byte stayed
     constant.
  4. For each cell instance, compute:
       - duration_us  (last_ts - first_ts)
       - energy_uJ    (sum(current_uA * voltage_V * dt) per sample)
  5. Group instances by gpio_byte value.
  6. Report per-byte: count, mean, stdev, min, max, CV of energy
     across all instances of that byte.
  7. Flag any cell with CV > --warn-cv-pct.

Output:
  - Stdout table
  - Optional CSV via --out-csv (per-byte stats)

Usage:
  python3 -m analysis.spread_check measurement/traces/variance_PPK2_v2_*/run_001.csv
  python3 -m analysis.spread_check trace.csv --warn-cv-pct 10
  python3 -m analysis.spread_check trace.csv --out-csv spread.csv

Exit codes:
  0 = all cells within CV threshold
  1 = at least one cell exceeds CV threshold
  2 = invalid input / unreadable CSV
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CellInstance:
    """One continuous segment in the trace where gpio_byte was constant."""
    gpio_byte: int
    first_ts_us: int
    last_ts_us: int
    n_samples: int
    energy_uJ: float

    @property
    def duration_us(self) -> int:
        return self.last_ts_us - self.first_ts_us


def parse_trace(csv_path: Path) -> list[dict]:
    """Read 4-column CSV: timestamp_us, current_uA, voltage_V, gpio_byte."""
    rows: list[dict] = []
    with csv_path.open(newline="") as f:
        r = csv.DictReader(f)
        required = {"timestamp_us", "current_uA", "voltage_V", "gpio_byte"}
        if not required.issubset(set(r.fieldnames or [])):
            raise ValueError(
                f"CSV missing required columns. Got: {r.fieldnames}, "
                f"need: {sorted(required)}"
            )
        for row in r:
            rows.append({
                "timestamp_us": int(float(row["timestamp_us"])),
                "current_uA":  float(row["current_uA"]),
                "voltage_V":   float(row["voltage_V"]),
                "gpio_byte":   int(row["gpio_byte"]),
            })
    return rows


def detect_cell_instances(rows: list[dict]) -> list[CellInstance]:
    """Walk samples; group into continuous same-gpio_byte segments.

    For each segment, integrate current * voltage over time to get
    energy in microjoules.
    """
    if not rows:
        return []

    instances: list[CellInstance] = []
    current_byte = rows[0]["gpio_byte"]
    seg_start_ts = rows[0]["timestamp_us"]
    seg_last_ts = rows[0]["timestamp_us"]
    seg_n = 1
    # Energy accumulator: for each sample i, energy_uJ contribution is
    #   I_uA * V_V * dt_s = (uA * V * s) = µJ
    # using dt from prev->current sample
    seg_energy_uJ = 0.0
    prev_row = rows[0]

    for row in rows[1:]:
        # Bug #1 fix: compute this interval's energy contribution FIRST,
        # using prev_row's values (left-edge integration). Previously this
        # block ran AFTER the transition check, which meant the interval
        # between A's last sample and B's first sample was credited to B
        # instead of A — contaminating B's energy with A's tail.
        dt_s = (row["timestamp_us"] - prev_row["timestamp_us"]) / 1e6
        seg_energy_uJ += prev_row["current_uA"] * prev_row["voltage_V"] * dt_s

        # Has the gpio_byte changed?
        if row["gpio_byte"] != current_byte:
            # Close out the current segment (now correctly includes the
            # transition interval since we added it above).
            instances.append(CellInstance(
                gpio_byte=current_byte,
                first_ts_us=seg_start_ts,
                last_ts_us=seg_last_ts,
                n_samples=seg_n,
                energy_uJ=seg_energy_uJ,
            ))
            # Start new segment
            current_byte = row["gpio_byte"]
            seg_start_ts = row["timestamp_us"]
            seg_n = 0
            seg_energy_uJ = 0.0

        seg_last_ts = row["timestamp_us"]
        seg_n += 1
        prev_row = row

    # Close the final segment
    instances.append(CellInstance(
        gpio_byte=current_byte,
        first_ts_us=seg_start_ts,
        last_ts_us=seg_last_ts,
        n_samples=seg_n,
        energy_uJ=seg_energy_uJ,
    ))

    return instances


def summarize_by_byte(instances: list[CellInstance]) -> dict[int, dict]:
    """Group cell instances by gpio_byte, compute stats per group."""
    groups: dict[int, list[CellInstance]] = {}
    for inst in instances:
        groups.setdefault(inst.gpio_byte, []).append(inst)

    summary: dict[int, dict] = {}
    for byte_val, group in sorted(groups.items()):
        energies = [g.energy_uJ for g in group]
        durations = [g.duration_us for g in group]
        n = len(energies)
        mean_e = statistics.mean(energies) if n > 0 else 0.0
        stdev_e = statistics.stdev(energies) if n > 1 else 0.0
        cv = (100 * stdev_e / mean_e) if mean_e > 0 else 0.0
        summary[byte_val] = {
            "gpio_byte": byte_val,
            "n_instances": n,
            "mean_energy_uJ": mean_e,
            "stdev_energy_uJ": stdev_e,
            "min_energy_uJ": min(energies) if energies else 0.0,
            "max_energy_uJ": max(energies) if energies else 0.0,
            "cv_pct": cv,
            "mean_duration_us": statistics.mean(durations) if durations else 0,
            "total_n_samples": sum(g.n_samples for g in group),
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("csv_path", type=Path,
                   help="4-column CSV from PPK2Backend or MockBackend")
    p.add_argument("--warn-cv-pct", type=float, default=15.0,
                   help="Flag cells with CV greater than this (default: 15.0)")
    p.add_argument("--out-csv", type=Path, default=None,
                   help="Write per-byte stats to CSV")
    p.add_argument("--ignore-byte", type=int, nargs="+", default=[],
                   help="gpio_byte values to ignore (e.g. 0 = idle)")
    args = p.parse_args(argv)

    if not args.csv_path.exists():
        print(f"FATAL: file not found: {args.csv_path}", file=sys.stderr)
        return 2

    print(f"Trace: {args.csv_path}")
    try:
        rows = parse_trace(args.csv_path)
    except Exception as e:
        print(f"FATAL: cannot parse CSV: {e}", file=sys.stderr)
        return 2
    print(f"  samples: {len(rows)}")

    if len(rows) < 2:
        print("FATAL: need at least 2 samples", file=sys.stderr)
        return 2

    instances = detect_cell_instances(rows)
    print(f"  cell instances detected: {len(instances)}")

    summary = summarize_by_byte(instances)
    if args.ignore_byte:
        for b in args.ignore_byte:
            summary.pop(b, None)

    print()
    print("━━━ Per-cell variability ━━━")
    print(f"  {'byte':>4}  {'n':>4}  {'mean_µJ':>12}  {'stdev_µJ':>12}  "
          f"{'CV %':>7}  {'min_µJ':>10}  {'max_µJ':>10}  {'avg_dur_µs':>10}")
    print("  " + "-" * 80)

    flagged = []
    for byte_val, s in summary.items():
        flag = "⚠" if s["cv_pct"] > args.warn_cv_pct else " "
        print(f"  {byte_val:>4}  {s['n_instances']:>4}  "
              f"{s['mean_energy_uJ']:>12.2f}  {s['stdev_energy_uJ']:>12.2f}  "
              f"{s['cv_pct']:>6.2f}% {flag} "
              f"{s['min_energy_uJ']:>10.2f}  {s['max_energy_uJ']:>10.2f}  "
              f"{s['mean_duration_us']:>10.0f}")
        if s["cv_pct"] > args.warn_cv_pct:
            flagged.append(byte_val)
    print()

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.out_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "gpio_byte", "n_instances",
                "mean_energy_uJ", "stdev_energy_uJ", "cv_pct",
                "min_energy_uJ", "max_energy_uJ",
                "mean_duration_us", "total_n_samples",
            ])
            w.writeheader()
            for s in summary.values():
                w.writerow(s)
        print(f"  CSV: {args.out_csv}")
        print()

    if flagged:
        print(f"  ⚠ {len(flagged)} cell(s) exceed CV threshold "
              f"({args.warn_cv_pct}%): bytes {flagged}")
        return 1

    print(f"  ✓ All cells within CV ≤ {args.warn_cv_pct}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
