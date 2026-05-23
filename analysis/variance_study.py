"""variance_study.py — replica orchestrator + per-phase variance reporter.

Drives a backend (Mock or real PPK2) to capture N independent replicas
of a single measurement cell. Parses each trace, computes energy via
compute_energy.compute_trace, and aggregates with
variance_summary.summarize_replicas.

This is the Day 7 deliverable: wraps `backend.measure_replica()` in a
loop, watchdog'd, and reports per-phase variance.

Output:
  - N CSV traces in `<out-dir>/run_001.csv` ... `run_NNN.csv`
  - manifest.json with replica metadata
  - variance_report.txt with per-gpio_byte mean ± stdev (and CV)
  - Exit 0 if all replicas succeeded + (optional) variance within tolerance

Usage:
  # Mock (no hardware needed):
  python3 -m analysis.variance_study --backend mock --replicas 3 \\
      --duration 3.0 --out /tmp/variance_mock

  # Real PPK2:
  python3 -m analysis.variance_study --backend ppk2 --replicas 5 \\
      --duration 30.0 --out measurement/traces/variance_BLS_baseline

Exit codes:
  0 = all replicas succeeded
  1 = one or more replicas failed
  2 = backend setup error
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List

# Repo root path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "measurement"))

from analysis.compute_energy import compute_trace, TraceEnergy
from analysis.parse_traces import parse_trace
from analysis.variance_summary import summarize_replicas, CellSummary


# Bug #3 fix: actual watchdog implementation. Used to wrap
# backend.measure_replica() so a hung USB / stuck pyserial read can't
# pin the whole sweep. SIGALRM is Linux-only and works only in the
# main thread — both true of the Pi context this script targets.
class WatchdogTimeout(Exception):
    """Raised when backend.measure_replica exceeds its allotted time."""


def _sigalrm_handler(_signum, _frame):
    raise WatchdogTimeout("measure_replica exceeded watchdog deadline")


def measure_replica_with_watchdog(backend, *, timeout_s: float, **kwargs):
    """Run backend.measure_replica with a SIGALRM-based timeout.

    Returns whatever the backend returns. If the watchdog fires, the
    exception is allowed to propagate so the caller can build a FAIL
    MeasurementResult and continue with the next replica.
    """
    if not hasattr(signal, "SIGALRM"):
        # Non-Linux fallback: no watchdog available. Better to run
        # without protection than refuse to run at all.
        return backend.measure_replica(**kwargs)

    old_handler = signal.signal(signal.SIGALRM, _sigalrm_handler)
    signal.alarm(max(1, int(timeout_s)))
    try:
        return backend.measure_replica(**kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def get_backend(name: str, ppk2_port: str | None = None) -> Any:
    """Lazy-import backend by name. For PPK2, auto-detect port if not given."""
    if name == "mock":
        from backends import MockBackend
        return MockBackend(repo_root=ROOT)
    elif name == "ppk2":
        from backends import PPK2Backend
        if ppk2_port is None:
            # Auto-detect via PPK2 enumeration
            from ppk2_api.ppk2_api import PPK2_API
            devs = PPK2_API.list_devices()
            if not devs:
                raise RuntimeError("no PPK2 device found via PPK2_API.list_devices()")
            ppk2_port = devs[0] if isinstance(devs[0], str) else devs[0][0]
            print(f"  auto-detected PPK2 port: {ppk2_port}")
        return PPK2Backend(serial_port=ppk2_port)
    else:
        raise ValueError(f"unknown backend: {name}")


def run_replicas(backend, out_dir: Path, n_replicas: int, duration_s: float,
                  gpio_source: str) -> dict[str, Any]:
    """Run N replicas, return manifest dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    manifest = {
        "started_at": datetime.now().isoformat(),
        "backend": type(backend).__name__,
        "n_replicas_requested": n_replicas,
        "duration_s_per_replica": duration_s,
        "gpio_source": gpio_source,
        "out_dir": str(out_dir),
        "replicas": [],
    }

    n_ok = 0
    n_fail = 0
    t_total = time.time()

    # Bug #3 fix: each replica has a deadline of duration_s * 3 + 30s.
    # Generous enough for normal slow PPK2 settle + USB enumeration,
    # tight enough to catch a true hang within a couple of minutes.
    watchdog_per_replica_s = duration_s * 3.0 + 30.0

    for i in range(1, n_replicas + 1):
        csv_path = out_dir / f"run_{i:03d}.csv"
        print(f"  [{i}/{n_replicas}] {csv_path.name}  duration={duration_s}s  "
              f"(watchdog={watchdog_per_replica_s:.0f}s)")
        t0 = time.time()
        try:
            result = measure_replica_with_watchdog(
                backend,
                timeout_s=watchdog_per_replica_s,
                csv_out=csv_path,
                duration_s=duration_s,
                gpio_source=gpio_source,
                log_dir=log_dir,
            )
        except WatchdogTimeout:
            # Synthesize a FAIL MeasurementResult so the manifest /
            # downstream loop logic stays uniform.
            from backends import MeasurementResult
            result = MeasurementResult(
                ok=False,
                csv_path=csv_path,
                sample_count=0,
                duration_actual_s=time.time() - t0,
                error_message=(
                    f"watchdog timeout after {watchdog_per_replica_s:.0f}s "
                    f"(backend.measure_replica did not return)"
                ),
            )
        except Exception as e:
            # Bug #2 fix: previously only WatchdogTimeout was caught.
            # Any other backend exception (SerialException, USBError,
            # OSError, KeyError from a malformed result, internal
            # backend bug) propagated out of run_replicas and killed
            # the whole sweep: no manifest.json was written, every
            # already-collected CSV was orphaned from the perspective
            # of downstream tooling, and the watchdog's whole purpose
            # — "continue past one bad replica" — was nullified by any
            # non-timeout fault. Broaden to Exception (NOT BaseException
            # so KeyboardInterrupt still works).
            import traceback
            tb_path = log_dir / f"run_{i:03d}.traceback.log"
            try:
                tb_path.write_text(traceback.format_exc())
            except Exception:
                pass
            from backends import MeasurementResult
            result = MeasurementResult(
                ok=False,
                csv_path=csv_path,
                sample_count=0,
                duration_actual_s=time.time() - t0,
                error_message=(
                    f"backend exception {type(e).__name__}: {e} "
                    f"(traceback in {tb_path.name})"
                ),
            )
        wall_s = time.time() - t0

        rec = {
            "index": i,
            "csv": str(csv_path),
            "ok": bool(result.ok),
            "sample_count": result.sample_count,
            "duration_actual_s": result.duration_actual_s,
            "wall_s": round(wall_s, 2),
            "error_message": result.error_message,
        }
        manifest["replicas"].append(rec)

        if result.ok:
            n_ok += 1
            print(f"       [done] {result.sample_count} samples, "
                  f"{result.duration_actual_s:.1f}s actual, {wall_s:.1f}s wall")
        else:
            n_fail += 1
            print(f"       [FAIL] {result.error_message}")

        # Inter-replica grace period.
        # Bug #6 fix: only sleep when running on real PPK2 hardware,
        # which actually re-enumerates over USB between replicas
        # (~1-2s). MockBackend has no USB and no enumeration, so a
        # 3-second sleep × n_replicas was pure dead time in CI and
        # mock tests. ~90s wasted on a 30-replica mock sweep.
        if i < n_replicas:
            backend_name = type(backend).__name__
            if backend_name == "PPK2Backend":
                print(f"       [wait 3s for PPK2 to re-enumerate]")
                time.sleep(3.0)
            # else: MockBackend or other — no inter-replica wait needed.

    manifest["wall_s_total"] = round(time.time() - t_total, 2)
    manifest["n_ok"] = n_ok
    manifest["n_fail"] = n_fail
    manifest["ended_at"] = datetime.now().isoformat()

    return manifest


