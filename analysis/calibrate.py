"""calibrate.py — known-resistor calibration for PPK2.

Validates that PPK2 source-mode measurements match Ohm's law within tolerance.

Procedure:
  1. Disconnect STM32 from PPK2.
  2. Connect a known resistor R (default: 33 Ω, ±1%) between
     PPK2 VOUT and PPK2 GND.
  3. Run this script.
  4. PPK2 sources 3.3V, measures current. Expected: I = V/R.
     For 33Ω @ 3.3V: I_expected = 100.0 mA. Tolerance: ±2%.

Output:
  - Pass/fail verdict
  - Mean measured current ± stdev
  - Percent deviation from V/R
  - CSV log of all samples to measurement/calibration-logs/

Usage:
  python3 -m analysis.calibrate
  python3 -m analysis.calibrate --resistor-ohms 33.0
  python3 -m analysis.calibrate --resistor-ohms 100.0 --voltage-mv 3300
  python3 -m analysis.calibrate --duration-s 10 --tolerance-pct 2.0

Exit codes:
  0 = calibration PASS within tolerance
  1 = calibration FAIL (out of tolerance, but measurement worked)
  2 = error (no PPK2, no samples, etc.)
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from ppk2_api.ppk2_api import PPK2_API
except ImportError as e:
    print(f"FATAL: cannot import ppk2_api: {e}", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--resistor-ohms",
        type=float,
        default=33.0,
        help="Known resistor value in ohms (default: 33.0)",
    )
    p.add_argument(
        "--voltage-mv",
        type=int,
        default=3300,
        help="PPK2 source voltage in mV (default: 3300)",
    )
    p.add_argument(
        "--duration-s",
        type=float,
        default=5.0,
        help="Sampling duration in seconds (default: 5.0)",
    )
    p.add_argument(
        "--tolerance-pct",
        type=float,
        default=2.0,
        help="Pass/fail tolerance as percent (default: 2.0)",
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=Path("measurement/calibration-logs"),
        help="Directory for CSV logs",
    )
    args = p.parse_args(argv)

    # Enumerate PPK2
    devs = PPK2_API.list_devices()
    if not devs:
        print("FATAL: no PPK2 device found", file=sys.stderr)
        return 2
    port = devs[0] if isinstance(devs[0], str) else devs[0][0]
    print(f"PPK2 port: {port}")

    # Expected current per Ohm's law
    voltage_v = args.voltage_mv / 1000.0
    expected_uA = (voltage_v / args.resistor_ohms) * 1_000_000.0
    expected_mA = expected_uA / 1000.0
    print(f"Resistor:  {args.resistor_ohms} Ω")
    print(f"Voltage:   {voltage_v} V")
    print(f"Expected:  {expected_mA:.2f} mA  ({expected_uA:.0f} µA)")
    print(f"Tolerance: ±{args.tolerance_pct}%  "
          f"[{expected_mA * (1 - args.tolerance_pct/100):.2f} .. "
          f"{expected_mA * (1 + args.tolerance_pct/100):.2f}] mA")
    print()

    # Open + calibrate PPK2
    ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    ppk2.set_source_voltage(args.voltage_mv)
    ppk2.use_source_meter()
    ppk2.toggle_DUT_power("ON")
    time.sleep(0.5)
    ppk2.start_measuring()
    time.sleep(0.3)
    _ = ppk2.get_data()  # drain initial buffer

    # Capture
    print(f"Sampling for {args.duration_s}s...")
    t0 = time.time()
    samples = []
    while time.time() - t0 < args.duration_s:
        raw = ppk2.get_data()
        if raw:
            res = ppk2.get_samples(raw)
            s = res[0] if isinstance(res, tuple) else res
            samples.extend(s)
        time.sleep(0.01)

    # Cleanup
    ppk2.toggle_DUT_power("OFF")
    ppk2.stop_measuring()

    if not samples:
        print("FATAL: no samples collected", file=sys.stderr)
        return 2

    # Stats
    n = len(samples)
    mean_uA = statistics.mean(samples)
    stdev_uA = statistics.stdev(samples) if n > 1 else 0.0
    mean_mA = mean_uA / 1000.0
    stdev_mA = stdev_uA / 1000.0

    deviation_uA = mean_uA - expected_uA
    deviation_pct = 100.0 * deviation_uA / expected_uA if expected_uA != 0 else float("inf")

    print(f"  n={n}  mean={mean_mA:.3f} mA  stdev={stdev_mA:.3f} mA")
    print(f"  deviation: {deviation_uA:+.1f} µA ({deviation_pct:+.2f}%)")

    # Save CSV log
    args.log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.log_dir / f"calibration_{ts}_R{int(args.resistor_ohms)}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_index", "current_uA"])
        for i, v in enumerate(samples):
            w.writerow([i, f"{v:.3f}"])

    # Save summary
    summary_path = args.log_dir / f"calibration_{ts}_R{int(args.resistor_ohms)}.txt"
    with summary_path.open("w") as f:
        f.write(f"PPK2 Calibration Report\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"PPK2 port: {port}\n")
        f.write(f"Resistor: {args.resistor_ohms} Ω\n")
        f.write(f"Voltage: {voltage_v} V ({args.voltage_mv} mV)\n")
        f.write(f"Duration: {args.duration_s} s\n")
        f.write(f"Samples: {n}\n")
        f.write(f"Expected current: {expected_mA:.3f} mA\n")
        f.write(f"Measured mean: {mean_mA:.3f} mA\n")
        f.write(f"Measured stdev: {stdev_mA:.3f} mA\n")
        f.write(f"Deviation: {deviation_uA:+.1f} µA ({deviation_pct:+.3f}%)\n")
        f.write(f"Tolerance: ±{args.tolerance_pct}%\n")
        f.write(f"Verdict: {'PASS' if abs(deviation_pct) <= args.tolerance_pct else 'FAIL'}\n")

    print(f"  CSV log: {csv_path}")
    print(f"  Summary: {summary_path}")
    print()

    # Verdict
    if abs(deviation_pct) <= args.tolerance_pct:
        print(f"  ✓ PASS — deviation {deviation_pct:+.2f}% within ±{args.tolerance_pct}%")
        return 0
    else:
        print(f"  ✗ FAIL — deviation {deviation_pct:+.2f}% exceeds ±{args.tolerance_pct}%")
        return 1


if __name__ == "__main__":
    sys.exit(main())
