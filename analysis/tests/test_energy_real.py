"""
test_energy_real.py — safety net for the AmorE energy study.

Verifies two things that must always hold after the synthetic→real migration:
  1. The published energy numbers are reproduced by compute_energy from logs/.
  2. No synthetic / stop-mode code is imported anywhere in analysis/.

Run:  python3 -m pytest analysis/tests/test_energy_real.py -v
"""
from __future__ import annotations
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # repo root
ANALYSIS = ROOT / "analysis"
RUN_DIR = ROOT / "logs" / "full_regression_20260530_092609"
ENERGY_JSON = RUN_DIR / "energy_real.json"

# Published, measured values (6 replicas, phase-aware). Source of truth.
EXPECTED = {
    "bn254_A":     {"E_mJ": 159.75, "time_ms": 421.51},
    "bn254_B":     {"E_mJ": 85.09,  "time_ms": 218.92},
    "bls12_381_A": {"E_mJ": 353.55, "time_ms": 900.94},
    "bls12_381_B": {"E_mJ": 180.76, "time_ms": 523.41},
}
EXPECTED_RATIOS = {
    "bn254_ratio":     {"energy": 1.88, "time": 1.93},
    "bls12_381_ratio": {"energy": 1.96, "time": 1.72},
}

# Forbidden import targets — the synthetic / stop-mode era, all removed.
FORBIDDEN_IMPORTS = {
    "baseline_data", "synthetic_cells", "sleep_model", "stop_validation",
    "MockBackend", "_figure_watermark", "fixtures", "current_synthesis",
}


def _load_energy() -> dict:
    if not ENERGY_JSON.exists():
        pytest.skip(f"{ENERGY_JSON} not present (run compute_energy first)")
    return json.loads(ENERGY_JSON.read_text())


@pytest.mark.parametrize("cell,exp", EXPECTED.items())
def test_energy_numbers(cell, exp):
    """energy_real.json reports the published mJ / ms for each cell."""
    data = _load_energy()
    assert cell in data, f"{cell} missing from energy_real.json"
    assert data[cell]["E_mJ"] == pytest.approx(exp["E_mJ"], abs=0.5), \
        f"{cell} energy {data[cell]['E_mJ']} != expected {exp['E_mJ']}"
    assert data[cell]["time_ms"] == pytest.approx(exp["time_ms"], abs=0.5), \
        f"{cell} time {data[cell]['time_ms']} != expected {exp['time_ms']}"


@pytest.mark.parametrize("key,exp", EXPECTED_RATIOS.items())
def test_ratios(key, exp):
    """The headline ratios (AmorE costs ~1.9x energy, ~1.7-1.9x time)."""
    data = _load_energy()
    assert data[key]["energy"] == pytest.approx(exp["energy"], abs=0.02)
    assert data[key]["time"] == pytest.approx(exp["time"], abs=0.02)


def test_amore_costs_more_than_relic():
    """Core finding: AmorE per-round energy EXCEEDS a direct RELIC pairing."""
    data = _load_energy()
    assert data["bn254_A"]["E_mJ"] > data["bn254_B"]["E_mJ"]
    assert data["bls12_381_A"]["E_mJ"] > data["bls12_381_B"]["E_mJ"]


def _iter_analysis_py():
    for p in ANALYSIS.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_synthetic_imports():
    """No analysis module imports any synthetic / stop-mode symbol.

    Parses the AST and inspects only real import statements — comments
    mentioning historical names (e.g. 'matching stop_validation.py') are fine.
    """
    offenders = []
    for p in _iter_analysis_py():
        try:
            tree = ast.parse(p.read_text(), filename=str(p))
        except SyntaxError as e:
            offenders.append(f"{p.name}: SYNTAX ERROR {e}")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [a.name for a in node.names]
            for n in names:
                for bad in FORBIDDEN_IMPORTS:
                    if bad in n:
                        offenders.append(f"{p.name}: imports '{n}' (forbidden: {bad})")
    assert not offenders, "synthetic/stop imports found:\n" + "\n".join(offenders)


def test_all_analysis_parses():
    """Every analysis module is syntactically valid (no broken leftovers)."""
    broken = []
    for p in _iter_analysis_py():
        try:
            ast.parse(p.read_text(), filename=str(p))
        except SyntaxError as e:
            broken.append(f"{p.name}: {e}")
    assert not broken, "unparseable analysis modules:\n" + "\n".join(broken)
