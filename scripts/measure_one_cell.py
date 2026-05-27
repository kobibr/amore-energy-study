#!/usr/bin/env python3
"""
measure_one_cell.py — Single-cell measurement orchestrator.

Owns the PPK2 for the full lifecycle of ONE cell:
  1. Open PPK2 (sole owner of the USB device)
  2. Configure source mode @ voltage_mV
  3. Power DUT ON (LED RED)
  4. Flash STM32 firmware via SSH/openocd (PPK2 supplying power)
  5. Wait for STM32 boot
  6. Start RPi server.py in background (Mode A only)
  7. Start PPK2 sampling, drain to CSV
  8. Wait expected_duration_s (firmware finishes; PPK2 keeps sampling
     so we cover the full execution window plus a safety buffer)
  9. Stop sampling, close CSV
 10. GDB telemetry dump (read g_results / g_pb_results from RAM)
 11. Power DUT OFF (LED GREEN), close PPK2

Robustness:
  - Open/close PPK2 inside a try/finally so the device is released
    even if mid-cell exceptions fire.
  - On any fatal error: power DUT OFF before re-raising.
  - Validate every step's postcondition (flash verify, sample count,
    GDB output).

This script is the single owner of the PPK2 during its run. Any other
process touching the PPK2 concurrently is a bug.

Usage:
  python3 measure_one_cell.py \\
      --curve BN254 --mode A --replica 1 \\
      --elf /path/to/amore_bn254.elf \\
      --rpi-user pi --rpi-host 10.x.x.x \\
      --honest-rounds 61 \\
      --duration 5000 \\
      --out /tmp/cell_dir

Exit codes:
  0 = cell completed (CSV + telemetry written)
  1 = recoverable failure (caller may retry)
  2 = fatal (don't retry, e.g. PPK2 not present, wrong wiring)
"""
from __future__ import annotations

import argparse
import csv as csv_mod
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_VOLTAGE_MV = 3300
BOOT_SETTLE_S = 0.5       # after toggle_DUT_power(ON)
POST_FLASH_SETTLE_S = 1.0  # after flash, before sampling
SERVER_STARTUP_S = 2.0    # let RPi server bind UART
DRAIN_INTERVAL_S = 0.05   # 50 ms — PPK2 buffer drain cadence
# PPK2 sample period is determined empirically per-run. The ppk2_api
# library does not expose a stable sample rate; in source mode we observe
# ~17 ksps (~58 us/sample), but this varies with USB latency and buffer
# fullness. We measure the actual period during a brief calibration window
# at the start of each cell, then use it to assign per-sample timestamps
# linearly within each batch.

# Stop-condition durations (seconds). Mode A: server.py controls;
# Mode B: fixed time per the C decision in the architecture review.
# These are CEILINGS (--duration overrides).
DEFAULT_DURATION_BN254_A = 5000   # 61 rounds × ~74s/round + slack
DEFAULT_DURATION_BLS_A   = 6500   # 61 honest × ~90s + 1 mal × ~90s + slack
DEFAULT_DURATION_BN254_B = 400    # 61 × ~3s pairings + slack
DEFAULT_DURATION_BLS_B   = 1200   # 10 pairings × ~87s + init 2s + slack


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ════════════════════════════════════════════════════════════════════════
#  BETON-BARZEL utilities: hard-verified state transitions for PPK2 + DUT
#  These wrap "fire and pray" operations with explicit wait+verify loops.
# ════════════════════════════════════════════════════════════════════════

def _ppk2_port_present() -> str | None:
    """Return path to PPK2 device by VID:PID, or None if absent."""
    import serial.tools.list_ports
    for p in serial.tools.list_ports.comports():
        try:
            if p.vid == 0x1915 and p.pid == 0xc00a:
                return p.device
        except Exception:
            pass
    return None


