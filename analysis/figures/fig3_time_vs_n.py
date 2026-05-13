"""Figure 3: Time per round vs batch size N — 2-panel.

Same structure as Figure 1, but y-axis = time (ms). Makes the speed
trade-off explicit, complements the energy trade-off in Figure 1.

Three lines per panel:
  - AmorE Mode A baseline (includes ServerWait time)
  - AmorE Mode A compute only (Blind + Verify, no wait)
  - Direct pairing — constant per pairing
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
    amore_round_time_ms, amore_serverwait_ms,
    OTS_MS, DIRECT_PAIRING_MS,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("report/figures/fig3_time_vs_n.pdf"))
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    Ns = np.arange(1, 51)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))

    for ax, curve, ax_title, color_amore, color_direct in [
        (axes[0], "BN254",     "BN254",     "tab:blue",   "tab:red"),
        (axes[1], "BLS12_381", "BLS12-381", "tab:orange", "tab:green"),
    ]:
        # AmorE total per-round = compute + ServerWait + amortized OTS
        compute_only = [amore_round_time_ms(curve, int(n)) for n in Ns]
        wait_ms = amore_serverwait_ms(curve)
        with_wait = [
            amore_round_time_ms(curve, int(n)) + wait_ms + OTS_MS[curve] / int(n)
            for n in Ns
        ]
        compute_with_ots = [
            amore_round_time_ms(curve, int(n)) + OTS_MS[curve] / int(n)
            for n in Ns
        ]
        direct_ms = DIRECT_PAIRING_MS[curve]

        ax.plot(Ns, with_wait, "-", color=color_amore, linewidth=2.2,
                label="AmorE total (compute + ServerWait + amort. OTS)")
        ax.plot(Ns, compute_with_ots, "--", color=color_amore, linewidth=1.8, alpha=0.7,
                label="AmorE compute only + amort. OTS")
        ax.axhline(direct_ms, linestyle=":", color=color_direct, linewidth=2.0,
                   label=f"Direct pairing ({direct_ms:.1f} ms)")

        ax.set_title(ax_title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Batch size N", fontsize=11)
        ax.set_ylabel("Time per round (ms)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)
        ax.set_ylim(bottom=0)

    fig.suptitle(
        "Figure 3: Time per round vs batch size N on STM32F407 Cortex-M4\n"
        "AmorE per-round time is dominated by ServerWait (UART + Pi pairing); "
        "compute-only ratio is 1.5× direct (BN254) or 3.6× (BLS, since 1919 ms vs 523 ms)",
        fontsize=11,
    )
    add_watermark(fig, scenario="BASELINE")
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
