"""Fig 2: Stacked phase breakdown by gpio_byte, per (curve, N).

Each stacked bar shows OneTimeSetup, Setup, ServerWait, Verify, Idle energy
contributions to a single AmorE batch. Two subplots: BN254 (left), BLS (right).
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


# gpio_byte 0 = Idle, 1 = Compute (Setup+Verify+OTS), 2 = ServerWait
PHASE_LABELS = {
    0: ("Idle",       "lightgray"),
    1: ("Compute",    "tab:orange"),
    2: ("ServerWait", "tab:blue"),
    4: ("UART burst", "tab:green"),
}


def _phase_energies_mean(cell_dir: Path) -> dict[int, float]:
    """Return mean energy_J per gpio_byte across replicas in the cell."""
    csvs = sorted(cell_dir.glob("run_*.csv"))
    if not csvs:
        return {}
    accum: dict[int, list[float]] = {}
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        for gb, agg in te.by_gpio_byte.items():
            accum.setdefault(gb, []).append(agg.total_energy_J)
    return {gb: float(np.mean(es)) for gb, es in accum.items()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--traces", type=Path, default=Path("measurement/traces"))
    p.add_argument("--out", type=Path, default=Path("figures/fig2_phase_breakdown.png"))
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    curves = [("BN254", "bn254"), ("BLS12_381", "bls12_381")]
    Ns = [1, 3, 10, 30]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=False)

    for ax, (curve_label, prefix) in zip(axes, curves):
        # gather per-N phase totals
        data: dict[int, dict[int, float]] = {}  # n → gb → energy
        for n in Ns:
            cell_dir = args.traces / f"{prefix}__a__N{n}__r3"
            data[n] = _phase_energies_mean(cell_dir)

        x = np.arange(len(Ns))
        width = 0.55

        bottoms = np.zeros(len(Ns))
        for gb in sorted(PHASE_LABELS.keys()):
            heights = np.array([data[n].get(gb, 0.0) * 1000 for n in Ns])  # mJ
            if heights.sum() == 0:
                continue
            label, color = PHASE_LABELS[gb]
            ax.bar(x, heights, width, bottom=bottoms, label=label, color=color,
                   edgecolor="white", linewidth=0.8)
            bottoms += heights

        ax.set_xticks(x)
        ax.set_xticklabels([f"N={n}" for n in Ns])
        ax.set_ylabel("Energy (mJ)" if curve_label == "BN254" else "")
        ax.set_title(f"{curve_label} — energy breakdown by phase", fontsize=12)
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Fig 2: Per-batch energy breakdown by phase", fontsize=13)
    fig.tight_layout()
    add_watermark(fig)

    fig.savefig(args.out, dpi=120)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
