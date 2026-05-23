"""Figure 4: Crossover region in (N, service-interval) plane - PLACEHOLDER."""
from __future__ import annotations
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analysis.baseline_data import (
    IDD_STOP_RANGE_UA, E_WAKEUP_RANGE_UJ,
    OTS_MS, V_NOMINAL,
    amore_round_time_ms, amore_serverwait_ms,
    amore_with_ots_per_round_mJ, direct_pairing_energy_mJ,
    DIRECT_PAIRING_MS,
)


def amore_active_time_s(curve, n):
    t_ms = OTS_MS[curve] + n * (amore_round_time_ms(curve, n) + amore_serverwait_ms(curve, server="real"))
    return t_ms / 1000.0


def amore_active_energy_mJ(curve, n):
    return n * amore_with_ots_per_round_mJ(curve, n, stop_mode=True, server="real")


def direct_active_time_s(curve, n):
    return n * DIRECT_PAIRING_MS[curve] / 1000.0


def direct_active_energy_mJ(curve, n):
    return n * direct_pairing_energy_mJ(curve)


def sleep_energy_mJ(t_sleep_s, idd_stop_uA):
    return max(0.0, t_sleep_s) * idd_stop_uA * 1e-6 * V_NOMINAL * 1e3


def total_session_energy_mJ(curve, n, T_s, idd_stop_uA, e_wakeup_uJ, strategy):
    if strategy == "amore":
        e_active = amore_active_energy_mJ(curve, n)
        t_active = amore_active_time_s(curve, n)
        n_wakes = 1
    else:
        e_active = direct_active_energy_mJ(curve, n)
        t_active = direct_active_time_s(curve, n)
        n_wakes = n
    t_sleep = T_s - t_active
    if t_sleep < 0:
        return float("inf")
    return e_active + sleep_energy_mJ(t_sleep, idd_stop_uA) + n_wakes * e_wakeup_uJ / 1000.0


def find_crossover_T(curve, n, idd_stop_uA, e_wakeup_uJ, T_min=0.1, T_max=3600.0, steps=200):
    t_min = max(T_min, amore_active_time_s(curve, n) * 1.01,
                direct_active_time_s(curve, n) * 1.01)
    if t_min >= T_max:
        return float("nan")
    Ts = np.logspace(np.log10(t_min), np.log10(T_max), steps)
    for T in Ts:
        e_a = total_session_energy_mJ(curve, n, T, idd_stop_uA, e_wakeup_uJ, "amore")
        e_d = total_session_energy_mJ(curve, n, T, idd_stop_uA, e_wakeup_uJ, "direct")
        if e_a < e_d:
            return T
    return float("nan")


def add_placeholder_watermark(fig):
    fig.text(0.5, 0.5, "PLACEHOLDER - UNVERIFIED PARAMETERS",
             fontsize=32, color="red", alpha=0.18,
             ha="center", va="center", rotation=30,
             fontweight="bold", zorder=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path("report/figures/fig4_crossover_PLACEHOLDER.pdf"))
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    idd_lo, idd_hi = IDD_STOP_RANGE_UA
    ew_lo, ew_hi   = E_WAKEUP_RANGE_UJ

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    Ns = np.array([1, 2, 3, 5, 10, 15, 20, 30, 50])

    for ax, curve, ax_title, color in [
        (axes[0], "BN254",     "BN254",     "tab:blue"),
        (axes[1], "BLS12_381", "BLS12-381", "tab:orange"),
    ]:
        T_lo = np.array([find_crossover_T(curve, int(n), idd_lo, ew_lo) for n in Ns])
        T_hi = np.array([find_crossover_T(curve, int(n), idd_hi, ew_hi) for n in Ns])
        valid_lo = ~np.isnan(T_lo)
        valid_hi = ~np.isnan(T_hi)

        any_data = False
        if valid_lo.any():
            ax.plot(Ns[valid_lo], T_lo[valid_lo], "o-", color=color,
                    linewidth=2.2, markersize=6,
                    label=f"Low (IDD={idd_lo}uA, E_wk={ew_lo}uJ)")
            any_data = True
        if valid_hi.any():
            ax.plot(Ns[valid_hi], T_hi[valid_hi], "s--", color=color,
                    linewidth=1.8, alpha=0.8, markersize=5,
                    label=f"High (IDD={idd_hi}uA, E_wk={ew_hi}uJ)")
            any_data = True

        both = valid_lo & valid_hi
        if both.any():
            ax.fill_between(Ns[both], T_lo[both], T_hi[both],
                            color=color, alpha=0.15, label="Uncertainty band")

        ax.set_title(ax_title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Batch size N", fontsize=11)
        ax.set_ylabel("Service interval T (s) where AmorE first wins", fontsize=11)
        ax.set_yscale("log")
        ax.set_xlim(0.5, max(Ns) + 1)
        ax.grid(True, alpha=0.3, which="both")

        if any_data:
            ax.legend(loc="upper right", fontsize=8)
            ax.text(0.05, 0.95,
                    "Below curve: direct wins\nAbove curve: AmorE wins",
                    transform=ax.transAxes, fontsize=9, va="top",
                    bbox=dict(boxstyle="round", facecolor="white", alpha=0.85))
        else:
            ax.text(0.5, 0.5,
                    "No crossover found in\nsearched (N, T) regime",
                    transform=ax.transAxes, fontsize=11,
                    ha="center", va="center", color="darkred",
                    bbox=dict(boxstyle="round", facecolor="lightyellow",
                              edgecolor="darkred", linewidth=1.5))

    fig.suptitle(
        "Figure 4: Energy crossover in (N, service-interval T) plane [PLACEHOLDER]\n"
        "Strategy A (AmorE): 1 wake + OTS + N rounds + sleep. "
        "Strategy B (direct): N wakes, 1 pairing each.\n"
        "AmorE saves N-1 wake-up overheads at cost of higher per-round compute.",
        fontsize=10,
    )
    add_placeholder_watermark(fig)
    fig.tight_layout()
    fig.savefig(args.out)
    print(f"  saved {args.out}")


if __name__ == "__main__":
    main()
