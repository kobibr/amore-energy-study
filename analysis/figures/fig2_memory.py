"""Figure 2: Memory footprint comparison.

Two sub-figures:
  2a: BN254 — AmorE client vs RELIC pairing, both measured. Headline:
      "33× less SRAM, 3× less Flash" on the same hardware.
  2b: BLS12-381 — AmorE client measured; RELIC NOT MEASURED on this
      hardware. Includes an estimated-upper-bound annotation.

Saves as:
  report/figures/fig2a_memory_bn254.pdf
  report/figures/fig2b_memory_bls.pdf

Source data: doc/AmorE_BN128_Results.txt §11.2 (BN254),
             doc/AmorE_BLS12_381_Results.txt §2 (BLS AmorE only).
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
    FLASH_KB, SRAM_KB,
    RELIC_BLS_FLASH_ESTIMATE_RANGE, RELIC_BLS_SRAM_ESTIMATE_RANGE,
)


def fig2a_bn254(out_path: Path) -> None:
    """Figure 2a: BN254 — measured comparison."""
    fig, ax = plt.subplots(figsize=(8, 5))

    categories = ["Flash", "SRAM"]
    amore_vals = [FLASH_KB["AmorE_BN254"], SRAM_KB["AmorE_BN254"]]
    relic_vals = [FLASH_KB["RELIC_BN254"], SRAM_KB["RELIC_BN254"]]

    x = np.arange(len(categories))
    width = 0.35

    bars_a = ax.bar(x - width/2, amore_vals, width, label="AmorE client",
                    color="tab:blue", edgecolor="white")
    bars_r = ax.bar(x + width/2, relic_vals, width, label="RELIC pairing",
                    color="tab:red", edgecolor="white")

    # Annotate each bar with its value
    for bars in (bars_a, bars_r):
        for b in bars:
            h = b.get_height()
            ax.text(b.get_x() + b.get_width()/2, h + 2,
                    f"{h:.1f} KB", ha="center", va="bottom", fontsize=9)

    # Ratio annotations
    for i, cat in enumerate(categories):
        ratio = relic_vals[i] / amore_vals[i]
        ax.annotate(
            f"{ratio:.1f}× lighter",
            xy=(x[i], max(amore_vals[i], relic_vals[i])),
            xytext=(x[i], max(amore_vals[i], relic_vals[i]) + 12),
            ha="center", fontsize=11, color="darkgreen", fontweight="bold",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=11)
    ax.set_ylabel("Memory usage (KB)", fontsize=11)
    ax.set_title("Figure 2a: Memory footprint — BN254\n"
                 "AmorE client vs RELIC pp_map_oatep_k12 (measured, same hardware)",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(0, max(relic_vals) * 1.25)

    add_watermark(fig, scenario="BASELINE")
    fig.tight_layout()
    fig.savefig(out_path)
    print(f"  ✓ saved {out_path}")


def fig2b_bls(out_path: Path) -> None:
    """Figure 2b: BLS12-381 — AmorE measured + estimated RELIC band."""
    fig, ax = plt.subplots(figsize=(8, 5))

    categories = ["Flash", "SRAM"]
    amore_vals = [FLASH_KB["AmorE_BLS12_381"], SRAM_KB["AmorE_BLS12_381"]]

    x = np.arange(len(categories))
    width = 0.35

    bars_a = ax.bar(x - width/2, amore_vals, width, label="AmorE client (measured)",
                    color="tab:orange", edgecolor="white")

    # RELIC estimated bars — show as hatched range
    relic_lo = [RELIC_BLS_FLASH_ESTIMATE_RANGE[0], RELIC_BLS_SRAM_ESTIMATE_RANGE[0]]
    relic_hi = [RELIC_BLS_FLASH_ESTIMATE_RANGE[1], RELIC_BLS_SRAM_ESTIMATE_RANGE[1]]
    relic_mid = [(lo + hi) / 2 for lo, hi in zip(relic_lo, relic_hi)]

    bars_r = ax.bar(x + width/2, relic_mid, width,
                    yerr=[(m - lo) for m, lo in zip(relic_mid, relic_lo)],
                    label="RELIC pairing (estimated, not measured)",
                    color="lightgray", edgecolor="darkgray", hatch="//",
                    capsize=8, error_kw={"linewidth": 2})

    for b, v in zip(bars_a, amore_vals):
        ax.text(b.get_x() + b.get_width()/2, v + 3,
                f"{v:.1f} KB", ha="center", va="bottom", fontsize=9)
    for b, lo, hi in zip(bars_r, relic_lo, relic_hi):
        ax.text(b.get_x() + b.get_width()/2, hi + 3,
                f"{lo}–{hi} KB", ha="center", va="bottom", fontsize=9, style="italic")

    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=11)
    ax.set_ylabel("Memory usage (KB)", fontsize=11)
    ax.set_title("Figure 2b: Memory footprint — BLS12-381\n"
                 "AmorE measured; RELIC scaled from BN254 (12/8-limb), "
                 "not built on this hardware", fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(0, max(relic_hi) * 1.15)

    add_watermark(fig, scenario="BASELINE")
    fig.tight_layout()
    fig.savefig(out_path)
    print(f"  ✓ saved {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=Path("report/figures"))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fig2a_bn254(args.out_dir / "fig2a_memory_bn254.pdf")
    fig2b_bls(args.out_dir / "fig2b_memory_bls.pdf")


if __name__ == "__main__":
    main()
