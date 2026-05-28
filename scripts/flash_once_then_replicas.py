#!/usr/bin/env python3
"""
flash_once_then_replicas.py

For ONE firmware (curve+mode), do:
  1. Flash STM32 once via SWD (PPK2 closed during flash to avoid bug)
  2. Show BIG prompt for user to unplug+replug PPK2
  3. Open PPK2, wait 15s for stabilization, verify D-channels healthy
  4. Run N replicas, each one:
       a. NRST pulse via RPi GPIO 18 to reset STM32 (PPK2 stays open)
       b. wait 3s for STM32 boot
       c. start_measuring, sample for duration_s, stop_measuring
       d. write CSV
  5. Final: PPK2 OFF, summary

The bug: openocd flash via SWD breaks PPK2 D-channels. Only physical
unplug+replug recovers. So we flash ONCE per firmware, prompt, then
keep PPK2 open and reset via NRST (no SWD activity).
"""
import argparse, csv, json, subprocess, sys, time, collections
from pathlib import Path
import serial.tools.list_ports
from ppk2_api.ppk2_api import PPK2_API


# ── Timings (BETON-BARZEL) ──
POST_FLASH_PROMPT_WAIT_S = 15.0  # wait after user replugs PPK2
POST_NRST_BOOT_S = 3.0           # STM32 boot time after NRST
SAMPLE_START_SETTLE_S = 0.5      # let measurement stabilize
DRAIN_INTERVAL_S = 0.05          # PPK2 buffer drain cadence
PPK2_SAMPLE_RATE_HZ = 100000
# Only D0 (PA0=COMPUTE) and D1 (PA1=WAIT) carry signal; D2-D7 float
# high (unconnected) so the raw byte reads 0xFF. Mask to the wires
# we actually use for diversity validation. See doc/NRST_DISCOVERY.md.
GPIO_TRIG_MASK = 0x03  # bits 0,1 = PA0,PA1 (Mode A uses both; Mode B uses bit0)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_ppk2_port():
    for p in serial.tools.list_ports.comports():
        if (p.vid, p.pid) == (0x1915, 0xc00a):
            return p.device
    return None


def stop_modem_manager():
    """Stop ModemManager so it doesn't grab /dev/ttyACM0."""
    r = subprocess.run(["sudo", "-n", "systemctl", "stop", "ModemManager"],
                       capture_output=True, text=True)
    time.sleep(1)


