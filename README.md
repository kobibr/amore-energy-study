# AmorE Energy Study

Energy measurement of the AmorE protocol (Amortized Remote Pairing
Evaluation) on STM32F407 + Raspberry Pi 3B, with PPK2 current sensing.

## Status

**PPK2 hardware in hand; production measurement sweep underway.**

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Run all tests
pytest

# Smoke test (5 sec)
python3 scripts/run_cell.py --smoke --out /tmp/smoke

# Generate all 4 figures (uses synthetic data)
for fig in plot_energy_per_round plot_phase_breakdown plot_mode_comparison plot_crossover; do
    python3 -m "analysis.${fig}"
done

# End-to-end integration test (~30s)
bash scripts/integration_smoke.sh
```

## Repository layout

```
amore-energy-study/
├── analysis/                       Python analysis pipeline
│   ├── parse_traces.py             CSV → list[Phase]
│   ├── compute_energy.py           Phase → energy_J
│   ├── variance_summary.py         replicas → mean ± stderr
│   ├── comm_energy_fit.py          payload_bytes → energy linear fit
│   ├── sleep_model.py              BatchModel + crossover
│   ├── plot_*.py                   4 figures (Fig 1–4)
│   ├── fixtures/synthetic_cells.py CSV generator from baseline data
│   └── tests/                      17 unit tests
├── measurement/
│   ├── ppk2-control/               Mock PPK2 server + client + 171 tests
│   └── traces/                     CSV traces (synthetic now, real later)
├── firmware/amore-fw/              submodule → kobibr/amore-bn254-cortex-m4
│                                   feature/energy-instrumentation branch
├── scripts/
│   ├── run_cell.py                 Measurement orchestrator
│   ├── integration_smoke.sh        End-to-end test
│   └── build_matrix.sh             Compile every firmware variant
├── docs/
│   ├── methodology.md              How the experiments work
│   └── comm_anchors.md             UART energy anchor points
├── figures/                        Generated PNGs (fig1–fig4)
└── internal/
    ├── diary.md                    Day-by-day log
    └── iter*.csv                   Earlier dev traces
```

## When PPK2 arrives

One-line change: `scripts/run_cell.py` (search for `# IMPORT-SWITCH`).
Everything else — analysis, plots, tests — is rate-agnostic and runs
unchanged on real PPK2 CSVs.

## Tags

The repo carries milestone tags marking incremental progress:

- scaffold — Mock PPK2 stack and project skeleton (171 tests)
- firmware-baseline — firmware submodule integration (STM32CubeF4)
- firmware-triggers — GPIO trigger instrumentation in firmware
- firmware-lowpower — Stop-mode and wake-up burst firmware variants
- framework-complete — orchestrator + analysis pipeline + figures + docs



## A note on tags


## Status checks

| Item | Count | Status |
|------|-------|--------|
| Unit tests passing | 232 | ✓ |
| Firmware ELF variants | 5 | ✓ |
| Synthetic cells | 10 × 3 replicas | ✓ |
| Figures generated | 4 PNGs | ✓ |
| Integration smoke runtime | <30s | ✓ |
