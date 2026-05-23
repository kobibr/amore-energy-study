"""Fig 1: Energy per round (mJ) vs batch size N."""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analysis._figure_watermark import add_watermark
import numpy as np

from analysis.parse_traces import parse_trace
from analysis.compute_energy import compute_trace


# Bug #1 fix: glob "*__N*__r*" matches both `..__N30__r3` AND
# `..__N30__r3__stop`, because the trailing `*` is greedy. We need a
# strict regex that ends right after the replica number.
_CELL_NAME_RE = re.compile(r"^(?P<prefix>.+)__N(?P<n>\d+)__r(?P<r>\d+)$")


def _energy_per_round_for_cell(cell_dir: Path, n: int):
    csvs = sorted(cell_dir.glob("run_*.csv"))
    e_per_round = []
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        protocol_E = sum(agg.total_energy_J
                         for gb, agg in te.by_gpio_byte.items() if gb != 0)
        e_per_round.append(protocol_E / n)
    if not e_per_round:
        return 0.0, 0.0
    mean = float(np.mean(e_per_round))
    sem = float(np.std(e_per_round, ddof=1) / np.sqrt(len(e_per_round))) if len(e_per_round) > 1 else 0.0
    return mean, sem


def _e_per_pairing_for_mode_b(cell_dir: Path, n: int):
    """Mode B per-pairing energy = compute (gb=1) only, divided by N.

    Bug #2 fix: previously this used the Mode-A formula (sum of all
    non-idle gpio_bytes), but Mode B has no ServerWait or UART phase,
    so the right definition is just compute. Aligns with
    plot_mode_comparison.py's `_e_per_pairing_mode_b`.
    """
    csvs = sorted(cell_dir.glob("run_*.csv"))
    vals = []
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        compute_E = te.by_gpio_byte.get(1)
        if compute_E:
            vals.append(compute_E.total_energy_J / n)
    if not vals:
        return 0.0
    return float(np.mean(vals))


def _strict_cell_dirs(traces: Path, prefix: str):
    """Yield cells matching ``<prefix>__N<n>__r<r>`` STRICTLY.

    Bug #1 fix: skip anything with extra suffix (e.g. ``__stop``).
    """
    for d in sorted(traces.glob(f"{prefix}__N*")):
        if not d.is_dir():
            continue
        m = _CELL_NAME_RE.match(d.name)
        if m is None:
            continue
        if m.group("prefix") != prefix:
            continue
        yield d, int(m.group("n"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--traces", type=Path, default=Path("measurement/traces"))
    p.add_argument("--out", type=Path, default=Path("figures/fig1_energy_per_round.png"))
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    cases = [
        ("BN254 Mode A",     "bn254__a",     "tab:blue",   "o-"),
        ("BLS12_381 Mode A", "bls12_381__a", "tab:orange", "s-"),
    ]
    fig, ax = plt.subplots(figsize=(8, 5))
    for label, prefix, color, style in cases:
        ns, means, sems = [], [], []
        # Bug #1 fix: use the strict matcher so __stop cells don't leak
        # into the baseline curves.
        for d, n in _strict_cell_dirs(args.traces, prefix):
            mean, sem = _energy_per_round_for_cell(d, n)
            if mean > 0:
                ns.append(n); means.append(mean * 1000); sems.append(sem * 1000)
        if ns:
            ax.errorbar(ns, means, yerr=sems, fmt=style, color=color, label=label,
                        markersize=8, capsize=4, linewidth=2)

    for curve, color in [("bn254", "tab:cyan"), ("bls12_381", "tab:red")]:
        # Bug #3 fix: sort the glob (kernel order is not deterministic),
        # warn if multiple match so we don't silently pick one.
        candidates = sorted(args.traces.glob(f"{curve}__b__N10__r*"))
        # Bug #1 fix (also applies here): filter __stop from Mode B too.
        candidates = [
            d for d in candidates
            if d.is_dir() and _CELL_NAME_RE.match(d.name) is not None
        ]
        if not candidates:
            continue
        if len(candidates) > 1:
            print(
                f"WARN: {len(candidates)} Mode-B cells matched "
                f"{curve}__b__N10__r*; using {candidates[-1].name} "
                f"(highest replica). Others ignored.",
                file=sys.stderr,
            )
        # Bug #2 fix: use the compute-only formula (matches Fig 3) for
        # the Mode B reference line.
        mean = _e_per_pairing_for_mode_b(candidates[-1], 10)
        if mean > 0:
            ax.axhline(mean * 1000, color=color, linestyle="--", alpha=0.7,
                       label=f"{curve.upper()} Mode B (direct)")

    ax.set_xscale("log")
    ax.set_xlabel("Batch size N", fontsize=12)
    ax.set_ylabel("Energy per round (mJ)", fontsize=12)
    ax.set_title("AmorE amortization: per-round energy vs batch size", fontsize=13)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=10, loc="upper right")
    fig.tight_layout()
    add_watermark(fig)

    fig.savefig(args.out, dpi=120)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
