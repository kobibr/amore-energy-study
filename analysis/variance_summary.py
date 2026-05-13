"""Summary statistics across replicas of the same cell.

Input: N TraceEnergy objects (one per replica).
Output: mean, stdev, stderr, CV for each gpio_byte's total energy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from .compute_energy import TraceEnergy


@dataclass
class Stats:
    n: int
    mean: float
    stdev: float       # sample stdev (divide by n-1)
    stderr: float      # stdev / sqrt(n)
    cv: float          # stdev / mean (coefficient of variation)
    min: float
    max: float


def _stats(values: List[float]) -> Stats:
    n = len(values)
    if n == 0:
        return Stats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    mean = sum(values) / n
    if n >= 2:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        stdev = math.sqrt(var)
    else:
        stdev = 0.0
    stderr = stdev / math.sqrt(n) if n > 0 else 0.0
    cv = stdev / mean if mean != 0 else 0.0
    return Stats(n=n, mean=mean, stdev=stdev, stderr=stderr, cv=cv,
                 min=min(values), max=max(values))


@dataclass
class CellSummary:
    n_replicas: int
    total_energy_J: Stats
    total_duration_us: Stats
    by_gpio_byte_energy_J: Dict[int, Stats]
    by_gpio_byte_duration_us: Dict[int, Stats]


def summarize_replicas(traces: List[TraceEnergy]) -> CellSummary:
    n = len(traces)
    total_energy = [t.total_energy_J for t in traces]
    total_dur = [float(t.total_duration_us) for t in traces]

    all_gb: set[int] = set()
    for t in traces:
        all_gb.update(t.by_gpio_byte.keys())

    by_gb_e: Dict[int, Stats] = {}
    by_gb_d: Dict[int, Stats] = {}
    for gb in sorted(all_gb):
        e_vals = [t.by_gpio_byte.get(gb).total_energy_J if gb in t.by_gpio_byte else 0.0
                  for t in traces]
        d_vals = [float(t.by_gpio_byte.get(gb).total_duration_us) if gb in t.by_gpio_byte else 0.0
                  for t in traces]
        by_gb_e[gb] = _stats(e_vals)
        by_gb_d[gb] = _stats(d_vals)

    return CellSummary(
        n_replicas=n,
        total_energy_J=_stats(total_energy),
        total_duration_us=_stats(total_dur),
        by_gpio_byte_energy_J=by_gb_e,
        by_gpio_byte_duration_us=by_gb_d,
    )
