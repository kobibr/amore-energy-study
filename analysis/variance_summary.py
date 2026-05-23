"""Summary statistics across replicas of the same cell.

Input: N TraceEnergy objects (one per replica).
Output: mean, stdev, stderr, CV for each gpio_byte's total energy.

Silent-bias fix 2026-05-23 (Bug #1)
-----------------------------------
Replicas that don't contain a given ``gpio_byte`` are EXCLUDED from
that phase's statistics rather than contributing 0.0 (the previous
behaviour). Substituting 0.0 for a missing phase:
  - Biased the mean downward in direct proportion to the fraction
    of replicas where the phase was absent.
  - Inflated stdev artificially: a phase present in 2 of 3 replicas
    with truly stable readings (CV ≪ 1%) was reported with CV ≈ 50%
    purely because the third replica contributed a (0 - mean)² term.
  - Caused ``--max-cv-pct`` gates in variance_study.py to fail on
    measurements that were actually stable.
Each per-gpio_byte Stats now exposes ``n`` reflecting present replicas
only; callers should compare ``n`` to ``n_replicas`` to detect phase
gaps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from .compute_energy import TraceEnergy


@dataclass
class Stats:
    n: int              # number of values that contributed; for per-gpio_byte
                        # this is the number of replicas where the phase
                        # appeared (not necessarily n_replicas total)
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
        # Bug #1 fix: filter out replicas missing this gpio_byte rather
        # than substituting 0.0. Stats.n now reflects how many replicas
        # actually contributed; callers should compare against n_replicas
        # to detect phase gaps. The previous "use 0.0 for missing"
        # approach biased mean downward by the missing fraction and
        # inflated stdev artificially, producing false CV alarms even
        # on perfectly stable measurements.
        e_vals = [t.by_gpio_byte[gb].total_energy_J
                  for t in traces if gb in t.by_gpio_byte]
        d_vals = [float(t.by_gpio_byte[gb].total_duration_us)
                  for t in traces if gb in t.by_gpio_byte]
        by_gb_e[gb] = _stats(e_vals)
        by_gb_d[gb] = _stats(d_vals)

    return CellSummary(
        n_replicas=n,
        total_energy_J=_stats(total_energy),
        total_duration_us=_stats(total_dur),
        by_gpio_byte_energy_J=by_gb_e,
        by_gpio_byte_duration_us=by_gb_d,
    )
