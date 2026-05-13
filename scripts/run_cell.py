"""run_cell.py — measurement orchestrator (backend-abstracted).

A "cell" is one (curve, mode, N) tuple with `replicas` independent runs.
Each replica produces one CSV trace via the configured backend.

Backend selection
-----------------
The orchestrator does not know whether data comes from a real PPK2 over
USB or a mock TCP server. It just calls ``backend.measure_replica(...)``.

IMPORT-SWITCH — a single line below selects the backend. Mock backend currently selected; real PPK2 swap-in is a single line change. Mock uses
``MockBackend``. When PPK2 arrives, change one import line to use
``PPK2Backend`` and run_cell behaves identically against real hardware
(after PPK2Backend is filled out — see measurement/backends.py).

Idempotency
-----------
A replica is considered complete when its CSV has at least one sample
row. Re-invoking run_cell.py skips already-complete replicas.

Smoke test
----------
``--smoke`` runs one 5-second replica with the idle scenario.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ─── IMPORT-SWITCH ────────────────────────────────────────────────────────────
# Currently selected (mock):
from measurement.backends import MockBackend as ActiveBackend
# Real PPK2 (single-line change when hardware arrives, after PPK2Backend is
# implemented — see measurement/backends.py PPK2Backend docstring):
# from measurement.backends import PPK2Backend as ActiveBackend
# ──────────────────────────────────────────────────────────────────────────────

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent


# ---------------------------------------------------------------------------
# Cell definition
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    curve: str
    mode: str
    n: int
    replicas: int
    duration_s: float
    gpio_source: str
    out_dir: Path

    @property
    def cell_id(self) -> str:
        return f"{self.curve.lower()}__{self.mode.lower()}__N{self.n}__r{self.replicas}"

    @property
    def cell_dir(self) -> Path:
        return self.out_dir / self.cell_id


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def is_run_complete(csv_path: Path) -> bool:
    """True iff the CSV has at least one sample row beyond the header."""
    if not csv_path.is_file():
        return False
    try:
        with csv_path.open() as f:
            f.readline()
            return bool(f.readline().strip())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Cell-level orchestration — backend-agnostic
# ---------------------------------------------------------------------------

def run_cell(cell: Cell, backend) -> int:
    """Run all replicas of a cell. Returns 0 if all succeeded.

    Writes manifest.json summarising the run.
    """
    print(f"\n══════════════════════════════════════════════════════════")
    print(f"  Cell: {cell.cell_id}")
    print(f"  backend: {type(backend).__name__}")
    print(f"══════════════════════════════════════════════════════════")
    print(f"  curve     : {cell.curve}")
    print(f"  mode      : {cell.mode}")
    print(f"  N         : {cell.n}")
    print(f"  replicas  : {cell.replicas}")
    print(f"  duration  : {cell.duration_s}s/replica")
    print(f"  source    : {cell.gpio_source}")
    print(f"  out_dir   : {cell.cell_dir}")
    print()

    cell.cell_dir.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_fail = 0
    n_skip = 0
    t0 = time.time()

    for i in range(1, cell.replicas + 1):
        csv_path = cell.cell_dir / f"run_{i:03d}.csv"

        if is_run_complete(csv_path):
            print(f"  [skip] {csv_path.name} already complete")
            n_skip += 1
            n_ok += 1
            continue

        print(f"  [start] {csv_path.name}  duration={cell.duration_s}s src={cell.gpio_source}")
        result = backend.measure_replica(
            csv_out=csv_path,
            duration_s=cell.duration_s,
            gpio_source=cell.gpio_source,
            log_dir=cell.cell_dir,
        )

        if result.ok:
            print(f"  [done] {csv_path.name}  ({result.sample_count} samples, "
                  f"{result.duration_actual_s:.1f}s wall)")
            n_ok += 1
        else:
            print(f"  [fail] {csv_path.name}  → {result.error_message}")
            n_fail += 1

    wall = time.time() - t0

    manifest = {
        "cell_id": cell.cell_id,
        "curve": cell.curve,
        "mode": cell.mode,
        "n": cell.n,
        "replicas_requested": cell.replicas,
        "replicas_ok": n_ok,
        "replicas_skipped_existing": n_skip,
        "replicas_fail": n_fail,
        "duration_s_per_replica": cell.duration_s,
        "wall_s_total": round(wall, 2),
        "gpio_source": cell.gpio_source,
        "backend": type(backend).__name__,
    }
    (cell.cell_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  manifest: {cell.cell_dir}/manifest.json")
    print(f"  result: {n_ok}/{cell.replicas} OK in {wall:.1f}s wall")
    return 0 if n_fail == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument("--curve", choices=["BN254", "BLS12_381"], default="BLS12_381")
    p.add_argument("--mode", choices=["A", "B", "C", "STOP", "WAKEUP"], default="A")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--replicas", type=int, default=3)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--gpio-source", default="fake-script:idle")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "measurement" / "traces")
    p.add_argument("--smoke", action="store_true",
                   help="5-second single-replica smoke test")
    args = p.parse_args(argv)

    if args.smoke:
        args.replicas = 1
        args.duration = 5.0
        args.gpio_source = "fake-script:idle"

    cell = Cell(
        curve=args.curve, mode=args.mode, n=args.n,
        replicas=args.replicas, duration_s=args.duration,
        gpio_source=args.gpio_source, out_dir=args.out,
    )
    backend = ActiveBackend(repo_root=REPO_ROOT) if ActiveBackend.__name__ == "MockBackend" else ActiveBackend()
    return run_cell(cell, backend)


if __name__ == "__main__":
    sys.exit(main())
