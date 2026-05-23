"""Compute energy (joules, millijoules) from a list of Phase records.

Per phase, when ``phase.mean_power_uW`` is provided (the new field added
in the 2026-05-23 parse_traces fix)::
    E_phase = mean_power_uW × duration_us × 1e-12  [J]   (CORRECT)

This integrates power at sample level, so it's exact even when Cov(V,I)
inside the phase is non-zero. Legacy Phases without ``mean_power_uW``
(constructed directly with the old 6-arg signature) fall back to::
    E_phase = mean_current_uA × mean_voltage_V × duration_us × 1e-12

Then we aggregate per gpio_byte to get total energy spent in each
logical phase across the trace.

Returns a dict keyed by gpio_byte with totals (energy, duration, samples)
plus per-phase records for downstream variance work.

Cov(V,I) handling (Bug #3 of the original review)
-------------------------------------------------
The naive formula mean(V)·mean(I)·t equals the true integral only when
Cov(V,I) ≈ 0. PPK2 in source-meter mode pegs VDD at 3.300 V across all
samples (mini_regression.sh layer 4 asserts ``n_bad_v == 0``), so the
bias is <0.01% in the AmorE setup. Still, the new ``mean_power_uW``
field eliminates the assumption entirely. A runtime guard
(``assert_phase_voltage_constant``) is provided as a defense-in-depth
helper for callers that have sample-level voltage data.

Aggregation policy (Bug #2 of the original review)
--------------------------------------------------
``mean_current_uA``, ``mean_voltage_V``, and ``mean_power_uW`` on
GpioByteAggregate are weighted by duration_us, NOT by sample count.
Sample-weighting produces values inconsistent with the energy total
whenever the PPK2 sample rate varies across phases (it can, due to USB
batching). Duration-weighting preserves the identity:
    mean_power_uW · total_duration_us · 1e-12  ≡  total_energy_J
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
    """Totals for a single gpio_byte across an entire trace.

    All three mean_* fields are duration-weighted (Bug #2 fix).
    ``mean_power_uW`` (Bug #1 fix) is the Cov(V,I)-resistant quantity;
    it's what you should use for further energy math. The separate
    ``mean_current_uA`` and ``mean_voltage_V`` are kept for reporting
    purposes (e.g. "average current during the Compute phase") but
    must NOT be multiplied together as a substitute for mean_power.
    """
    gpio_byte: int
    total_energy_J: float = 0.0
    total_duration_us: int = 0
    total_samples: int = 0
    n_phases: int = 0
    mean_current_uA: float = 0.0   # weighted by duration_us
    mean_voltage_V: float = 0.0    # weighted by duration_us
    mean_power_uW: float = 0.0     # weighted by duration_us; Bug #1 fix


@dataclass
class TraceEnergy:
    """Per-trace energy summary."""
    per_phase: List[PhaseEnergy] = field(default_factory=list)
    by_gpio_byte: Dict[int, GpioByteAggregate] = field(default_factory=dict)
    total_energy_J: float = 0.0
    total_duration_us: int = 0


# Bug #3 fix: tolerance for the constant-V-per-phase assumption.
# Per Phase, we trust mean_voltage_V to summarize voltage during that phase
# only when the variation is below this threshold. PPK2 in source-meter mode
# normally holds 3.300 V flat; any non-trivial swing means we're picking up
# rail dynamics that violate Cov(V,I)=0 and the I·V·t formula stops being
# exact. We cannot detect this from the Phase struct alone (it already
# averaged the samples), so we instead document the precondition loudly
# and provide a helper for parse_traces / callers that DO have sample-level
# data to verify it before constructing a Phase.
PHASE_VOLTAGE_TOLERANCE_FRAC = 0.01  # 1%


def assert_phase_voltage_constant(samples_voltage_V) -> None:
    """Verify that voltage was effectively constant during a phase.

    Bug #3 guard: the E = mean(I)·mean(V)·t formula is only exact when
    Cov(V, I) ≈ 0 inside the phase. The cleanest proxy is to verify V
    barely moves. Callers with access to raw per-sample voltage should
    call this before building a Phase; if it raises, switch that phase
    to per-sample power integration (E = sum(V_i · I_i · dt)) instead.

    Currently NOT called from this module (compute_energy doesn't see
    raw samples). Provided as a hook for parse_traces or other code
    that does have sample-level visibility.
    """
    if not samples_voltage_V:
        return
    v_min = min(samples_voltage_V)
    v_max = max(samples_voltage_V)
    v_mean = sum(samples_voltage_V) / len(samples_voltage_V)
    if v_mean <= 0:
        return  # degenerate; let downstream code handle it
    spread = (v_max - v_min) / v_mean
    if spread > PHASE_VOLTAGE_TOLERANCE_FRAC:
        raise ValueError(
            f"phase voltage not constant: spread={spread*100:.2f}% "
            f"(min={v_min:.4f} V, max={v_max:.4f} V, mean={v_mean:.4f} V). "
            f"E = I·V·t is not exact when Cov(V,I) ≠ 0; use per-sample "
            f"power integration for this phase."
        )


def phase_energy(phase: Phase) -> float:
    """Energy contribution of a single Phase, in joules.

    Bug #1 fix: prefers ``phase.mean_power_uW`` (the Cov(V,I)-resistant,
    sample-level integral of power) when available. This is what the
    new parse_traces always emits.

    Falls back to the legacy ``mean_current_uA × mean_voltage_V × t``
    formula only for Phases built without ``mean_power_uW`` (e.g. tests
    using the 6-arg positional constructor). That fallback assumes
    Cov(V, I) ≈ 0 within the phase, which holds for the AmorE PPK2
    source-meter setup but isn't guaranteed in general.
    """
    if phase.mean_power_uW > 0.0:
        # Correct path: sample-level mean(V·I) × duration.
        return phase.mean_power_uW * phase.duration_us * 1e-12
    # Legacy path: only correct when V is constant inside the phase.
    return (
        phase.mean_current_uA *
        phase.mean_voltage_V *
        phase.duration_us *
        1e-12
    )


def compute_trace(phases: List[Phase]) -> TraceEnergy:
    """Aggregate Phase records into per-trace and per-gpio_byte totals.

    Bug #1 fix (silent-bias re-review 2026-05-23): emits a warning to
    stderr when any single gpio_byte contains phases whose mean
    currents vary by more than ``PHASE_CURRENT_RATIO_WARN`` (100×).
    This is the "ambiguous gpio_byte" signal:

    Real PPK2 captures with Stop-mode firmware will see Stop
    ServerWait under gpio_byte=0 (PA1 falls to 0 in Stop), aliasing
    with ordinary Idle (50 mA). The two appear as the same key in
    by_gpio_byte but have radically different physics. Without the
    warning, the duration-weighted aggregate ``mean_current_uA`` for
    that key is a meaningless intermediate value, and any downstream
    code that reads it gets garbage silently. The warning surfaces
    the problem so the caller can either:
      - re-classify phases by current range before aggregating, or
      - report by_phase results instead of by_gpio_byte for the
        affected key.

    Synthetic data does not trigger this warning because
    synthetic_cells uses GPIO_BYTE_STOP_SYNTHETIC (=8) to keep Stop
    and Idle on separate keys.
    """
    out = TraceEnergy()
    for ph in phases:
        e = phase_energy(ph)
        out.per_phase.append(PhaseEnergy(phase=ph, energy_J=e))
        out.total_energy_J += e
        out.total_duration_us += ph.duration_us

        agg = out.by_gpio_byte.setdefault(
            ph.gpio_byte, GpioByteAggregate(gpio_byte=ph.gpio_byte)
        )
        # Bug #2 fix: weight running means by duration_us, not sample count.
        # Sample-weighting only matches duration-weighting when the PPK2
        # sample rate is constant across phases — which it isn't, in the
        # presence of USB-batch boundary effects.
        #
        # Bug #1 fix: also maintain mean_power_uW (sample-level mean(V·I))
        # at the aggregate level, duration-weighted. Combined with
        # duration weighting, this preserves the strict identity:
        #     agg.mean_power_uW * agg.total_duration_us * 1e-12
        #         ≡ agg.total_energy_J
        # The mean_current_uA / mean_voltage_V fields remain for
        # reporting but should not be multiplied together for energy.
        #
        # Bug #1 piggyback: skip the weighted update if duration_us is 0
        # for this phase. A zero-duration phase has no time-integral
        # contribution to any rolling mean, and skipping it also avoids
        # the divide-by-zero that would otherwise hit on the first phase
        # of a gpio_byte when ph.duration_us == 0.
        if ph.duration_us > 0:
            prev_duration = agg.total_duration_us
            new_duration  = prev_duration + ph.duration_us
            # Bug #1 fix: prefer phase.mean_power_uW when the new
            # parse_traces populated it; otherwise reconstruct from
            # I·V (legacy fallback for old-style Phases).
            ph_power_uW = (
                ph.mean_power_uW if ph.mean_power_uW > 0.0
                else ph.mean_current_uA * ph.mean_voltage_V
            )
            agg.mean_current_uA = (
                agg.mean_current_uA * prev_duration
                + ph.mean_current_uA * ph.duration_us
            ) / new_duration
            agg.mean_voltage_V = (
                agg.mean_voltage_V * prev_duration
                + ph.mean_voltage_V * ph.duration_us
            ) / new_duration
            agg.mean_power_uW = (
                agg.mean_power_uW * prev_duration
                + ph_power_uW * ph.duration_us
            ) / new_duration
        agg.total_energy_J += e
        agg.total_duration_us += ph.duration_us
        agg.total_samples += ph.samples
        agg.n_phases += 1

    # Bug #1 (silent-bias re-review): runtime ambiguity check.
    _warn_on_ambiguous_gpio_byte(out)
    return out


# Bug #1 fix (silent-bias re-review): threshold for the "phases in
# this gpio_byte vary too much" warning. 100× is conservative —
# Idle/Stop differ by ~100,000×, ordinary compute-phase variation
# is well within 10×, so this catches the genuine aliasing case
# without false alarms from normal noise.
PHASE_CURRENT_RATIO_WARN = 100.0


def _warn_on_ambiguous_gpio_byte(out: TraceEnergy) -> None:
    """Print a stderr warning per gpio_byte whose phases span >100× in current.

    Surfaces the "Stop+Idle aliased under gpio_byte=0 in real PPK2
    captures" silent-bias risk. The aggregate mean_current_uA for
    such a key is a meaningless duration-weighted average of two
    physically different regimes; downstream tooling should re-
    classify by current range before consuming it.

    Idempotent: called once at the end of compute_trace. Writes to
    stderr (not raises) so the caller can still inspect the
    aggregate and decide what to do.
    """
    import sys
    # Group per-phase mean currents by gpio_byte
    per_gb_currents: Dict[int, List[float]] = {}
    for pe in out.per_phase:
        per_gb_currents.setdefault(pe.phase.gpio_byte, []).append(
            pe.phase.mean_current_uA
        )
    for gb, currents in per_gb_currents.items():
        # Filter out phases that contributed no current (e.g. mock
        # noise floor below 1 nA); ratio is undefined for zeros.
        nonzero = [c for c in currents if c > 0.0]
        if len(nonzero) < 2:
            continue
        ratio = max(nonzero) / min(nonzero)
        if ratio > PHASE_CURRENT_RATIO_WARN:
            print(
                f"WARNING: gpio_byte={gb} contains phases spanning "
                f"{ratio:.0f}× in mean current "
                f"(min={min(nonzero):.2f} µA, max={max(nonzero):.2f} µA). "
                f"The duration-weighted aggregate mean_current_uA "
                f"({out.by_gpio_byte[gb].mean_current_uA:.2f} µA) "
                f"is a mathematical average across heterogeneous "
                f"physics — likely Stop+Idle aliased under gpio_byte=0. "
                f"Use per_phase data or a current-range classifier "
                f"instead of by_gpio_byte for this key.",
                file=sys.stderr,
            )