def wait_for_ppk2_present(timeout_s: float = 60.0,
                           required_consecutive: int = 5,
                           wait_between_s: float = 2.0) -> str:
    """BETON-BARZEL: block until PPK2 is detectable AT THE SAME PATH for
    N consecutive checks. Each check is *only* a listing of serial ports —
    we do NOT open the PPK2, because doing so triggers a USB disconnect/
    reconnect cycle that destabilizes the device.
    
    Returns the device path. Fatal if timeout."""
    t0 = time.time()
    last_port = None
    stable = 0
    attempt = 0
    while time.time() - t0 < timeout_s:
        attempt += 1
        port = _ppk2_port_present()
        if port:
            if port == last_port:
                stable += 1
                if stable >= required_consecutive:
                    log(f"[ppk2-wait] STABLE at {port} ({required_consecutive} consecutive enumerations, {time.time()-t0:.1f}s)")
                    return port
            else:
                last_port = port
                stable = 1
        else:
            if attempt <= 3 or attempt % 5 == 0:
                log(f"[ppk2-wait] attempt {attempt}: PPK2 absent from device list")
            stable = 0
            last_port = None
        time.sleep(wait_between_s)
    fatal(f"PPK2 not stable after {timeout_s}s (got {stable}/{required_consecutive} consecutive enumerations)")


