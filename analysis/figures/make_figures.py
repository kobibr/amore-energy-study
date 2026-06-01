#!/usr/bin/env python3
"""
make_figures.py - AmorE energy study figures, from measured data only.

Inputs (all measured, no synthetic):
  - energy_real.json   : phase-aware energy/time/current (FULL_REGRESSION, 6 replicas)
  - MEMORY (below)     : arm-none-eabi-size -A on the current ELFs (apples-to-apples)
  - OTS_CYCLES (below) : measured OneTimeSetup duration (deterministic, gdb-read)

Produces (PNG + PDF):
  fig1_energy.png   - per-round compute energy: AmorE vs RELIC (BN254, BLS12-381)
  fig2_memory.png   - Flash + working SRAM: AmorE vs RELIC
  fig3_time.png     - per-round compute time: AmorE vs RELIC

Headline: AmorE on Cortex-M4 (no asm) costs ~1.9x energy and ~1.7-1.9x time
vs an in-place RELIC pairing. Its value is memory footprint (up to 21x less
working SRAM), pairing-library avoidance on the client, and verifiability --
NOT energy or speed.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# ---- measured memory (arm-none-eabi-size -A on current ELFs; KB) ----
# Flash = .isr+.text+.rodata+.data ; SRAM-working = .data+.bss (globals only)
MEMORY = {
    "BN254":     {"amore_flash": 20.0, "relic_flash": 84.3,
                  "amore_sram": 3.2,  "relic_sram": 67.3},
    "BLS12_381": {"amore_flash": 20.6, "relic_flash": 238.7,
                  "amore_sram": 3.2,  "relic_sram": 21.0},
}

# ---- measured OneTimeSetup (deterministic, cycles) ----
OTS_CYCLES = {"BN254": 91_615_135}   # BLS measured separately when available
F_HZ = 168e6

# ---- styling ----
C_AMORE = "#2b6cb0"   # blue
C_RELIC = "#c05621"   # orange
plt.rcParams.update({"font.size": 11})


def load(run_dir: Path) -> dict:
    p = run_dir / "energy_real.json"
    return json.loads(p.read_text())


def _bars(ax, labels, amore_vals, relic_vals, ylabel, title, ratios=None, unit=""):
    import numpy as np
    x = range(len(labels))
    w = 0.38
    xa = [i - w/2 for i in x]
    xr = [i + w/2 for i in x]
    ba = ax.bar(xa, amore_vals, w, label="AmorE", color=C_AMORE)
    br = ax.bar(xr, relic_vals, w, label="RELIC (direct pairing)", color=C_RELIC)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontweight="bold")
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    # value labels
    for bars in (ba, br):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:.1f}{unit}", (b.get_x()+b.get_width()/2, h),
                        ha="center", va="bottom", fontsize=9,
                        xytext=(0, 2), textcoords="offset points")
    # ratio annotations above each pair
    if ratios:
        top = max(max(amore_vals), max(relic_vals))
        for i, r in enumerate(ratios):
            ax.annotate(f"{r:.2f}x", (i, top*1.12), ha="center", fontsize=10,
                        fontweight="bold", color="#444")
        ax.set_ylim(0, top*1.25)


def fig_energy(data, out: Path):
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    labels = ["BN254", "BLS12-381"]
    amore = [data["bn254_A"]["E_mJ"], data["bls12_381_A"]["E_mJ"]]
    relic = [data["bn254_B"]["E_mJ"], data["bls12_381_B"]["E_mJ"]]
    ratios = [data["bn254_ratio"]["energy"], data["bls12_381_ratio"]["energy"]]
    _bars(ax, labels, amore, relic, "Energy per round (mJ)",
          "Compute energy: AmorE vs direct pairing", ratios, " mJ")
    fig.tight_layout()
    fig.savefig(out/"fig1_energy.png", dpi=150)
    fig.savefig(out/"fig1_energy.pdf")
    plt.close(fig)
    print(f"  wrote fig1_energy  (AmorE {amore} vs RELIC {relic} mJ, ratios {ratios})")


def fig_time(data, out: Path):
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    labels = ["BN254", "BLS12-381"]
    amore = [data["bn254_A"]["time_ms"], data["bls12_381_A"]["time_ms"]]
    relic = [data["bn254_B"]["time_ms"], data["bls12_381_B"]["time_ms"]]
    ratios = [data["bn254_ratio"]["time"], data["bls12_381_ratio"]["time"]]
    _bars(ax, labels, amore, relic, "Time per round (ms)",
          "Compute time: AmorE vs direct pairing", ratios, " ms")
    fig.tight_layout()
    fig.savefig(out/"fig3_time.png", dpi=150)
    fig.savefig(out/"fig3_time.pdf")
    plt.close(fig)
    print(f"  wrote fig3_time    (AmorE {amore} vs RELIC {relic} ms, ratios {ratios})")


def fig_memory(out: Path):
    fig, axs = plt.subplots(1, 2, figsize=(9.5, 4.2))
    for ax, kind, ylab, title in [
        (axs[0], "flash", "Flash (KB)", "Flash (code+rodata)"),
        (axs[1], "sram",  "SRAM (KB)",  "Working SRAM (globals)"),
    ]:
        labels = ["BN254", "BLS12-381"]
        amore = [MEMORY["BN254"][f"amore_{kind}"], MEMORY["BLS12_381"][f"amore_{kind}"]]
        relic = [MEMORY["BN254"][f"relic_{kind}"], MEMORY["BLS12_381"][f"relic_{kind}"]]
        ratios = [r/a for a, r in zip(amore, relic)]
        _bars(ax, labels, amore, relic, ylab, title, ratios, "")
    fig.suptitle("Memory footprint: AmorE vs RELIC (measured, apples-to-apples)",
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(out/"fig2_memory.png", dpi=150)
    fig.savefig(out/"fig2_memory.pdf")
    plt.close(fig)
    print(f"  wrote fig2_memory  (Flash {ratios} ...; see MEMORY table)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="FULL_REGRESSION dir containing energy_real.json")
    ap.add_argument("--out", type=Path, default=Path("report/figures"))
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    data = load(args.run_dir)
    print("Generating figures from measured data:")
    fig_energy(data, args.out)
    fig_memory(args.out)
    fig_time(data, args.out)
    # report OTS amortization context (one-time, deterministic)
    ots = OTS_CYCLES["BN254"]; per = data["bn254_A"]["cycles"]
    print(f"\n  OTS (BN254) = {ots:,} cyc = {ots/F_HZ*1000:.0f} ms (one-time, amortized over N rounds)")
    print(f"  per-round compute = {per:,} cyc; OTS/per-round = {ots/per:.2f}")
    print(f"  -> at N>=10 the OTS adds <13% to per-round; asymptote stays at "
          f"{data['bn254_A']['E_mJ']} mJ (still > {data['bn254_B']['E_mJ']} mJ RELIC)")
    print(f"\n  all figures in {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
