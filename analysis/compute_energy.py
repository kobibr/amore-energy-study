#!/usr/bin/env python3
"""Phase-aware energy from real PPK2 captures — reads logs/ only.

For every measurement cell in a full_regression run, compute the
compute-only energy of one AmorE round (Mode A) and one RELIC pairing
(Mode B), from measured DWT cycles (telemetry) and the measured
compute-phase current (PPK2 CSV), then average over replicas.

Energy model (compute-only, phase-aware):

    E = (compute_cycles / F_HZ) * I_compute * V

  - compute_cycles : DWT cycle count of the work being measured.
      Mode A -> amortized blind+verify per round at N=50 (telemetry).
      Mode B -> pairing_min (telemetry).
  - I_compute      : mean current during the compute phase.
      Mode A -> samples with GPIO bit0 == 1 (blind+verify markers).
      Mode B -> relic_bench has no phase markers and is pure pairing,
                so the full-trace mean IS the pairing current.
      Mean (not median) is used: energy is the time-integral of power,
      E = integral(I)dt = mean(I) * duration, and V is constant
      (PPK2 source-meter, 3.300 V), so mean(I)*V is the exact mean power.
  - V              : 3.300 V (source-meter), with -5% R33 calibration
                     applied to current.

No synthetic data. No baseline constants. Every number traces to a
sample in logs/ or a DWT counter in telemetry.txt.
"""
from __future__ import annotations
import argparse, csv, json, re, statistics, sys
from pathlib import Path

F_HZ = 168_000_000.0
V_NOMINAL = 3.300
R33_CAL = 0.95          # PPK2 -5% absolute accuracy (33-ohm reference)
CURVES = ("bn254", "bls12_381")
REPLICAS = range(1, 7)
CURRENT_SANITY_UA = (0.0, 200_000.0)   # drop impossible samples

def compute_phase_mean_uA(csv_path: Path, compute_only: bool) -> tuple[float, int]:
    """Mean current (uA, calibrated) over the compute phase (bit0) or full trace.

    Streaming: never holds the whole CSV in memory (cells are ~80M rows).
    Returns (mean_uA_calibrated, n_samples)."""
    total = 0.0; n = 0
    lo, hi = CURRENT_SANITY_UA
    with open(csv_path, newline="") as fh:
        rd = csv.reader(fh); next(rd, None)
        for row in rd:
            try:
                c = float(row[1]); g = int(row[3]) & 0x03
            except (ValueError, IndexError):
                continue
            if not (lo < c < hi):
                continue
            if compute_only and not (g & 0x01):   # bit0 = compute marker
                continue
            total += c; n += 1
    if n == 0:
        return 0.0, 0
    return (total / n) * R33_CAL, n

def telemetry_cycles(txt_path: Path, mode: str) -> int | None:
    """Mode A -> amort at N=50; Mode B -> pairing_min."""
    text = txt_path.read_text(errors="replace")
    if mode == "A":
        m = re.search(r"\[N=50\][^\n]*amort=(\d+)", text)
        return int(m.group(1)) if m else None
    m = re.search(r"pairing_min\s*=\s*(\d+)", text)
    return int(m.group(1)) if m else None

def energy_mJ(cycles: int, I_uA_cal: float) -> float:
    I_A = I_uA_cal / 1e6
    return (cycles / F_HZ) * I_A * V_NOMINAL * 1e3   # mJ

def main() -> int:
    ap = argparse.ArgumentParser(description="Phase-aware energy from logs/ only.")
    ap.add_argument("run_dir", type=Path,
                    help="logs/full_regression_<timestamp>")
    ap.add_argument("--out", type=Path, default=None,
                    help="energy_real.json path (default: <run_dir>/energy_real.json)")
    args = ap.parse_args()
    meas = args.run_dir / "measurements"
    if not meas.is_dir():
        print(f"ERROR: {meas} not found", file=sys.stderr); return 1

    results: dict[str, dict] = {}
    for curve in CURVES:
        for mode in ("A", "B"):
            Is, cycs, nsmp = [], [], []
            for r in REPLICAS:
                cell = meas / f"{curve}__{mode}__r{r}"
                csv_f, txt_f = cell / "run_001.csv", cell / "telemetry.txt"
                if not (csv_f.exists() and txt_f.exists()):
                    continue
                I, n = compute_phase_mean_uA(csv_f, compute_only=(mode == "A"))
                c = telemetry_cycles(txt_f, mode)
                if I > 0 and c:
                    Is.append(I); cycs.append(c); nsmp.append(n)
            if not Is:
                print(f"{curve} {mode}: no data", file=sys.stderr); continue
            I_med = statistics.median(Is)
            I_std = statistics.pstdev(Is) if len(Is) > 1 else 0.0
            C_med = int(statistics.median(cycs))
            E = energy_mJ(C_med, I_med)
            results[f"{curve}_{mode}"] = {
                "I_mA": round(I_med / 1000, 2),
                "I_std_mA": round(I_std / 1000, 2),
                "cycles": C_med,
                "time_ms": round(C_med / F_HZ * 1e3, 2),
                "E_mJ": round(E, 2),
                "n_replicas": len(Is),
                "phase": "compute(bit0)" if mode == "A" else "full(pairing)",
            }
            print(f"{curve} Mode {mode}: I={I_med/1000:6.2f}±{I_std/1000:.2f} mA "
                  f"cyc={C_med:>12,} E={E:7.2f} mJ (n={len(Is)})")

    print("\n=== RATIOS (1:1, AmorE round vs one RELIC pairing) ===")
    for cv in CURVES:
        a, b = results.get(f"{cv}_A"), results.get(f"{cv}_B")
        if a and b:
            er = a["E_mJ"] / b["E_mJ"]; tr = a["time_ms"] / b["time_ms"]
            results[f"{cv}_ratio"] = {"energy": round(er, 2), "time": round(tr, 2)}
            print(f"  {cv}: energy {a['E_mJ']}/{b['E_mJ']} = {er:.2f}x | "
                  f"time {a['time_ms']}/{b['time_ms']} = {tr:.2f}x")

    out = args.out or (args.run_dir / "energy_real.json")
    out.write_text(json.dumps(results, indent=2))
    print(f"\n-> wrote {out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
