"""Compute energy (joules, millijoules) from a list of Phase records.

Per phase::
    E_phase = mean_current_uA × mean_voltage_V × duration_us × 1e-12  [J]

Then we aggregate per gpio_byte to get total energy spent in each
logical phase across the trace.

Returns a dict keyed by gpio_byte with totals (energy, duration, samples)
plus per-phase records for downstream variance work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .parse_traces import Phase


@dataclass
class PhaseEnergy:
    """Energy of one Phase. Convenience wrapper over the Phase dataclass."""
    phase: Phase
    energy_J: float

    @property
    def energy_mJ(self) -> float:
        return self.energy_J * 1000.0

    @property
    def energy_uJ(self) -> float:
        return self.energy_J * 1e6


@dataclass
class GpioByteAggregate:
    """Totals for a single gpio_byte across an entire trace."""
    gpio_byte: int
    total_energy_J: float = 0.0
    total_duration_us: int = 0
    total_samples: int = 0
    n_phases: int = 0
    mean_current_uA: float = 0.0   # weighted by samples
    mean_voltage_V: float = 0.0    # weighted by samples


@dataclass
class TraceEnergy:
    """Per-trace energy summary."""
    per_phase: List[PhaseEnergy] = field(default_factory=list)
    by_gpio_byte: Dict[int, GpioByteAggregate] = field(default_factory=dict)
    total_energy_J: float = 0.0
    total_duration_us: int = 0


def phase_energy(phase: Phase) -> float:
    """E = I × V × t   (uA × V × us → uA·V·us = J × 1e-12)."""
    return (
        phase.mean_current_uA *
        phase.mean_voltage_V *
        phase.duration_us *
        1e-12
    )


def compute_trace(phases: List[Phase]) -> TraceEnergy:
    out = TraceEnergy()
    for ph in phases:
        e = phase_energy(ph)
        out.per_phase.append(PhaseEnergy(phase=ph, energy_J=e))
        out.total_energy_J += e
        out.total_duration_us += ph.duration_us

        agg = out.by_gpio_byte.setdefault(
            ph.gpio_byte, GpioByteAggregate(gpio_byte=ph.gpio_byte)
        )
        # Sample-weighted running mean for current and voltage
        prev_samples = agg.total_samples
        new_samples = prev_samples + ph.samples
        agg.mean_current_uA = (
            agg.mean_current_uA * prev_samples + ph.mean_current_uA * ph.samples
        ) / new_samples
        agg.mean_voltage_V = (
            agg.mean_voltage_V * prev_samples + ph.mean_voltage_V * ph.samples
        ) / new_samples
        agg.total_energy_J += e
        agg.total_duration_us += ph.duration_us
        agg.total_samples = new_samples
        agg.n_phases += 1

    return out
