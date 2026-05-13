"""Fig 1: Energy per round (mJ) vs batch size N."""
from __future__ import annotations
import argparse
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analysis._figure_watermark import add_watermark
import numpy as np

from analysis.parse_traces import parse_trace
from analysis.compute_energy import compute_trace


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
        for d in sorted(args.traces.glob(f"{prefix}__N*__r*")):
            try:
                n = int(d.name.split("__N")[1].split("__")[0])
            except (IndexError, ValueError):
                continue
            mean, sem = _energy_per_round_for_cell(d, n)
            if mean > 0:
                ns.append(n); means.append(mean * 1000); sems.append(sem * 1000)
        if ns:
            ax.errorbar(ns, means, yerr=sems, fmt=style, color=color, label=label,
                        markersize=8, capsize=4, linewidth=2)

    for curve, color in [("bn254", "tab:cyan"), ("bls12_381", "tab:red")]:
        d = list(args.traces.glob(f"{curve}__b__N10__r*"))
        if d:
            mean, _ = _energy_per_round_for_cell(d[0], 10)
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
