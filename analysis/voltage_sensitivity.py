"""voltage_sensitivity.py — measure I and P across supply voltages.

PRD §5.5 deliverable: sweep PPK2 source voltage across a range
(default 3000/3300/3600 mV) and measure the STM32's mean current
draw at each.

DESIGN NOTE — PPK2 instance lifecycle:
  ppk2-api 0.9.2 does not handle repeated start/stop or voltage
  changes within a single Python object cleanly: ADC calibration
  state becomes stale, producing junk current readings.
  This module therefore opens a FRESH PPK2_API instance for every
  voltage measurement, exactly mirroring the proven pattern in
  measurement/backends.py::PPK2Backend.measure_replica() that
  has demonstrated CV=0.22% across replicas at a single voltage.

  Between iterations we sleep 3 seconds to let the USB device
  re-enumerate and the rail discharge.

Output:
  - Stdout summary table
  - CSV : measurement/voltage-sensitivity/voltage_<ts>.csv
  - Text: measurement/voltage-sensitivity/voltage_<ts>.txt

Usage:
  python3 -m analysis.voltage_sensitivity
  python3 -m analysis.voltage_sensitivity --voltages 3000 3300 3600
  python3 -m analysis.voltage_sensitivity --duration 6 --boot-skip 2

Exit codes:
  0 = sweep completed successfully
  1 = safety abort (mean current > SAFETY_MAX_mA at some voltage)
  2 = setup error (no PPK2, no samples, invalid args)
"""
from __future__ import annotations

import argparse
import csv
import gc
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


SAFETY_MAX_mA = 250.0  # Abort if measured current exceeds this
INTER_VOLTAGE_SLEEP_S = 3.0  # USB re-enumeration window
SETTLE_AFTER_POWER_ON_S = 1.5
DRAIN_INTERVAL_S = 0.01


def open_fresh_ppk2(port: str | None = None) -> tuple[object, str]:
    """Open a fresh PPK2 instance and return (api, port).

    Calls list_devices() each time so we don't cache a stale port
    after USB re-enumeration.
    """
    devs = PPK2_API.list_devices()
    if not devs:
        raise RuntimeError("no PPK2 device found")
    resolved = port or (devs[0] if isinstance(devs[0], str) else devs[0][0])
    api = PPK2_API(resolved, timeout=2, write_timeout=2)
    api.get_modifiers()  # MANDATORY — calibration constants
    return api, resolved