def wait_for_ppk2_absent(timeout_s: float = 10.0) -> None:
    """Block until PPK2 device disappears from /dev. Used between hard cycles."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _ppk2_port_present() is None:
            log(f"[ppk2-wait] absent (took {time.time()-t0:.1f}s)")
            return
        time.sleep(0.2)
    # If still present after timeout, that's OK — PPK2 may not actually
    # disappear on toggle_DUT_power; just log and continue
    log(f"[ppk2-wait] still present after {timeout_s}s (this is OK if DUT power only)")


def stop_modem_manager() -> None:
    """Stop ModemManager (background service that grabs CDC-ACM devices).
    Without this, PPK2 randomly becomes unavailable mid-run.
    BETON-BARZEL: wait+verify with generous timeout."""
    log("[mm] stopping ModemManager (required for stable PPK2 access)")
    subprocess.run(["sudo", "-n", "systemctl", "stop", "ModemManager"],
                   capture_output=True, check=False)
    # Verify stopped: poll status until inactive.
    # is-active returns exit 3 for inactive (that's by design, not error).
    # Capture stdout only; ignore exit code.
    for attempt in range(30):  # 30 × 0.5s = 15s budget
        r = subprocess.run(["sudo", "-n", "systemctl", "is-active", "ModemManager"],
                           capture_output=True, text=True, check=False)
        state = (r.stdout or "").strip()
        if state in ("inactive", "failed"):
            log(f"[mm] confirmed stopped (state={state}, {attempt*0.5:.1f}s)")
            time.sleep(1.0)  # extra settling
            return
        time.sleep(0.5)
    fatal("ModemManager did not stop within 15s — aborting (would corrupt PPK2)")


def start_modem_manager() -> None:
    """Restart ModemManager for normal system operation. Wait+verify.
    BETON-BARZEL: generous timeout, but only WARN on failure (not fatal —
    measurements are already done by this point)."""
    log("[mm] restarting ModemManager (post-run cleanup)")
    subprocess.run(["sudo", "-n", "systemctl", "start", "ModemManager"],
                   capture_output=True, check=False)
    for attempt in range(20):  # 20 × 0.5s = 10s budget
        r = subprocess.run(["sudo", "-n", "systemctl", "is-active", "ModemManager"],
                           capture_output=True, text=True, check=False)
        state = (r.stdout or "").strip()
        if state == "active":
            log(f"[mm] confirmed active again ({attempt*0.5:.1f}s)")
            return
        time.sleep(0.5)
    log("[mm] WARNING: ModemManager did not restart in 10s — system runs without MM")


def hard_power_cycle_dut(ppk2, voltage_mv: int):
    """BETON-BARZEL DUT power-cycle. Required after flash so PPK2's digital
    channel state resets properly.
    
    Steps (all with generous waits + verify):
      1. DUT OFF                         → wait 3s
      2. Close PPK2 serial                → wait 3s
      3. Verify PPK2 stable (5 opens)     → wait 2s
      4. Reopen PPK2 + reconfigure        → wait 1s
      5. DUT ON                           → wait 3s (firmware boots, Triggers_Init runs)
    
    Returns the new PPK2 handle. Old handle is invalidated."""
    from ppk2_api.ppk2_api import PPK2_API
    import gc
    
    # 1. DUT OFF
    log("[dut] hard power-cycle step 1/5: DUT OFF")
    try:
        ppk2.toggle_DUT_power("OFF")
    except Exception as e:
        log(f"[dut]   toggle_DUT_power(OFF) raised: {e}")
    time.sleep(3.0)  # generous: USB host needs time to notice
    
    # 2. Close PPK2 serial
    log("[dut] hard power-cycle step 2/5: close PPK2 serial")
    try:
        ppk2.ser.close()
    except Exception:
        pass
    del ppk2
    gc.collect()
    time.sleep(3.0)  # generous: let USB subsystem settle
    
    # 3. Verify PPK2 stable
    log("[dut] hard power-cycle step 3/5: verify PPK2 stable (5 consecutive)")
    port = wait_for_ppk2_present(timeout_s=60.0, required_consecutive=5, wait_between_s=2.0)
    time.sleep(2.0)  # extra settling
    
    # 4. Reopen + reconfigure
    log(f"[dut] hard power-cycle step 4/5: reopen PPK2 at {port}")
    new_ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
    new_ppk2.get_modifiers()
    new_ppk2.set_source_voltage(voltage_mv)
    new_ppk2.use_source_meter()
    time.sleep(1.0)  # let mode change settle
    
    # 5. DUT ON
    log("[dut] hard power-cycle step 5/5: DUT ON + boot wait")
    new_ppk2.toggle_DUT_power("ON")
    time.sleep(3.0)  # firmware boots, Triggers_Init() runs, GPIO settles
    
    log("[dut] hard power-cycle complete")
    return new_ppk2



def fatal(msg: str, code: int = 2) -> None:
    log(f"FATAL: {msg}")
    sys.exit(code)


def discover_ppk2_port(hint: str | None) -> str:
    """Find PPK2 serial port, preferring `hint` if it exists."""
    import serial.tools.list_ports
    if hint and Path(hint).exists():
        return hint
    for p in serial.tools.list_ports.comports():
        desc = p.description or ""
        if "PPK" in desc or "Nordic" in desc:
            return p.device
    return ""


def flash_via_rpi(
    elf_path: Path,
    rpi_user: str,
    rpi_host: str,
    *,
    retries: int = 5,         # was 3 — network blips need more
    backoff_s: float = 5.0,   # was 3 — first retry delay
) -> bool:
    """Flash STM32 via RPi GPIO SWD with retries + verification.

    PPK2 MUST be powering DUT before calling this.
    """
    elf_basename = elf_path.name
    rpi_elf = f"/home/pi/{elf_basename}"

    for attempt in range(1, retries + 1):
        log(f"[flash] attempt {attempt}/{retries}: {elf_basename}")
        try:
            subprocess.run(
                ["scp", "-q", str(elf_path), f"{rpi_user}@{rpi_host}:{rpi_elf}"],
                check=True, timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log(f"[flash] scp failed: {e}")
            if attempt < retries:
                # Exponential-ish backoff: 5s, 15s, 30s, 60s, 120s
                # Long enough to cover a router reboot
                delays = [5, 15, 30, 60, 120]
                delay = delays[min(attempt - 1, len(delays) - 1)]
                log(f"[flash] sleeping {delay}s before retry {attempt+1}/{retries}")
                time.sleep(delay)
            continue

        try:
            result = subprocess.run(
                [
                    "ssh", f"{rpi_user}@{rpi_host}",
                    f"sudo openocd -f /home/pi/rpi_swd.cfg "
                    f"-c 'init; reset halt; program {rpi_elf} verify reset exit'",
                ],
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            log(f"[flash] openocd timed out (attempt {attempt})")
            if attempt < retries:
                delays = [5, 15, 30, 60, 120]
                delay = delays[min(attempt - 1, len(delays) - 1)]
                log(f"[flash] sleeping {delay}s before retry {attempt+1}/{retries}")
                time.sleep(delay)
            continue

        if "Programming Finished" in out and "Verified OK" in out:
            log(f"[flash] {elf_basename}: programmed + verified")
            return True
        else:
            log(f"[flash] verification failed (attempt {attempt}). openocd tail:")
            for line in out.splitlines()[-10:]:
                log(f"      {line}")
            if attempt < retries:
                time.sleep(backoff_s * attempt)

    return False


def gdb_dump(
    elf_path: Path,
    rpi_user: str,
    rpi_host: str,
    mode: str,
    out_txt: Path,
) -> bool:
    """Dump STM32 telemetry via GDB over openocd-pipe-through-SSH."""
    if mode == "A":
        gdb_cmds = f"""set pagination off
