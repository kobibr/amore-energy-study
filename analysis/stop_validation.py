"""stop_validation.py — Day 9 IDD_STOP anchor measurement.

Validates the STM32F407 Stop-mode current consumption against datasheet.

Procedure:
  1. STM32 firmware (stop_test.elf) already flashed and running.
     The firmware boots, configures peripherals, enters Stop mode,
     and stays there indefinitely.
  2. PPK2 sources 3.3V to STM32 in Source mode.
  3. Sample for N seconds.
  4. Discard first M seconds (boot + RAM init + transient).
  5. Compute mean current over the stable Stop window.
  6. Validate against datasheet target (default 0.5 µA, tolerance ±100 µA).

Why we measure this:
  Day 9 of the PRD calls for a Stop-mode anchor. Our sleep_model.py
  assumes a specific IDD_STOP value when computing total round energy
  for AmorE vs direct comparisons. If this anchor is wrong, the entire
  energy-savings claim drifts. This script makes the anchor measurable.

Output:
  - CSV of all samples: measurement/stop-validation/stop_<timestamp>.csv
  - Summary text:        measurement/stop-validation/stop_<timestamp>.txt
  - Stdout verdict:      PASS / FAIL with mean and deviation from target

Usage:
  python3 -m analysis.stop_validation
  python3 -m analysis.stop_validation --duration 10 --boot-skip 2
  python3 -m analysis.stop_validation --target-uA 0.5 --tolerance-uA 100

Exit codes:
  0 = stop-mode current within tolerance
  1 = stop-mode current out of tolerance
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
        "--voltage-mv", type=int, default=3300,
        help="PPK2 source voltage in mV (default: 3300)",
    )
    p.add_argument(
        "--duration", type=float, default=10.0,
        help="Total sampling duration in seconds (default: 10.0)",
    )
    p.add_argument(
        "--boot-skip", type=float, default=2.0,
        help="Seconds to discard at start (boot/transient) (default: 2.0)",
    )
    p.add_argument(
        "--target-uA", type=float, default=0.5,
        help="Datasheet target Stop-mode current in µA (default: 0.5)",
    )
    p.add_argument(
        "--tolerance-uA", type=float, default=100.0,
        help="Pass/fail tolerance in µA absolute (default: 100.0)",
    )
    p.add_argument(
        "--out-dir", type=Path,
        default=Path("measurement/stop-validation"),
        help="Directory for CSV + summary outputs",
    )
    args = p.parse_args(argv)

    if args.boot_skip >= args.duration:
        print(f"FATAL: boot-skip ({args.boot_skip}s) must be less than duration "
              f"({args.duration}s)", file=sys.stderr)
        return 2

    # Enumerate PPK2
    devs = PPK2_API.list_devices()
    if not devs:
        print("FATAL: no PPK2 device found", file=sys.stderr)
        return 2
    port = devs[0] if isinstance(devs[0], str) else devs[0][0]
    print(f"PPK2 port: {port}")

    voltage_v = args.voltage_mv / 1000.0
    print(f"Voltage:      {voltage_v} V")
    print(f"Duration:     {args.duration} s  (discard first {args.boot_skip} s)")
    print(f"Target:       {args.target_uA} µA  (tolerance ±{args.tolerance_uA} µA)")
    print(f"Pass window:  [{max(0, args.target_uA - args.tolerance_uA):.2f}, "
          f"{args.target_uA + args.tolerance_uA:.2f}] µA")
    print()

    # Open + configure
    ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    ppk2.set_source_voltage(args.voltage_mv)
    ppk2.use_source_meter()
    ppk2.toggle_DUT_power("ON")
    time.sleep(0.5)
    ppk2.start_measuring()
    time.sleep(0.3)
    _ = ppk2.get_data()  # drain

    # Capture full window. Tag each sample with timestamp offset.
    # Use monotonic clock so NTP jumps cannot corrupt relative timing.
    print(f"Sampling for {args.duration}s...")
    samples_all = []   # list of (t_offset_s, current_uA)
    try:
        t0 = time.monotonic()
        t_prev = t0
        while time.monotonic() - t0 < args.duration:
            t_now = time.monotonic()
            raw = ppk2.get_data()
            if raw:
                res = ppk2.get_samples(raw)
                s = res[0] if isinstance(res, tuple) else res
                # Spread samples linearly across the inter-arrival interval
                # [t_prev, t_now]. This is more accurate than attaching all
                # samples in the batch to the same t_now — for a 250-sample
                # batch arriving every ~10 ms, the spread is ~40 us per sample,
                # which matters at the boot/stop boundary.
                t_off_start = t_prev - t0
                t_off_end = t_now - t0
                n = len(s)
                if n > 0:
                    dt = (t_off_end - t_off_start) / n if n > 1 else 0.0
                    for i, v in enumerate(s):
                        samples_all.append((t_off_start + i * dt, v))
                t_prev = t_now
            time.sleep(0.01)
    finally:
        # Ensure clean PPK2 state even if the capture loop raised.
        try:
            ppk2.stop_measuring()
        except Exception:
            pass
        try:
            ppk2.toggle_DUT_power("OFF")
        except Exception:
            pass

    if not samples_all:
        print("FATAL: no samples collected", file=sys.stderr)
        return 2

    # Split into boot vs stop windows
    boot = [v for (t, v) in samples_all if t < args.boot_skip]
    stop = [v for (t, v) in samples_all if t >= args.boot_skip]

    if not stop:
        print(f"FATAL: no samples after boot-skip={args.boot_skip}s", file=sys.stderr)
        return 2

    # Stats
    n_boot = len(boot)
    n_stop = len(stop)
    boot_mean_uA = statistics.mean(boot) if boot else 0.0
    boot_max_uA = max(boot) if boot else 0.0
    stop_mean_uA = statistics.mean(stop)
    stop_stdev_uA = statistics.stdev(stop) if n_stop > 1 else 0.0

    # Refuse to PASS based on too-few stop samples (stdev=0 with 1 sample
    # looks deceptively perfect). Require at least 100 samples post-boot.
    MIN_STOP_SAMPLES = 100
    if n_stop < MIN_STOP_SAMPLES:
        print(f"⚠ WARNING: only {n_stop} samples in stop window "
              f"(< {MIN_STOP_SAMPLES} required for reliable stats)",
              file=sys.stderr)
        print(f"   stdev may be misleadingly low; verdict cannot be trusted.",
              file=sys.stderr)
    stop_min_uA = min(stop)
    stop_max_uA = max(stop)
    deviation_uA = stop_mean_uA - args.target_uA

    print()
    print(f"Boot window ({args.boot_skip:.1f}s, {n_boot} samples):")
    print(f"  mean = {boot_mean_uA:.2f} µA   max = {boot_max_uA:.2f} µA")
    print()
    print(f"Stop window ({args.duration - args.boot_skip:.1f}s, {n_stop} samples):")
    print(f"  mean = {stop_mean_uA:.3f} µA   stdev = {stop_stdev_uA:.3f} µA")
    print(f"  range = [{stop_min_uA:.3f} .. {stop_max_uA:.3f}] µA")
    print(f"  deviation from target: {deviation_uA:+.3f} µA")
    print()

    # Write CSV
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.out_dir / f"stop_{ts}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sample_index", "t_offset_s", "current_uA", "window"])
        for i, (t, v) in enumerate(samples_all):
            window = "boot" if t < args.boot_skip else "stop"
            w.writerow([i, f"{t:.6f}", f"{v:.4f}", window])

    # Write summary
    summary_path = args.out_dir / f"stop_{ts}.txt"
    verdict_pass = abs(deviation_uA) <= args.tolerance_uA
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"STM32 Stop-mode Validation Report\n")
        f.write(f"=" * 60 + "\n")
        f.write(f"Timestamp:       {datetime.now().isoformat()}\n")
        f.write(f"PPK2 port:       {port}\n")
        f.write(f"Voltage:         {voltage_v} V\n")
        f.write(f"Duration total:  {args.duration} s\n")
        f.write(f"Boot-skip:       {args.boot_skip} s\n")
        f.write(f"\n")
        f.write(f"Boot window:\n")
        f.write(f"  samples: {n_boot}\n")
        f.write(f"  mean:    {boot_mean_uA:.2f} µA\n")
        f.write(f"  max:     {boot_max_uA:.2f} µA\n")
        f.write(f"\n")
        f.write(f"Stop window:\n")
        f.write(f"  samples: {n_stop}\n")
        f.write(f"  mean:    {stop_mean_uA:.3f} µA\n")
        f.write(f"  stdev:   {stop_stdev_uA:.3f} µA\n")
        f.write(f"  min:     {stop_min_uA:.3f} µA\n")
        f.write(f"  max:     {stop_max_uA:.3f} µA\n")
        f.write(f"\n")
        f.write(f"Target:          {args.target_uA} µA\n")
        f.write(f"Tolerance:       ±{args.tolerance_uA} µA\n")
        f.write(f"Deviation:       {deviation_uA:+.3f} µA\n")
        f.write(f"Verdict:         {'PASS' if verdict_pass else 'FAIL'}\n")

    print(f"CSV:     {csv_path}")
    print(f"Summary: {summary_path}")
    print()

    if verdict_pass:
        print(f"  ✓ PASS — Stop-mode current {stop_mean_uA:.2f} µA within "
              f"±{args.tolerance_uA} µA of {args.target_uA} µA target")
        return 0
    else:
        print(f"  ✗ FAIL — Stop-mode current {stop_mean_uA:.2f} µA exceeds "
              f"±{args.tolerance_uA} µA tolerance from {args.target_uA} µA target")
        if stop_mean_uA > 1000:
            print(f"    ({stop_mean_uA/1000:.2f} mA — MCU likely NOT in Stop mode;")
            print(f"     check that stop_test.elf is flashed and running)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
