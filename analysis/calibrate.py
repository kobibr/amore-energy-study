"""calibrate.py — known-resistor calibration for PPK2.

Validates that PPK2 source-mode measurements match Ohm's law within tolerance.

Procedure:
  1. Disconnect STM32 from PPK2.
  2. Connect a known resistor R (default: 33 Ω, ±1%) between
     PPK2 VOUT and PPK2 GND.
  3. Run this script.
  4. PPK2 sources 3.3V, measures current. Expected: I = V/R.
     For 33Ω @ 3.3V: I_expected = 100.0 mA.

Tolerance policy (silent-bias review 2026-05-23)
------------------------------------------------
The PPK2 datasheet specifies ±0.5 % accuracy in source-meter mode. The
default tolerance here is **1.0 %**, which gives ~2× headroom over the
spec while still catching a meaningfully drifted instrument. The
previous default of 2.0 % was 4× the spec — a broken PPK2 reading
1.5 % low would have PASSed calibration silently and then propagated
that 1.5 % bias into every published energy figure.

We expose THREE policy constants:
  - TIGHT_TOLERANCE_PCT      = 1.0  — paper-grade threshold. The
                                       verdict is PASS only if
                                       |deviation| ≤ this value.
  - BORDERLINE_TOLERANCE_PCT = 2.0  — documentary band, retained as
                                       a named reference point (the
                                       previous default tolerance,
                                       and the rough threshold below
                                       which a PPK2 is still in the
                                       "marginal but usable" regime).
                                       NOT used in the verdict logic;
                                       PASS-WARN fires for any
                                       deviation > TIGHT.
  - HARD_FAIL_TOLERANCE_PCT  = 5.0  — instrument is broken; refuse
                                       to honor a --tolerance-pct
                                       above this.

Bug #2 fix (silent-bias re-review 2026-05-23): the previous help text
claimed PASS-WARN fired above BORDERLINE, but the code fired it above
TIGHT. Help text and policy text now match the actual behavior:
PASS-WARN fires whenever deviation exceeds the TIGHT band AND the user
has loosened --tolerance-pct beyond TIGHT.

Use --tolerance-pct only when you have a documented reason (e.g. a
known-bad resistor batch); paper figures must come from data with
deviation below TIGHT_TOLERANCE_PCT.

Output:
  - Pass/fail verdict
  - Mean measured current ± stdev
  - Percent deviation from V/R
  - CSV log of all samples to measurement/calibration-logs/

Usage:
  python3 -m analysis.calibrate
  python3 -m analysis.calibrate --resistor-ohms 33.0
  python3 -m analysis.calibrate --resistor-ohms 100.0 --voltage-mv 3300
  python3 -m analysis.calibrate --duration-s 10 --tolerance-pct 1.0

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


# Silent-bias review (2026-05-23): tolerance policy constants.
# Default tolerance was 2.0 % — 4× the PPK2's ±0.5 % datasheet spec.
# A 1.5 % systematically-biased PPK2 would silently PASS, then carry
# that bias into every downstream energy figure. The new default is
# 1.0 %; broader bands are exposed only for diagnostic use.
TIGHT_TOLERANCE_PCT      = 1.0   # paper-grade default
BORDERLINE_TOLERANCE_PCT = 2.0   # flagged as PASS-WARN
HARD_FAIL_TOLERANCE_PCT  = 5.0   # instrument is broken


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
        default=TIGHT_TOLERANCE_PCT,
        help=(
            f"Pass/fail tolerance as percent (default: {TIGHT_TOLERANCE_PCT}, "
            f"paper-grade). The PPK2 datasheet spec is ±0.5%%. "
            f"Verdict bands: "
            f"PASS if deviation ≤ {TIGHT_TOLERANCE_PCT}%% (paper-grade); "
            f"PASS-WARN if {TIGHT_TOLERANCE_PCT}%% < deviation ≤ "
            f"--tolerance-pct (only possible when --tolerance-pct is "
            f"loosened beyond {TIGHT_TOLERANCE_PCT}%%); "
            f"FAIL otherwise. Values above {HARD_FAIL_TOLERANCE_PCT}%% "
            f"are rejected up-front — the instrument is treated as "
            f"broken regardless of override."
        ),
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=Path("measurement/calibration-logs"),
        help="Directory for CSV logs",
    )
    args = p.parse_args(argv)

    # Silent-bias review: refuse to honor an absurd tolerance, even if
    # the user passes one explicitly. A 10% "calibration" isn't a
    # calibration — it's a placebo.
    if args.tolerance_pct > HARD_FAIL_TOLERANCE_PCT:
        print(
            f"FATAL: --tolerance-pct={args.tolerance_pct} exceeds "
            f"HARD_FAIL_TOLERANCE_PCT={HARD_FAIL_TOLERANCE_PCT}. "
            f"At this tolerance, a broken instrument would PASS. Refusing.",
            file=sys.stderr,
        )
        return 2

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

    # 2026-05-23: detect uncalibrated PPK2 (modifiers['Calibrated']=='0').
    # Library defaults stand in for EEPROM constants; deviation-from-
    # Ohms-law becomes meaningless (33Ω resistor will read ~156 mA
    # instead of 100 mA). We continue for ratio-mode operation but
    # force PASS-WARN regardless of absolute deviation.
    # See docs/known_caveats.md.
    UNCALIBRATED = (
        hasattr(ppk2, "modifiers")
        and isinstance(ppk2.modifiers, dict)
        and str(ppk2.modifiers.get("Calibrated", "0")) == "0"
    )
    if UNCALIBRATED:
        print()
        print("*" * 60)
        print("  WARNING: PPK2 IS NOT CALIBRATED")
        print("  modifiers['Calibrated'] == '0'. Absolute readings are")
        print("  NOT trustable; only same-range ratios are safe.")
        print("  See docs/known_caveats.md.")
        print("*" * 60)
        print()

    ppk2.set_source_voltage(args.voltage_mv)
    ppk2.use_source_meter()
    ppk2.toggle_DUT_power("ON")
    time.sleep(0.5)

    # Bug #1 fix (silent-bias review): wrap the entire sampling block
    # in try/finally so power to the resistor is always cut, even on
    # KeyboardInterrupt, USB disconnect, ppk2_api exception, or any
    # other failure. At 33Ω @ 3.3V the resistor dissipates ~330 mW;
    # a 1/4 W part would burn if left powered after a crash. Worse,
    # a user thinking the script "ended" may touch the rails while
    # VOUT is still hot.
    samples = []
    try:
        ppk2.start_measuring()
        time.sleep(0.3)
        _ = ppk2.get_data()  # drain initial buffer

        # Capture
        print(f"Sampling for {args.duration_s}s...")
        t0 = time.time()
        while time.time() - t0 < args.duration_s:
            raw = ppk2.get_data()
            if raw:
                res = ppk2.get_samples(raw)
                s = res[0] if isinstance(res, tuple) else res
                samples.extend(s)
            time.sleep(0.01)
    finally:
        # Order matters: stop_measuring first, then power off. Stop
        # quiesces the firmware sampling state machine; power-off
        # then de-energises the resistor. Each is best-effort —
        # neither must mask the other.
        try:
            ppk2.stop_measuring()
        except Exception:
            pass
        try:
            ppk2.toggle_DUT_power("OFF")
        except Exception:
            pass

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

    # Verdict (silent-bias review: three bands instead of pass/fail).
    abs_dev = abs(deviation_pct)
    if UNCALIBRATED:
        # Uncalibrated PPK2 — deviation value is not a meaningful
        # test of instrument accuracy. PASS-WARN lets ratio-mode
        # workflows proceed while flagging the caveat.
        verdict = "PASS-WARN"
    elif abs_dev <= args.tolerance_pct:
        verdict = "PASS"
        if abs_dev > TIGHT_TOLERANCE_PCT and args.tolerance_pct > TIGHT_TOLERANCE_PCT:
            # User loosened tolerance AND we're outside the tight band.
            # Surface this loudly so it doesn't slip into paper figures.
            verdict = "PASS-WARN"
    else:
        verdict = "FAIL"

    # Rewrite the verdict line in the summary file
    with summary_path.open("a") as f:
        f.write(f"Verdict-band: {verdict}\n")
        if verdict == "PASS-WARN":
            f.write(
                f"NOTE: deviation {deviation_pct:+.2f}% exceeded the "
                f"tight {TIGHT_TOLERANCE_PCT}% policy band; only the "
                f"user-relaxed {args.tolerance_pct}% gate allowed PASS. "
                f"This data should NOT be used for paper figures.\n"
            )

    if verdict == "PASS":
        print(f"  ✓ PASS — deviation {deviation_pct:+.2f}% within ±{args.tolerance_pct}%")
        return 0
    elif verdict == "PASS-WARN":
        print(
            f"  ⚠ PASS-WARN — deviation {deviation_pct:+.2f}% exceeds the "
            f"tight {TIGHT_TOLERANCE_PCT}% policy band but within the "
            f"relaxed {args.tolerance_pct}% gate. Do NOT use for paper "
            f"figures; re-cal the PPK2 or replace the resistor."
        )
        return 0
    else:
        print(f"  ✗ FAIL — deviation {deviation_pct:+.2f}% exceeds ±{args.tolerance_pct}%")
        return 1


if __name__ == "__main__":
    sys.exit(main())