set print pretty on
target extended-remote | ssh {rpi_user}@{rpi_host} 'sudo openocd -f /home/pi/rpi_swd.cfg -c "gdb_port pipe; log_output /dev/null"'
file {elf_path}
printf "=== STM32 TELEMETRY (Mode A) ===\\n"
printf "status   = 0x%08x\\n", g_results.status
printf "wall_ms  = %u\\n", g_results.wall_ms
printf "total_rounds_sent = %u\\n", g_results.total_rounds_sent
printf "total_verify_ok   = %u\\n", g_results.total_verify_ok
printf "[N=1]  blind_total=%llu  verify_total=%llu  amort=%u\\n", g_results.blind_total_cycles[0], g_results.verify_total_cycles[0], g_results.amort_cycles[0]
printf "[N=10] blind_total=%llu  verify_total=%llu  amort=%u\\n", g_results.blind_total_cycles[1], g_results.verify_total_cycles[1], g_results.amort_cycles[1]
printf "[N=50] blind_total=%llu  verify_total=%llu  amort=%u\\n", g_results.blind_total_cycles[2], g_results.verify_total_cycles[2], g_results.amort_cycles[2]
printf "=== END ===\\n"
quit
"""
    else:
        gdb_cmds = f"""set pagination off
set print pretty on
target extended-remote | ssh {rpi_user}@{rpi_host} 'sudo openocd -f /home/pi/rpi_swd.cfg -c "gdb_port pipe; log_output /dev/null"'
file {elf_path}
printf "=== STM32 TELEMETRY (Mode B) ===\\n"
printf "status        = 0x%08x\\n", g_pb_results.status
printf "current_phase = 0x%02x\\n", g_pb_results.current_phase
printf "last_error    = 0x%08x\\n", g_pb_results.last_error
printf "init_ok       = %u\\n", g_pb_results.init_ok
printf "sanity_ok     = %u\\n", g_pb_results.sanity_ok
printf "n_iterations  = %u\\n", g_pb_results.n_iterations
printf "init_cycles   = %u\\n", g_pb_results.init_cycles
printf "pairing_min   = %u\\n", g_pb_results.pairing_min_cycles
printf "=== END ===\\n"
quit
"""

    # Pick GDB binary
    for gdb_bin in ("gdb-multiarch", "arm-none-eabi-gdb"):
        if subprocess.run(["which", gdb_bin], capture_output=True).returncode == 0:
            break
    else:
        log("[gdb] no GDB found")
        return False

    script = Path("/tmp") / f"gdb_dump_{os.getpid()}.gdb"
    script.write_text(gdb_cmds)
    try:
        result = subprocess.run(
            [gdb_bin, "-nx", "-batch", "-x", str(script)],
            capture_output=True, text=True, timeout=60,
        )
        lines = (result.stdout + result.stderr).splitlines()
        keep = [
            ln for ln in lines
            if any(ln.startswith(prefix) for prefix in
                   ("status", "wall", "total", "n_iter", "cycles", "[N=",
                    "current", "last", "init", "sanity", "n_iterations",
                    "blind_total", "verify_total", "amort",
                    "pairing_min", "==="))
        ]
        out_txt.write_text("\n".join(keep) + "\n")
        ok = bool(keep) and any("status" in ln for ln in keep)
        return ok
    except subprocess.TimeoutExpired:
        log("[gdb] timeout")
        return False
    finally:
        script.unlink(missing_ok=True)


def default_duration(curve: str, mode: str) -> int:
    table = {
        ("BN254", "A"):     DEFAULT_DURATION_BN254_A,
        ("BLS12_381", "A"): DEFAULT_DURATION_BLS_A,
        ("BN254", "B"):     DEFAULT_DURATION_BN254_B,
        ("BLS12_381", "B"): DEFAULT_DURATION_BLS_B,
    }
    return table.get((curve, mode), 300)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--curve", required=True, choices=["BN254", "BLS12_381"])
    p.add_argument("--mode", required=True, choices=["A", "B"])
    p.add_argument("--replica", type=int, required=True)
    p.add_argument("--elf", type=Path, required=True,
                   help="Path to firmware ELF for this curve+mode")
    p.add_argument("--out", type=Path, required=True,
                   help="Output directory for CSV + telemetry + manifest")
    p.add_argument("--rpi-user", default="pi")
    p.add_argument("--rpi-host", required=True)
    p.add_argument("--ppk2-port", default=None,
                   help="PPK2 serial port (auto-discover if omitted)")
    p.add_argument("--voltage-mv", type=int, default=DEFAULT_VOLTAGE_MV)
    p.add_argument("--honest-rounds", type=int, default=61,
                   help="Rounds for RPi server.py (Mode A only)")
    p.add_argument("--duration", type=float, default=None,
                   help="Sampling cap (s). Default = curve+mode standard.")
    p.add_argument("--smoke", action="store_true",
                   help="Smoke mode: shorter duration, honest_rounds=1")
    args = p.parse_args(argv)

    if not args.elf.exists():
        fatal(f"ELF not found: {args.elf}")
    if not args.out.exists():
        args.out.mkdir(parents=True, exist_ok=True)

    # Apply smoke overrides
    if args.smoke:
        args.honest_rounds = 1
        if args.duration is None:
            # Smoke duration must cover at least one full pairing for Mode B.
            # Mode A: 1 honest + 1 malicious = ~150s (BN254) / ~180s (BLS), 220s fits both.
            # Mode B BN254: sanity ~5s + 1 pairing ~3s = ~10s, but we wait for 2 pairings to validate.
            # Mode B BLS:   sanity ~5s + 1 pairing ~87s = ~95s. Need 200s+ to validate.
            if args.mode == "A":
                args.duration = 220.0
            elif args.curve == "BN254":
                args.duration = 60.0   # Mode B BN254: 10 pairings × ~3s = 30s + slack
            else:
                args.duration = 400.0  # Mode B BLS: sanity + 2 pairings + slack
    if args.duration is None:
        args.duration = float(default_duration(args.curve, args.mode))

    # Discover PPK2 port
    port = discover_ppk2_port(args.ppk2_port)
    if not port:
        fatal("No PPK2 device found")

    log(f"PPK2 port:    {port}")
    log(f"Curve/Mode:   {args.curve} / Mode {args.mode}")
    log(f"Replica:      {args.replica}")
    log(f"ELF:          {args.elf}")
    log(f"Duration cap: {args.duration:.0f}s")
    log(f"Honest rounds: {args.honest_rounds}")
    log(f"Output dir:   {args.out}")

    csv_path = args.out / "run_001.csv"
    telem_path = args.out / "telemetry.txt"
    log_path = args.out / "cell.log"
    manifest_path = args.out / "manifest.json"

    try:
        from ppk2_api.ppk2_api import PPK2_API
    except ImportError as e:
        fatal(f"ppk2_api not installed: {e}")

    # ── PPK2 ownership block ──────────────────────────────────────────────
    ppk2 = None
    csv_fp = None
    server_proc = None
    samples_collected = 0
    t_start = time.time()
    err_msg = ""

    def emergency_off():
        """Best-effort PPK2 OFF if anything goes wrong."""
        nonlocal ppk2
        if ppk2 is not None:
            try:
                ppk2.toggle_DUT_power("OFF")
            except Exception:
                pass

    def emergency_server_kill():
        nonlocal server_proc
        if server_proc is not None and server_proc.poll() is None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except Exception:
                try:
                    server_proc.kill()
                except Exception:
                    pass

    # Install signal handlers so Ctrl+C still powers down
    def signal_handler(signum, _frame):
        log(f"received signal {signum} — emergency shutdown")
        emergency_server_kill()
        emergency_off()
        sys.exit(3)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 0. BETON-BARZEL pre-flight:
        #    Assert ModemManager is stopped (full_regression.sh did it).
        #    Re-stop defensively in case caller didn't (e.g. manual run).
        stop_modem_manager()
        port = wait_for_ppk2_present(timeout_s=60.0)
        log(f"[ppk2] opening at {port}")

        # 1. Open PPK2
        ppk2 = PPK2_API(port, timeout=2, write_timeout=2)
        ppk2.get_modifiers()

        # Detect calibration state — paper-grade caveat
        uncalibrated = (
            hasattr(ppk2, "modifiers")
            and isinstance(ppk2.modifiers, dict)
            and str(ppk2.modifiers.get("Calibrated", "0")) == "0"
        )
        if uncalibrated:
            log("[ppk2] WARNING: PPK2 uncalibrated (Calibrated=0)")
            log("              Absolute mA values are not reliable;")
            log("              only ratios within same current range are safe.")

        # 2. Configure source mode + voltage
        ppk2.set_source_voltage(args.voltage_mv)
        ppk2.use_source_meter()
        log(f"[ppk2] source mode @ {args.voltage_mv}mV")

        # 3. Power on DUT
        ppk2.toggle_DUT_power("ON")
        log("[ppk2] DUT power ON (LED should be RED)")
        time.sleep(BOOT_SETTLE_S)

        # 4. Flash STM32 (PPK2 is supplying power, so flash will succeed)
        if not flash_via_rpi(args.elf, args.rpi_user, args.rpi_host):
            err_msg = "flash failed after retries"
            return 1
        time.sleep(POST_FLASH_SETTLE_S)

        # 4b. CRITICAL: hard power-cycle DUT after flash.
        # The openocd 'reset run' at the end of flash leaves PPK2's internal
        # digital channel state stuck — gpio_byte reads 0 only, no transitions.
        # A hardware OFF→close-PPK2→re-discover→reopen→ON cycle restores
        # full digital sampling.
        # Reproduced 2026-05-27 in Day 5; documented in DEBUG_NOTES.
        log("[ppk2] hard power-cycling DUT (post-flash digital reset)")
        ppk2 = hard_power_cycle_dut(ppk2, args.voltage_mv)

        # 5. (Mode A only) Start RPi server.py
        #    Curve-aware: BN254 uses server_bn254.py, BLS12_381 uses server.py
        #    (default BLS). The two scripts share the same UART protocol but
        #    differ in py_ecc imports + buffer sizes. See ISSUES.md / Day 5.
        server_log_path = args.out / "server.log"
        if args.mode == "A":
            if args.curve == "BN254":
                server_script = "/home/pi/amore-bn254-cortex-m4/rpi/server_bn254.py"
            else:
                server_script = "/home/pi/amore-bn254-cortex-m4/rpi/server.py"
            log(f"[server] starting RPi {Path(server_script).name} "
                f"(honest_rounds={args.honest_rounds})")
            with open(server_log_path, "w") as srv_log:
                server_proc = subprocess.Popen(
                    [
                        "ssh", f"{args.rpi_user}@{args.rpi_host}",
                        f"python3 {server_script} "
                        f"--port /dev/ttyAMA0 --baud 921600 "
                        f"--honest-rounds {args.honest_rounds}",
                    ],
                    stdout=srv_log, stderr=subprocess.STDOUT,
                )
            time.sleep(SERVER_STARTUP_S)
            if server_proc.poll() is not None:
                err_msg = f"server.py exited prematurely (code {server_proc.returncode}); see {server_log_path}"
                return 1

        # 6. Start PPK2 sampling
        log("[ppk2] start_measuring")
        ppk2.start_measuring()
        t_sample_start = time.time()

        # 6b. Calibrate sample period (empirical)
        # Drain for ~1 second, count actual samples, derive period.
        # This gives a per-cell calibrated period that adapts to USB/load.
        CALIB_S = 1.0
        calib_samples = 0
        calib_start = time.time()
        while time.time() - calib_start < CALIB_S:
            raw = ppk2.get_data()
            if raw:
                res = ppk2.get_samples(raw)
                s = res[0] if isinstance(res, tuple) else res
                calib_samples += len(s)
            time.sleep(DRAIN_INTERVAL_S)
        calib_elapsed = time.time() - calib_start
        if calib_samples < 100:
            log(f"[calib] WARNING: only {calib_samples} samples in {calib_elapsed:.2f}s — using default 58us")
            sample_period_us = 58
        else:
            sample_period_us = max(1, int(round(calib_elapsed * 1e6 / calib_samples)))
            log(f"[calib] {calib_samples} samples in {calib_elapsed:.2f}s → {sample_period_us}us/sample "
                f"({calib_samples/calib_elapsed:.0f} samples/s)")
        # The calibration samples are not part of the measurement — they
        # were drained but not written to CSV. The real sampling window
        # starts now.
        t_sample_start = time.time()

        # 7. Drain loop — write to CSV continuously
        csv_fp = csv_path.open("w", encoding="utf-8", newline="")
        writer = csv_mod.writer(csv_fp)
        writer.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])

        voltage_V = args.voltage_mv / 1000.0
        sample_ts_us = 0
        last_progress_log = t_sample_start

        _batch_count = [0]
        _digital_stats = {}
        def write_batch(samples, digital):
            """Write one batch of samples to CSV with monotonic timestamps."""
            nonlocal sample_ts_us, samples_collected
            # DEBUG: log first few batches' digital state
            _batch_count[0] += 1
            if _batch_count[0] <= 5 or _batch_count[0] % 500 == 0:
                if digital is None:
                    info = "digital=None"
                elif len(digital) == 0:
                    info = "digital=[]"
                else:
                    uniq = set(digital[:200])
                    info = f"digital_len={len(digital)} uniq_first200={uniq}"
                log(f"[debug] batch #{_batch_count[0]} samples={len(samples)} {info}")
            for i, current_uA in enumerate(samples):
                gpio_byte = digital[i] if (digital is not None and i < len(digital)) else 0
                writer.writerow([sample_ts_us, f"{current_uA:.3f}",
                                 f"{voltage_V:.3f}", int(gpio_byte) & 0xFF])
                sample_ts_us += sample_period_us
            samples_collected += len(samples)

        while True:
            elapsed = time.time() - t_sample_start
            if elapsed >= args.duration:
                log(f"[drain] duration cap reached ({elapsed:.1f}s)")
                break
            # Mode A early-exit: server.py finished → STM32 done
            if args.mode == "A" and server_proc is not None and server_proc.poll() is not None:
                log(f"[drain] server.py exited (code {server_proc.returncode}); draining final samples")
                # Drain another 2 seconds to capture trailing GPIO transitions
                time.sleep(2.0)
                raw = ppk2.get_data()
                if raw:
                    res = ppk2.get_samples(raw)
                    s = res[0] if isinstance(res, tuple) else res
                    digital = res[1] if isinstance(res, tuple) and len(res) > 1 else None
                    write_batch(s, digital)
                break

            # Normal drain tick
            raw = ppk2.get_data()
            if raw:
                res = ppk2.get_samples(raw)
                s = res[0] if isinstance(res, tuple) else res
                digital = res[1] if isinstance(res, tuple) and len(res) > 1 else None
                write_batch(s, digital)

            # Progress log every 30 seconds
            if time.time() - last_progress_log > 30.0:
                log(f"[drain] elapsed {elapsed:.0f}s, {samples_collected} samples so far")
                last_progress_log = time.time()

            time.sleep(DRAIN_INTERVAL_S)

        # 8. Stop sampling
        ppk2.stop_measuring()
        log(f"[ppk2] stop_measuring ({samples_collected} samples)")

        if csv_fp:
            csv_fp.close()
            csv_fp = None

        # Validate CSV
        if samples_collected < 100:
            err_msg = f"too few samples ({samples_collected})"
            return 1

        # 9. (Mode A) Wait for server.py to finish if still running
        if server_proc is not None:
            try:
                server_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                log("[server] still running after drain — terminating")
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_proc.kill()
            server_proc = None

        # 10. GDB telemetry — STM32 still powered, so SWD works
        log("[gdb] reading STM32 telemetry")
        gdb_ok = gdb_dump(args.elf, args.rpi_user, args.rpi_host, args.mode, telem_path)
        if gdb_ok:
            telem = telem_path.read_text()
            status_line = next((l for l in telem.splitlines() if l.startswith("status")), "")
            log(f"[gdb] {status_line}")
            if "0x600d0000" in status_line.lower():
                log("[gdb] status = 0x600D0000 (all checks passed) ✓")
            else:
                log(f"[gdb] WARNING: status not 0x600D0000 — {status_line}")
        else:
            log("[gdb] WARNING: telemetry dump empty (continuing)")

        # 11. Done — return code 0
        return 0

    except Exception as e:
        err_msg = f"unhandled exception: {type(e).__name__}: {e}"
        log(f"EXCEPTION: {err_msg}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Always close in reverse order
        if csv_fp is not None:
            try: csv_fp.close()
            except Exception: pass

        if server_proc is not None and server_proc.poll() is None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except Exception:
                try: server_proc.kill()
                except Exception: pass

        if ppk2 is not None:
            try:
                ppk2.toggle_DUT_power("OFF")
                log("[ppk2] DUT power OFF (LED should be GREEN)")
            except Exception as e:
                log(f"[ppk2] OFF failed: {e}")
            try:
                if hasattr(ppk2, "ser") and ppk2.ser is not None:
                    ppk2.ser.close()
            except Exception:
                pass

        # Write manifest
        manifest = {
            "curve": args.curve,
            "mode": args.mode,
            "replica": args.replica,
            "elf_path": str(args.elf),
            "csv_path": str(csv_path) if csv_path.exists() else None,
            "telemetry_path": str(telem_path) if telem_path.exists() else None,
            "samples_collected": samples_collected,
            "sample_period_us": sample_period_us if 'sample_period_us' in dir() else None,
            "samples_per_second": (
                int(round(1e6 / sample_period_us))
                if 'sample_period_us' in dir() and sample_period_us > 0 else None
            ),
            "duration_cap_s": args.duration,
            "honest_rounds": args.honest_rounds,
            "voltage_mv": args.voltage_mv,
            "t_start_unix": t_start,
            "t_end_unix": time.time(),
            "wall_s": time.time() - t_start,
            "error_message": err_msg,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))
        # ── BETON-BARZEL post-cell validation: gpio_byte diversity ──
        # If gpio_byte is uniformly 0 across all samples, the PPK2 digital
        # channels failed to capture firmware phase transitions. Without this,
        # phase-resolved energy analysis is impossible. Fail the cell.
        log("[validate] checking gpio_byte diversity in CSV")
        gpio_counts = {}
        with open(csv_path) as fp:
            next(fp)  # skip header
            for line in fp:
                try:
                    val = int(line.rstrip().split(",")[3])
                    gpio_counts[val] = gpio_counts.get(val, 0) + 1
                except (ValueError, IndexError):
                    continue
        unique_gpio = sorted(gpio_counts.keys())
        total = sum(gpio_counts.values())
        log(f"[validate] gpio_byte unique values: {unique_gpio} (total {total} samples)")
        if unique_gpio == [0]:
            log("[validate] FAIL: gpio_byte stuck at 0 — PPK2 digital capture broken")
            log("[validate]       Cannot do phase-resolved analysis without diversity")
            log("[validate]       Refusing to mark cell as successful")
            return 2
        elif len(unique_gpio) == 1:
            log(f"[validate] FAIL: gpio_byte stuck at {unique_gpio[0]} — phase capture broken")
            return 2
        # Diversity OK
        log(f"[validate] ✓ gpio_byte diversity confirmed ({len(unique_gpio)} distinct values)")

        log(f"manifest: {manifest_path}")
        # NOTE: do NOT restart ModemManager here — must stay stopped
        # across all cells. full_regression.sh restarts MM at end of run.


if __name__ == "__main__":
    sys.exit(main())