def flash_stm32(rpi_user, rpi_host, elf_path, voltage_mv=3300):
    """Flash STM32 via SWD. Powers DUT via PPK2 during flash, then releases PPK2.
    
    Sequence:
      1. Open PPK2 briefly, power DUT, close PPK2 (DUT stays powered)
      2. scp ELF to RPi
      3. openocd flash (DUT is powered, so flash can write)
      4. After flash, DUT continues running on PPK2 power
    """
    # 1. Power DUT via PPK2 (then close PPK2 so it's available for next step)
    port = find_ppk2_port()
    if not port:
        log("[flash] PPK2 not found")
        return False
    log(f"[flash] opening PPK2 briefly to power DUT")
    ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    ppk2.set_source_voltage(voltage_mv)
    ppk2.use_source_meter()
    ppk2.toggle_DUT_power("ON")
    time.sleep(2)  # let voltage stabilize
    log(f"[flash] DUT powered @ {voltage_mv}mV")
    # IMPORTANT: do NOT close ppk2 — closing the serial may cut power.
    # Keep ppk2 object alive in caller. We'll close it later in main flow.

    # 2. scp ELF
    elf_basename = Path(elf_path).name
    rpi_elf = f"/home/pi/{elf_basename}"
    log(f"[flash] scp {elf_basename} → {rpi_user}@{rpi_host}:{rpi_elf}")
    r = subprocess.run(["scp", "-q", str(elf_path), f"{rpi_user}@{rpi_host}:{rpi_elf}"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        log(f"[flash] scp FAILED: {r.stderr}")
        ppk2.toggle_DUT_power("OFF")
        ppk2.ser.close()
        return False

    # 3. flash via openocd (PPK2 keeps DUT powered)
    #    Release any NRST holder first — openocd needs GPIO 18 as srst.
    nrst_release(rpi_user, rpi_host)
    log(f"[flash] openocd program {elf_basename}")
    r = subprocess.run(["ssh", f"{rpi_user}@{rpi_host}",
                       "sudo openocd -f /home/pi/rpi_swd.cfg "
                       "-c 'init' -c 'reset halt' "
                       f"-c 'program {rpi_elf} verify reset exit'"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    if "Verified OK" not in out or "Programming Finished" not in out:
        log(f"[flash] FLASH FAILED — STM32 may be in corrupt state.")
        log(f"[flash] tail of openocd output:")
        for line in out[-800:].split("\n"):
            log(f"        {line}")
        # Try recovery: unlock + mass_erase + retry
        log(f"[flash] attempting recovery: unlock + mass_erase")
        r2 = subprocess.run(["ssh", f"{rpi_user}@{rpi_host}",
                           "sudo openocd -f /home/pi/rpi_swd.cfg "
                           "-c 'init' -c 'reset halt' "
                           "-c 'stm32f4x unlock 0' "
                           "-c 'reset halt' "
                           "-c 'stm32f4x mass_erase 0' "
                           "-c 'reset halt' -c 'exit'"],
                           capture_output=True, text=True, timeout=60)
        out2 = r2.stdout + r2.stderr
        if "mass erase complete" not in out2:
            log("[flash] recovery FAILED")
            ppk2.toggle_DUT_power("OFF")
            ppk2.ser.close()
            return False
        log("[flash] mass_erase OK, retrying program")
        r3 = subprocess.run(["ssh", f"{rpi_user}@{rpi_host}",
                           "sudo openocd -f /home/pi/rpi_swd.cfg "
                           "-c 'init' -c 'reset halt' "
                           f"-c 'program {rpi_elf} verify reset exit'"],
                           capture_output=True, text=True, timeout=120)
        out3 = r3.stdout + r3.stderr
        if "Verified OK" not in out3 or "Programming Finished" not in out3:
            log("[flash] retry STILL failed")
            log(out3[-500:])
            ppk2.toggle_DUT_power("OFF")
            ppk2.ser.close()
            return False
        log("[flash] ✓ recovery + reflash successful")
    else:
        log(f"[flash] ✓ {elf_basename} programmed + verified")

    # Keep DUT powered, but close PPK2 so user can unplug.
    # toggle_DUT_power("ON") state is HW-latched in PPK2; closing serial
    # doesn't cut it.
    ppk2.ser.close()
    log("[flash] PPK2 serial closed; DUT remains powered (HW-latched)")
    return True


def nrst_release(rpi_user, rpi_host):
    """Kill any background gpioset holding NRST, freeing GPIO 18.

    Must be called before any openocd command (which needs GPIO 18 as
    srst) and before a fresh nrst_pulse.
    """
    subprocess.run(
        ["ssh", f"{rpi_user}@{rpi_host}",
         "sudo pkill -f 'gpioset.*gpiochip0 18' 2>/dev/null; true"],
        capture_output=True, text=True, timeout=10)
    time.sleep(0.3)


def nrst_pulse(rpi_user, rpi_host):
    """Reset STM32 via NRST (GPIO 18), then HOLD NRST HIGH actively.

    CRITICAL (Day 5 root-cause fix): the previous implementation let the
    line FLOAT after the pulse. The STM32 internal NRST pull-up (~40k) is
    too weak against noise on the floating RPi GPIO, so NRST kept spuriously
    re-triggering every ~2.7ms -> permanent reset loop. Firmware never got
    past CURVE_INIT (mis-diagnosed for days as a RELIC hang).

    Fix: after the LOW pulse, a BACKGROUND gpioset actively drives NRST
    HIGH and stays alive (setsid, survives SSH). Holds NRST high stably
    with zero SWD activity -> PPK2 D-channels preserved AND STM32 runs
    without reset-looping. Killed by nrst_release() before next pulse or
    any openocd access. See doc/NRST_DISCOVERY.md.
    """
    log("[nrst] resetting STM32 via GPIO 18, then holding NRST HIGH")
    nrst_release(rpi_user, rpi_host)
    r = subprocess.run(
        ["ssh", f"{rpi_user}@{rpi_host}",
         "sudo timeout 0.1 gpioset -c gpiochip0 18=0"],
        capture_output=True, text=True, timeout=10)
    if r.returncode not in (0, 124):
        log(f"[nrst] LOW pulse FAILED: rc={r.returncode} stderr={r.stderr}")
        return False
    r = subprocess.run(
        ["ssh", f"{rpi_user}@{rpi_host}",
         "sudo setsid bash -c 'gpioset -c gpiochip0 18=1' "
         "</dev/null >/dev/null 2>&1 & echo held"],
        capture_output=True, text=True, timeout=10)
    if "held" not in (r.stdout + r.stderr):
        log(f"[nrst] HOLD-HIGH FAILED: rc={r.returncode} stderr={r.stderr}")
        return False
    log("[nrst] OK pulsed LOW then HOLDING HIGH (no float, no reset-loop)")
    return True


def open_ppk2(port, voltage_mv=3300):
    """Open PPK2, configure source mode, turn DUT ON."""
    log(f"[ppk2] opening at {port}")
    ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
    ppk2.get_modifiers()
    ppk2.set_source_voltage(voltage_mv)
    ppk2.use_source_meter()
    ppk2.toggle_DUT_power("ON")
    log(f"[ppk2] DUT power ON @ {voltage_mv}mV")
    return ppk2


def sample_digital(ppk2, duration_s, label):
    """Sample PPK2 digital channels for diagnostic purposes."""
    ppk2.start_measuring()
    time.sleep(SAMPLE_START_SETTLE_S)
    seen = collections.Counter()
    t0 = time.time()
    while time.time() - t0 < duration_s:
        raw = ppk2.get_data()
        if raw:
            r = ppk2.get_samples(raw)
            if isinstance(r, tuple) and len(r) > 1:
                seen.update(r[1])
        time.sleep(DRAIN_INTERVAL_S)
    ppk2.stop_measuring()
    total = sum(seen.values()) or 1
    log(f"[probe:{label}] D-channels: {dict(seen)} ({total} samples)")
    return dict(seen)


def measure_replica(ppk2, csv_out, duration_s, replica_num):
    """Run one replica: NRST pulse done before this. Sample to CSV.
    
    Returns (samples_count, unique_gpio_values).
    """
    log(f"[replica {replica_num}] start_measuring, sampling {duration_s}s")
    ppk2.start_measuring()
    time.sleep(SAMPLE_START_SETTLE_S)

    csv_fp = csv_out.open("w", encoding="utf-8", newline="")
    writer = csv.writer(csv_fp)
    writer.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte", "gpio_masked"])

    samples_count = 0
    gpio_seen = collections.Counter()
    t_us = 0
    period_us = int(1_000_000 / PPK2_SAMPLE_RATE_HZ)
    t0 = time.time()
    last_log = t0
    try:
        while time.time() - t0 < duration_s:
            raw = ppk2.get_data()
            if raw:
                result = ppk2.get_samples(raw)
                if isinstance(result, tuple) and len(result) >= 2:
                    currents, digitals = result[0], result[1]
                    for current_uA, gpio in zip(currents, digitals):
                        gpio_m = gpio & GPIO_TRIG_MASK
                        writer.writerow([t_us, f"{current_uA:.2f}", "3.30", gpio, gpio_m])
                        t_us += period_us
                        gpio_seen[gpio_m] += 1
                        samples_count += 1
            time.sleep(DRAIN_INTERVAL_S)
            if time.time() - last_log > 30:
                log(f"[replica {replica_num}] elapsed {time.time()-t0:.0f}s, {samples_count} samples")
                last_log = time.time()
    finally:
        ppk2.stop_measuring()
        csv_fp.close()

    log(f"[replica {replica_num}] done: {samples_count} samples → {csv_out.name}")
    log(f"[replica {replica_num}] gpio diversity: {dict(gpio_seen)}")
    return samples_count, sorted(gpio_seen.keys())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--curve", required=True, choices=["BN254", "BLS12_381"])
    p.add_argument("--mode", required=True, choices=["A", "B"])
    p.add_argument("--elf", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--replicas", type=int, default=10)
    p.add_argument("--duration-s", type=float, default=220.0,
                   help="Sample duration per replica")
    p.add_argument("--rpi-user", default="pi")
    p.add_argument("--rpi-host", default="raspberrypi.local")
    p.add_argument("--ppk2-port", default="/dev/ttyACM0")
    p.add_argument("--voltage-mv", type=int, default=3300)
    p.add_argument("--auto-confirm", action="store_true",
                   help="Skip the manual PPK2 unplug prompt (for testing)")
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    log(f"flash_once_then_replicas: {args.curve} {args.mode}, "
        f"{args.replicas} replicas × {args.duration_s}s")

    # ─── Phase 1: Flash (PPK2 closed) ──────────────────────────────
    log("")
    log("═══ Phase 1: Flash STM32 (PPK2 not connected to script) ═══")
    stop_modem_manager()
    if not flash_stm32(args.rpi_user, args.rpi_host, args.elf):
        log("✗ Flash failed")
        return 1

    # ─── Phase 2: Big prompt for user ──────────────────────────────
    log("")
    print()
    print("┌────────────────────────────────────────────────────────────┐")
    print("│                                                            │")
    print("│   ⚠  ACTION REQUIRED  ⚠                                   │")
    print("│                                                            │")
    print("│   1. UNPLUG the PPK2 USB cable from this computer          │")
    print("│   2. Wait ~5 seconds                                       │")
    print("│   3. PLUG it back in                                       │")
    print("│   4. Press ENTER to continue                               │")
    print("│                                                            │")
    print("│   (Reason: PPK2 firmware bug — D-channels stuck after      │")
    print("│   any SWD flash. Only physical unplug recovers.)           │")
    print("│                                                            │")
    print("└────────────────────────────────────────────────────────────┘")
    print()
    if not args.auto_confirm:
        input("Press ENTER after replug → ")

    # ─── Phase 3: Open PPK2 + verify D-channels ────────────────────
    log("")
    log("═══ Phase 3: Open PPK2 + verify D-channels healthy ═══")
    stop_modem_manager()
    log(f"[wait] {POST_FLASH_PROMPT_WAIT_S:.0f}s for PPK2 internal stabilization")
    time.sleep(POST_FLASH_PROMPT_WAIT_S)

    port = find_ppk2_port()
    if not port:
        log(f"[ppk2] no PPK2 found — did you replug?")
        return 1
    log(f"[ppk2] found at {port}")

    ppk2 = open_ppk2(port, args.voltage_mv)
    time.sleep(POST_NRST_BOOT_S)  # firmware boots since we just powered DUT

    # Verify D-channels work — sample 5s
    seen = sample_digital(ppk2, 5, "post-replug")
    if len(seen) == 1 and 0 in seen:
        log("✗ D-channels STILL stuck at 0 after replug")
        log("  PPK2 not recovered. Check connections, try unplug+replug again.")
        ppk2.toggle_DUT_power("OFF")
        return 2
    log("✓ D-channels healthy")

    # ─── Phase 4: Run replicas with NRST between ───────────────────
    log("")
    log(f"═══ Phase 4: Run {args.replicas} replicas (NRST between, no flash) ═══")

    cell_results = []
    for r in range(1, args.replicas + 1):
        log("")
        log(f"───── Replica {r}/{args.replicas} ─────")

        # NRST to reset STM32 (does NOT touch PPK2)
        if not nrst_pulse(args.rpi_user, args.rpi_host):
            log(f"[replica {r}] NRST failed — skipping")
            cell_results.append({"replica": r, "status": "nrst-fail"})
            continue
        time.sleep(POST_NRST_BOOT_S)

        csv_out = args.out_dir / f"run_{r:03d}.csv"
        try:
            n, gpio_uniques = measure_replica(ppk2, csv_out, args.duration_s, r)
            cell_results.append({
                "replica": r,
                "samples": n,
                "gpio_uniques": gpio_uniques,
                "csv": csv_out.name,
            })
            if len(gpio_uniques) == 1 and 0 in gpio_uniques:
                log(f"[replica {r}] ⚠ WARNING: gpio_byte stuck at 0!")
        except Exception as e:
            log(f"[replica {r}] EXCEPTION: {e}")
            cell_results.append({"replica": r, "status": "exception", "err": str(e)})

    # ─── Phase 5: Cleanup ──────────────────────────────────────────
    log("")
    log("═══ Phase 5: Cleanup ═══")
    ppk2.toggle_DUT_power("OFF")
    log("[ppk2] DUT power OFF")
    nrst_release(args.rpi_user, args.rpi_host)
    log("[nrst] released GPIO 18 holder")

    # Summary
    summary_path = args.out_dir / "summary.json"
    summary = {
        "curve": args.curve,
        "mode": args.mode,
        "elf": str(args.elf),
        "replicas_attempted": args.replicas,
        "replicas_completed": sum(1 for r in cell_results if "samples" in r),
        "replicas_with_gpio_diversity": sum(
            1 for r in cell_results
            if "gpio_uniques" in r and len(r["gpio_uniques"]) > 1
        ),
        "results": cell_results,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    log(f"[summary] {summary['replicas_completed']}/{args.replicas} complete, "
        f"{summary['replicas_with_gpio_diversity']} with gpio diversity → {summary_path}")

    return 0 if summary['replicas_completed'] == args.replicas else 1


if __name__ == "__main__":
    sys.exit(main())
