"""Fig 2: Stacked phase breakdown by gpio_byte, per (curve, N).

Each stacked bar shows energy contributions from four phase categories
captured by the firmware GPIO triggers:

  - Idle       (gpio_byte = 0)  : all GPIO triggers low
  - Compute    (gpio_byte = 1)  : OneTimeSetup, Setup, Verify all collapsed
                                  here (PA0 high, PA1/PA4 low). The
                                  current firmware does NOT separate
                                  OTS / Setup / Verify into distinct
                                  gpio_byte values; if a paper figure
                                  needs that finer breakdown, firmware
                                  instrumentation has to change first.
  - ServerWait (gpio_byte = 2)  : MCU waiting on Pi server compute
  - UART burst (gpio_byte = 4)  : message transit on the UART (when
                                  the firmware enables PA4 around
                                  uart_send / uart_recv)

Two subplots: BN254 (left), BLS (right). Two-bug fix 2026-05-23:
  - Bug #3: missing cell directories now print a clear WARNING and
    skip the column instead of plotting a zero bar that silently
    misrepresents the data.
  - Bug #4: docstring no longer claims a 5-way breakdown that does
    not exist in the source data.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analysis._figure_watermark import add_watermark
import numpy as np

from analysis.parse_traces import parse_trace
from analysis.compute_energy import compute_trace


# gpio_byte coding matches firmware/amore-fw/inc/triggers.h:
#   bit 0 (PA0) = Compute
#   bit 1 (PA1) = ServerWait
#   bit 2 (PA4) = UART burst
# Pure phases below; combined bits (e.g. 3 = compute+wait) are rare
# and intentionally not plotted here. See Bug #4 docstring fix.
#
# gpio_byte=8 is OUT-OF-BAND for the firmware (whose GPIO encoding
# tops out at 7). It is reserved by synthetic_cells.py for the Stop
# ServerWait phase (Bug #1 of the 2026-05-23 re-review): the
# physical PA1 cannot stay high in Stop, so real PPK2 captures will
# see gpio_byte=0 there — synthetic data uses 8 to keep Idle and
# Stop on separate aggregation keys for unambiguous plotting.
PHASE_LABELS = {
    0: ("Idle",            "lightgray"),
    1: ("Compute",         "tab:orange"),
    2: ("ServerWait",      "tab:blue"),
    4: ("UART burst",      "tab:green"),
    8: ("Stop ServerWait", "tab:purple"),   # synthetic-only label, Bug #1 fix
}

# Sentinel returned when a cell directory is missing/empty.
# Caller can distinguish "no data" (None) from "data with zero
# contribution" ({gb: 0.0, ...}) — Bug #3 fix.
_NO_DATA: None = None


def _phase_energies_mean(cell_dir: Path) -> dict[int, float] | None:
    """Return mean energy_J per gpio_byte across replicas in the cell.

    Returns None if the cell_dir doesn't exist OR contains no
    ``run_*.csv`` files. Previously returned an empty dict in both
    those cases, causing the plot to silently emit a zero-height bar
    that looked indistinguishable from "ServerWait collapsed to zero
    energy" — a real result and a missing-data result rendered the
    same. Now we return a sentinel and the caller skips the column
    with a visible message.
    """
    if not cell_dir.exists():
        print(f"  WARNING: cell directory missing: {cell_dir}",
              file=sys.stderr)
        return _NO_DATA
    csvs = sorted(cell_dir.glob("run_*.csv"))
    if not csvs:
        print(f"  WARNING: no run_*.csv files in {cell_dir}",
              file=sys.stderr)
        return _NO_DATA
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
        # gather per-N phase totals; track missing cells so the bar
        # for them is drawn distinctly (Bug #3 fix).
        data: dict[int, dict[int, float] | None] = {}  # n → gb→J | None
        missing_ns: list[int] = []
        for n in Ns:
            cell_dir = args.traces / f"{prefix}__a__N{n}__r3"
            cell_data = _phase_energies_mean(cell_dir)
            data[n] = cell_data
            if cell_data is _NO_DATA:
                missing_ns.append(n)

        x = np.arange(len(Ns))
        width = 0.55

        bottoms = np.zeros(len(Ns))
        for gb in sorted(PHASE_LABELS.keys()):
            # Bug #3 fix: a missing cell contributes np.nan to that
            # column so matplotlib leaves a visible gap, NOT zero.
            heights = np.array([
                data[n].get(gb, 0.0) * 1000 if data[n] is not _NO_DATA else np.nan
                for n in Ns
            ])
            if np.nansum(heights) == 0:
                continue
            label, color = PHASE_LABELS[gb]
            ax.bar(x, np.where(np.isnan(heights), 0.0, heights),
                   width, bottom=bottoms, label=label, color=color,
                   edgecolor="white", linewidth=0.8)
            bottoms = np.where(np.isnan(heights), bottoms, bottoms + heights)

        # Annotate missing columns with a label so the reader can't
        # mistake them for a stack that summed to zero.
        for i, n in enumerate(Ns):
            if data[n] is _NO_DATA:
                ax.text(
                    x[i], 0,
                    "no data",
                    ha="center", va="bottom",
                    color="red", fontsize=9, fontweight="bold",
                )

        ax.set_xticks(x)
        ax.set_xticklabels([f"N={n}" for n in Ns])
        ax.set_ylabel("Energy (mJ)" if curve_label == "BN254" else "")
        title_suffix = ""
        if missing_ns:
            title_suffix = f"  ({len(missing_ns)} N missing)"
        ax.set_title(
            f"{curve_label} — energy breakdown by phase{title_suffix}",
            fontsize=12,
        )
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Fig 2: Per-batch energy breakdown by phase", fontsize=13)
    fig.tight_layout()
    add_watermark(fig)

    fig.savefig(args.out, dpi=120)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
