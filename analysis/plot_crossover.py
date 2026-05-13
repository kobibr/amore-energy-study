"""Fig 4: AmorE crossover — baseline firmware vs proposed Stop-mode optimization.

This figure tells the central story of the AmorE energy thesis:

  - In the baseline firmware (busy-wait UART recv during ServerWait), the
    MCU stays at ~55 mA throughout — and AmorE per-round energy stays
    *above* the cost of a single direct pairing. No crossover.

  - In the proposed optimization (MCU enters Stop mode during ServerWait,
    dropping to ~0.5 µA), the ServerWait phase contributes ~negligible
    energy — and a crossover N* emerges where AmorE finally beats direct
    pairing.

The figure is a 2x2 grid: rows = scenarios (BASELINE / WITH_STOP),
columns = curves (BN254 / BLS12_381). Each subplot plots E/round(N) for
AmorE with horizontal threshold lines at k×E_direct_pairing.

Source data:
  - measurement/traces/<curve>__a__N<n>__r3/          (baseline cells)
  - measurement/traces/<curve>__a__N<n>__r3__stop/    (stop-mode cells)
  - measurement/traces/<curve>__b__N10__r3/           (direct pairing)
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
from analysis.sleep_model import BatchModel, find_crossover


def _fit_batch_model(cell_dir_n: Path, n: int) -> BatchModel | None:
    """Fit BatchModel from a single cell at known N.

    Strategy: parse all replicas, average per-phase energies, attribute
    to (ots, per_round_compute, per_round_wait).

    Wait-energy attribution is gpio_byte-aware:
      - baseline cells:  gpio_byte=2 is ServerWait
      - stop-mode cells: ServerWait is in gpio_byte=0 phases (after a
                         gpio_byte=1 round-start), but those samples have
                         current << I_IDLE so we can't trivially detect
                         them by gpio_byte alone.

    For now we use a simpler robust approach: sum ALL non-compute, non-OTS
    energy as the per-round "non-compute" cost, attributing the *first*
    gpio_byte=1 phase to OTS and everything else to per-round.

    NOTE: This is a brittle approach; will be replaced
    it with an algebraic fit from N=1 and N=30 cells (truly robust).
    Returning None if the cell is missing keeps Fig 4 partial-resilient.
    """
    csvs = sorted(cell_dir_n.glob("run_*.csv"))
    if not csvs:
        return None

    ots_es = []
    per_round_es_compute = []
    per_round_es_wait = []

    for c in csvs:
        phases = parse_trace(c)
        if not phases:
            continue
        te = compute_trace(phases)

        # OTS = first gpio_byte=1 phase
        first_compute = next((p for p in te.per_phase if p.phase.gpio_byte == 1), None)
        ots_e = first_compute.energy_J if first_compute else 0.0
        ots_es.append(ots_e)

        # All compute energy (gpio_byte=1) minus OTS, divided by n
        compute_agg = te.by_gpio_byte.get(1)
        compute_total_e = compute_agg.total_energy_J if compute_agg else 0.0
        per_round_es_compute.append((compute_total_e - ots_e) / n)

        # Non-compute, non-OTS energy: everything else (idle + serverwait).
        # In baseline that's gb=2; in stop-mode that's mostly gb=0 between
        # rounds. We sum both and treat as "wait" for the model.
        non_compute_total = te.total_energy_J - compute_total_e
        per_round_es_wait.append(non_compute_total / n)

    if not ots_es:
        return None

    return BatchModel(
        e_setup_per_round_J=float(np.mean(per_round_es_compute)) * 0.5,
        e_verify_per_round_J=float(np.mean(per_round_es_compute)) * 0.5,
        e_serverwait_per_round_J=float(np.mean(per_round_es_wait)),
        e_one_time_setup_J=float(np.mean(ots_es)),
    )


def _e_direct_pairing(cell_dir_b: Path, n: int) -> float:
    csvs = sorted(cell_dir_b.glob("run_*.csv"))
    vals = []
    for c in csvs:
        phases = parse_trace(c)
        te = compute_trace(phases)
        compute_E = te.by_gpio_byte.get(1)
        if compute_E:
            vals.append(compute_E.total_energy_J / n)
    return float(np.mean(vals)) if vals else 0.0


def _plot_one_scenario(ax, traces_dir: Path, curve_prefix: str,
                       scenario_suffix: str, scenario_label: str,
                       color: str) -> str:
    """Render one subplot. Returns a status string for the title.

    scenario_suffix = "" for baseline, "__stop" for Stop-mode cells.
    """
    cell_dir = traces_dir / f"{curve_prefix}__a__N30__r3{scenario_suffix}"
    direct_dir = traces_dir / f"{curve_prefix}__b__N10__r3"

    m = _fit_batch_model(cell_dir, n=30)
    e_direct = _e_direct_pairing(direct_dir, n=10)

    if m is None:
        ax.text(0.5, 0.5, f"Missing cell:\n{cell_dir.name}",
                ha="center", va="center", transform=ax.transAxes)
        return "MISSING"

    Ns = np.arange(1, 101)
    e_curve = np.array([m.e_per_round(int(n)) for n in Ns]) * 1000  # mJ

    ax.plot(Ns, e_curve, "-", color=color, linewidth=2.5,
            label=f"AmorE @ N (model)")

    # Asymptote
    ax.axhline(m.asymptote() * 1000, color=color, linestyle=":", alpha=0.6,
               label=f"asymptote ({m.asymptote()*1000:.1f} mJ)")

    # Threshold lines + crossover annotations
    has_crossover = False
    for k, ls in [(1, "--"), (3, "-.")]:
        threshold = k * e_direct * 1000
        ax.axhline(threshold, color="gray", linestyle=ls, alpha=0.8,
                   label=f"k={k} × direct ({threshold:.1f} mJ)")
        n_star = find_crossover(m, e_direct, k=k, n_max=200)
        if n_star is not None and 1 <= n_star <= 100:
            has_crossover = True
            ax.axvline(n_star, color="red" if k == 1 else "darkred",
                       linestyle=":", alpha=0.5)
            ax.annotate(f"N*={n_star} (k={k})",
                        xy=(n_star, threshold),
                        xytext=(n_star + 5, threshold * 1.15),
                        fontsize=8, color="red" if k == 1 else "darkred",
                        arrowprops=dict(arrowstyle="->", color="red", alpha=0.5))

    ax.set_xscale("log")
    ax.set_xlabel("Batch size N")
    ax.set_ylabel("Energy per round (mJ)")
    ax.set_yscale("log")  # log Y so we can see both 0.0X mJ (stop) and 100s of mJ (baseline)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7, loc="best")

    return "✓ crossover found" if has_crossover else "NO crossover in [1,100]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, default=Path("measurement/traces"))
    ap.add_argument("--out", type=Path, default=Path("figures/fig4_crossover.png"))
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)

    # Row 0: BASELINE — busy-wait firmware
    s00 = _plot_one_scenario(axes[0, 0], args.traces, "bn254",
                              "", "BASELINE", "tab:blue")
    axes[0, 0].set_title(f"BN254 — BASELINE (busy-wait UART at 55 mA)\n{s00}",
                          fontsize=11)

    s01 = _plot_one_scenario(axes[0, 1], args.traces, "bls12_381",
                              "", "BASELINE", "tab:orange")
    axes[0, 1].set_title(f"BLS12_381 — BASELINE (busy-wait UART at 55 mA)\n{s01}",
                          fontsize=11)

    # Row 1: WITH_STOP — proposed optimization
    s10 = _plot_one_scenario(axes[1, 0], args.traces, "bn254",
                              "__stop", "WITH_STOP", "tab:green")
    axes[1, 0].set_title(f"BN254 — WITH_STOP (proposed: 0.5 µA during ServerWait)\n{s10}",
                          fontsize=11)

    s11 = _plot_one_scenario(axes[1, 1], args.traces, "bls12_381",
                              "__stop", "WITH_STOP", "tab:red")
    axes[1, 1].set_title(f"BLS12_381 — WITH_STOP (proposed: 0.5 µA during ServerWait)\n{s11}",
                          fontsize=11)

    fig.suptitle(
        "Fig 4: AmorE crossover — current firmware (top) vs proposed Stop-mode optimization (bottom)\n"
        "Top row shows AmorE losing to direct pairing; bottom row shows where the win lives.",
        fontsize=13,
    )

    add_watermark(fig)
    fig.tight_layout()
    fig.savefig(args.out, dpi=120)
    print(f"  ✓ saved {args.out}")


if __name__ == "__main__":
    main()
