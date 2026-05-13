"""Fig 3: AmorE per-round vs direct pairing energy.

For each curve, show:
  - AmorE per-round energy at N=30 (best amortization in our sweep)
  - Direct pairing energy (Mode B, per-pairing average)
  - Ratio annotation: AmorE / Direct
"""
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


def _e_per_round_mode_a(cell_dir: Path, n: int) -> tuple[float, float]:
    csvs = sorted(cell_dir.glob("run_*.csv"))
    vals = []
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        protocol_E = sum(agg.total_energy_J for gb, agg in te.by_gpio_byte.items() if gb != 0)
        vals.append(protocol_E / n)
    if not vals:
        return 0.0, 0.0
    return float(np.mean(vals)), float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0


def _e_per_pairing_mode_b(cell_dir: Path, n: int) -> tuple[float, float]:
    csvs = sorted(cell_dir.glob("run_*.csv"))
    vals = []
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        # In Mode B, gpio_byte 1 = active pairings. Divide by n.
        compute_E = te.by_gpio_byte.get(1)
        if compute_E:
            vals.append(compute_E.total_energy_J / n)
    if not vals:
        return 0.0, 0.0
    return float(np.mean(vals)), float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--traces", type=Path, default=Path("measurement/traces"))
    p.add_argument("--out", type=Path, default=Path("figures/fig3_mode_comparison.png"))
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    curves = ["BN254", "BLS12_381"]
    a_means, a_sems = [], []
    b_means, b_sems = [], []
    for curve in curves:
        prefix = curve.lower()
        a_mean, a_sem = _e_per_round_mode_a(args.traces / f"{prefix}__a__N30__r3", n=30)
        b_mean, b_sem = _e_per_pairing_mode_b(args.traces / f"{prefix}__b__N10__r3", n=10)
        a_means.append(a_mean * 1000); a_sems.append(a_sem * 1000)
        b_means.append(b_mean * 1000); b_sems.append(b_sem * 1000)

    x = np.arange(len(curves))
    width = 0.35

    fig, ax = plt.subplots(figsize=(8, 5.5))
    bars_a = ax.bar(x - width/2, a_means, width, yerr=a_sems, capsize=4,
                    label="AmorE @ N=30 (per round)", color="tab:orange", edgecolor="white")
    bars_b = ax.bar(x + width/2, b_means, width, yerr=b_sems, capsize=4,
                    label="Direct pairing (per pairing)", color="tab:cyan", edgecolor="white")

    # Annotate ratios
    for i, (a, b) in enumerate(zip(a_means, b_means)):
        if b > 0:
            ratio = a / b
            ax.text(i, max(a, b) * 1.05, f"AmorE/Direct = {ratio:.2f}×",
                    ha="center", fontsize=10, fontweight="bold",
                    color="darkgreen" if ratio < 1 else "darkred")

    ax.set_xticks(x)
    ax.set_xticklabels(curves)
    ax.set_ylabel("Energy (mJ)")
    ax.set_title("Fig 3: AmorE per-round vs direct pairing energy", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    add_watermark(fig)

    fig.savefig(args.out, dpi=120)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
