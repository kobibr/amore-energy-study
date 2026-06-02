#!/usr/bin/env python3
"""
compute_fair.py — reproduce the AmorE fair-comparison result (time AND a
projected energy figure) from the raw microbench telemetry. One command:

    python3 compute_fair.py

derives the whole table from the committed raw g_micro files only.

PROVENANCE — what is measured vs derived vs projected (read this):
  MEASURED  : p_cyc and every primitive (g_micro, on-chip DWT, min of 16),
              for both curves; AND the client current I (RELIC pairing,
              Mode B, real PPK2 capture: BN254 118 mA, BLS12-381 104 mA).
  DERIVED   : the AmorE client cost (paper Table-1 formula applied to the
              measured primitives). The client (Setup+Verify) is NOT
              implemented on RELIC, so its cost is COMPUTED, not run.
  PROJECTED : energy = derived_cycles * measured_I * V. Same RELIC backend
              on both sides, so the measured pairing current is used as the
              client current (energy% == time%). This is NOT a direct
              end-to-end energy measurement of the batch (no communication,
              no server-wait). A direct measurement requires Level 2
              (implement batch Setup+Verify on RELIC, measure with PPK2) —
              left as FUTURE WORK.

Inputs (committed next to this script, or pass paths as args):
    raw/BN254_r1.txt   raw/BLS12_381_r1.txt   (gdb dump of g_micro, cycles)

Formula assignment (paper Table 1; protocol Fig. 2):
  single (M=1): mT + N*(2*m1 + m2 + mbar2 + mbarT + memT)
     2*m1 = m1_fix(U=[u]P, generator) + m1_var(C=[..](U+A), NOT a generator)
     m2   = m2_fix(V)   mbar2 = m2_short(D)   mbarT = mT_short(rho^r)
  batch (M>1):  mT + N*(m1 + 2*m2 + M*(mbar1 + mbarT + memT))
     m1=m1_fix  2*m2=m2_fix+m2_var  mbar1=m1_short
"""
import sys, re
from pathlib import Path

FIELDS = ["p_cyc","m1_var","m1_fix","m1_short","m2_var","m2_fix",
          "m2_short","mT_full","mT_short","memT"]
V = 3.3
N_AMORT = 10
# MEASURED RELIC pairing current (Mode B, real PPK2), A. Proxy for the
# RELIC client current (same backend).
CURRENT_A = {"BN254": 0.118, "BLS12_381": 0.104, "BLS12-381": 0.104}

def load(path):
    txt = Path(path).read_text(); g = {}
    for f in FIELDS:
        m = re.search(rf"^\s*{re.escape(f)}\s*=\s*(\d+)", txt, re.M)
        if not m: raise SystemExit(f"{path}: missing field '{f}'")
        g[f] = int(m.group(1))
    return g

def single_pp(g, N):  # per-pairing, single
    pr = g["m1_fix"]+g["m1_var"]+g["m2_fix"]+g["m2_short"]+g["mT_short"]+g["memT"]
    return g["mT_full"]/N + pr

def batch_pp(g, N, M):  # per-pairing, batch
    pr = g["m1_fix"]+g["m2_fix"]+g["m2_var"]+M*(g["m1_short"]+g["mT_short"]+g["memT"])
    return (g["mT_full"]/N + pr)/M

def batch_total(g, N, M):  # total client cycles for M delegated pairings
    return g["mT_full"]/N + g["m1_fix"]+g["m2_fix"]+g["m2_var"] \
           + M*(g["m1_short"]+g["mT_short"]+g["memT"])

def E_mJ(cyc, I): return cyc/168e6 * I * V * 1000
def T_ms(cyc):    return cyc/168e6 * 1000

def report(name, g, N=N_AMORT):
    p = g["p_cyc"]
    print(f"\n===== {name} (N={N}) — TIME [from MEASURED cycles] =====")
    print(f"  p_cyc = {p:,} cyc (MEASURED pairing)")
    print(f"  ratios: m1_fix/m1_var={g['m1_fix']/g['m1_var']:.2f}  "
          f"m2_fix/m2_var={g['m2_fix']/g['m2_var']:.2f}  "
          f"mT_short/mT_full={g['mT_short']/g['mT_full']:.2f}")
    s = single_pp(g, N)
    print(f"  single (M=1): {s:13,.0f} cyc  gain {100*(1-s/p):+5.1f}%   [DERIVED]")
    for M in (5,10,50):
        b = batch_pp(g, N, M)
        print(f"  batch M={M:<3d}: {b:13,.0f} cyc  gain {100*(1-b/p):+5.1f}%   [DERIVED]")

    I = CURRENT_A.get(name)
    if I is None:
        print(f"  (no measured current for '{name}' — skipping energy)"); return
    M = 50
    local = M*p
    amore = batch_total(g, N, M)
    print(f"  --- 50 pairings: ENERGY [PROJECTED = DERIVED cyc x MEASURED {I*1000:.0f} mA] ---")
    print(f"    50x LOCAL pairing : {T_ms(local):8.0f} ms   {E_mJ(local,I):8.0f} mJ   [MEASURED cyc x MEASURED I]")
    print(f"    AmorE batch (50)  : {T_ms(amore):8.0f} ms   {E_mJ(amore,I):8.0f} mJ   [DERIVED cyc x MEASURED I -> PROJECTED]")
    print(f"    --> AmorE saves {100*(1-amore/local):.0f}%  "
          f"({E_mJ(local,I)-E_mJ(amore,I):,.0f} mJ,  {T_ms(local)-T_ms(amore):,.0f} ms)  [compute-only]")

def main():
    here = Path(__file__).resolve().parent
    files = sys.argv[1:] or [here/"raw"/"BN254_r1.txt", here/"raw"/"BLS12_381_r1.txt"]
    print("AmorE fair comparison — client (RELIC primitives) vs local pairing")
    print("MEASURED primitives -> DERIVED client cost -> PROJECTED energy.")
    print("Energy is compute-only (no comm, no server-wait). Direct end-to-end = Level 2 (future work).")
    for f in files:
        f = Path(f)
        if not f.exists(): print(f"  (skip, not found: {f})"); continue
        report(f.stem.replace("_r1",""), load(f))
    print("\nNOTE 2*m1 = m1_fix + m1_var (U generator; C=[..](U+A) not). "
          "2*m1_fix understates BN254 single by ~10 pts.")

if __name__ == "__main__":
    main()
