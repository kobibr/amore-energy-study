# AmorE Energy Study

Energy and resource measurement of the AmorE protocol (Amortized Remote
Pairing Evaluation) on resource-constrained microcontrollers.

## What this project measures

The AmorE protocol delegates expensive bilinear-pairing operations from
a constrained client to an untrusted helper, preserving input privacy
and providing cheating-detection. This repository measures, on an
STM32F407 Cortex-M4 with 192 KB SRAM and 1 MB Flash, what AmorE *costs*
in:

- **Active energy per round** — joules consumed by the client to
  complete one pairing equivalent via the protocol, compared against
  native RELIC pairing.
- **Memory footprint** — Flash and SRAM used by AmorE versus RELIC.
- **Time per round** — wall-clock latency from request to verified result.
- **Crossover behaviour** — under duty-cycled workloads, where AmorE's
  active-energy penalty is potentially offset by Stop-mode quiescence
  during the helper's compute.

Two pairing-friendly curves are evaluated end-to-end: **BN254** and
**BLS12-381**.

## Headline findings

On STM32F407, AmorE pays a measured active-energy premium relative to
native RELIC pairing in exchange for substantial resource savings:

| Resource          | AmorE (BN254) | RELIC (BN254) | Ratio          |
|-------------------|---------------|---------------|----------------|
| Flash             | 18.5 KB       | 55.1 KB       | 3.0× lighter   |
| SRAM              | 3.1 KB        | 101.4 KB      | 32.7× lighter  |
| Time per round    | 381.8 ms      | 252.3 ms      | 1.51× slower   |
| Energy per round  | ~107 mJ       | ~71 mJ        | 1.51× heavier  |

For BLS12-381 the comparison is naturally made against three direct
pairings (the protocol's per-round verification structure involves three
pairing-equivalent server operations):

| Metric            | AmorE          | 3× direct      | Ratio          |
|-------------------|----------------|----------------|----------------|
| Time per round    | 1919 ms        | 1570 ms        | 1.22× slower   |
| Energy per round  | ~538 mJ        | ~441 mJ        | 1.22× heavier  |

The trade-off is **memory and privacy in exchange for active energy and
time**. On microcontrollers smaller than the F407 (where RELIC will not
fit at all), AmorE becomes the only feasible option.

## Where AmorE wins

The active-energy penalty above is measured at full client utilization.
The expected operating mode for an AmorE deployment is duty-cycled —
the client wakes, performs one batch of pairings, returns to deep
sleep. Under this regime the dominant energy term is sleep current
during the helper's compute, not the client's own compute. With a
Stop-mode-capable firmware (proposed extension; not yet integrated),
the client's contribution to per-round energy drops by several orders
of magnitude during the wait phase. This crossover analysis is a
forward-looking part of the study and depends on Stop-mode firmware
validation pending PPK2 instrumentation.

## Repository layout

```
amore-energy-study/
├── analysis/                Python analysis pipeline
│   ├── baseline_data.py     Measured timing/memory constants
│   ├── parse_traces.py      CSV → Phase records
│   ├── compute_energy.py    Phase → joules
│   ├── variance_summary.py  Replica statistics
│   ├── sleep_model.py       Batch energy model + crossover
│   ├── comm_energy_fit.py   UART payload → energy fit
│   └── figures/             Plot scripts (fig1, fig2, fig3)
├── measurement/
│   ├── ppk2-control/        Mock PPK2 server + client
│   ├── backends.py          Backend abstraction (Mock + PPK2)
│   └── traces/              CSV trace data (synthetic for now)
├── scripts/
│   ├── run_cell.py          Measurement orchestrator
│   └── integration_smoke.sh End-to-end sanity check
├── firmware/amore-fw/       Firmware submodule (instrumented)
├── report/figures/          Paper-bound figure PDFs
└── docs/                    Project overview (this file)
```

## How to reproduce the figures

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Verify the test suite
pytest

# Regenerate the four primary figures
for fig in fig1_energy_vs_n fig2_memory fig3_time_vs_n; do
    python3 -m analysis.figures.${fig}
done

# Output: report/figures/fig1_energy_vs_n.pdf
#         report/figures/fig2a_memory_bn254.pdf
#         report/figures/fig2b_memory_bls.pdf
#         report/figures/fig3_time_vs_n.pdf
```

All four primary figures regenerate from the constants in
`analysis/baseline_data.py`, which cite their source measurements in
`doc/AmorE_*_Results.txt` from the firmware repository. The constants
are locked by `analysis/tests/test_baseline_constants.py` — any drift
fails the test suite.

## Data sources

| Measurement                          | Source                                       |
|--------------------------------------|----------------------------------------------|
| BN254 amortized per-round timing     | `doc/AmorE_BN128_Results.txt` §4.2, §4.3     |
| BLS12-381 amortized per-round timing | `doc/AmorE_BLS12_381_Results.txt` §4.2       |
| Direct pairing time (RELIC)          | `doc/AmorE_*_Results.txt` §5 / §11           |
| Memory footprint (Flash, SRAM)       | `doc/AmorE_*_Results.txt` §2, §11.2          |

Measurements collected on STM32F407 at 168 MHz using the DWT cycle
counter, with cross-validation across N ∈ {1, 10, 50} batch sizes.
Coefficient of variation across replicates is < 0.001% for direct
pairing and < 0.5% for AmorE rounds.

## Status

Real PPK2 hardware instrumentation pending
device arrival; the measurement orchestrator is designed so that
swapping the mock backend for the real device requires a single import
change.

## Citation

If you reference this work, please cite the AmorE protocol paper:

> Aranha, D.F. et al. *AmorE: Amortized Remote pairing Evaluation*.
> Cryptology ePrint Archive, Report 2024/1187. <https://eprint.iacr.org/2024/1187>

Hardware measurement methodology and results from this repository
should be cited separately once the corresponding write-up is published.

## License

See `LICENSE` at the repository root.
