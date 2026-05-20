"""audit_table.py — compile final results table from all measurement sources.

Walks the artifact tree and assembles a single auditable table:

  Claim → Value → Source file → Status (measured / computed / pending)

The intent is "what would Diego/reviewer want to see on one page?" For each
numeric claim in the paper, this table records the value, its origin,
and an explicit pending / measured / computed marker. No claim should
appear in the paper that this table can't back-link to.

Sources scanned:
  - measurement/voltage-sensitivity/voltage_*.txt
  - measurement/traces/*/variance_report.txt
  - measurement/calibration-logs/calibration_*.txt
  - measurement/stop-validation/stop_*.txt
  - firmware/amore-fw/logs/stm_report_*.txt

Computed claims (no measurement file, derived analytically):
  - comm_projection results (BLE/LoRa) via analysis.comm_projection
  - direct vs amore byte counts via firmware/amore-fw/inc/amore_uart.h

Output:
  - Stdout: human-readable table grouped by category
  - --md-out FILE:  markdown version (for docs/audit_table.md)
  - --csv-out FILE: CSV version (for spreadsheet / paper supplements)

Usage:
  python3 -m analysis.audit_table
  python3 -m analysis.audit_table --md-out docs/audit_table.md
  python3 -m analysis.audit_table --csv-out measurement/audit_table.csv

Exit codes:
  0 = table compiled (regardless of pending claims)
  2 = file-system or parsing error
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class AuditEntry:
    category: str
    claim: str
    value: str
    unit: str
    source: str
    status: str   # "measured" | "computed" | "pending"
    notes: str = ""

    def to_row(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────
# Parsers for each evidence source
# ─────────────────────────────────────────────────────────────────────

def parse_voltage_sensitivity(path: Path) -> list[AuditEntry]:
    """Pull I and P at each measured voltage from voltage_*.txt."""
    if not path.exists():
        return []
    text = path.read_text()
    entries: list[AuditEntry] = []

    # Lines like:
    #     3000   103.67±19.42   18.7%     311.006   ...
    row_re = re.compile(
        r"^\s*(\d+)\s+(\d+\.\d+)\s*\xb1\s*(\d+\.\d+)\s+(\d+\.\d+)%\s+(\d+\.\d+)"
    )
    src = path.name
    for line in text.splitlines():
        m = row_re.match(line)
        if m:
            v_mV = int(m.group(1))
            I_mA = float(m.group(2))
            I_std = float(m.group(3))
            P_mW = float(m.group(5))
            v_label = f"{v_mV/1000:.1f}V"
            entries.append(AuditEntry(
                category="electrical",
                claim=f"I @ {v_label} active",
                value=f"{I_mA:.2f} ± {I_std:.2f}",
                unit="mA",
                source=src,
                status="measured",
            ))
            entries.append(AuditEntry(
                category="electrical",
                claim=f"P @ {v_label} active",
                value=f"{P_mW:.2f}",
                unit="mW",
                source=src,
                status="measured",
            ))
    return entries


def parse_variance_report(path: Path) -> list[AuditEntry]:
    """Pull overall stats from variance_report.txt."""
    if not path.exists():
        return []
    text = path.read_text()
    entries: list[AuditEntry] = []
    src = str(path.relative_to(Path("measurement").parent)
              if path.is_absolute() else path)

    # variance_report.txt format includes total energy + CV
    # Look for lines like:
    #   total_energy_J  : mean=63.4123  stdev=0.1398
    m = re.search(r"total_energy_J\s*[:=]\s*mean=([\d.]+)\s+stdev=([\d.]+)", text)
    if m:
        mean_J = float(m.group(1))
        stdev_J = float(m.group(2))
        cv_pct = (100 * stdev_J / mean_J) if mean_J > 0 else 0.0
        entries.append(AuditEntry(
            category="variance",
            claim="Total energy per replica (between-replicas CV)",
            value=f"{mean_J:.4f} ± {stdev_J:.4f}",
            unit="J",
            source=src,
            status="measured",
            notes=f"CV = {cv_pct:.3f}%",
        ))
    return entries


def parse_stm_report(path: Path) -> list[AuditEntry]:
    """Pull amort/round numbers from firmware telemetry."""
    if not path.exists():
        return []
    text = path.read_text()
    entries: list[AuditEntry] = []
    src = path.name

    # [Batch N=1] section — amort/round = X cycles (Y ms)
    batch_re = re.compile(
        r"\[Batch N=(\d+)\].*?amort/round\s*=\s*(\d+)\s*cycles\s*\(([\d.]+)\s*ms\)",
        re.DOTALL,
    )
    for m in batch_re.finditer(text):
        N = int(m.group(1))
        cycles = int(m.group(2))
        ms = float(m.group(3))
        if cycles == 0:
            entries.append(AuditEntry(
                category="compute",
                claim=f"amort/round @ N={N}",
                value="—",
                unit="ms",
                source=src,
                status="pending",
                notes="batch incomplete in this run",
            ))
        else:
            entries.append(AuditEntry(
                category="compute",
                claim=f"amort/round @ N={N}",
                value=f"{ms:.1f}",
                unit="ms",
                source=src,
                status="measured",
                notes=f"{cycles} cycles @ 168MHz",
            ))

    # OneTimeSetup
    ots = re.search(r"ots_cycles\s*=\s*(\d+)\s*\(([\d.]+)\s*ms", text)
    if ots:
        entries.append(AuditEntry(
            category="compute",
            claim="One-time setup",
            value=f"{float(ots.group(2)):.1f}",
            unit="ms",
            source=src,
            status="measured",
        ))

    return entries


def parse_stop_validation(path: Path) -> list[AuditEntry]:
    """Pull stop-mode current from stop_validation summary."""
    if not path.exists():
        return []
    text = path.read_text()
    entries: list[AuditEntry] = []
    src = path.name

    m = re.search(r"Stop window:.*?mean:\s*([\d.]+)\s*\u00b5A", text, re.DOTALL)
    if m:
        uA = float(m.group(1))
        # If stop_test.elf was flashed we'd expect ~0.5 uA; large values mean
        # the firmware wasn't actually in stop mode at the time of capture.
        if uA < 100:
            status = "measured"
            notes = "stop-mode firmware flashed"
        else:
            status = "pending"
            notes = (
                f"reported {uA:.1f} uA — too high for stop-mode; "
                "needs stop_test.elf flashed"
            )
        entries.append(AuditEntry(
            category="electrical",
            claim="IDD_STOP (Stop-mode current)",
            value=f"{uA:.3f}",
            unit="µA",
            source=src,
            status=status,
            notes=notes,
        ))
    return entries


def parse_calibration(path: Path) -> list[AuditEntry]:
    """Record presence of a calibration log."""
    if not path.exists():
        return []
    return [AuditEntry(
        category="metrology",
        claim="Calibration evidence on file",
        value="present",
        unit="",
        source=path.name,
        status="measured",
        notes="See file for resistor value + ratio",
    )]


def compute_comm_projections() -> list[AuditEntry]:
    """Run analysis.comm_projection in-process to get its numbers."""
    entries: list[AuditEntry] = []
    try:
        from analysis.comm_projection import (
            ANCHORS, project_amore, project_direct,
        )
    except Exception as e:
        return [AuditEntry(
            category="comm",
            claim="Comm projections",
            value="ERR",
            unit="",
            source="comm_projection.py",
            status="pending",
            notes=f"import failed: {e}",
        )]
    for radio, anchor in ANCHORS.items():
        amore = project_amore(anchor)
        direct_50 = project_direct(50, anchor)
        # crossover
        per_pairing = project_direct(1, anchor)
        crossover_N = (
            amore["E_total_mJ"] / per_pairing["E_total_mJ"]
            if per_pairing["E_total_mJ"] > 0 else 0.0
        )
        entries.append(AuditEntry(
            category="comm",
            claim=f"Comm energy per round (AmorE, {radio})",
            value=f"{amore['E_total_mJ']:.3f}",
            unit="mJ",
            source="comm_projection.py (datasheet)",
            status="computed",
            notes="constant across N",
        ))
        entries.append(AuditEntry(
            category="comm",
            claim=f"Comm energy N=50 (Direct, {radio})",
            value=f"{direct_50['E_total_mJ']:.3f}",
            unit="mJ",
            source="comm_projection.py (datasheet)",
            status="computed",
        ))
        entries.append(AuditEntry(
            category="comm",
            claim=f"Comm-only crossover N ({radio})",
            value=f"{crossover_N:.2f}",
            unit="pairings",
            source="comm_projection.py (datasheet)",
            status="computed",
        ))
    return entries


# ─────────────────────────────────────────────────────────────────────
# Collector
# ─────────────────────────────────────────────────────────────────────

def collect_all(root: Path) -> list[AuditEntry]:
    entries: list[AuditEntry] = []

    # Voltage sensitivity
    for f in sorted((root / "measurement/voltage-sensitivity").glob("voltage_*.txt")):
        entries.extend(parse_voltage_sensitivity(f))

    # Variance reports
    for f in sorted((root / "measurement/traces").glob("**/variance_report.txt")):
        entries.extend(parse_variance_report(f))

    # Stop validation
    for f in sorted((root / "measurement/stop-validation").glob("stop_*.txt")):
        entries.extend(parse_stop_validation(f))

    # Calibration
    for f in sorted((root / "measurement/calibration-logs").glob("calibration_*.txt")):
        entries.extend(parse_calibration(f))

    # Firmware telemetry — most recent
    fw_logs = sorted(
        (root / "firmware/amore-fw/logs").glob("stm_report_*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if fw_logs:
        entries.extend(parse_stm_report(fw_logs[0]))

    # Computed
    entries.extend(compute_comm_projections())

    return entries


# ─────────────────────────────────────────────────────────────────────
# Output formats
# ─────────────────────────────────────────────────────────────────────

def render_stdout(entries: list[AuditEntry]) -> None:
    by_cat: dict[str, list[AuditEntry]] = {}
    for e in entries:
        by_cat.setdefault(e.category, []).append(e)

    for cat in sorted(by_cat):
        print(f"\n━━━ {cat.upper()} ━━━")
        for e in by_cat[cat]:
            mark = {"measured": "✓", "computed": "≈", "pending": "·"}[e.status]
            val = f"{e.value} {e.unit}".strip()
            print(f"  {mark} {e.claim:<48s} {val:>20s}   ({e.source})")
            if e.notes:
                print(f"      └ {e.notes}")

    # Status counts
    counts = {"measured": 0, "computed": 0, "pending": 0}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    total = sum(counts.values())
    print()
    print(f"  Total entries: {total}  "
          f"({counts['measured']} measured, "
          f"{counts['computed']} computed, "
          f"{counts['pending']} pending)")


def render_markdown(entries: list[AuditEntry]) -> str:
    out: list[str] = []
    out.append("# Audit Table — AmorE Energy Study")
    out.append("")
    out.append(f"Compiled by `analysis/audit_table.py`. Each claim is")
    out.append(f"annotated with status (measured / computed / pending)")
    out.append(f"and the source file it draws from.")
    out.append("")
    by_cat: dict[str, list[AuditEntry]] = {}
    for e in entries:
        by_cat.setdefault(e.category, []).append(e)

    for cat in sorted(by_cat):
        out.append(f"## {cat.capitalize()}")
        out.append("")
        out.append("| Claim | Value | Status | Source | Notes |")
        out.append("|-------|-------|--------|--------|-------|")
        for e in by_cat[cat]:
            val = f"{e.value} {e.unit}".strip()
            mark = {"measured": "✓ measured",
                    "computed": "≈ computed",
                    "pending":  "· pending"}[e.status]
            notes = e.notes.replace("|", "\\|") if e.notes else ""
            out.append(f"| {e.claim} | {val} | {mark} | `{e.source}` | {notes} |")
        out.append("")

    return "\n".join(out)


def render_csv(entries: list[AuditEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["category", "claim", "value", "unit", "source", "status", "notes"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for e in entries:
            w.writerow(e.to_row())


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, default=Path("."),
                   help="Project root (default: current dir)")
    p.add_argument("--md-out", type=Path, default=None,
                   help="Write Markdown table to this file")
    p.add_argument("--csv-out", type=Path, default=None,
                   help="Write CSV table to this file")
    args = p.parse_args(argv)

    try:
        entries = collect_all(args.root)
    except Exception as e:
        print(f"FATAL: collection error: {e}", file=sys.stderr)
        return 2

    if not entries:
        print("  ⚠ no evidence sources found")
        return 0

    render_stdout(entries)

    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(entries))
        print(f"\n  Markdown: {args.md_out}")

    if args.csv_out:
        render_csv(entries, args.csv_out)
        print(f"  CSV:      {args.csv_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
