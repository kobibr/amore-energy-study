"""Figure 1: Energy per round vs batch size N — 2-panel (BN254 / BLS12_381).

Three lines per panel:
  - AmorE Mode A baseline           (busy-wait ServerWait at 55 mA)
  - AmorE Mode A + Stop optimization (proposed; Stop mode at 0.5 µA)
  - Direct pairing × N              (linear, slope = energy per pairing)

Linear y-axis per panel, independent ranges.

Source data: analysis/baseline_data.py (measured timings from
doc/AmorE_*_Results.txt; energy = time × I × V).
"""
from __future__ import annotations
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analysis._figure_watermark import add_watermark
from analysis.baseline_data import (
    amore_with_ots_per_round_mJ,
    direct_pairing_energy_mJ,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("report/figures/fig1_energy_vs_n.pdf"))
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    Ns = np.arange(1, 51)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))

    for ax, curve, ax_title, color_amore, color_direct in [
        (axes[0], "BN254",     "BN254",     "tab:blue",   "tab:red"),
        (axes[1], "BLS12_381", "BLS12-381", "tab:orange", "tab:green"),
    ]:
        # AmorE baseline (busy-wait ServerWait)
        e_baseline = [amore_with_ots_per_round_mJ(curve, int(n), stop_mode=False) for n in Ns]
        # AmorE with Stop mode optimization
        e_stop     = [amore_with_ots_per_round_mJ(curve, int(n), stop_mode=True)  for n in Ns]
        # Direct pairing — constant per pairing (not per round)
        e_direct_one = direct_pairing_energy_mJ(curve)

        ax.plot(Ns, e_baseline, "-",  color=color_amore, linewidth=2.2,
                label="AmorE Mode A (baseline)")
        ax.plot(Ns, e_stop, "--", color=color_amore, linewidth=1.8, alpha=0.8,
                label="AmorE Mode A + Stop (proposed)")
        # Direct pairing reference line — it's the same per pairing regardless of N
        ax.axhline(e_direct_one, linestyle=":", color=color_direct, linewidth=2.0,
                   label=f"Direct pairing ({e_direct_one:.1f} mJ)")

        ax.set_title(ax_title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Batch size N", fontsize=11)
        ax.set_ylabel("Energy per round (mJ)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)
        # Linear y, independent range. Let matplotlib auto-pick.
        ax.set_ylim(bottom=0)

    fig.suptitle(
        "Figure 1: Energy per round vs batch size N on STM32F407 Cortex-M4\n"
        "BN254: AmorE costs 1.51× a single direct pairing. "
        "BLS12-381: AmorE costs 1.22× three direct pairings (the closer\n"
        "analogue, since each AmorE round contains three pairing-equivalent "
        "verification steps on the server). Trade-off:\n"
        "33× less SRAM, 3× less Flash, input privacy, cheating detection.",
        fontsize=11,
    )
    add_watermark(fig, scenario="BASELINE")
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