def compute_per_replica_energy(csv_paths: List[Path]) -> List[TraceEnergy]:
    """Parse + compute energy for each replica CSV."""
    traces: List[TraceEnergy] = []
    for path in csv_paths:
        phases = parse_trace(path)
        te = compute_trace(phases)
        traces.append(te)
    return traces


def write_variance_report(report_path: Path, manifest: dict,
                           summary: CellSummary) -> None:
    """Write the human-readable per-phase variance report."""
    with report_path.open("w") as f:
        f.write("Variance Study Report\n")
        f.write("=" * 70 + "\n")
        f.write(f"Started:  {manifest['started_at']}\n")
        f.write(f"Ended:    {manifest['ended_at']}\n")
        f.write(f"Backend:  {manifest['backend']}\n")
        f.write(f"Replicas: {manifest['n_ok']} ok / "
                f"{manifest['n_replicas_requested']} requested\n")
        f.write(f"Duration: {manifest['duration_s_per_replica']}s per replica\n")
        f.write(f"GPIO src: {manifest['gpio_source']}\n")
        f.write(f"Wall:     {manifest['wall_s_total']}s total\n")
        f.write("\n")

        # Total energy
        te = summary.total_energy_J
        f.write(f"Total energy (J) across replicas:\n")
        f.write(f"  n={te.n}  mean={te.mean:.6f}  stdev={te.stdev:.6f}  "
                f"stderr={te.stderr:.6f}  CV={te.cv*100:.2f}%  "
                f"[min={te.min:.6f}, max={te.max:.6f}]\n")
        f.write("\n")

        # Total duration
        td = summary.total_duration_us
        f.write(f"Total duration (µs) across replicas:\n")
        f.write(f"  n={td.n}  mean={td.mean:.1f}  stdev={td.stdev:.1f}  "
                f"stderr={td.stderr:.1f}  CV={td.cv*100:.2f}%  "
                f"[min={td.min:.1f}, max={td.max:.1f}]\n")
        f.write("\n")

        # Per-gpio_byte energy
        f.write("Per-gpio_byte energy (mJ) across replicas:\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'gpio_byte':>10}  {'n':>3}  {'mean_mJ':>12}  "
                f"{'stdev_mJ':>12}  {'stderr_mJ':>12}  {'CV%':>8}\n")
        f.write("-" * 70 + "\n")
        for gb in sorted(summary.by_gpio_byte_energy_J.keys()):
            s = summary.by_gpio_byte_energy_J[gb]
            f.write(f"{gb:>10}  {s.n:>3}  "
                    f"{s.mean*1000:>12.4f}  {s.stdev*1000:>12.4f}  "
                    f"{s.stderr*1000:>12.4f}  {s.cv*100:>7.2f}%\n")
        f.write("-" * 70 + "\n")
        f.write("\n")

        # Per-gpio_byte duration
        f.write("Per-gpio_byte duration (ms) across replicas:\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'gpio_byte':>10}  {'n':>3}  {'mean_ms':>12}  "
                f"{'stdev_ms':>12}  {'CV%':>8}\n")
        f.write("-" * 70 + "\n")
        for gb in sorted(summary.by_gpio_byte_duration_us.keys()):
            s = summary.by_gpio_byte_duration_us[gb]
            f.write(f"{gb:>10}  {s.n:>3}  "
                    f"{s.mean/1000:>12.3f}  {s.stdev/1000:>12.3f}  "
                    f"{s.cv*100:>7.2f}%\n")
        f.write("-" * 70 + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--backend", choices=["mock", "ppk2"], default="mock",
        help="Backend implementation (default: mock)",
    )
    p.add_argument(
        "--replicas", type=int, default=3,
        help="Number of independent replicas (default: 3)",
    )
    p.add_argument(
        "--duration", type=float, default=5.0,
        help="Duration in seconds per replica (default: 5.0)",
    )
    p.add_argument(
        "--gpio-source", default="fake-script:idle",
        help="GPIO source for mock backend (default: fake-script:idle)",
    )
    p.add_argument(
        "--out", type=Path, required=True,
        help="Output directory for CSVs + manifest + report",
    )
    p.add_argument(
        "--ppk2-port", type=str, default=None,
        help="Override PPK2 serial port (default: auto-detect via PPK2_API.list_devices())",
    )
    p.add_argument(
        "--max-cv-pct", type=float, default=None,
        help="Optional: fail if any gpio_byte energy CV%% > threshold",
    )
    args = p.parse_args(argv)

    # Resolve backend
    try:
        backend = get_backend(args.backend, ppk2_port=args.ppk2_port)
    except Exception as e:
        print(f"FATAL backend setup: {e}", file=sys.stderr)
        return 2

    print(f"=== variance_study.py ===")
    print(f"  backend:  {type(backend).__name__}")
    print(f"  replicas: {args.replicas}")
    print(f"  duration: {args.duration}s per replica")
    print(f"  source:   {args.gpio_source}")
    print(f"  out:      {args.out}")
    print()

    # Run all replicas
    print("=== Capturing replicas ===")
    manifest = run_replicas(
        backend=backend,
        out_dir=args.out,
        n_replicas=args.replicas,
        duration_s=args.duration,
        gpio_source=args.gpio_source,
    )

    # Persist manifest
    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  manifest: {manifest_path}")

    # If any failed, bail
    if manifest["n_fail"] > 0:
        print(f"\n  ✗ {manifest['n_fail']} replicas failed — see manifest")
        return 1

    # Compute energy per replica
    print("\n=== Computing energy per replica ===")
    csv_paths = [Path(r["csv"]) for r in manifest["replicas"] if r["ok"]]
    traces = compute_per_replica_energy(csv_paths)
    print(f"  ✓ {len(traces)} traces computed")

    # Aggregate via summarize_replicas
    summary = summarize_replicas(traces)

    # Write report
    report_path = args.out / "variance_report.txt"
    write_variance_report(report_path, manifest, summary)
    print(f"\n  report: {report_path}")

    # Print compact summary table to stdout
    print()
    print(f"  {'gpio_byte':>10}  {'n':>3}  {'mean_mJ':>12}  "
          f"{'stdev_mJ':>12}  {'CV%':>8}")
    print("  " + "-" * 60)
    for gb in sorted(summary.by_gpio_byte_energy_J.keys()):
        s = summary.by_gpio_byte_energy_J[gb]
        print(f"  {gb:>10}  {s.n:>3}  "
              f"{s.mean*1000:>12.4f}  {s.stdev*1000:>12.4f}  "
              f"{s.cv*100:>7.2f}%")
    print()

    te = summary.total_energy_J
    print(f"  TOTAL energy: mean={te.mean*1000:.3f} mJ  "
          f"stdev={te.stdev*1000:.3f} mJ  CV={te.cv*100:.2f}%")
    print()

    # Optional tolerance check
    if args.max_cv_pct is not None:
        bad = [gb for gb, s in summary.by_gpio_byte_energy_J.items()
               if s.cv * 100 > args.max_cv_pct]
        if bad:
            print(f"  ✗ FAIL: gpio_bytes {bad} have CV%% > {args.max_cv_pct}")
            return 1
        print(f"  ✓ all CV%% within ±{args.max_cv_pct}%")

    print("  ✓ variance study complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
