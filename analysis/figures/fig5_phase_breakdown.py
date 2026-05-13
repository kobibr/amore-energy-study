"""Figure 5: Per-phase energy breakdown — stacked bars per N, per curve.

Each round of AmorE Mode A decomposes into four phases:
  - Setup   : OneTimeSetup amortized contribution (OTS / N)
  - Blind   : Blind operation (50% of compute, per C4 limitation)
  - ServerWait : sleeping/waiting while helper computes
  - Verify  : Verify operation (50% of compute, per C4 limitation)

The Setup/Verify 50/50 split is a known limitation (per C4 of the
original code review — only 3 GPIO trigger lines available, so Blind
and Verify can't be split-measured into Setup/Verify sub-phases).

Baseline (busy-wait ServerWait) shown; Stop-mode variant is in Fig 1.
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
    OTS_MS, energy_from_time_ms, I_ACTIVE_MA, V_NOMINAL,
)


def phase_energies_mJ(curve, n):
    """Return (setup, blind, serverwait, verify) per-round energies."""
    compute_ms_total = amore_round_time_ms(curve, n)
    wait_ms = amore_serverwait_ms(curve)

    # Setup phase: OTS amortized
    e_setup = energy_from_time_ms(OTS_MS[curve]) / n

    # Compute phase: Blind + Verify, split 50/50 per C4 limitation
    e_compute_half = energy_from_time_ms(compute_ms_total / 2)
    e_blind = e_compute_half
    e_verify = e_compute_half

    # ServerWait at I_ACTIVE (baseline busy-wait)
    e_wait = energy_from_time_ms(wait_ms)

    return e_setup, e_blind, e_wait, e_verify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path("report/figures/fig5_phase_breakdown.pdf"))
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))
    Ns = [1, 3, 10, 30, 50]

    colors = {
        "Setup (amort. OTS)": "#9b59b6",
        "Blind":              "#3498db",
        "ServerWait":         "#e67e22",
        "Verify":             "#2ecc71",
    }

    for ax, curve, ax_title in [
        (axes[0], "BN254",     "BN254"),
        (axes[1], "BLS12_381", "BLS12-381"),
    ]:
        setups   = []
        blinds   = []
        waits    = []
        verifies = []
        for n in Ns:
            s, b, w, v = phase_energies_mJ(curve, n)
            setups.append(s); blinds.append(b); waits.append(w); verifies.append(v)

        x = np.arange(len(Ns))
        bottom = np.zeros(len(Ns))

        for label, vals in [
            ("Setup (amort. OTS)", setups),
            ("Blind",              blinds),
            ("ServerWait",         waits),
            ("Verify",             verifies),
        ]:
            ax.bar(x, vals, bottom=bottom, label=label, color=colors[label],
                   edgecolor="white", linewidth=0.5)
            bottom = bottom + np.asarray(vals)

        # Annotate totals on top
        for i, total in enumerate(bottom):
            ax.text(i, total + max(bottom)*0.02, f"{total:.0f}",
                    ha="center", va="bottom", fontsize=8)

        ax.set_xticks(x)
        ax.set_xticklabels([f"N={n}" for n in Ns], fontsize=10)
        ax.set_title(ax_title, fontsize=12, fontweight="bold")
        ax.set_ylabel("Energy per round (mJ)", fontsize=11)
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle(
        "Figure 5: Per-phase energy breakdown of AmorE Mode A (baseline ServerWait)\n"
        "Setup/Verify shown as 50/50 split of compute time — only 3 GPIO trigger lines\n"
        "available, so Blind and Verify cannot yet be split-measured (see C4 of the code review).",
        fontsize=10,
    )

    add_watermark(fig, scenario="BASELINE")
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