def measure_one_voltage(voltage_mV: int, duration_s: float,
                        boot_skip_s: float) -> dict:
    """Open fresh PPK2, set voltage, capture, return stats. Closes on exit.

    Mirrors PPK2Backend.measure_replica() lifecycle:
      1. PPK2_API(...) fresh instance
      2. get_modifiers()
      3. set_source_voltage + use_source_meter
      4. toggle_DUT_power(ON), settle
      5. start_measuring, drain loop
      6. stop_measuring, toggle_DUT_power(OFF)
      7. instance falls out of scope → GC cleanup
    """
    ppk2 = None
    try:
        ppk2, port = open_fresh_ppk2()

        # Configure
        ppk2.set_source_voltage(voltage_mV)
        ppk2.use_source_meter()

        # Power on, settle
        ppk2.toggle_DUT_power("ON")
        time.sleep(SETTLE_AFTER_POWER_ON_S)

        # Start sampling
        ppk2.start_measuring()
        time.sleep(0.3)
        _ = ppk2.get_data()  # drain initial buffer

        # Bug #4 fix: use monotonic clock, matching stop_validation.py.
        # NTP step during sampling could otherwise shift t_off and move
        # samples in/out of the boot_skip window.
        # Bug #5 fix: spread samples linearly over each batch's inter-
        # arrival interval, instead of stamping the whole batch with
        # t_now. The docstring claims this "exactly mirrors" PPK2Backend
        # — but only with this fix does it actually mirror stop_validation's
        # corrected behavior.
        t0 = time.monotonic()
        t_prev = t0
        samples_all: list[tuple[float, float]] = []
        while time.monotonic() - t0 < duration_s:
            t_now = time.monotonic()
            raw = ppk2.get_data()
            if raw:
                res = ppk2.get_samples(raw)
                s = res[0] if isinstance(res, tuple) else res
                t_off_start = t_prev - t0
                t_off_end = t_now - t0
                n = len(s)
                if n > 0:
                    dt = (t_off_end - t_off_start) / n if n > 1 else 0.0
                    for i, v in enumerate(s):
                        samples_all.append((t_off_start + i * dt, v))
                t_prev = t_now
            time.sleep(DRAIN_INTERVAL_S)

        ppk2.stop_measuring()
        ppk2.toggle_DUT_power("OFF")

    finally:
        if ppk2:
            try:
                ppk2.toggle_DUT_power("OFF")
            except Exception:
                pass
        # Force destruction so the next iteration sees a clean USB
        del ppk2
        gc.collect()

    if not samples_all:
        raise RuntimeError(f"no samples collected at {voltage_mV} mV")

    steady = [v for (t, v) in samples_all if t >= boot_skip_s]
    if not steady:
        raise RuntimeError(
            f"no samples after boot-skip={boot_skip_s}s at {voltage_mV} mV"
        )

    n = len(steady)
    mean_uA = statistics.mean(steady)
    stdev_uA = statistics.stdev(steady) if n > 1 else 0.0
    smin_uA = min(steady)
    smax_uA = max(steady)
    n_neg = sum(1 for v in steady if v < 0)

    voltage_V = voltage_mV / 1000.0
    mean_mA = mean_uA / 1000.0
    power_mW = voltage_V * mean_mA

    return {
        "voltage_mV": voltage_mV,
        "voltage_V": voltage_V,
        "n_samples_steady": n,
        "n_negative": n_neg,
        "mean_mA": mean_mA,
        "stdev_mA": stdev_uA / 1000.0,
        "min_mA": smin_uA / 1000.0,
        "max_mA": smax_uA / 1000.0,
        "power_mW": power_mW,
        "energy_per_s_mJ": power_mW,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--voltages", type=int, nargs="+", default=[3000, 3300, 3600],
        help="Source voltages in mV (default: 3000 3300 3600)",
    )
    p.add_argument(
        "--duration", type=float, default=5.0,
        help="Sampling duration per voltage in seconds (default: 5.0)",
    )
    p.add_argument(
        "--boot-skip", type=float, default=1.5,
        help="Seconds to discard at start of each capture (default: 1.5)",
    )
    p.add_argument(
        "--nominal-mV", type=int, default=3300,
        help="Nominal voltage for %%-delta baseline (default: 3300)",
    )
    p.add_argument(
        "--out-dir", type=Path,
        default=Path("measurement/voltage-sensitivity"),
    )
    args = p.parse_args(argv)

    # Sanity check
    for v in args.voltages:
        if v < 1800 or v > 3600:
            print(f"FATAL: voltage {v} outside safe range [1800, 3600] mV "
                  f"for STM32F4xx VDD", file=sys.stderr)
            return 2
    if args.boot_skip >= args.duration:
        print(f"FATAL: boot-skip ({args.boot_skip}s) >= duration "
              f"({args.duration}s)", file=sys.stderr)
        return 2

    print(f"Voltages:  {args.voltages} mV")
    print(f"Duration:  {args.duration}s per V (skip first {args.boot_skip}s)")
    print(f"Nominal:   {args.nominal_mV} mV")
    print(f"Safety:    abort if any voltage exceeds {SAFETY_MAX_mA} mA")
    print(f"Pattern:   fresh PPK2 instance per voltage, "
          f"{INTER_VOLTAGE_SLEEP_S}s sleep between")
    print()

    results: list[dict] = []
    safety_aborted = False

    for i, v_mV in enumerate(args.voltages, 1):
        print(f"━━━ [{i}/{len(args.voltages)}] Measuring at {v_mV} mV ━━━")
        try:
            r = measure_one_voltage(v_mV, args.duration, args.boot_skip)
        except Exception as e:
            print(f"  ✗ ERROR at {v_mV} mV: {e}")
            print(f"  Aborting sweep.")
            return 2

        results.append(r)

        if r["mean_mA"] > SAFETY_MAX_mA:
            print(f"  ⚠ SAFETY ABORT: mean {r['mean_mA']:.1f} mA > "
                  f"{SAFETY_MAX_mA} mA")
            safety_aborted = True
            break

        cv = (100 * r["stdev_mA"] / r["mean_mA"]) if r["mean_mA"] > 0 else 0.0
        print(f"  n={r['n_samples_steady']}  "
              f"mean={r['mean_mA']:.2f}±{r['stdev_mA']:.2f} mA "
              f"(CV={cv:.1f}%)  "
              f"range=[{r['min_mA']:.1f}..{r['max_mA']:.1f}] mA  "
              f"P={r['power_mW']:.2f} mW  "
              f"neg={r['n_negative']}")
        print()

        # Inter-voltage sleep — let USB re-enumerate before next instance
        if i < len(args.voltages):
            print(f"  (sleeping {INTER_VOLTAGE_SLEEP_S}s before next voltage)")
            time.sleep(INTER_VOLTAGE_SLEEP_S)
            print()

    if not results:
        print("FATAL: no results collected", file=sys.stderr)
        return 2

    # Find nominal for delta calc
    nominal = next((r for r in results if r["voltage_mV"] == args.nominal_mV),
                    None)
    if nominal is None:
        nominal = results[len(results) // 2]

    print()
    print("━━━ Summary table ━━━")
    print(f"  {'V (mV)':>8}  {'I (mA)':>11}  {'CV':>6}  {'P (mW)':>10}  "
          f"{'%ΔI':>8}  {'%ΔP':>8}  {'neg':>5}")
    print("  " + "-" * 65)
    for r in results:
        dI = 100.0 * (r["mean_mA"] - nominal["mean_mA"]) / nominal["mean_mA"]
        dP = 100.0 * (r["power_mW"] - nominal["power_mW"]) / nominal["power_mW"]
        cv = (100 * r["stdev_mA"] / r["mean_mA"]) if r["mean_mA"] > 0 else 0.0
        print(f"  {r['voltage_mV']:>8}  "
              f"{r['mean_mA']:>7.2f}±{r['stdev_mA']:<4.2f}  "
              f"{cv:>5.1f}%  "
              f"{r['power_mW']:>10.3f}  "
              f"{dI:>+7.2f}%  {dP:>+7.2f}%  {r['n_negative']:>5}")
    print()

    # Output files
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.out_dir / f"voltage_{ts}.csv"
    txt_path = args.out_dir / f"voltage_{ts}.txt"

    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "voltage_mV", "voltage_V", "n_samples_steady", "n_negative",
            "mean_mA", "stdev_mA", "min_mA", "max_mA",
            "power_mW", "energy_per_s_mJ",
        ])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"  CSV:     {csv_path}")

    with txt_path.open("w") as f:
        f.write("Voltage-sensitivity Sweep Report\n")
        f.write("=" * 64 + "\n")
        f.write(f"Timestamp:  {datetime.now().isoformat()}\n")
        f.write(f"Voltages:   {args.voltages} mV\n")
        f.write(f"Duration:   {args.duration}s per V "
                f"(skip first {args.boot_skip}s)\n")
        f.write(f"Pattern:    fresh PPK2 instance per voltage, "
                f"{INTER_VOLTAGE_SLEEP_S}s inter-voltage sleep\n")
        f.write(f"Nominal:    {args.nominal_mV} mV "
                f"(I={nominal['mean_mA']:.3f} mA, "
                f"P={nominal['power_mW']:.3f} mW)\n")
        f.write(f"\n")
        f.write(f"{'V (mV)':>8}  {'I (mA)':>11}  {'CV':>6}  "
                f"{'P (mW)':>10}  {'%ΔI':>8}  {'%ΔP':>8}  {'neg':>5}\n")
        f.write("-" * 65 + "\n")
        for r in results:
            dI = 100.0 * (r["mean_mA"] - nominal["mean_mA"]) / nominal["mean_mA"]
            dP = 100.0 * (r["power_mW"] - nominal["power_mW"]) / nominal["power_mW"]
            cv = (100 * r["stdev_mA"] / r["mean_mA"]) if r["mean_mA"] > 0 else 0.0
            f.write(f"{r['voltage_mV']:>8}  "
                    f"{r['mean_mA']:>7.2f}±{r['stdev_mA']:<4.2f}  "
                    f"{cv:>5.1f}%  "
                    f"{r['power_mW']:>10.3f}  "
                    f"{dI:>+7.2f}%  {dP:>+7.2f}%  "
                    f"{r['n_negative']:>5}\n")
        f.write(f"\n")
        if safety_aborted:
            f.write(f"⚠ SWEEP ABORTED for safety (current > "
                    f"{SAFETY_MAX_mA} mA at some voltage).\n")
        else:
            f.write(f"✓ Sweep completed without safety abort.\n")
    print(f"  Summary: {txt_path}")
    print()

    if safety_aborted:
        return 1
    print("  ✓ Voltage-sensitivity sweep complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
